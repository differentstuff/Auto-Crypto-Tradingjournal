"""
scanner_prompts.py — Prompt builders for the setup scanner.

Provides:
- _build_prompt(): full per-symbol prompt for Sonnet scoring.
- _build_shared_prefix(): legacy combined prefix (stable + market context).
- _build_scanner_stable(): cacheable stable prefix (rulebook + scoring scale).
- _build_batch_prompt(): single prompt for all top-N finalists.
- _quick_score(): Haiku pass-1 cheap pre-filter.
"""

import json
import logging

from prompt_fragments import SCORING_SCALE, LEVEL_PROXIMITY_RULES, MARKET_CONTEXT_RULES, DRAW_ON_LIQUIDITY_RULES
from constants import MODEL, FAST_MODEL, SCANNER_MIN_SCORE, PROMPT_CACHE_MIN_CHARS
from ai_client import send as ai_send
from helpers import strip_fence, build_cached_messages
from scanner_criteria import CRITERIA_DEFAULTS, _disabled_criteria_block

logger = logging.getLogger(__name__)


def _detect_archetype(ctx: dict, direction: str) -> str:
    """
    Detect the most likely setup archetype from 4H indicators.
    Returns: "reversal" | "breakout" | "continuation"

    Reversal: WaveTrend crossover at RSI extremes (price exhausted, about to turn)
    Breakout: Volume spike + momentum RSI + rising ADX (price escaping consolidation)
    Continuation: Default — trend is established, looking for pullback entry
    """
    inds = ctx.get("4H", {}).get("indicators", {}) or {}
    wt       = inds.get("wavetrend", {}) or {}
    rsi_val  = (inds.get("rsi",    {}) or {}).get("value",  50)
    vol_ratio= (inds.get("volume", {}) or {}).get("ratio",   1.0)
    adx_val  = (inds.get("adx",    {}) or {}).get("value",  20)

    wt_signal = wt.get("signal", "")
    is_long   = direction.lower() == "long"

    # Reversal: WaveTrend crossover at RSI extremes
    if wt_signal in ("gold_buy", "buy") and rsi_val < 38:
        return "reversal"
    if wt_signal == "sell" and rsi_val > 62:
        return "reversal"

    # Breakout: volume spike + RSI in momentum zone + some trend starting
    if vol_ratio > 1.8 and adx_val > 18:
        if is_long and 50 < rsi_val < 78:
            return "breakout"
        if not is_long and 22 < rsi_val < 50:
            return "breakout"

    return "continuation"


def _archetype_rubric(archetype: str, direction: str, min_score: int) -> str:
    """Return a scoring rubric tailored to the detected setup archetype."""
    is_long = direction.lower() == "long"

    if archetype == "reversal":
        rsi_zone = "< 32" if is_long else "> 68"
        return (
            f"SETUP ARCHETYPE — REVERSAL / MEAN REVERSION\n"
            f"Primary trigger: WaveTrend crossover signal is REQUIRED (gold_buy/buy for Long, sell for Short).\n"
            f"RSI extremes required for 8+: RSI {rsi_zone} — exhaustion must be clear.\n"
            f"S/R anchor: Entry MUST be at or near a named structural level (support for Long, resistance for Short).\n"
            f"ADX: LOW is favourable (< 28) — ranging/choppy markets are ripe for reversals.\n"
            f"Score 9-10: WT gold/crossover signal + RSI extreme + S/R anchor + MFI divergence + low ADX.\n"
            f"Score 7-8: WT signal + RSI extreme + S/R anchor.\n"
            f"Score 6: WT signal + RSI extreme only (S/R less clear).\n"
            f"PENALISE: No WT crossover signal present. Strong EMA alignment opposing the reversal (trend too strong). ADX > 35."
        )

    if archetype == "breakout":
        rsi_zone = "55–78" if is_long else "22–45"
        return (
            f"SETUP ARCHETYPE — BREAKOUT / MOMENTUM EXPANSION\n"
            f"Non-negotiable: Volume ≥ 1.5× average — conviction breakouts require participation.\n"
            f"RSI acceptable: {rsi_zone} — momentum present, not yet exhausted.\n"
            f"Price must be breaking or have broken a key S/R level with candle body close beyond it.\n"
            f"MACD: Bullish/bearish cross with growing histogram adds significant conviction.\n"
            f"ADX: Ideally rising from below 25 — breakout launching from consolidation.\n"
            f"Score 9-10: Volume > 2× + clean S/R break + MACD growing + ADX rising + RSI in range.\n"
            f"Score 7-8: Volume > 1.5× + S/R break + at least one momentum signal confirming.\n"
            f"Score 6: Volume marginally above avg + S/R break (weaker conviction).\n"
            f"PENALISE: Volume below average (false breakout risk). RSI > 80 / < 20 (already exhausted). No S/R level being broken."
        )

    # continuation (default)
    rsi_zone = "45–68" if is_long else "32–55"
    return (
        f"SETUP ARCHETYPE — CONTINUATION / TREND FOLLOWING\n"
        f"Primary requirement: EMA stack aligned AND ADX ≥ 18 (trend must exist).\n"
        f"RSI sweet spot: {rsi_zone} — trend momentum WITHOUT exhaustion. RSI outside this range is a warning.\n"
        f"MACD: Aligned and ideally growing histogram = additional conviction.\n"
        f"Volume: Above average confirms institutional participation.\n"
        f"Score 9-10: EMA stack + ADX ≥ 25 + MACD growing + RSI in sweet spot + volume above avg + clean pullback to S/R.\n"
        f"Score 7-8: EMA stack + MACD aligned + RSI in range + clear entry level.\n"
        f"Score 6: At least 2 of 3 primary signals (EMA stack/ADX/MACD) + valid entry and SL.\n"
        f"PENALISE: RSI > 74 or < 26 (overextended — near exhaustion). ADX < 15 (no trend). EMA stack opposing direction."
    )


