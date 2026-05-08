"""
prompt_builder.py — Shared context assembler for all AI modules.

Single source of truth for common prompt sections: rulebook, calibration,
chart context (compact), and similar trades. Enforces a character budget so
no single analysis call can balloon indefinitely as the rulebook grows.

Priority order (highest signal density first):
  1. Market context   (passed in by caller — already fetched)
  2. Rulebook         (personalised warnings / strengths)
  3. Calibration      (score accuracy feedback loop)
  4. Chart context    (compact technical summary + confluence score)
  5. Similar trades   (recent history for this exact symbol + direction)
"""

import chart_context
import ai_rulebook
import ai_pattern_detector

# ~1 400 tokens of context at 4 chars/token — leaves plenty for the main prompt
MAX_CONTEXT_CHARS = 5_600

# Setup-type-specific scoring rubrics (P14)
_RUBRICS = {
    "breakout": (
        "BREAKOUT RUBRIC: 9-10 = clean volume-confirmed break of multi-touch level with retest entry, "
        "ATR-wide SL below break zone, R:R ≥ 3:1. 7-8 = clear level break, moderate volume, structural SL. "
        "6 = break with weak volume or SL inside noise. Penalise false-break patterns heavily."
    ),
    "reversal": (
        "REVERSAL RUBRIC: 9-10 = extreme RSI divergence at major S/R, multi-TF confirmation, "
        "clear candle rejection pattern, R:R ≥ 3:1. 7-8 = strong level + indicator signal. "
        "6 = moderate confluence only. Penalise reversals against the weekly trend unless very strong."
    ),
    "continuation": (
        "CONTINUATION RUBRIC: 9-10 = pullback to EMA in strong trend with RSI reset 45-55, "
        "higher low structure intact, R:R ≥ 2.5:1. 7-8 = clear trend + EMA touch. "
        "6 = shallower trend or choppy structure. Never score > 7 if ADX < 25."
    ),
    "range": (
        "RANGE RUBRIC: 9-10 = clearly defined range with 3+ touches per side, entry at range boundary, "
        "SL outside range, TP at opposite boundary, R:R ≥ 2:1. 7-8 = 2 touches minimum. "
        "6 = narrow range or overlapping candles. Penalise range trades when ADX > 30 (trending)."
    ),
}


def get_setup_rubric(setup_type: str) -> str:
    """Return the scoring rubric for a given setup type (case-insensitive prefix match)."""
    if not setup_type:
        return ""
    lower = setup_type.lower()
    for key, rubric in _RUBRICS.items():
        if key in lower or lower in key:
            return rubric
    return ""


def build_context(
    conn,
    symbol: str = None,
    direction: str = None,
    setup_type: str = None,
    market_str: str = "",
    include_chart: bool = True,
    include_rulebook: bool = True,
    include_calibration: bool = True,
    include_similar: bool = True,
    include_strengths: bool = True,
    timeframes: list = None,
    exchange_filter: str = None,   # 'bitget' | 'blofin' | None (all)
) -> str:
    """
    Assemble the shared context block for a Claude prompt.

    conn            — open DB connection (caller owns lifecycle)
    symbol          — coin symbol (e.g. "BTCUSDT") for chart + similar trades
    direction       — "Long" or "Short" for similar-trade filtering
    setup_type      — optional setup label for narrower similar-trade match
    market_str      — pre-formatted market context string (pass "" to skip)
    include_*       — toggle individual sections off for lightweight callers
    timeframes      — TF list for chart context (default ["4H", "1D"])

    Returns a single string to embed verbatim in any Claude prompt.
    """
    sections   = []
    remaining  = MAX_CONTEXT_CHARS
    _truncated = []   # sections skipped or cut due to budget

    # ── 1. Market context (caller provides pre-fetched string) ────────────────
    if market_str and remaining > 0:
        block = f"CURRENT MARKET CONTEXT:\n{market_str}"
        sections.append(block)
        remaining -= len(block)

    # ── 2. Rulebook ───────────────────────────────────────────────────────────
    if include_rulebook and conn is not None:
        if remaining > 500:
            rb = ai_rulebook.get_rulebook_for_prompt(conn)
            if rb:
                sections.append(rb)
                remaining -= len(rb)
        else:
            _truncated.append(f"rulebook (budget={remaining})")

    # ── 3. Calibration — filtered by exchange when active ─────────────────────
    if include_calibration and conn is not None:
        if remaining > 300:
            cal = ai_rulebook.get_calibration_for_prompt(conn, exchange=exchange_filter)
            if cal:
                sections.append(cal)
                remaining -= len(cal)
        else:
            _truncated.append(f"calibration (budget={remaining})")

    # ── 4. Chart context (compact single-line-per-TF format) ─────────────────
    if include_chart and symbol:
        if remaining > 400:
            tfs = timeframes or ["4H", "1D"]
            ctx = chart_context.get_chart_context(symbol, tfs)
            lines = []
            for tf in tfs:
                pt = ctx.get(tf, {}).get("prompt_text", "")
                if pt:
                    if len(pt) > remaining - 200:
                        _truncated.append(f"chart/{tf} trimmed ({len(pt)}→{remaining-200} chars)")
                        pt = pt[:remaining - 200] + "…"
                    lines.append(pt)
                    remaining -= len(pt)
            conf = chart_context.confluence_score(symbol, tfs, ctx=ctx)
            if conf:
                conf_line = (
                    f"CONFLUENCE ({'/'.join(tfs)}): {conf['label']} "
                    f"({conf['score']:+.2f}/{conf['max']} — "
                    f"{conf['bullish']} bullish / {conf['bearish']} bearish signals)"
                )
                lines.append(conf_line)
                remaining -= len(conf_line)
            if lines:
                sections.append("\n".join(lines))
        else:
            _truncated.append(f"chart (budget={remaining})")

    # ── 5. Positive pattern strengths (anti-pattern injection) ───────────────
    if include_strengths and conn is not None:
        if remaining > 150:
            strengths = ai_pattern_detector.get_top_strengths_for_prompt(conn)
            if strengths:
                if len(strengths) > remaining:
                    strengths = strengths[:remaining]
                sections.append(strengths)
                remaining -= len(strengths)

    # ── 6. Similar past trades ────────────────────────────────────────────────
    if include_similar and conn is not None and symbol:
        if remaining > 200:
            sim = ai_rulebook.get_similar_trades_for_prompt(
                symbol, setup_type or "", direction or "", conn
            )
            if sim:
                if len(sim) > remaining:
                    _truncated.append(f"similar trades trimmed ({len(sim)}→{remaining} chars)")
                    sim = sim[:remaining] + "\n  [truncated]"
                sections.append(sim)
        else:
            _truncated.append(f"similar trades (budget={remaining})")

    if _truncated:
        print(f"[prompt_builder] context budget truncated: {', '.join(_truncated)}", flush=True)

    return "\n\n".join(sections)
