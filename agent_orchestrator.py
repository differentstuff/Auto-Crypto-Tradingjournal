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

from constants import MODEL, FAST_MODEL
from consensus import compute_consensus, add_gemini_consensus
import gemini_client
import agent_data_collector
import agent_data_interpreter
import agent_market_sentiment
import agent_data_reviewer
import agent_trade_prep
import agent_risk_mgmt
import agent_trade_monitor
from agent_types import (AnalysisResult, TradePrepInput, RiskInput, MonitorInput,
                         empty_interpreter, empty_sentiment, empty_reviewer)

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
        interpreted = empty_interpreter(symbol)
        sentiment   = empty_sentiment()

    try:
        reviewed = agent_data_reviewer.run({
            "interpreted": interpreted, "symbol": symbol,
            "direction": direction, "setup_type": setup_type,
        }, conn)
    except Exception:
        reviewed = empty_reviewer()

    try:
        prep = agent_trade_prep.run(TradePrepInput(
            collected=collected, interpreted=interpreted,
            reviewed=reviewed, sentiment=sentiment,
            call_text=call_text, account_equity=account_equity,
            setup_type=setup_type,
        ), conn)
    except Exception as e:
        return _degraded(f"TradePrep failed: {e}")

    try:
        risk = agent_risk_mgmt.run(RiskInput(
            trade_prep=prep, account_equity=account_equity,
            open_positions=open_positions,
        ), conn)
    except Exception as e:
        risk = agent_risk_mgmt._blocked([f"RiskMgmt error: {e}"])

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
        interpreted = empty_interpreter(position.get("symbol", ""))
        sentiment   = empty_sentiment()

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