def _build_macro_header(macro_ctx: dict) -> str:
    """Short macro context header prepended to every scanner setup prompt."""
    if not macro_ctx:
        return ""
    parts = []
    vix = macro_ctx.get("vix")
    fg  = macro_ctx.get("fear_greed")
    dom = macro_ctx.get("btc_dominance")
    if vix:
        regime = macro_ctx.get("regime", "").replace("_", " ").upper()
        parts.append(f"VIX {vix:.0f} ({regime})")
    if fg is not None:
        fg_label = "Extreme Fear" if fg < 25 else "Fear" if fg < 45 else "Neutral" if fg < 55 else "Greed" if fg < 75 else "Extreme Greed"
        parts.append(f"F&G {fg}/100 ({fg_label})")
    if dom:
        parts.append(f"BTC dom {dom:.0f}%")
    if macro_ctx.get("macro_risk"):
        hrs = macro_ctx.get("hours_until")
        evt = macro_ctx.get("next_event", "macro event")
        hrs_str = f" in {hrs:.0f}h" if hrs else ""
        parts.append(f"⚠️ {evt}{hrs_str}")
    return "MACRO: " + " | ".join(parts) + "\n\n" if parts else ""


def _build_prompt(symbol, ctx, conf, direction, mkt_str, history, rulebook_str, min_score=SCANNER_MIN_SCORE,
                  macro_ctx: dict = None, archetype: str = ""):
    inds_4h = ctx.get("4H", {}).get("indicators", {})
    ema     = inds_4h.get("ema", {}) or {}
    price   = ema.get("current_price", 0)
    atr_val = (inds_4h.get("atr", {}) or {}).get("value", 0)

    sr_4h = inds_4h.get("support_resistance", [])
    sr_text = "\n".join(
        f"  {l['type'].upper():10s} {l['price']:.6g}  "
        f"(strength {l.get('strength', 1)}, {l.get('touches', 1)} touches)"
        for l in sorted(sr_4h, key=lambda x: -x.get("touches", 1))[:6]
    ) or "  None detected"

    tl_4h = inds_4h.get("trendlines", [])
    tl_text = "\n".join(
        f"  {l.get('direction','?').upper()} trendline — {l.get('timeframe','4H')}, "
        f"{l.get('touches',1)} touches"
        for l in tl_4h[:3]
    ) or "  None"

    pt_4h = ctx.get("4H", {}).get("prompt_text", "No 4H data")
    pt_1d = ctx.get("1D", {}).get("prompt_text", "No 1D data")

    conf_line = (
        f"{conf['label']} ({conf['score']:+.2f}/{conf['max']} — "
        f"{conf['bullish']} bullish / {conf['bearish']} bearish signals)"
    )

    price_note = f"Current price: {price:.6g}  |  1H-equivalent ATR: ~{atr_val:.4g}" if price else ""
    hist_text  = json.dumps(history) if history.get("trades") else "No closed trades on this symbol yet"
    mkt_block  = f"\nMARKET CONTEXT:\n{mkt_str}\n" if mkt_str else ""
    rb_block   = f"\nTRADER RULEBOOK (known patterns — respect these):\n{rulebook_str}\n" if rulebook_str else ""
    macro_header = _build_macro_header(macro_ctx or {})

    archetype = archetype or _detect_archetype(ctx, direction)
    rubric_block = _archetype_rubric(archetype, direction, min_score)

    return f"""{macro_header}You are a professional crypto futures analyst. Score the current {direction.upper()} setup for {symbol} on a 1-10 scale and provide specific trade parameters.

TECHNICAL SUMMARY:
{pt_4h}
{pt_1d}

CONFLUENCE: {conf_line}
{price_note}

KEY S/R LEVELS (4H):
{sr_text}

ACTIVE TRENDLINES:
{tl_text}
{mkt_block}
TRADER HISTORY ON {symbol}:
{hist_text}
{rb_block}
─────────────────────────────────────────
{MARKET_CONTEXT_RULES}

{SCORING_SCALE}

{LEVEL_PROXIMITY_RULES}

{rubric_block}

REQUIREMENTS for any score ≥ {min_score}:
- A specific entry zone anchored to a named structural level (S/R, EMA, trendline) with exact prices
- A stop loss beyond the nearest structural level and ≥ 1× ATR from entry — state the level and ATR distance explicitly
- At least two take-profit levels at significant resistance/support — state exactly what each targets
- Detailed rationale for EVERY level: name the S/R zone, reference the indicator value, explain WHY

RATIONALE DEPTH REQUIRED:
  entry_rationale: "Price is pulling back to the 4H EMA50 ($X) which coincides with the 1D support zone at $Y (5 touches). RSI has cooled to 48 from 68 — reset without breaking structure."
  sl_rationale: "Below the 1D support at $Z and the swing low at $W. Distance of $D = 1.8× 4H ATR ($A), placing the stop clearly outside noise."
  tp1_rationale: "Previous 4H resistance at $R1, high-volume rejection on [date]. R:R 1:2.3 from midpoint entry."
  tp2_rationale: "Weekly resistance cluster and the 1.618 Fibonacci extension from last major swing. R:R 1:4.1."

If the setup scores below {min_score} (no valid entry level, SL inside ATR noise, or no logical TP):
{{"setup_score": 0, "reason": "one sentence why this doesn't qualify"}}

Otherwise respond with this exact structure:
{{
  "setup_score": 8, "setup_label": "Strong",
  "direction": "{direction}",
  "why_this_score": "2-3 sentences explaining specifically what earns this score and what would need to be different for a 9 or a 7",
  "entry_zone": {{"low": 0.0, "high": 0.0, "rationale": "Name the level, give the price, explain WHY this is the entry zone"}},
  "sl_price": 0.0,
  "sl_rationale": "Name the structural level, state ATR distance (e.g. 1.6× 4H ATR), explain the invalidation logic",
  "tp1_price": 0.0,
  "tp1_rationale": "Name the resistance/target, explain why price is likely to stall or reverse there",
  "tp2_price": 0.0,
  "tp2_rationale": "Name the higher target, explain the structural or Fibonacci significance",
  "rr_ratio": "1:X.X",
  "chart_pattern": "Specific pattern name — or null if none",
  "key_conditions": ["Specific signal with values, e.g. RSI 47 reset from 71", "MACD bull crossover on 4H", "EMA stack 20>50>200 bullish"],
  "risks": ["Specific risk with context", "Second risk"],
  "urgency": "Now|1-4h|Today|1-3 days",
  "timeframe": "4H",
  "confluence_summary": "One sentence: the 2-3 most important aligned signals that create conviction",
  "summary": "2-3 sentence overall assessment referencing actual price numbers from the technical picture"
}}

Respond with ONLY valid JSON — no markdown, no code fences."""


