"""
chart_context.py — OHLCV candle fetch + technical indicator computation.

Pulls candles from Bitget (authenticated, no extra cost) and computes a
comprehensive indicator suite via pandas-ta. Results are cached per
(symbol, timeframe) for 10 minutes to avoid hammering the API.

Indicators computed:
  Trend  : EMA 20/50/200, SMA 50/200, ADX(14), trend alignment
  Momentum: RSI(14), Stoch RSI(14), MACD(12,26,9)
  Volatility: ATR(14), Bollinger Bands(20,2)
  Volume : OBV, volume vs 20-period average

Timeframe granularity strings (Bitget):
  '1m' '3m' '5m' '15m' '30m' '1H' '2H' '4H' '6H' '12H' '1D' '3D' '1W'
"""

from concurrent.futures import ThreadPoolExecutor

import pandas as pd

import bitget_client
from chart_candles import _cache, _cache_lock, _cached, get_candles, get_candles_at_time  # noqa: F401
from chart_indicators import compute_all_indicators, compute_wavetrend
from chart_patterns import detect_trendlines, detect_all_trendlines, detect_fibonacci  # noqa: F401
from chart_sr import detect_support_resistance
from chart_confluence import (  # noqa: F401
    confluence_score, SMT_SYMBOLS,
    _smt_weight, _rsi_weight, _macd_weight, _ema_weight, _adx_weight,
    _wt_weight, _volume_weight, _cvd_weight, _mfi_weight, _get_tf_weights,
)


# ── Indicator computation ──────────────────────────────────────────────────────

def compute_indicators(df: pd.DataFrame) -> dict:
    """
    Compute the full indicator suite for a candle DataFrame.

    Delegates indicator computation to chart_indicators.compute_all_indicators,
    then adds S/R levels (from chart_sr) and trendlines (detected locally).
    """
    result = compute_all_indicators(df)
    if result.get("ok"):
        sr = detect_support_resistance(df)
        if sr:
            result["support_resistance"] = sr
        tl = detect_trendlines(df)
        if tl:
            result["trendlines"] = tl
    return result


# ── Formatting for Claude ──────────────────────────────────────────────────────

def format_for_prompt(symbol: str, indicators: dict, timeframe: str) -> str:
    """
    Convert indicator dict to a compact single-line summary for Claude prompts.
    ~80 chars vs the old ~15 lines — significant token saving at no accuracy cost.
    """
    if not indicators.get("ok"):
        return ""

    parts = []

    if "rsi" in indicators:
        r = indicators["rsi"]
        sig = "OB" if r["value"] > 70 else ("OS" if r["value"] < 30 else "neu")
        parts.append(f"RSI {r['value']}({sig})")

    if "macd" in indicators:
        m = indicators["macd"]
        cross = "↑XO" if m.get("crossover") else ("↓XO" if m.get("crossunder") else "")
        parts.append(f"MACD {m['trend'][:4]}{cross}")

    if "ema" in indicators:
        e  = indicators["ema"]
        al = e.get("alignment", "")
        sk = e.get("stack", "")
        if "fully bullish" in al:
            parts.append("EMA ↑all")
        elif "fully bearish" in al:
            parts.append("EMA ↓all")
        elif "bullish" in sk:
            parts.append("EMA ↑stk")
        elif "bearish" in sk:
            parts.append("EMA ↓stk")
        else:
            parts.append("EMA mix")

    if "adx" in indicators:
        a   = indicators["adx"]
        ds  = "↑" if "bullish" in a.get("direction", "") else ("↓" if "bearish" in a.get("direction", "") else "")
        parts.append(f"ADX {a['value']}{ds}")

    if "atr" in indicators:
        parts.append(f"ATR {indicators['atr']['pct']}%")

    if "bollinger" in indicators:
        parts.append(f"BB {indicators['bollinger']['position_pct']}%")

    if "volume" in indicators:
        v = indicators["volume"]
        arrow = "↑" if v["ratio"] > 1.5 else ("↓" if v["ratio"] < 0.7 else "")
        parts.append(f"Vol {v['ratio']}x{arrow}")

    if "wavetrend" in indicators:
        wt = indicators["wavetrend"]
        wt1v = wt.get("wt1", 0)
        sig  = wt.get("signal")
        zone = wt.get("zone", "neutral")
        cross = wt.get("cross")
        if sig == "gold_buy":
            wt_str = f"WT GOLD↑({wt1v})"   # extreme OS cross — strongest buy
        elif sig == "buy":
            wt_str = f"WT↑XO-OS({wt1v})"  # bullish cross in oversold
        elif sig == "sell":
            wt_str = f"WT↓XO-OB({wt1v})"  # bearish cross in overbought
        elif cross == "bullish":
            wt_str = f"WT↑XO({wt1v})"     # cross outside OS zone
        elif cross == "bearish":
            wt_str = f"WT↓XO({wt1v})"
        else:
            zone_tag = "OB" if zone == "overbought" else ("OS" if zone == "oversold" else "")
            wt_str = f"WT {wt1v}{('('+zone_tag+')') if zone_tag else ''}"
        parts.append(wt_str)

    if "support_resistance" in indicators:
        sr   = indicators["support_resistance"]
        sups = sorted([l for l in sr if l["type"] == "support"],   key=lambda x: -x["price"])
        ress = sorted([l for l in sr if l["type"] == "resistance"], key=lambda x:  x["price"])
        if sups:
            parts.append(f"S:{sups[0]['price']}")
        if ress:
            parts.append(f"R:{ress[0]['price']}")

    if "cvd" in indicators:
        c = indicators["cvd"]
        arrow = "↑" if c["trend"] == "rising" else ("↓" if c["trend"] == "falling" else "→")
        parts.append(f"CVD{arrow}")

    return (f"{symbol} {timeframe}: " + " | ".join(parts)) if parts else ""




