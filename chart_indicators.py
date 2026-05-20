"""
chart_indicators.py — Pure indicator computation functions.

Accepts a DataFrame (columns: open, high, low, close, volume) and returns
structured dicts. No API calls, no caching, no side effects.

All functions degrade gracefully when < 30 bars are available.
Uses only numpy/pandas — no external indicator library dependency.
This ensures compatibility with Python 3.14+ and avoids version lock-in.

Public API (stable — tested by tests/test_chart_indicators.py):
  compute_rsi, compute_ema_alignment, compute_macd, compute_adx,
  compute_prompt_text

Extended API (full suite used by chart_context.compute_indicators):
  compute_wavetrend, compute_stochrsi, compute_bollinger, compute_atr,
  compute_volume, compute_recent_candles, compute_cvd, compute_all_indicators
"""
from __future__ import annotations
import pandas as pd
import numpy as np


# ── Stable public functions (format unchanged — tests depend on these) ─────────

def compute_rsi(df: pd.DataFrame, period: int = 14) -> dict:
    """RSI(period) using Wilder smoothing. Returns {"value": float, "level": str}."""
    if len(df) < 30:
        return {"value": 50.0, "level": "neutral"}
    try:
        close = df["close"].astype(float)
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, 1e-9)
        rsi_s = 100 - (100 / (1 + rs))
        val = rsi_s.iloc[-1]
        if pd.isna(val):
            return {"value": 50.0, "level": "neutral"}
        val = round(float(val), 1)
        level = "overbought" if val > 70 else "oversold" if val < 30 else "neutral"
        return {"value": val, "level": level}
    except Exception:
        return {"value": 50.0, "level": "neutral"}


def compute_ema_alignment(df: pd.DataFrame) -> dict:
    """EMA 20/50/200 alignment. Returns alignment + stack keys."""
    default = {"ema20": 0.0, "ema50": 0.0, "ema200": 0.0,
               "current_price": 0.0, "alignment": "neutral", "stack": "mixed"}
    if df.empty or len(df) < 30:
        return default
    try:
        close = df["close"].astype(float)
        emas: dict[str, float] = {}
        for length in [20, 50, 200]:
            if len(df) >= length:
                s = close.ewm(span=length, adjust=False).mean()
                if not pd.isna(s.iloc[-1]):
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
    except Exception:
        return default


def compute_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """MACD with crossover detection. Returns {"macd","signal","histogram","bias",...}."""
    default = {"macd": 0.0, "signal": 0.0, "histogram": 0.0,
               "bias": "bearish", "histogram_growing": False,
               "crossover": False, "crossunder": False}
    if len(df) < 30:
        return default
    try:
        close = df["close"].astype(float)
        ema_fast = close.ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line

        mv = round(float(macd_line.iloc[-1]), 4)
        sv = round(float(signal_line.iloc[-1]), 4)
        hv = round(float(histogram.iloc[-1]), 4)
        hp = float(histogram.iloc[-2]) if len(histogram) > 1 else hv
        mp = float(macd_line.iloc[-2]) if len(macd_line) > 1 else mv
        sp = float(signal_line.iloc[-2]) if len(signal_line) > 1 else sv

        if any(pd.isna(v) for v in (mv, sv, hv)):
            return default

        return {
            "macd": mv, "signal": sv, "histogram": hv,
            "bias":              "bullish" if mv > sv else "bearish",
            "histogram_growing": hv > hp,
            "crossover":         (mv > sv) and (mp <= sp),
            "crossunder":        (mv < sv) and (mp >= sp),
        }
    except Exception:
        return default


