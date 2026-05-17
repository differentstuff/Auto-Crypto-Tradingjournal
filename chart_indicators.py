"""
chart_indicators.py — Pure indicator computation functions.

Accepts a DataFrame (columns: open, high, low, close, volume) and returns
structured dicts. No API calls, no caching, no side effects.

All functions degrade gracefully when < 30 bars are available.

Public API (stable — tested by tests/test_chart_indicators.py):
  compute_rsi, compute_ema_alignment, compute_macd, compute_adx,
  compute_prompt_text

Extended API (full suite used by chart_context.compute_indicators):
  compute_wavetrend, compute_stochrsi, compute_bollinger, compute_atr,
  compute_volume, compute_recent_candles, compute_cvd, compute_all_indicators
"""
from __future__ import annotations
import pandas as pd
import pandas_ta as ta


# ── Stable public functions (format unchanged — tests depend on these) ─────────

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

    if len(above) == total:        alignment = "bullish"
    elif len(below) == total:      alignment = "bearish"
    elif len(above) > len(below):  alignment = "mixed-bullish"
    elif len(below) > len(above):  alignment = "mixed-bearish"
    else:                          alignment = "neutral"

    e20, e50, e200 = emas.get("ema20", 0.0), emas.get("ema50", 0.0), emas.get("ema200", 0.0)
    if e20 and e50 and e200:
        stack = "bullish" if e20 > e50 > e200 else "bearish" if e20 < e50 < e200 else "mixed"
    else:
        stack = "mixed"

    return {**default, **emas, "current_price": cur, "alignment": alignment, "stack": stack}


def compute_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """MACD with crossover detection. Returns {"macd","signal","histogram","bias",...}."""
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
        "bias":              "bullish" if mv > sv else "bearish",
        "histogram_growing": hv > hp,
        "crossover":         (mv > sv) and (mp <= sp),
        "crossunder":        (mv < sv) and (mp >= sp),
    }


def compute_adx(df: pd.DataFrame, period: int = 14) -> dict:
    """ADX trend strength and direction."""
    default = {"value": 0.0, "trend_strength": "weak", "direction": "undetermined"}
    if df.empty or len(df) < 30:
        return default
    adx_df = ta.adx(df["high"], df["low"], df["close"], length=period)
    if adx_df is None or adx_df.empty:
        return default

    ac  = [c for c in adx_df.columns if c.startswith("ADX_")]
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


# ── Extended API — full suite consumed by chart_context.compute_indicators ─────

def compute_wavetrend(df: pd.DataFrame,
                      n1: int = 10, n2: int = 21,
                      ob: float = 53, os_: float = -53,
                      mfi_period: int = 60) -> pd.DataFrame:
    """
    Compute WaveTrend (VMC Cipher A/B).

    Returns a DataFrame aligned to df with columns:
      wt1, wt2, histogram, mfi, cross_bull, cross_bear, signal
    """
    hlc3 = (df["high"] + df["low"] + df["close"]) / 3.0
    esa  = hlc3.ewm(span=n1, adjust=False).mean()
    d    = (hlc3 - esa).abs().ewm(span=n1, adjust=False).mean()
    ci   = (hlc3 - esa) / (0.015 * d.replace(0, float("nan"))).fillna(1e-9)
    wt1  = ci.ewm(span=n2, adjust=False).mean()
    wt2  = wt1.rolling(4, min_periods=1).mean()
    hist = wt1 - wt2

    mfi_src = hlc3 * df["volume"]
    mfi_rsi = ta.rsi(mfi_src, length=mfi_period)
    if mfi_rsi is not None and not mfi_rsi.empty:
        mfi = (mfi_rsi - 50.0) * 2.0
    else:
        mfi = pd.Series(0.0, index=df.index)

    cross_bull = (wt1 > wt2) & (wt1.shift(1) <= wt2.shift(1))
    cross_bear = (wt1 < wt2) & (wt1.shift(1) >= wt2.shift(1))

    signal = pd.Series(None, index=df.index, dtype=object)
    gold_mask = cross_bull & (wt2 < -80)
    buy_mask  = cross_bull & (wt2 < os_) & ~gold_mask
    sell_mask = cross_bear & (wt2 > ob)
    signal[gold_mask] = "gold_buy"
    signal[buy_mask]  = "buy"
    signal[sell_mask] = "sell"

    return pd.DataFrame({
        "wt1":       wt1.round(2),
        "wt2":       wt2.round(2),
        "histogram": hist.round(2),
        "mfi":       mfi.round(2),
        "cross_bull": cross_bull,
        "cross_bear": cross_bear,
        "signal":    signal,
    }, index=df.index)