def _build_shared_prefix(mkt_str: str, rulebook_str: str,
                          min_score: int = SCANNER_MIN_SCORE,
                          criteria: dict = None) -> str:
    """
    Full shared prefix (legacy path — used when stable_prefix is not passed separately).
    For caching-aware callers use _build_scanner_stable() + mkt_block separately.
    """
    stable   = _build_scanner_stable(rulebook_str, min_score, criteria)
    mkt_part = f"MARKET CONTEXT:\n{mkt_str}\n\n" if mkt_str else ""
    return mkt_part + stable


def _build_scanner_stable(rulebook_str: str, min_score: int = SCANNER_MIN_SCORE,
                           criteria: dict = None) -> str:
    """
    Cacheable scanner prefix: rulebook + scoring scale + criteria.
    No market data — stable across scan runs (changes only when rulebook updates).
    Pass as stable_prefix to build_cached_messages() so Anthropic caches it.
    """
    cr       = criteria or CRITERIA_DEFAULTS
    rb_block = f"TRADER RULEBOOK:\n{rulebook_str}\n\n" if rulebook_str else ""
    dis_block = _disabled_criteria_block(cr)
    dis_part  = f"\n{dis_block}\n" if dis_block else ""
    caps = []
    if cr.get("sr_anchor", True): caps.append("no structural entry")
    if cr.get("atr_sl",    True): caps.append("SL inside ATR noise")
    if cr.get("rr_minimum",True): caps.append("R:R below 2:1")
    cap_str = " or ".join(caps) if caps else "no valid setup"
    return (
        f"{rb_block}"
        + SCORING_SCALE + "\n"
        + "5=Mod(borderline), 6=Accept(R:R≥2), 7=Good(R:R≥2.5), "
        + "8=Strong(R:R≥3), 9=Excellent(multi-TF,R:R≥3.5), 10=Perfect(R:R≥4)\n"
        + f"Score <{min_score} if: {cap_str}.{dis_part}\n\n"
        + DRAW_ON_LIQUIDITY_RULES
    )