def compute_adx(df: pd.DataFrame, period: int = 14) -> dict:
    """ADX trend strength and direction using Wilder smoothing."""
    default = {"value": 0.0, "trend_strength": "weak", "direction": "undetermined"}
    if df.empty or len(df) < 30:
        return default
    try:
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        close = df["close"].astype(float)
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        up_move = high.diff()
        down_move = -low.diff()
        plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
        minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
        atr = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        plus_di = 100 * plus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean() / atr.replace(0, 1e-9)
        minus_di = 100 * minus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean() / atr.replace(0, 1e-9)
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-9)
        adx_s = dx.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

        av = adx_s.iloc[-1]
        if pd.isna(av):
            return default
        av = round(float(av), 1)
        strength = "strong" if av > 25 else "trending" if av > 20 else "weak"
        dp, dn = plus_di.iloc[-1], minus_di.iloc[-1]
        direction = "undetermined"
        if not pd.isna(dp) and not pd.isna(dn):
            direction = "bullish" if float(dp) > float(dn) else "bearish"
        return {"value": av, "trend_strength": strength, "direction": direction}
    except Exception:
        return default


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
        atr_result = compute_atr(df)
        if atr_result:
            parts.append(f"ATR {atr_result['pct']}%")
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
    Compute WaveTrend (VMC Cipher A/B) using pure numpy/pandas.

    Returns a DataFrame aligned to df with columns:
      wt1, wt2, histogram, mfi, cross_bull, cross_bear, signal
    """
    try:
        hlc3 = (df["high"].astype(float) + df["low"].astype(float) + df["close"].astype(float)) / 3.0
        esa  = hlc3.ewm(span=n1, adjust=False).mean()
        d    = (hlc3 - esa).abs().ewm(span=n1, adjust=False).mean()
        ci   = (hlc3 - esa) / (0.015 * d.replace(0, float("nan"))).fillna(1e-9)
        wt1  = ci.ewm(span=n2, adjust=False).mean()
        wt2  = wt1.rolling(4, min_periods=1).mean()
        hist = wt1 - wt2

        # MFI approximation using RSI of (hlc3 * volume)
        mfi_src = hlc3 * df["volume"].astype(float)
        mfi_delta = mfi_src.diff()
        mfi_gain = mfi_delta.clip(lower=0)
        mfi_loss = (-mfi_delta).clip(lower=0)
        mfi_avg_gain = mfi_gain.ewm(alpha=1 / mfi_period, min_periods=mfi_period, adjust=False).mean()
        mfi_avg_loss = mfi_loss.ewm(alpha=1 / mfi_period, min_periods=mfi_period, adjust=False).mean()
        mfi_rs = mfi_avg_gain / mfi_avg_loss.replace(0, 1e-9)
        mfi_rsi = 100 - (100 / (1 + mfi_rs))
        mfi = (mfi_rsi - 50.0) * 2.0

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
    except Exception:
        cols = ["wt1", "wt2", "histogram", "mfi", "cross_bull", "cross_bear", "signal"]
        return pd.DataFrame(columns=cols)


def compute_stochrsi(df: pd.DataFrame, period: int = 14, k: int = 3, d: int = 3) -> dict | None:
    """Stochastic RSI(14). Returns {"k","d","signal"} or None."""
    if len(df) < 30:
        return None
    try:
        close = df["close"].astype(float)
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, 1e-9)
        rsi_s = 100 - (100 / (1 + rs))
        rsi_min = rsi_s.rolling(period).min()
        rsi_max = rsi_s.rolling(period).max()
        stoch = (rsi_s - rsi_min) / (rsi_max - rsi_min).replace(0, 1e-9) * 100
        k_line = stoch.rolling(k).mean()
        d_line = k_line.rolling(d).mean()
        k_val, d_val = k_line.iloc[-1], d_line.iloc[-1]
        if pd.isna(k_val) or pd.isna(d_val):
            return None
        k_v, d_v = round(float(k_val), 1), round(float(d_val), 1)
        return {
            "k": k_v, "d": d_v,
            "signal": (
                "overbought (K>80)" if k_v > 80 else
                "oversold (K<20)"   if k_v < 20 else
                "neutral"
            ),
        }
    except Exception:
        return None


def compute_bollinger(df: pd.DataFrame, period: int = 20, std_dev: float = 2.0) -> dict | None:
    """Bollinger Bands(20,2). Returns {"upper","mid","lower","position_pct","band_width","signal"} or None."""
    if len(df) < 30:
        return None
    try:
        close = df["close"].astype(float)
        mid = close.rolling(period).mean()
        std = close.rolling(period).std(ddof=0)
        upper = mid + std_dev * std
        lower = mid - std_dev * std
        bwidth = (upper - lower) / mid.replace(0, 1e-9) * 100

        u, m, lo, bw = upper.iloc[-1], mid.iloc[-1], lower.iloc[-1], bwidth.iloc[-1]
        if any(pd.isna(v) for v in (u, m, lo)):
            return None

        price = float(close.iloc[-1])
        band_range = float(u) - float(lo)
        position_pct = round((price - float(lo)) / band_range * 100, 1) if band_range > 0 else 50.0

        return {
            "upper":        round(float(u), 4),
            "mid":          round(float(m), 4),
            "lower":        round(float(lo), 4),
            "position_pct": position_pct,
            "band_width":   round(float(bw), 4) if not pd.isna(bw) else None,
            "signal": (
                "near upper band (overbought zone)" if position_pct > 80 else
                "near lower band (oversold zone)"   if position_pct < 20 else
                "mid-band area"
            ),
        }
    except Exception:
        return None


def compute_atr(df: pd.DataFrame, period: int = 14) -> dict | None:
    """ATR(period) using Wilder smoothing. Returns {"value","pct","comment"} or None."""
    if len(df) < 30:
        return None
    try:
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        close = df["close"].astype(float)
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr_s = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        atr_val = atr_s.iloc[-1]
        if pd.isna(atr_val):
            return None
        atr_val = round(float(atr_val), 4)
        cur_price = float(close.iloc[-1])
        atr_pct = round(atr_val / cur_price * 100, 2) if cur_price > 0 else 0.0
        return {
            "value":   atr_val,
            "pct":     atr_pct,
            "comment": f"typical candle range {atr_pct}% of price — useful for SL sizing",
        }
    except Exception:
        return None


def compute_volume(df: pd.DataFrame) -> dict | None:
    """Volume vs 20-bar average. Returns {"current","avg_20","ratio","signal"} or None."""
    if len(df["volume"]) < 20:
        return None
    try:
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
    except Exception:
        return None


def compute_recent_candles(df: pd.DataFrame) -> list[str] | None:
    """Last 3 candle body descriptions. Returns list of 3 strings or None."""
    if len(df) < 3:
        return None
    try:
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
    except Exception:
        return None


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

    # RSI
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

    # MACD
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

    # EMA
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

    # ADX
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
        if not wt_df.empty:
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
        body      = df["close"].astype(float) - df["open"].astype(float)
        body_abs  = body.abs()
        ratio     = (body_abs / (body_abs + 1e-9)).clip(0.10, 0.90)
        buy_vol   = df["volume"].astype(float) * ratio.where(body >= 0, 1 - ratio)
        sell_vol  = df["volume"].astype(float) - buy_vol
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