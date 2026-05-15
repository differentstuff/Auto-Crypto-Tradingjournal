"""
scanner_stages.py — Stage 1 and Stage 2 pipeline functions for the setup scanner.

Stage 1: Confluence pre-filter (parallel, no AI).
Stage 2: Technical quality gate (no AI, instant).
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import chart_context
from constants import SCANNER_MIN_SCORE
from scanner_criteria import CRITERIA_DEFAULTS

logger = logging.getLogger(__name__)


def _fetch_one(symbol: str):
    try:
        tfs = ["4H", "1D"]
        ctx  = chart_context.get_chart_context(symbol, tfs)
        conf = chart_context.confluence_score(symbol, tfs, ctx=ctx)
        return symbol, ctx, conf
    except Exception as e:
        logger.warning("chart context fetch failed for %s: %s", symbol, e)
        return symbol, None, None


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
            if conf["bullish"] >= threshold:
                out.append((symbol, ctx, conf, "Long"))
            elif conf["bearish"] >= threshold:
                out.append((symbol, ctx, conf, "Short"))
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
