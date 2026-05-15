"""
agent_risk_mgmt.py — RiskManagement agent.

Pure math — no AI call. Validates position sizing, SL quality, Kelly
criterion, and directional concentration. _calc_sizing() migrated here
from ai_call.py.
"""
import trade_utils
from agent_types import RiskInput, RiskResult

LEVERAGE      = 10
MAX_SAME_SIDE = 4     # correlation warning threshold


def run(inp: RiskInput, conn=None) -> RiskResult:
    prep           = inp["trade_prep"]
    account_equity = inp["account_equity"]
    open_positions = inp["open_positions"]

    entry     = prep["entry_price"]
    sl        = prep["sl_price"]
    direction = prep["direction"]
    is_long   = direction.lower() == "long"
    warnings  = []

    # Validate SL side
    if entry and sl:
        if is_long and sl >= entry:
            return _blocked(["Long stop loss must be below entry price"])
        if not is_long and sl <= entry:
            return _blocked(["Short stop loss must be above entry price"])

    sizing = _calc_sizing(account_equity, entry, sl, direction=direction)
    if "error" in sizing:
        return _blocked([sizing["error"]])

    # ATR SL quality check
    atr_warn = ""
    atr_valid = True
    if entry and sl:
        try:
            symbol = prep.get("raw_json", {}).get("symbol", "") or ""
            atr_warn = trade_utils.atr_sl_warning(symbol, entry, sl)
            atr_valid = not bool(atr_warn)
        except Exception:
            pass
    if atr_warn:
        warnings.append(atr_warn)

    # Directional concentration
    corr_warn = _correlation_check(direction, open_positions)
    if corr_warn:
        warnings.append(corr_warn)

    max_risk = _max_risk_check(direction, open_positions)
    if max_risk:
        warnings.append(f"Already {MAX_SAME_SIDE}+ {direction} positions — high directional risk")

    kelly = _kelly(prep)
    approved = atr_valid and not max_risk

    return RiskResult(
        approved             = approved,
        position_size_usdt   = sizing.get("notional", 0.0),
        margin_usdt          = sizing.get("margin", 0.0),
        risk_pct             = sizing.get("risk_pct", 1.0),
        atr_sl_valid         = atr_valid,
        correlation_warning  = corr_warn,
        max_risk_hit         = max_risk,
        kelly_fraction       = kelly,
        warnings             = warnings,
        sizing_breakdown     = sizing,
    )


def _calc_sizing(account_equity: float, entry: float, sl: float,
                 dca_price: float = None, dca_pct: int = 40,
                 leverage: int = LEVERAGE, direction: str = "Long") -> dict:
    """Position sizing based on fixed risk % of equity. Migrated from ai_call.py."""
    is_long  = direction.lower() == "long"
    has_dca  = dca_price is not None
    risk_pct = 2.0 if has_dca else 1.0
    risk_amt = round(account_equity * risk_pct / 100, 2)

    if has_dca:
        e1_pct    = 100 - dca_pct
        avg_entry = (entry * e1_pct + dca_price * dca_pct) / 100
    else:
        avg_entry = entry

    if is_long and avg_entry <= sl:
        return {"error": "Long stop loss must be below entry price"}
    if not is_long and avg_entry >= sl:
        return {"error": "Short stop loss must be above entry price"}

    stop_dist = abs(avg_entry - sl) / avg_entry
    if stop_dist == 0:
        return {"error": "Entry and stop loss are the same price"}

    notional = round(risk_amt / stop_dist, 0)
    margin   = round(notional / leverage, 2)

    return {
        "account_equity": round(account_equity, 2),
        "risk_pct":       risk_pct,
        "risk_amt":       risk_amt,
        "avg_entry":      avg_entry,
        "stop_dist_pct":  round(stop_dist * 100, 3),
        "notional":       notional,
        "margin":         margin,
        "leverage":       leverage,
    }


def _kelly(prep: dict) -> float:
    """Kelly criterion using setup_score as edge proxy. Capped 0.05–0.25."""
    score = prep.get("setup_score", 5)
    # Map score 1-10 to win_rate proxy 0.35–0.75
    win_rate = 0.35 + (score / 10) * 0.40
    avg_win_r = 2.0  # conservative 2:1 R:R baseline
    f = (win_rate * avg_win_r - (1 - win_rate)) / avg_win_r
    return round(max(0.05, min(0.25, f)), 3)


def _correlation_check(direction: str, positions: list) -> str:
    side = "long" if direction.lower() == "long" else "short"
    count = sum(1 for p in positions if str(p.get("side", "")).lower() == side)
    if count >= 3:
        return f"Already {count} {direction} positions open — directional concentration risk"
    return ""


def _max_risk_check(direction: str, positions: list) -> bool:
    side  = "long" if direction.lower() == "long" else "short"
    count = sum(1 for p in positions if str(p.get("side", "")).lower() == side)
    return count >= MAX_SAME_SIDE


def _blocked(warnings: list) -> RiskResult:
    return RiskResult(
        approved=False, position_size_usdt=0.0, margin_usdt=0.0,
        risk_pct=0.0, atr_sl_valid=False, correlation_warning="",
        max_risk_hit=False, kelly_fraction=0.05,
        warnings=warnings, sizing_breakdown={},
    )


def blocked(warnings: list) -> "RiskResult":
    """Public alias — use from outside this module."""
    return _blocked(warnings)
