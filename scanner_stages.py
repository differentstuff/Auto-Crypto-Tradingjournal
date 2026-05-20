"""
scanner_stages.py — Stage 1 and Stage 2 pipeline functions for the setup scanner.

Stage 1: Confluence pre-filter (parallel, no AI).
Stage 2: Technical quality gate (no AI, instant).
"""

import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed

import chart_context
from constants import SCANNER_MIN_SCORE
from scanner_criteria import CRITERIA_DEFAULTS

logger = logging.getLogger(__name__)


def _get_scan_macro_context() -> dict:
    """
    Fetch global macro context for scanner. Called once per scan run, not per symbol.
    Degrades gracefully on any failure.
    """
    result = {
        "vix": None, "dxy": None, "es": None, "es_change_pct": None,
        "regime": "unknown", "macro_risk": False,
        "btc_dominance": None, "eth_dominance": None,
        "usdt_dominance": None, "others_dominance": None,
        "total2_usd": None, "total3_usd": None,
        "fear_greed": None,
        "meme_cap_usd": None, "stable_dominance_pct": None,
        "next_event": None, "hours_until": None,
    }
    try:
        from market_context import get_macro_regime
        mr = get_macro_regime()
        result["vix"]           = mr.get("vix")
        result["dxy"]           = mr.get("dxy")
        result["es"]            = mr.get("es")
        result["es_change_pct"] = mr.get("es_change_pct")
        result["regime"]        = mr.get("regime", "unknown")
    except Exception:
        pass
    try:
        from market_context import get_fear_greed
        fg = get_fear_greed()
        result["fear_greed"] = fg.get("value")
    except Exception:
        pass
    try:
        from finnhub_client import get_upcoming_events
        eco = get_upcoming_events(hours_ahead=24)
        result["macro_risk"]  = eco.get("macro_risk", False)
        result["next_event"]  = eco.get("next_event")
        result["hours_until"] = eco.get("hours_until")
    except Exception:
        pass
    try:
        from coingecko_client import get_global_market
        gm = get_global_market()
        result["btc_dominance"]    = gm.get("btc_dominance_pct")
        result["eth_dominance"]    = gm.get("eth_dominance_pct")
        result["usdt_dominance"]   = gm.get("usdt_dominance_pct")
        result["others_dominance"] = gm.get("others_dominance_pct")
        result["total2_usd"]       = gm.get("total2_usd")
        result["total3_usd"]       = gm.get("total3_usd")
    except Exception:
        pass
    try:
        from coingecko_client import get_category_caps
        cats = get_category_caps()
        result["meme_cap_usd"]        = cats.get("meme_cap_usd")
        result["stable_dominance_pct"] = cats.get("stable_dominance_pct")
    except Exception:
        pass
    return result


def _apply_macro_cap(score: float, macro_ctx: dict) -> tuple:
    """
    Apply macro regime caps to a setup score.
    Returns (capped_score, list_of_warnings).
    """
    warnings = []
    vix = macro_ctx.get("vix")
    regime = macro_ctx.get("regime", "unknown")

    # VIX cap: high fear suppresses score
    if vix is not None:
        if math.isnan(vix):
            vix = 30.0  # conservative default — triggers the VIX 25-35 cap (7.5)
        if vix > 35:
            cap = 6.0
            if score > cap:
                warnings.append(f"VIX {vix:.0f} (extreme fear) — score capped at {cap}")
                score = min(score, cap)
        elif vix > 25:
            cap = 7.5
            if score > cap:
                warnings.append(f"VIX {vix:.0f} (elevated fear) — score capped at {cap}")
                score = min(score, cap)

    # Macro event cap: major event in next 12h
    if macro_ctx.get("macro_risk"):
        hrs = macro_ctx.get("hours_until")
        evt = macro_ctx.get("next_event", "macro event")
        cap = 7.0
        if score > cap:
            hrs_str = f" in {hrs:.0f}h" if hrs else ""
            warnings.append(f"{evt}{hrs_str} — macro risk, score capped at {cap}")
            score = min(score, cap)

    return score, warnings


def _fetch_one(symbol: str):
    try:
        tfs = ["4H", "1D"]
        ctx  = chart_context.get_chart_context(symbol, tfs)
        conf = chart_context.confluence_score(symbol, tfs, ctx=ctx)
        return symbol, ctx, conf
    except Exception as e:
        logger.warning("chart context fetch failed for %s: %s", symbol, e)
        return symbol, None, None


