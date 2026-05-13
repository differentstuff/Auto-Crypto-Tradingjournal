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