def compute_stochrsi(df: pd.DataFrame) -> dict | None:
    """Stochastic RSI(14). Returns {"k","d","signal"} or None."""
    if len(df) < 30:
        return None
    stochrsi = ta.stochrsi(df["close"], length=14, rsi_length=14, k=3, d=3)
    if stochrsi is None or stochrsi.empty:
        return None
    k_col = [c for c in stochrsi.columns if "STOCHRSIk" in c]
    d_col = [c for c in stochrsi.columns if "STOCHRSId" in c]
    if not (k_col and d_col):
        return None
    k_v = stochrsi[k_col[0]].iloc[-1]
    d_v = stochrsi[d_col[0]].iloc[-1]
    if pd.isna(k_v) or pd.isna(d_v):
        return None
    k, d = round(float(k_v), 1), round(float(d_v), 1)
    return {
        "k": k, "d": d,
        "signal": (
            "overbought (K>80)" if k > 80 else
            "oversold (K<20)"   if k < 20 else
            "neutral"
        ),
    }


def compute_bollinger(df: pd.DataFrame) -> dict | None:
    """Bollinger Bands(20,2). Returns {"upper","mid","lower","position_pct","band_width","signal"} or None."""
    if len(df) < 30:
        return None
    bbands = ta.bbands(df["close"], length=20, std=2)
    if bbands is None or bbands.empty:
        return None
    upper_col  = [c for c in bbands.columns if "BBU" in c]
    lower_col  = [c for c in bbands.columns if "BBL" in c]
    mid_col    = [c for c in bbands.columns if "BBM" in c]
    bwidth_col = [c for c in bbands.columns if "BBB" in c]
    if not (upper_col and lower_col and mid_col):
        return None
    upper = float(bbands[upper_col[0]].iloc[-1])
    lower = float(bbands[lower_col[0]].iloc[-1])
    mid   = float(bbands[mid_col[0]].iloc[-1])
    if any(pd.isna(v) for v in (upper, lower, mid)):
        return None
    price = float(df["close"].iloc[-1])
    band_range   = upper - lower
    position_pct = round((price - lower) / band_range * 100, 1) if band_range > 0 else 50.0
    bw = round(float(bbands[bwidth_col[0]].iloc[-1]), 4) if bwidth_col else None
    return {
        "upper":        round(upper, 4),
        "mid":          round(mid, 4),
        "lower":        round(lower, 4),
        "position_pct": position_pct,
        "band_width":   bw,
        "signal": (
            "near upper band (overbought zone)" if position_pct > 80 else
            "near lower band (oversold zone)"   if position_pct < 20 else
            "mid-band area"
        ),
    }


def compute_atr(df: pd.DataFrame, period: int = 14) -> dict | None:
    """ATR(period). Returns {"value","pct","comment"} or None."""
    if len(df) < 30:
        return None
    atr_s = ta.atr(df["high"], df["low"], df["close"], length=period)
    if atr_s is None or atr_s.empty or pd.isna(atr_s.iloc[-1]):
        return None
    atr_val   = round(float(atr_s.iloc[-1]), 4)
    cur_price = float(df["close"].iloc[-1])
    atr_pct   = round(atr_val / cur_price * 100, 2) if cur_price else 0
    return {
        "value":   atr_val,
        "pct":     atr_pct,
        "comment": f"typical candle range {atr_pct}% of price — useful for SL sizing",
    }


def compute_volume(df: pd.DataFrame) -> dict | None:
    """Volume vs 20-bar average. Returns {"current","avg_20","ratio","signal"} or None."""
    if len(df["volume"]) < 20:
        return None
    vol_now = float(df["volume"].iloc[-1])
    vol_avg = float(df["volume"].iloc[-20:].mean())
    ratio   = round(vol_now / vol_avg, 2) if vol_avg else 1.0
    return {
        "current": round(vol_now, 2),
        "avg_20":  round(vol_avg, 2),
        "ratio":   ratio,
        "signal": (
            f"high volume ({ratio}x avg)" if ratio > 1.5 else
            f"low volume ({ratio}x avg)"  if ratio < 0.7 else
            f"average volume ({ratio}x avg)"
        ),
    }


