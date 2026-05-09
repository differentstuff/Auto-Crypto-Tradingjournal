"""
chart_indicators.py — Pure indicator computation functions.

Accepts a DataFrame (columns: open, high, low, close, volume) and returns
structured dicts. No API calls, no caching, no side effects.

All functions degrade gracefully when < 30 bars are available.
"""
from __future__ import annotations
import pandas as pd
import pandas_ta as ta


def compute_rsi(df: pd.DataFrame, period: int = 14) -> dict:
    """RSI(period). Returns {"value": float, "level": str}."""
    if len(df) < 30:
        return {"value": 50.0, "level": "neutral"}
    rsi_s = ta.rsi(df["close"], length=period)
    if rsi_s is None or rsi_s.empty or pd.isna(rsi_s.iloc[-1]):
        return {"value": 50.0, "level": "neutral"}
    val = round(float(rsi_s.iloc[-1]), 1)
    level = "overbought" if val > 70 else "oversold" if val < 30 else "neutral"
    return {"value": val, "level": level}


def compute_ema_alignment(df: pd.DataFrame) -> dict:
    """EMA 20/50/200 alignment. Returns alignment + stack keys."""
    default = {"ema20": 0.0, "ema50": 0.0, "ema200": 0.0,
               "current_price": 0.0, "alignment": "neutral", "stack": "mixed"}
    if df.empty or len(df) < 30:
        return default

    close = df["close"]
    emas: dict[str, float] = {}
    for length in [20, 50, 200]:
        if len(df) >= length:
            s = ta.ema(close, length=length)
            if s is not None and not s.empty and not pd.isna(s.iloc[-1]):
                emas[f"ema{length}"] = round(float(s.iloc[-1]), 4)

    if not emas:
        return default

    cur = round(float(close.iloc[-1]), 4)
    above = [k for k, v in emas.items() if cur > v > 0]
    below = [k for k, v in emas.items() if cur < v > 0]
    total = len(emas)

    if len(above) == total:   alignment = "bullish"
    elif len(below) == total: alignment = "bearish"
    elif len(above) > len(below): alignment = "mixed-bullish"
    elif len(below) > len(above): alignment = "mixed-bearish"
    else:                     alignment = "neutral"

    e20, e50, e200 = emas.get("ema20", 0.0), emas.get("ema50", 0.0), emas.get("ema200", 0.0)
    if e20 and e50 and e200:
        stack = "bullish" if e20 > e50 > e200 else "bearish" if e20 < e50 < e200 else "mixed"
    else:
        stack = "mixed"

    return {**default, **emas, "current_price": cur, "alignment": alignment, "stack": stack}


def compute_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """MACD with crossover detection."""
    default = {"macd": 0.0, "signal": 0.0, "histogram": 0.0,
               "bias": "bearish", "histogram_growing": False,
               "crossover": False, "crossunder": False}
    if len(df) < 30:
        return default
    m = ta.macd(df["close"], fast=fast, slow=slow, signal=signal)
    if m is None or m.empty:
        return default

    mc = [c for c in m.columns if c.startswith("MACD_")]
    sc = [c for c in m.columns if c.startswith("MACDs_")]
    hc = [c for c in m.columns if c.startswith("MACDh_")]
    if not (mc and sc and hc):
        return default

    mv, sv, hv = m[mc[0]].iloc[-1], m[sc[0]].iloc[-1], m[hc[0]].iloc[-1]
    if pd.isna(mv) or pd.isna(sv) or pd.isna(hv):
        return default

    mv, sv, hv = round(float(mv), 4), round(float(sv), 4), round(float(hv), 4)
    hp = float(m[hc[0]].iloc[-2]) if len(m) > 1 else hv
    mp = float(m[mc[0]].iloc[-2]) if len(m) > 1 else mv
    sp = float(m[sc[0]].iloc[-2]) if len(m) > 1 else sv

    return {
        "macd": mv, "signal": sv, "histogram": hv,
        "bias": "bullish" if mv > sv else "bearish",
        "histogram_growing": hv > hp,
        "crossover":  (mv > sv) and (mp <= sp),
        "crossunder": (mv < sv) and (mp >= sp),
    }