def enrich_finalists_1h(finalists: list) -> list:
    """
    Fetch 1H candles for each finalist and add to their ctx dict.
    Called after Stage 2, before Stage 3 Haiku quick-score and Sonnet batch scoring.
    Populates ctx["1H"] with full indicators + S/R levels + prompt_text so
    Stage 3 can use 1H data for fresher entry zones and stop placement.
    """
    from chart_context import get_candles as _get_candles, compute_indicators, format_for_prompt
    enriched = []
    for item in finalists:
        sym, ctx, conf, direction = item
        if "1H" not in ctx:
            try:
                candles_1h = _get_candles(sym, "1H")
                if candles_1h is not None and not candles_1h.empty:
                    inds_1h = compute_indicators(candles_1h)   # includes S/R + trendlines
                    pt_1h   = format_for_prompt(sym, inds_1h, "1H")
                    ctx["1H"] = {"indicators": inds_1h, "prompt_text": pt_1h}
            except Exception:
                pass
        enriched.append((sym, ctx, conf, direction))
    return enriched


def _stage1(symbols: list, min_score: int = SCANNER_MIN_SCORE,
            _update_fn=None) -> list:
    """Return [(symbol, ctx, conf, direction)] with enough aligned signals.
    Emits live progress via _update_fn() as futures complete (if provided)."""
    threshold = 1 if min_score <= 3 else 2
    total = len(symbols)
    out   = []
    done  = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_fetch_one, sym): sym for sym in symbols}
        for f in as_completed(futures):
            symbol, ctx, conf = f.result()
            done += 1
            if _update_fn and (done % 10 == 0 or done == total):
                _update_fn(
                    stage_detail  = f"{done} / {total} symbols fetched",
                    stage_progress= int(done / total * 100),
                )
            if ctx is None or conf is None:
                continue
            # Pick the dominant direction by net signal strength (not first-match
            # if/elif which suppressed Shorts whenever bullish merely met threshold
            # even with much stronger bearish signals — see 2026-05-20 bias audit).
            bull = conf["bullish"]
            bear = conf["bearish"]
            if max(bull, bear) >= threshold:
                direction = "Long" if bull > bear else "Short"
                out.append((symbol, ctx, conf, direction))
    return out


def _stage2(candidates: list, min_score: int = SCANNER_MIN_SCORE,
             criteria: dict = None) -> list:
    """
    Technical quality gate — no AI, no network calls.
    Respects `criteria` dict: disabled criteria skip the corresponding hard filter.
    Skipped entirely when min_score ≤ 4 (user wants AI to be the sole judge).
    Caps output at 30 to control Claude API cost.
    """
    cr = criteria or CRITERIA_DEFAULTS

    if min_score <= 4:
        out = list(candidates)
        out.sort(key=lambda x: -x[2].get("score", 0))
        return out[:30]

    out = []
    for symbol, ctx, conf, direction in candidates:
        inds = ctx.get("4H", {}).get("indicators", {})
        if not inds.get("ok"):
            continue

        rsi_val = inds.get("rsi", {}).get("value", 50)
        adx_val = (inds.get("adx", {}) or {}).get("value", 0)
        sr      = inds.get("support_resistance", [])
        ema     = inds.get("ema", {}) or {}
        macd    = inds.get("macd", {}) or {}
        adx_d   = inds.get("adx",  {}) or {}
        price   = ema.get("current_price")
        atr_val = (inds.get("atr", {}) or {}).get("value", 0)

        # Reject: RSI already deeply overextended in signal direction
        if cr.get("rsi", True):
            if direction == "Long"  and rsi_val > 78:
                continue
            if direction == "Short" and rsi_val < 22:
                continue

        # Reject: no trend structure (choppy / flat)
        if cr.get("adx", True) and adx_val < 15:
            continue

        # Reject: no S/R structure to define entry/SL/TP
        if cr.get("sr_anchor", True):
            if len(sr) < 2:
                continue
            if price and atr_val and sr:
                distances = [abs(l["price"] - price) for l in sr]
                if min(distances) > atr_val * 4:
                    continue

        # Require at least 2 aligned 4H signals (only from enabled indicators)
        bull_4h = bear_4h = 0
        if cr.get("rsi", True):
            if rsi_val > 55:   bull_4h += 1
            elif rsi_val < 45: bear_4h += 1
        if cr.get("macd", True):
            if macd.get("trend") == "bullish":   bull_4h += 1
            elif macd.get("trend") == "bearish": bear_4h += 1
        if cr.get("ema_stack", True):
            if "bullish" in ema.get("alignment", ""):   bull_4h += 1
            elif "bearish" in ema.get("alignment", ""): bear_4h += 1
        if cr.get("adx", True):
            if "bullish" in adx_d.get("direction", ""):   bull_4h += 1
            elif "bearish" in adx_d.get("direction", ""): bear_4h += 1

        # Minimum 2 aligned signals — but only if at least 2 criteria are enabled
        enabled_signal_criteria = sum(cr.get(k, True) for k in ("rsi", "macd", "ema_stack", "adx"))
        if enabled_signal_criteria >= 2:
            if direction == "Long"  and bull_4h < 2:
                continue
            if direction == "Short" and bear_4h < 2:
                continue

        out.append((symbol, ctx, conf, direction))

    out.sort(key=lambda x: -x[2].get("score", 0))
    return out[:30]