def compute_recent_candles(df: pd.DataFrame) -> list[str] | None:
    """Last 3 candle body descriptions. Returns list of 3 strings or None."""
    if len(df) < 3:
        return None
    candles = []
    for i in range(-3, 0):
        row = df.iloc[i]
        o, c_p, h, lo = float(row["open"]), float(row["close"]), float(row["high"]), float(row["low"])
        body       = abs(c_p - o)
        full_range = h - lo
        body_pct   = round(body / full_range * 100, 0) if full_range else 0
        candle_type = "doji" if body_pct < 20 else ("bullish" if c_p > o else "bearish")
        candles.append(f"{candle_type} (body {body_pct:.0f}% of range)")
    return candles


def compute_cvd(df: pd.DataFrame) -> dict | None:
    """
    Cumulative Volume Delta (Money Flow Multiplier approximation).
    Returns {"value","trend","signal"} or None.
    """
    if len(df) < 4:
        return None
    try:
        h_arr = df["high"].values.astype(float)
        l_arr = df["low"].values.astype(float)
        c_arr = df["close"].values.astype(float)
        v_arr = df["volume"].values.astype(float)
        running = 0.0
        cvd_series = []
        for i in range(len(h_arr)):
            denom = h_arr[i] - l_arr[i]
            delta = v_arr[i] * (2 * c_arr[i] - l_arr[i] - h_arr[i]) / denom if denom > 0 else 0.0
            running += delta
            cvd_series.append(running)
        cvd_now  = cvd_series[-1]
        cvd_prev = cvd_series[-4] if len(cvd_series) >= 4 else cvd_series[0]
        trend = "rising" if cvd_now > cvd_prev * 1.001 else (
                "falling" if cvd_now < cvd_prev * 0.999 else "flat")
        return {
            "value":  round(cvd_now, 2),
            "trend":  trend,
            "signal": (
                "bullish (net buy pressure)"  if trend == "rising" else
                "bearish (net sell pressure)" if trend == "falling" else
                "neutral"
            ),
        }
    except Exception:
        return None


