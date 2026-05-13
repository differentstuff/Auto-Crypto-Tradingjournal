"""
agent_data_reviewer.py — DataReviewer + KPI Generator agent.

Reads from DB to generate backtest context and trading KPIs. Quality-
gates the technical picture before an expensive Claude call. Never raises.
"""
from analytics import get_backtest_context, get_dashboard_kpis
from trade_history import get_symbol_summary
from prompt_builder import get_setup_rubric
from agent_types import ReviewerInput, ReviewerResult


def run(inp: ReviewerInput, conn) -> ReviewerResult:
    interpreted = inp["interpreted"]
    symbol      = inp["symbol"]
    direction   = inp["direction"]
    setup_type  = inp["setup_type"]

    backtest = ""
    kpis     = {}
    history  = {}
    try:
        backtest = get_backtest_context(conn, symbol, direction, setup_type)
    except Exception:
        pass

    try:
        raw_kpis = get_dashboard_kpis(filters={"symbol": symbol}, conn=conn)
        kpis = {
            "win_rate_pct":  raw_kpis.get("win_rate", 0),
            "avg_win":       raw_kpis.get("avg_win", 0),
            "avg_loss":      raw_kpis.get("avg_loss", 0),
            "profit_factor": raw_kpis.get("profit_factor", 0),
            "total_trades":  raw_kpis.get("total_trades", 0),
        }
    except Exception:
        pass

    try:
        history = get_symbol_summary(symbol, conn)
    except Exception:
        pass

    rubric = get_setup_rubric(setup_type)
    quality, warnings = _signal_quality(interpreted, setup_type)

    return ReviewerResult(
        signal_quality   = quality,
        warnings         = warnings,
        backtest_context = backtest,
        kpis             = kpis,
        symbol_history   = history,
        rubric           = rubric,
    )


def _signal_quality(interpreted: dict, setup_type: str) -> tuple:
    score    = 10.0
    warnings = []

    conf       = interpreted.get("confluence_score", {})
    conf_score = conf.get("score", 0)
    if conf_score < 3:
        score -= 2.0
        warnings.append(f"Confluence {conf_score:.1f} — weak multi-signal alignment")

    # ADX gate for trend-dependent setups
    if setup_type in ("breakout", "continuation"):
        for tf, data in interpreted.get("by_timeframe", {}).items():
            adx_val = data.get("adx", {}).get("value")
            if adx_val is not None:
                try:
                    if float(adx_val) < 20:
                        score -= 1.5
                        warnings.append(f"ADX {adx_val} ({tf}) — no clear trend for {setup_type}")
                except (TypeError, ValueError):
                    pass
                break

    # S/R touch count
    sr = interpreted.get("sr_levels", [])
    if sr and all(s.get("touches", 2) < 2 for s in sr[:3]):
        score -= 1.0
        warnings.append("Only 1 S/R touch on nearest levels — weak level")

    # RSI neutral zone
    for tf, data in interpreted.get("by_timeframe", {}).items():
        rsi_val = data.get("rsi", {}).get("value")
        if rsi_val is not None:
            try:
                rsi = float(rsi_val)
                if 40 <= rsi <= 60:
                    score -= 0.5
                    warnings.append(f"RSI {rsi:.0f} ({tf}) — neutral zone")
            except (TypeError, ValueError):
                pass
        break

    if interpreted.get("momentum_bias") == "conflicted":
        score -= 1.0
        warnings.append("Conflicted momentum — indicators disagree on direction")

    return round(max(0.0, min(10.0, score)), 1), warnings
