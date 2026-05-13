"""
agent_orchestrator.py — Multi-agent consensus scoring and model routing.

This module is the "master chief" layer of the trading journal's AI stack.
It does not call any AI directly — it coordinates results from multiple
AI providers (Claude, Gemini, Grok) into a single consensus output.

Core functions:
  compute_consensus(claude_score, gemini_score) → consensus dict
  add_gemini_consensus(setups, ctx_map)          → setups enriched with consensus
  route_model(task)                              → MODEL or FAST_MODEL constant

Consensus algorithm:
  |claude - gemini| ≤ 1 → high confidence, avg score, "✓ Confirmed"
  |claude - gemini| ≤ 2 → medium confidence, avg score, "~ Aligned"
  |claude - gemini| ≤ 3 → low confidence, Claude 60% weight, "⚠ Divergent"
  |claude - gemini| > 3 → very low, Claude score kept, "⚡ REVIEW"

Model routing — task → optimal model:
  Tasks that are classification/rating with short output → FAST_MODEL (Haiku)
  Tasks that need structured reasoning, long JSON, or code → MODEL (Sonnet)
"""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from constants import (
    MODEL, FAST_MODEL,
    CONSENSUS_HIGH_DELTA, CONSENSUS_MED_DELTA, CONSENSUS_LOW_DELTA,
)
import gemini_client
import agent_data_collector
import agent_data_interpreter
import agent_market_sentiment
import agent_data_reviewer
import agent_trade_prep
import agent_risk_mgmt
import agent_trade_monitor
from agent_types import AnalysisResult, TradePrepInput, RiskInput, MonitorInput

# ── Task → model routing table ─────────────────────────────────────────────────

_FAST_TASKS = frozenset({
    "hindsight",        # retroactive blind scoring: simple classification
    "live_trade",       # quick action recommendation: Hold/Close/Adjust
    "trade_grader",     # A/B/C/D grade: simple rubric classification
    "scanner_quick",    # haiku quick-score pass: 0-10 with one sentence
})

_SONNET_TASKS = frozenset({
    "call_analyzer",    # full structured JSON, complex reasoning
    "advisor",          # portfolio coaching, long-form advice
    "rulebook",         # synthesises entire trade history
    "scanner_batch",    # scores N setups simultaneously, needs context
    "limit_analyzer",   # limit order analysis: needs entry/risk reasoning
    "pattern_detector", # cross-pattern compound analysis
})


def route_model(task: str) -> str:
    """Return the optimal model constant for a given task name."""
    if task in _FAST_TASKS:
        return FAST_MODEL
    return MODEL  # default to Sonnet for unknown tasks


# ── Consensus algorithm ────────────────────────────────────────────────────────

def compute_consensus(
    claude_score: int | float,
    gemini_score: int | float,
) -> dict:
    """
    Compute multi-agent consensus from Claude and Gemini scores.

    Returns:
      {
        "consensus_score": float,     # the agreed-upon score (1-10)
        "claude_score":    int,
        "gemini_score":    int,
        "delta":           int,       # absolute difference
        "confidence":      str,       # high | medium | low | very_low
        "flag":            str,       # ✓ Confirmed | ~ Aligned | ⚠ Divergent | ⚡ REVIEW
        "prompt_line":     str,       # compact line for Claude prompts
      }
    """
    c = int(round(claude_score))
    g = int(round(gemini_score))
    delta = abs(c - g)

    if delta <= CONSENSUS_HIGH_DELTA:
        score      = round((c + g) / 2, 1)
        confidence = "high"
        flag       = "✓ Confirmed"
    elif delta <= CONSENSUS_MED_DELTA:
        score      = round((c + g) / 2, 1)
        confidence = "medium"
        flag       = "~ Aligned"
    elif delta <= CONSENSUS_LOW_DELTA:
        # Mild divergence — weight Claude higher (has full context)
        score      = round(c * 0.60 + g * 0.40, 1)
        confidence = "low"
        flag       = "⚠ Divergent"
    else:
        # Strong conflict — keep Claude score, surface for user review
        score      = float(c)
        confidence = "very_low"
        flag       = "⚡ REVIEW"

    prompt_line = (
        f"CONSENSUS SCORE: {score}/10 [{flag}] "
        f"(Claude {c} · Gemini {g} · Δ{delta})"
    )
    return {
        "consensus_score": score,
        "claude_score":    c,
        "gemini_score":    g,
        "delta":           delta,
        "confidence":      confidence,
        "flag":            flag,
        "prompt_line":     prompt_line,
    }