def compute_all_indicators(df: pd.DataFrame) -> dict:
    """
    Full indicator suite in chart_context format.

    Returns {"ok": bool, ...} with all indicator sub-dicts.
    Does NOT include support_resistance or trendlines — chart_context adds those.
    """
    if df is None or df.empty or len(df) < 30:
        return {"ok": False, "error": "Insufficient candle data"}

    result: dict = {"ok": True, "candles_used": len(df)}

    # RSI — adapt "level" → "signal" with verbose labels
    rsi = compute_rsi(df)
    result["rsi"] = {
        "value":  rsi["value"],
        "signal": (
            "overbought (>70)" if rsi["level"] == "overbought" else
            "oversold (<30)"   if rsi["level"] == "oversold"   else
            "neutral"
        ),
    }

    # Stochastic RSI
    stochrsi = compute_stochrsi(df)
    if stochrsi:
        result["stoch_rsi"] = stochrsi

    # MACD — rename "bias" → "trend", "histogram_growing" → "histogram_trend"
    macd = compute_macd(df)
    result["macd"] = {
        "macd":            macd["macd"],
        "signal":          macd["signal"],
        "histogram":       macd["histogram"],
        "trend":           macd["bias"],
        "histogram_trend": "growing" if macd["histogram_growing"] else "shrinking",
        "crossover":       macd["crossover"],
        "crossunder":      macd["crossunder"],
    }

    # EMA — expand short alignment codes to verbose strings chart_context expects
    ema       = compute_ema_alignment(df)
    cur_price = ema.get("current_price", 0.0)
    ema_vals  = {k: v for k, v in ema.items() if k.startswith("ema") and v}
    if ema_vals:
        above = [f"EMA{k[3:]}" for k, v in ema_vals.items() if cur_price > v > 0]
        below = [f"EMA{k[3:]}" for k, v in ema_vals.items() if cur_price < v > 0]
        total = len(ema_vals)

        if len(above) == total:
            alignment = "fully bullish — price above all EMAs"
        elif len(below) == total:
            alignment = "fully bearish — price below all EMAs"
        else:
            alignment = (
                f"mixed — above {', '.join(above)}; below {', '.join(below)}"
                if above else f"below {', '.join(below)}"
            )

        stack_s = ema.get("stack", "mixed")
        stack = (
            "bullish (20 > 50 > 200)" if stack_s == "bullish" else
            "bearish (20 < 50 < 200)" if stack_s == "bearish" else
            "mixed"
        )
        result["ema"] = {**ema_vals, "current_price": cur_price,
                         "alignment": alignment, "stack": stack}

    # Bollinger Bands
    bb = compute_bollinger(df)
    if bb:
        result["bollinger"] = bb

    # ATR
    atr = compute_atr(df)
    if atr:
        result["atr"] = atr

    # ADX — expand short strings to verbose labels
    adx = compute_adx(df)
    if adx["value"] > 0:
        strength_map  = {
            "strong":   "strong trend (>25)",
            "trending": "trending (20–25)",
            "weak":     "weak/no trend (<20)",
        }
        direction_map = {
            "bullish": "bullish (+DI > -DI)",
            "bearish": "bearish (-DI > +DI)",
        }
        adx_result: dict = {
            "value":    adx["value"],
            "strength": strength_map.get(adx["trend_strength"], "weak/no trend (<20)"),
        }
        if adx["direction"] in direction_map:
            adx_result["direction"] = direction_map[adx["direction"]]
        result["adx"] = adx_result

    # Volume
    vol = compute_volume(df)
    if vol:
        result["volume"] = vol

    # Recent candles
    recent = compute_recent_candles(df)
    if recent:
        result["recent_candles"] = recent

    # WaveTrend
    try:
        wt_df    = compute_wavetrend(df)
        wt1_last = float(wt_df["wt1"].iloc[-1])
        wt2_last = float(wt_df["wt2"].iloc[-1])
        mfi_last = float(wt_df["mfi"].iloc[-1])
        sig_last = wt_df["signal"].iloc[-1]
        cb_last  = bool(wt_df["cross_bull"].iloc[-1])
        cs_last  = bool(wt_df["cross_bear"].iloc[-1])
        result["wavetrend"] = {
            "wt1":       round(wt1_last, 2),
            "wt2":       round(wt2_last, 2),
            "histogram": round(wt1_last - wt2_last, 2),
            "mfi":       round(mfi_last, 2),
            "cross":     "bullish" if cb_last else ("bearish" if cs_last else None),
            "zone":      (
                "overbought" if wt1_last >  53 else
                "oversold"   if wt1_last < -53 else
                "neutral"
            ),
            "signal":    sig_last,
        }
    except Exception:
        pass

    # CVD
    cvd = compute_cvd(df)
    if cvd:
        result["cvd"] = cvd

    result["order_flow"] = compute_order_flow_delta(df)

    return result


def compute_order_flow_delta(df: pd.DataFrame) -> dict | None:
    """
    Tick-rule proxy for per-candle aggressor delta.
    Positive delta = net buying pressure; negative = net selling pressure.
    Returns: {delta, cumulative_delta, signal, divergence}
    """
    if df is None or len(df) < 3:
        return None
    try:
        body      = df["close"] - df["open"]
        body_abs  = body.abs()
        ratio     = (body_abs / (body_abs + 1e-9)).clip(0.10, 0.90)
        buy_vol   = df["volume"] * ratio.where(body >= 0, 1 - ratio)
        sell_vol  = df["volume"] - buy_vol
        delta_bar = buy_vol - sell_vol

        delta     = float(delta_bar.iloc[-1])
        cum_delta = float(delta_bar.sum())

        price_high    = df["close"].iloc[-1] > df["close"].iloc[-5:-1].max()
        prior_avg     = float(delta_bar.iloc[-5:-1].mean()) if len(delta_bar) >= 5 else 0.0
        divergence    = bool(price_high and delta < prior_avg)

        signal = ("buying_pressure"  if delta > 0 else
                  "selling_pressure" if delta < 0 else "neutral")

        return {"delta": delta, "cumulative_delta": cum_delta,
                "signal": signal, "divergence": divergence}
    except Exception:
        return None
