"""
agent_data_interpreter.py — DataInterpreter agent.

Pure function — no AI, no DB, no network. Transforms raw candles from
CollectorResult into structured technical signals for downstream agents.
"""
import chart_indicators
import chart_sr
import chart_context as cc

from agent_types import InterpreterInput, InterpreterResult

_ANALYST_INSTRUCTIONS = """You are a senior technical analyst specialising in crypto futures (USDT-M perpetuals, 10x leverage).

You receive pre-computed indicators. Do NOT restate raw numbers — synthesise them into trading insight.

## MANDATORY OUTPUT (exactly these 6 sections, no additions):

**TREND** (1 sentence): EMA stack + ADX direction and strength.
**MOMENTUM** (1 sentence): RSI + MACD + WaveTrend confluence verdict.
**STRUCTURE** (1 sentence): Nearest key S/R level and its significance to the setup.
**SIGNAL COUNT** (format: X/12 aligned): Count signals agreeing with the primary bias.
**BIAS** (one of: STRONG LONG | LONG | NEUTRAL | SHORT | STRONG SHORT)
**CONFIDENCE** (one of: HIGH | MED | LOW)

## CONFIDENCE RULES:
- HIGH: ≥8/12 signals aligned, ADX > 20, EMA stack clean, within kill zone
- MED: 6–7/12 aligned OR ADX 15–20 OR outside kill zone
- LOW: <6/12 aligned OR ADX < 15 OR VIX > 30 flagged in context OR HMM=ranging with low conviction

## BIAS RULES:
- STRONG: ≥8 aligned, ADX > 25, clear EMA stack
- LONG/SHORT: 6–7 aligned
- NEUTRAL: <6 aligned or signals conflicting
"""

# Public alias for use as system= parameter in downstream AI calls
ANALYST_INSTRUCTIONS = _ANALYST_INSTRUCTIONS


def run(inp: InterpreterInput) -> InterpreterResult:
    collected = inp["collected"]
    symbol    = collected["symbol"]
    candles   = collected["candles"]

    by_tf = {}
    for tf, df in candles.items():
        if df is None or df.empty:
            by_tf[tf] = {}
            continue
        try:
            by_tf[tf] = chart_indicators.compute_all_indicators(df)
        except Exception:
            by_tf[tf] = {}

    # S/R from primary timeframe (prefer 4H)
    _4h = candles.get("4H")
    if _4h is not None and not _4h.empty:
        primary_df = _4h
    else:
        primary_df = next(
            (df for df in candles.values() if df is not None and not df.empty), None
        )
    sr_levels = []
    if primary_df is not None and not primary_df.empty:
        try:
            sr_levels = chart_sr.detect_support_resistance(primary_df)
        except Exception:
            pass

    # confluence_score expects ctx in format {tf: {"indicators": {...}, "ok": True}}
    # Only compute confluence when at least one timeframe has real indicator data.
    conf_ctx = {tf: {"indicators": data, "ok": bool(data)} for tf, data in by_tf.items()}
    conf = {}
    if any(v["ok"] for v in conf_ctx.values()):
        try:
            conf = cc.confluence_score(symbol, list(candles.keys()), ctx=conf_ctx)
        except Exception:
            pass

    return InterpreterResult(
        symbol           = symbol,
        by_timeframe     = by_tf,
        sr_levels        = sr_levels,
        confluence_score = conf,
        trend_direction  = _trend(by_tf),
        momentum_bias    = _momentum(conf),
        prompt_text      = _prompt_text(symbol, by_tf, conf, sr_levels),
    )


def _trend(by_tf: dict) -> str:
    bullish = bearish = 0
    for data in by_tf.values():
        ema = data.get("ema", {})
        bias = str(ema.get("bias", "") or ema.get("trend", "") or ema.get("alignment", "")).lower()
        if "bullish" in bias:
            bullish += 1
        elif "bearish" in bias:
            bearish += 1
    if bullish > bearish:
        return "bullish"
    if bearish > bullish:
        return "bearish"
    return "neutral"


def _momentum(conf: dict) -> str:
    label = conf.get("label", "").lower()
    if "strong" in label:
        return "strong"
    if label in ("bullish", "bearish"):
        return "moderate"
    if "neutral" in label:
        return "weak"
    return "conflicted"


def _prompt_text(symbol: str, by_tf: dict, conf: dict, sr: list) -> str:
    parts = [f"[{symbol}]"]
    for tf, data in by_tf.items():
        if not data:
            continue
        rsi_v  = data.get("rsi",  {}).get("value", "?")
        ema_b  = (data.get("ema",  {}).get("bias")
                  or data.get("ema", {}).get("trend")
                  or data.get("ema", {}).get("alignment", "?"))
        adx_v  = data.get("adx",  {}).get("value", "?")
        macd_s = data.get("macd", {}).get("signal", "?")
        parts.append(f"{tf}: RSI {rsi_v} | EMA {ema_b} | ADX {adx_v} | MACD {macd_s}")
    if conf:
        parts.append(f"Confluence {conf.get('label','?')} ({conf.get('score',0):.1f}/{conf.get('max',0):.1f})")
    if sr:
        near = sr[:3]
        sr_str = " ".join(f"{s.get('type','?')}@{s.get('price','?')}" for s in near)
        parts.append(f"S/R: {sr_str}")
    return " | ".join(parts)[:500]