def _quick_score(symbol: str, ctx: dict, conf: dict, direction: str,
                 shared_prefix: str, min_score: int = SCANNER_MIN_SCORE) -> dict | None:
    """
    Pass 1 — cheap Haiku call returning only a score (0-10) and confirmed direction.
    Returns None if score < min_score or on any error.
    """
    pt_4h = ctx.get("4H", {}).get("prompt_text", "")
    pt_1d = ctx.get("1D", {}).get("prompt_text", "")
    conf_line = f"{conf['label']} ({conf['bullish']}↑/{conf['bearish']}↓)"

    inds_4h = ctx.get("4H", {}).get("indicators", {})
    sr = inds_4h.get("support_resistance", [])
    sr_compact = "  ".join(
        f"{'S' if l['type']=='support' else 'R'}:{l['price']:.6g}({l.get('touches',1)}t)"
        for l in sorted(sr, key=lambda x: -x.get("touches", 1))[:4]
    ) or "none"

    variable = (
        f"Score this {direction.upper()} setup for {symbol} — return score 0-10 "
        f"and one short sentence explaining the key factor behind the score.\n\n"
        f"{pt_4h}\n{pt_1d}\n"
        f"Confluence: {conf_line}\nS/R: {sr_compact}\n\n"
        f'If score < {min_score}: {{"score":0}}\n'
        f'If score >= {min_score}: {{"score":7,"direction":"{direction}",'
        f'"reason":"one sentence — main factor (e.g. \'4H EMA stack bullish, RSI reset to 52, clean S/R entry zone\')"}}\n'
        "Respond with ONLY valid JSON — no extras."
    )

    try:
        msg_text, _cached = ai_send(
            "scanner_quick", FAST_MODEL,
            [{"role": "user", "content": [
                {"type": "text", "text": shared_prefix},
                {"type": "text", "text": variable},
            ]}],
            max_tokens=120,
        )
        r = json.loads(strip_fence(msg_text.strip()))
        if r.get("score", 0) < min_score:
            return None
        return {
            "score":     r["score"],
            "direction": r.get("direction", direction),
            "reason":    r.get("reason", ""),
        }
    except Exception as e:
        logger.warning("quick-score failed for %s: %s", symbol, e)
        return None