def compute_adx(df: pd.DataFrame, period: int = 14) -> dict:
    """ADX trend strength and direction."""
    default = {"value": 0.0, "trend_strength": "weak", "direction": "undetermined"}
    if df.empty or len(df) < 30:
        return default
    adx_df = ta.adx(df["high"], df["low"], df["close"], length=period)
    if adx_df is None or adx_df.empty:
        return default

    ac = [c for c in adx_df.columns if c.startswith("ADX_")]
    dmp = [c for c in adx_df.columns if c.startswith("DMP_")]
    dmn = [c for c in adx_df.columns if c.startswith("DMN_")]
    if not ac:
        return default

    av = adx_df[ac[0]].iloc[-1]
    if pd.isna(av):
        return default
    av = round(float(av), 1)

    strength = "strong" if av > 25 else "trending" if av > 20 else "weak"
    direction = "undetermined"
    if dmp and dmn:
        dp, dn = adx_df[dmp[0]].iloc[-1], adx_df[dmn[0]].iloc[-1]
        if not pd.isna(dp) and not pd.isna(dn):
            direction = "bullish" if float(dp) > float(dn) else "bearish"

    return {"value": av, "trend_strength": strength, "direction": direction}


def compute_prompt_text(df: pd.DataFrame, sr_levels: list[float]) -> str:
    """
    Compute all indicators and return a compact single-line summary < 250 chars.
    Returns empty string if < 30 bars.
    """
    if df.empty or len(df) < 30:
        return ""

    parts: list[str] = []

    rsi = compute_rsi(df)
    sig = "OB" if rsi["value"] > 70 else ("OS" if rsi["value"] < 30 else "neu")
    parts.append(f"RSI {rsi['value']}({sig})")

    macd = compute_macd(df)
    cross = "↑XO" if macd["crossover"] else ("↓XO" if macd["crossunder"] else "")
    parts.append(f"MACD {macd['bias'][:4]}{cross}")

    ema = compute_ema_alignment(df)
    sk = ema.get("stack", "mixed")
    al = ema["alignment"]
    if al == "bullish" and sk == "bullish":   parts.append("EMA ↑all")
    elif al == "bearish" and sk == "bearish": parts.append("EMA ↓all")
    elif "bullish" in sk: parts.append("EMA ↑stk")
    elif "bearish" in sk: parts.append("EMA ↓stk")
    else:                  parts.append("EMA mix")

    adx = compute_adx(df)
    da = "↑" if adx["direction"] == "bullish" else "↓" if adx["direction"] == "bearish" else ""
    st = {"strong": "str", "trending": "trn", "weak": "wk"}.get(adx["trend_strength"], "wk")
    parts.append(f"ADX {adx['value']}{da}({st})")

    try:
        atr_s = ta.atr(df["high"], df["low"], df["close"], length=14)
        if atr_s is not None and not atr_s.empty and not pd.isna(atr_s.iloc[-1]):
            cur = float(df["close"].iloc[-1])
            atr_pct = round(float(atr_s.iloc[-1]) / cur * 100, 2) if cur else 0
            parts.append(f"ATR {atr_pct}%")
    except Exception:
        pass

    if sr_levels and len(df) > 0:
        cur_p = float(df["close"].iloc[-1])
        sups  = sorted([p for p in sr_levels if p < cur_p], reverse=True)
        ress  = sorted([p for p in sr_levels if p >= cur_p])
        if sups:  parts.append(f"S:{round(sups[0], 4)}")
        if ress:  parts.append(f"R:{round(ress[0], 4)}")

    text = " | ".join(parts)
    return text[:249] if len(text) > 249 else text
