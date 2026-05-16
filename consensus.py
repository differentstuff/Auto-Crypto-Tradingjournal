"""
consensus.py — Multi-agent consensus scoring between Claude and Gemini outputs.

Extracted from agent_orchestrator.py so agent_trade_prep can import
compute_consensus without creating a circular dependency.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed

import gemini_client
from constants import CONSENSUS_HIGH_DELTA, CONSENSUS_MED_DELTA, CONSENSUS_LOW_DELTA


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

    if delta <= CONSENSUS_HIGH_DELTA:    # ≤ 1 point
        score      = round(c * 0.85 + g * 0.15, 1)  # Gemini as validation only
        confidence = "high"
        flag       = "✓ Confirmed"
    elif delta <= CONSENSUS_MED_DELTA:   # ≤ 2 points
        score      = round(c * 0.90 + g * 0.10, 1)  # Claude dominant
        confidence = "medium"
        flag       = "~ Aligned"
    elif delta <= CONSENSUS_LOW_DELTA:   # ≤ 3 points
        score      = float(c)            # Claude only — Gemini has insufficient context
        confidence = "low"
        flag       = "⚠ Divergent"
    else:                                # > 3 points
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
                # _final_score always equals consensus_score (which is now mostly Claude's score)
                setup["_final_score"] = consensus["consensus_score"]
            else:
                setup["_final_score"] = float(setup.get("setup_score", 0))

    # Setups beyond top-N keep their Claude score as _final_score
    for s in setups[max_setups:]:
        s["_final_score"] = float(s.get("setup_score", 0))

    # Re-sort by consensus-adjusted final score
    setups.sort(key=lambda x: -x.get("_final_score", 0))
    return setups