def _build_batch_prompt(finalists, histories, min_score=SCANNER_MIN_SCORE, criteria=None,
                        nansen_signals=None):
    """Build a single prompt for all top-N symbols. Returns user_prompt string."""
    parts = []
    for i, (symbol, ctx, conf, direction, score, _reason) in enumerate(finalists, 1):
        inds_4h = ctx.get("4H", {}).get("indicators", {})
        ema     = inds_4h.get("ema", {}) or {}
        price   = ema.get("current_price", 0)
        atr_val = (inds_4h.get("atr", {}) or {}).get("value", 0)
        pt_4h   = ctx.get("4H", {}).get("prompt_text", "No 4H data")
        pt_1d   = ctx.get("1D", {}).get("prompt_text", "No 1D data")
        sr_4h   = inds_4h.get("support_resistance", [])
        sr_text = "  ".join(
            f"{'S' if l['type']=='support' else 'R'}:{l['price']:.6g}({l.get('touches',1)}t)"
            for l in sorted(sr_4h, key=lambda x: -x.get("touches", 1))[:4]
        ) or "none"
        hist = histories.get(symbol, {"trades": 0})
        conf_line = f"{conf['label']} ({conf['bullish']}↑/{conf['bearish']}↓)"
        # Nansen smart money line (only when 5+ traders — already filtered in client)
        ns      = (nansen_signals or {}).get(symbol, {})
        ns_line = f"\n{ns['prompt_line']}" if ns.get("ok") else ""
        archetype = _detect_archetype(ctx, direction)
        archetype_line = f"Archetype: {archetype.upper()}"
        parts.append(
            f"--- SETUP {i}: {symbol} ({direction.upper()}) ---\n"
            f"{archetype_line}\n"
            f"{pt_4h}\n{pt_1d}\n"
            f"Confluence: {conf_line}  |  Price: {price:.6g}  |  ATR: {atr_val:.4g}\n"
            f"S/R: {sr_text}\n"
            f"History: {json.dumps(hist)}{ns_line}"
        )

    cr = criteria or CRITERIA_DEFAULTS
    dis_block = _disabled_criteria_block(cr)
    dis_part  = f"\n{dis_block}\n" if dis_block else ""

    # Dynamic score cap rules based on enabled criteria
    level_rules = []
    if cr.get("sr_anchor", True): level_rules.append("Entry >1×ATR from level → max 6")
    if cr.get("atr_sl",    True): level_rules.append("SL <1×ATR from entry → max 6")
    if cr.get("rr_minimum",True): level_rules.append("R:R<2:1 → max 6")
    level_str = ". ".join(level_rules) + "." if level_rules else ""

    setups_text = "\n\n".join(parts)
    scoring_hint = SCORING_SCALE[:60] + " ... 9=Excellent(R:R≥3.5), 10=Perfect(R:R≥4)"
    archetype_hints = (
        "ARCHETYPES: REVERSAL=WaveTrend+RSI extreme at S/R required, "
        "penalise strong opposing EMA trend. "
        "BREAKOUT=Volume>1.5x required, RSI in momentum zone, price breaking S/R. "
        "CONTINUATION=EMA stack+ADX≥18 required, RSI in sweet spot 45-68(L)/32-55(S)."
    )
    user_prompt = (
        f"Analyze these {len(finalists)} crypto futures setups. "
        f"Return a JSON ARRAY of exactly {len(finalists)} objects — one per setup, in the same order.\n\n"
        f"{setups_text}\n\n"
        f"{archetype_hints}\n"
        f"{scoring_hint}\n"
        f"{level_str}{dis_part}\n"
        f"For setups scoring >= {min_score}, use this structure:\n"
        '{"symbol":"X","direction":"Long","setup_score":7,"setup_label":"Good",'
        '"why_this_score":"2-3 sentences","entry_zone":{"low":0,"high":0,"rationale":"..."},'
        '"sl_price":0,"sl_rationale":"...","tp1_price":0,"tp1_rationale":"...",'
        '"tp2_price":0,"tp2_rationale":"...","rr_ratio":"1:X","chart_pattern":null,'
        '"key_conditions":["..."],"risks":["..."],"urgency":"Now|1-4h|Today|1-3 days",'
        '"timeframe":"4H","confluence_summary":"...","summary":"..."}\n'
        f'For setups scoring below {min_score}: {{"symbol":"X","setup_score":0,"reason":"why"}}\n\n'
        "Respond with ONLY a valid JSON array — no markdown, no code fences."
    )
    return user_prompt