# ── Scanner consensus enrichment ───────────────────────────────────────────────

def add_gemini_consensus(
    setups: list,
    ctx_map: dict,           # symbol → chart context (from scanner run)
    max_setups: int = 5,     # only score top-N (cost optimisation)
) -> list:
    """
    Run Gemini scoring in parallel for the top-N scanner finalists and attach
    consensus data to each setup dict.

    setups:  list of setup dicts (already Claude-scored, sorted by score desc)
    ctx_map: {symbol: chart_ctx} from the scanner run — provides compact indicators
    Returns: same list with "_consensus" key added to scored setups.
    """
    if not gemini_client.is_configured():
        return setups

    top = setups[:max_setups]

    def _gem_score(setup: dict) -> tuple[dict, dict | None]:
        sym  = setup.get("_symbol") or setup.get("symbol", "")
        dir_ = setup.get("direction", "Long")
        # Build compact indicator string from chart context
        ctx  = ctx_map.get(sym, {})
        pt   = ctx.get("4H", {}).get("prompt_text", "") or ctx.get("1D", {}).get("prompt_text", "")
        conds = setup.get("key_conditions", [])
        return setup, gemini_client.score_setup(sym, dir_, pt[:250], conds)

    with ThreadPoolExecutor(max_workers=min(max_setups, 5)) as ex:
        futures = {ex.submit(_gem_score, s): s for s in top}
        for fut in as_completed(futures):
            setup, gem_result = fut.result()
            if gem_result and "score" in gem_result:
                claude_score = setup.get("setup_score", 0)
                consensus    = compute_consensus(claude_score, gem_result["score"])
                setup["_consensus"]     = consensus
                setup["_gemini_score"]  = gem_result
                # Bump or penalise final rank based on consensus confidence
                if consensus["confidence"] == "high":
                    setup["_final_score"] = consensus["consensus_score"]
                elif consensus["confidence"] == "very_low":
                    # Strong conflict → demote slightly to surface for review
                    setup["_final_score"] = consensus["consensus_score"] - 0.5
                else:
                    setup["_final_score"] = consensus["consensus_score"]
            else:
                setup["_final_score"] = float(setup.get("setup_score", 0))

    # Setups beyond top-N keep their Claude score as _final_score
    for s in setups[max_setups:]:
        s["_final_score"] = float(s.get("setup_score", 0))

    # Re-sort by consensus-adjusted final score
    setups.sort(key=lambda x: -x.get("_final_score", 0))
    return setups


# ── Pipeline runners ───────────────────────────────────────────────────────────

def run_call_analysis(
    call_text: str,
    symbol: str,
    direction: str,
    account_equity: float,
    setup_type: str,
    open_positions: list,
    conn,
) -> AnalysisResult:
    """
    Full 5-stage pipeline for a trade call analysis.
    Returns AnalysisResult — a flat dict for persistence to analyzed_calls.
    On blocking failure returns AnalysisResult with error= and degraded=True.
    """
    import json

    try:
        collected = agent_data_collector.run({
            "symbol": symbol, "direction": direction, "timeframes": ["4H", "1D"],
        })
    except Exception as e:
        return _degraded(str(e))

    try:
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_interp = ex.submit(agent_data_interpreter.run, {"collected": collected})
            f_sent   = ex.submit(agent_market_sentiment.run,
                                 {"symbol": symbol, "direction": direction,
                                  "collected": collected})
        interpreted = f_interp.result()
        sentiment   = f_sent.result()
    except Exception:
        interpreted = _empty_interp(symbol)
        sentiment   = _empty_sent()

    try:
        reviewed = agent_data_reviewer.run({
            "interpreted": interpreted, "symbol": symbol,
            "direction": direction, "setup_type": setup_type,
        }, conn)
    except Exception:
        reviewed = _empty_review()

    prep = agent_trade_prep.run(TradePrepInput(
        collected=collected, interpreted=interpreted,
        reviewed=reviewed, sentiment=sentiment,
        call_text=call_text, account_equity=account_equity,
        setup_type=setup_type,
    ), conn)

    risk = agent_risk_mgmt.run(RiskInput(
        trade_prep=prep, account_equity=account_equity,
        open_positions=open_positions,
    ), conn)

    return AnalysisResult(
        setup_score=prep["setup_score"], direction=prep["direction"],
        entry_price=prep["entry_price"], sl_price=prep["sl_price"],
        tp1_price=prep["tp1_price"], tp2_price=prep["tp2_price"],
        rr_ratio=prep["rr_ratio"],
        key_conditions=prep["key_conditions"],
        pattern_warnings=prep["pattern_warnings"],
        cot_reasoning=prep["cot_reasoning"],
        gemini_score=prep["gemini_score"], consensus=prep["consensus"],
        raw_json=prep["raw_json"], chart_png_b64=prep["chart_png_b64"],
        risk_approved=risk["approved"],
        risk_verdict_json=json.dumps(risk),
        position_size_usdt=risk["position_size_usdt"],
        margin_usdt=risk["margin_usdt"],
        kelly_fraction=risk["kelly_fraction"],
        macro_bias=sentiment["macro_bias"],
        contra_signal=sentiment["contra_signal"],
        sentiment_score=sentiment["sentiment_score"],
        signal_quality=reviewed["signal_quality"],
        reviewer_warnings=reviewed["warnings"],
        error="", degraded=False,
    )