# ── Main entry point ───────────────────────────────────────────────────────────

def get_historical_context(symbol: str, timeframes: list, end_time_ms: int) -> dict:
    """
    Like get_chart_context() but reconstructed at a specific historical timestamp.
    Not cached — used for hindsight analysis only.
    """
    def _compute(tf):
        limit = 200 if tf in ("1H", "4H") else 100
        df    = get_candles_at_time(symbol, tf, end_time_ms, limit)
        inds  = compute_indicators(df)
        return tf, {"indicators": inds, "prompt_text": format_for_prompt(symbol, inds, tf)}

    result = {}
    with ThreadPoolExecutor(max_workers=len(timeframes)) as ex:
        for tf, data in ex.map(_compute, timeframes):
            result[tf] = data
    return result


def get_chart_context(symbol: str, timeframes: list = None) -> dict:
    """
    Fetch candles and compute indicators for one or more timeframes in parallel.
    Returns: {timeframe: {indicators, prompt_text}, ...}
    """
    if timeframes is None:
        timeframes = ["4H", "1D"]

    def _compute(tf):
        limit = 200 if tf in ("1H", "4H") else 100
        df    = get_candles(symbol, tf, limit)
        inds  = compute_indicators(df)
        return tf, {"indicators": inds, "prompt_text": format_for_prompt(symbol, inds, tf)}

    result = {}
    with ThreadPoolExecutor(max_workers=len(timeframes)) as ex:
        for tf, data in ex.map(_compute, timeframes):
            result[tf] = data
    return result


# confluence_score, SMT_SYMBOLS, and all _*_weight helpers live in chart_confluence.py
# They are re-exported above via: from chart_confluence import ...


def format_multi_tf_for_prompt(symbol: str, timeframes: list = None) -> str:
    """
    Get chart context for multiple timeframes and return combined prompt text.
    """
    ctx = get_chart_context(symbol, timeframes or ["4H", "1D"])
    blocks = [v["prompt_text"] for v in ctx.values() if v.get("prompt_text")]
    return "\n\n".join(blocks)


def get_candles_for_chart(symbol: str, timeframe: str = "4H", limit: int = 200) -> dict:
    """
    Return OHLCV candles + S/R levels formatted for the frontend chart modal.
    Candle timestamps are in seconds (as required by LightweightCharts).
    Also returns htf_levels: weekly S/R so major structural zones are always
    visible even when viewing intraday timeframes.
    """
    df = get_candles(symbol, timeframe, limit=limit)
    if df is None or df.empty:
        return {"candles": [], "levels": [], "htf_levels": [], "symbol": symbol, "timeframe": timeframe}

    levels     = detect_support_resistance(df)
    trendlines = detect_all_trendlines(symbol)  # 1W+1D+4H+1H, extended to now
    fibonacci  = detect_fibonacci(df)

    # WaveTrend series for the oscillator pane
    try:
        wt_df      = compute_wavetrend(df)
        import math as _math
        def _f(v):
            """Float safe for JSON — NaN/Inf become None."""
            try:
                fv = float(v)
                return None if _math.isnan(fv) or _math.isinf(fv) else round(fv, 2)
            except Exception:
                return None
        wt_series  = [
            {
                "time":      int(row["timestamp"] // 1000),
                "wt1":       _f(wt_df["wt1"].iloc[i]),
                "wt2":       _f(wt_df["wt2"].iloc[i]),
                "histogram": _f(wt_df["histogram"].iloc[i]),
                "mfi":       _f(wt_df["mfi"].iloc[i]),
                "signal":    _f(wt_df["signal"].iloc[i]),
            }
            for i, (_, row) in enumerate(df.iterrows())
        ]
    except Exception:
        wt_series = []

    # Weekly S/R — always fetch so major zones show on intraday charts
    htf_levels = []
    if timeframe not in ("1W", "3D"):
        df_weekly = get_candles(symbol, "1W", limit=100)
        if df_weekly is not None and not df_weekly.empty:
            htf_raw = detect_support_resistance(df_weekly, max_levels=6)
            # Tag as htf and deduplicate against current-TF levels (within 0.6%)
            cur_prices = {l["price"] for l in levels}
            for lvl in htf_raw:
                if not any(abs(lvl["price"] - p) / max(p, 1e-9) < 0.006 for p in cur_prices):
                    htf_levels.append({**lvl, "htf": True, "timeframe": "1W"})

    candles = [
        {
            "time":   int(row["timestamp"] // 1000),
            "open":   round(float(row["open"]),  8),
            "high":   round(float(row["high"]),  8),
            "low":    round(float(row["low"]),   8),
            "close":  round(float(row["close"]), 8),
            "volume": round(float(row["volume"]), 2),
        }
        for _, row in df.iterrows()
    ]

    return {
        "candles":       candles,
        "levels":        levels,
        "htf_levels":    htf_levels,
        "trendlines":    trendlines,
        "fibonacci":     fibonacci,
        "wavetrend":     wt_series,
        "symbol":        symbol,
        "timeframe":     timeframe,
        "current_price": round(float(df["close"].iloc[-1]), 8),
    }