def run_scanner_prep(symbol: str, direction: str, collected, interpreted,
                     reviewed, sentiment, conn):
    """Stage 3b entry point for the scanner — replaces inline Sonnet batch call."""
    return agent_trade_prep.run(TradePrepInput(
        collected=collected, interpreted=interpreted,
        reviewed=reviewed, sentiment=sentiment,
        call_text="", account_equity=0.0, setup_type="scanner",
    ), conn)


def run_monitor(position: dict, original_prep: dict):
    """Entry point for the monitor scheduler — runs the lightweight Haiku chain."""
    try:
        collected   = agent_data_collector.run({
            "symbol": position["symbol"],
            "direction": position.get("side", "long").title(),
            "timeframes": ["4H", "1D"],
        })
        interpreted = agent_data_interpreter.run({"collected": collected})
        sentiment   = agent_market_sentiment.run({
            "symbol": position["symbol"],
            "direction": position.get("side", "long").title(),
            "collected": collected,
        })
    except Exception as e:
        # Return safe default if data collection fails
        from agent_types import InterpreterResult, SentimentResult
        interpreted = _empty_interp(position.get("symbol", ""))
        sentiment   = _empty_sent()

    return agent_trade_monitor.run(MonitorInput(
        position=position, original_prep=original_prep or {},
        interpreted=interpreted, sentiment=sentiment,
    ))


# ── Fallback helpers ───────────────────────────────────────────────────────────

def _degraded(error: str) -> AnalysisResult:
    return AnalysisResult(
        setup_score=0, direction="", entry_price=0.0, sl_price=0.0,
        tp1_price=0.0, tp2_price=0.0, rr_ratio=0.0, key_conditions=[],
        pattern_warnings=[], cot_reasoning="", gemini_score=0, consensus={},
        raw_json={}, chart_png_b64="", risk_approved=False,
        risk_verdict_json="{}", position_size_usdt=0.0, margin_usdt=0.0,
        kelly_fraction=0.05, macro_bias="neutral", contra_signal=False,
        sentiment_score=5.0, signal_quality=0.0, reviewer_warnings=[],
        error=error, degraded=True,
    )


def _empty_interp(symbol: str) -> dict:
    from agent_types import InterpreterResult
    return InterpreterResult(symbol=symbol, by_timeframe={}, sr_levels=[],
                              confluence_score={}, trend_direction="neutral",
                              momentum_bias="conflicted", prompt_text="")


def _empty_sent() -> dict:
    from agent_types import SentimentResult
    return SentimentResult(macro_bias="neutral", sentiment_score=5.0,
                           funding_bias="neutral", crowd_position="balanced",
                           contra_signal=False, key_factors=[], grok_summary="",
                           prompt_text="")


def _empty_review() -> dict:
    from agent_types import ReviewerResult
    return ReviewerResult(signal_quality=5.0, warnings=[], backtest_context="",
                          kpis={}, symbol_history={}, rubric="")
