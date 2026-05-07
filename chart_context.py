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

import time
from typing import Optional

import pandas as pd
import pandas_ta as ta

import bitget_client

# ── Cache ──────────────────────────────────────────────────────────────────────

_cache: dict = {}
CACHE_TTL = 600  # 10 minutes


def _cached(key: str, fn, ttl: int = CACHE_TTL):
    now = time.time()
    if key in _cache:
        ts, data = _cache[key]
        if now - ts < ttl:
            return data
    result = fn()
    _cache[key] = (now, result)
    return result


# ── Candle fetch ───────────────────────────────────────────────────────────────

def get_candles(symbol: str, timeframe: str = "4H", limit: int = 200) -> pd.DataFrame:
    """
    Fetch OHLCV candles from Bitget and return as a DataFrame.
    Columns: timestamp, open, high, low, close, volume (base), quote_volume
    """
    sym = symbol.upper()
    if not sym.endswith("USDT"):
        sym += "USDT"

    def _fetch():
        try:
            raw = bitget_client._get("/api/v2/mix/market/candles", {
                "symbol":      sym,
                "productType": "USDT-FUTURES",
                "granularity": timeframe,
                "limit":       str(limit),
            })
            if not raw or not isinstance(raw, list):
                return pd.DataFrame()

            df = pd.DataFrame(raw, columns=[
                "timestamp", "open", "high", "low", "close", "volume", "quote_volume"
            ])
            for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df["timestamp"] = pd.to_numeric(df["timestamp"])
            df = df.sort_values("timestamp").reset_index(drop=True)
            return df
        except Exception:
            return pd.DataFrame()

    return _cached(f"candles_{sym}_{timeframe}_{limit}", _fetch)


# ── Support / Resistance detection ────────────────────────────────────────────

def detect_support_resistance(df: pd.DataFrame, n_swing: int = 5,
                               tolerance_pct: float = 0.003,
                               max_levels: int = 8) -> list:
    """
    Swing-pivot S/R detection with clustering.
    n_swing: candles each side of a pivot to qualify as local high/low.
    tolerance_pct: prices within this % are merged into one level.
    Returns list of {price, type, strength, touches} sorted ascending.
    """
    if len(df) < n_swing * 2 + 3:
        return []

    highs = df["high"].values.astype(float)
    lows  = df["low"].values.astype(float)
    close = float(df["close"].iloc[-1])

    pivot_highs, pivot_lows = [], []
    for i in range(n_swing, len(df) - n_swing):
        window_h = highs[i - n_swing : i + n_swing + 1]
        window_l = lows[i  - n_swing : i + n_swing + 1]
        if highs[i] >= window_h.max():
            pivot_highs.append(highs[i])
        if lows[i] <= window_l.min():
            pivot_lows.append(lows[i])

    def _cluster(prices, level_type):
        if not prices:
            return []
        levels = []
        for p in sorted(prices):
            merged = False
            for lvl in levels:
                centroid = lvl["_sum"] / lvl["_n"]
                if abs(p - centroid) / centroid < tolerance_pct:
                    lvl["_sum"] += p
                    lvl["_n"]   += 1
                    merged = True
                    break
            if not merged:
                levels.append({"_sum": p, "_n": 1, "type": level_type})
        result = []
        for lvl in levels:
            price = lvl["_sum"] / lvl["_n"]
            touches = (
                sum(1 for h in highs if abs(h - price) / price < tolerance_pct) +
                sum(1 for l in lows  if abs(l - price) / price < tolerance_pct)
            )
            result.append({
                "price":    round(price, 8),
                "type":     lvl["type"],
                "strength": lvl["_n"],
                "touches":  touches,
            })
        return result

    supports    = _cluster(pivot_lows,  "support")
    resistances = _cluster(pivot_highs, "resistance")

    # Keep only supports near/below price and resistances near/above
    supports    = [l for l in supports    if l["price"] < close * 1.02]
    resistances = [l for l in resistances if l["price"] > close * 0.98]

    supports    = sorted(supports,    key=lambda x: (-x["touches"], -x["strength"]))[:max_levels // 2]
    resistances = sorted(resistances, key=lambda x: (-x["touches"], -x["strength"]))[:max_levels // 2]

    return sorted(supports + resistances, key=lambda x: x["price"])


# ── Trendline detection ────────────────────────────────────────────────────────

def detect_trendlines(df: pd.DataFrame, n_swing: int = 5, max_lines: int = 4) -> list:
    """
    Detect ascending support trendlines (uptrend) and descending resistance
    trendlines (downtrend) via swing-pivot pair validation.

    For each pair of pivots with the correct slope direction, the line is
    accepted only if no candle body violates it between the two anchor points
    (within 0.5% tolerance). Lines are then extended to the current candle.

    Returns list of {type, p1_time, p2_time, p1_price, p2_price, touches,
                     anchor1, anchor2} — up to max_lines total.
    """
    if len(df) < n_swing * 2 + 10:
        return []

    highs = df["high"].values.astype(float)
    lows  = df["low"].values.astype(float)
    times = df["timestamp"].values.astype(float)
    n     = len(df)
    tol   = 0.005  # 0.5% — crypto needs a bit of breathing room

    pivot_h = [i for i in range(n_swing, n - n_swing)
               if highs[i] >= highs[i - n_swing:i + n_swing + 1].max()]
    pivot_l = [i for i in range(n_swing, n - n_swing)
               if lows[i]  <= lows[i  - n_swing:i + n_swing + 1].min()]

    def _score(pivots, arr, ltype, must_be_above):
        candidates = []
        for a in range(len(pivots)):
            for b in range(a + 1, len(pivots)):
                i1, i2 = pivots[a], pivots[b]
                p1, p2 = arr[i1], arr[i2]
                if must_be_above  and p2 <= p1: continue
                if not must_be_above and p2 >= p1: continue

                slope = (p2 - p1) / (i2 - i1)

                # Validate: no candle violates the line between i1 and i2
                valid = True
                for k in range(i1, i2 + 1):
                    lv = p1 + slope * (k - i1)
                    if must_be_above  and arr[k] < lv * (1 - tol): valid = False; break
                    if not must_be_above and arr[k] > lv * (1 + tol): valid = False; break
                if not valid:
                    continue

                # Count additional touches beyond i2
                touches = 2
                for k in range(i2 + 1, n):
                    lv  = p1 + slope * (k - i1)
                    if abs(arr[k] - lv) / max(lv, 1e-9) < 0.004:
                        touches += 1

                end_price = p1 + slope * (n - 1 - i1)
                if end_price <= 0:
                    continue

                candidates.append({
                    "type":     ltype,
                    "p1_time":  int(times[i1]     // 1000),
                    "p2_time":  int(times[n - 1]  // 1000),
                    "p1_price": round(p1, 8),
                    "p2_price": round(end_price, 8),
                    "touches":  touches,
                    "anchor1":  round(p1, 8),
                    "anchor2":  round(p2, 8),
                })

        candidates.sort(key=lambda x: (-x["touches"], -x["p1_time"]))
        seen, unique = [], []
        for c in candidates:
            sig = (round(c["anchor1"], 2), round(c["anchor2"], 2))
            if sig not in seen:
                seen.append(sig)
                unique.append(c)
            if len(unique) >= max_lines // 2:
                break
        return unique

    up   = _score(pivot_l, lows,  "uptrend",   True)
    down = _score(pivot_h, highs, "downtrend", False)
    return up + down


# ── Indicator computation ──────────────────────────────────────────────────────

def compute_indicators(df: pd.DataFrame) -> dict:
    """
    Compute a full indicator suite on a candle DataFrame.
    Returns a structured dict with current values + trend descriptions.
    """
    if df.empty or len(df) < 30:
        return {"ok": False, "error": "Insufficient candle data"}

    close  = df["close"]
    high   = df["high"]
    low    = df["low"]
    volume = df["volume"]

    result = {"ok": True, "candles_used": len(df)}

    # ── RSI ────────────────────────────────────────────────────────────────────
    rsi_s = ta.rsi(close, length=14)
    if rsi_s is not None and not rsi_s.empty:
        rsi_val = round(float(rsi_s.iloc[-1]), 1)
        result["rsi"] = {
            "value": rsi_val,
            "signal": (
                "overbought (>70)" if rsi_val > 70 else
                "oversold (<30)"   if rsi_val < 30 else
                "neutral"
            ),
        }

    # ── Stochastic RSI ─────────────────────────────────────────────────────────
    stochrsi = ta.stochrsi(close, length=14, rsi_length=14, k=3, d=3)
    if stochrsi is not None and not stochrsi.empty:
        k_col = [c for c in stochrsi.columns if "STOCHRSIk" in c]
        d_col = [c for c in stochrsi.columns if "STOCHRSId" in c]
        if k_col and d_col:
            k = round(float(stochrsi[k_col[0]].iloc[-1]), 1)
            d = round(float(stochrsi[d_col[0]].iloc[-1]), 1)
            result["stoch_rsi"] = {
                "k": k, "d": d,
                "signal": (
                    "overbought (K>80)" if k > 80 else
                    "oversold (K<20)"   if k < 20 else
                    "neutral"
                ),
            }

    # ── MACD ───────────────────────────────────────────────────────────────────
    macd_df = ta.macd(close, fast=12, slow=26, signal=9)
    if macd_df is not None and not macd_df.empty:
        macd_col = [c for c in macd_df.columns if c.startswith("MACD_")]
        sig_col  = [c for c in macd_df.columns if c.startswith("MACDs_")]
        hist_col = [c for c in macd_df.columns if c.startswith("MACDh_")]
        if macd_col and sig_col and hist_col:
            macd_v = round(float(macd_df[macd_col[0]].iloc[-1]), 4)
            sig_v  = round(float(macd_df[sig_col[0]].iloc[-1]),  4)
            hist_v = round(float(macd_df[hist_col[0]].iloc[-1]), 4)
            hist_prev = float(macd_df[hist_col[0]].iloc[-2]) if len(macd_df) > 1 else hist_v
            crossover = macd_v > sig_v and float(macd_df[macd_col[0]].iloc[-2] if len(macd_df) > 1 else macd_v) <= float(macd_df[sig_col[0]].iloc[-2] if len(macd_df) > 1 else sig_v)
            crossunder = macd_v < sig_v and float(macd_df[macd_col[0]].iloc[-2] if len(macd_df) > 1 else macd_v) >= float(macd_df[sig_col[0]].iloc[-2] if len(macd_df) > 1 else sig_v)
            result["macd"] = {
                "macd":      macd_v,
                "signal":    sig_v,
                "histogram": hist_v,
                "trend":     "bullish" if macd_v > sig_v else "bearish",
                "histogram_trend": "growing" if hist_v > hist_prev else "shrinking",
                "crossover":  crossover,
                "crossunder": crossunder,
            }

    # ── EMAs ───────────────────────────────────────────────────────────────────
    emas = {}
    for length in [20, 50, 200]:
        if len(df) >= length:
            ema_s = ta.ema(close, length=length)
            if ema_s is not None and not ema_s.empty:
                emas[f"ema{length}"] = round(float(ema_s.iloc[-1]), 4)

    if emas:
        cur_price = round(float(close.iloc[-1]), 4)
        ema_result = {**emas, "current_price": cur_price}

        above = [f"EMA{k[3:]}" for k, v in emas.items() if cur_price > v]
        below = [f"EMA{k[3:]}" for k, v in emas.items() if cur_price < v]

        if len(above) == len(emas):
            ema_result["alignment"] = "fully bullish — price above all EMAs"
        elif len(below) == len(emas):
            ema_result["alignment"] = "fully bearish — price below all EMAs"
        else:
            ema_result["alignment"] = (
                f"mixed — above {', '.join(above)}; below {', '.join(below)}"
                if above else f"below {', '.join(below)}"
            )

        # Check EMA order (20 > 50 > 200 = bullish stack)
        if "ema20" in emas and "ema50" in emas and "ema200" in emas:
            if emas["ema20"] > emas["ema50"] > emas["ema200"]:
                ema_result["stack"] = "bullish (20 > 50 > 200)"
            elif emas["ema20"] < emas["ema50"] < emas["ema200"]:
                ema_result["stack"] = "bearish (20 < 50 < 200)"
            else:
                ema_result["stack"] = "mixed"

        result["ema"] = ema_result

    # ── Bollinger Bands ────────────────────────────────────────────────────────
    bbands = ta.bbands(close, length=20, std=2)
    if bbands is not None and not bbands.empty:
        upper_col  = [c for c in bbands.columns if "BBU" in c]
        lower_col  = [c for c in bbands.columns if "BBL" in c]
        mid_col    = [c for c in bbands.columns if "BBM" in c]
        bwidth_col = [c for c in bbands.columns if "BBB" in c]
        if upper_col and lower_col and mid_col:
            upper = float(bbands[upper_col[0]].iloc[-1])
            lower = float(bbands[lower_col[0]].iloc[-1])
            mid   = float(bbands[mid_col[0]].iloc[-1])
            price = float(close.iloc[-1])
            band_range = upper - lower
            position_pct = round((price - lower) / band_range * 100, 1) if band_range > 0 else 50
            bw = round(float(bbands[bwidth_col[0]].iloc[-1]), 4) if bwidth_col else None
            result["bollinger"] = {
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

    # ── ATR ────────────────────────────────────────────────────────────────────
    atr_s = ta.atr(high, low, close, length=14)
    if atr_s is not None and not atr_s.empty:
        atr_val   = round(float(atr_s.iloc[-1]), 4)
        cur_price = float(close.iloc[-1])
        atr_pct   = round(atr_val / cur_price * 100, 2) if cur_price else 0
        result["atr"] = {
            "value":   atr_val,
            "pct":     atr_pct,
            "comment": f"typical candle range {atr_pct}% of price — useful for SL sizing",
        }

    # ── ADX (trend strength) ───────────────────────────────────────────────────
    adx_df = ta.adx(high, low, close, length=14)
    if adx_df is not None and not adx_df.empty:
        adx_col = [c for c in adx_df.columns if c.startswith("ADX_")]
        dmp_col = [c for c in adx_df.columns if c.startswith("DMP_")]
        dmn_col = [c for c in adx_df.columns if c.startswith("DMN_")]
        if adx_col:
            adx_val = round(float(adx_df[adx_col[0]].iloc[-1]), 1)
            adx_result = {
                "value": adx_val,
                "strength": (
                    "strong trend (>25)"  if adx_val > 25 else
                    "trending (20–25)"    if adx_val > 20 else
                    "weak/no trend (<20)"
                ),
            }
            if dmp_col and dmn_col:
                dmp = float(adx_df[dmp_col[0]].iloc[-1])
                dmn = float(adx_df[dmn_col[0]].iloc[-1])
                adx_result["direction"] = "bullish (+DI > -DI)" if dmp > dmn else "bearish (-DI > +DI)"
            result["adx"] = adx_result

    # ── Volume vs average ──────────────────────────────────────────────────────
    if len(volume) >= 20:
        vol_now = float(volume.iloc[-1])
        vol_avg = float(volume.iloc[-20:].mean())
        vol_ratio = round(vol_now / vol_avg, 2) if vol_avg else 1
        result["volume"] = {
            "current":      round(vol_now, 2),
            "avg_20":       round(vol_avg, 2),
            "ratio":        vol_ratio,
            "signal": (
                f"high volume ({vol_ratio}x avg)" if vol_ratio > 1.5 else
                f"low volume ({vol_ratio}x avg)"  if vol_ratio < 0.7 else
                f"average volume ({vol_ratio}x avg)"
            ),
        }

    # ── Recent candle pattern (last 3 candles) ─────────────────────────────────
    if len(df) >= 3:
        candles = []
        for i in range(-3, 0):
            row = df.iloc[i]
            o, c_p, h, l = float(row["open"]), float(row["close"]), float(row["high"]), float(row["low"])
            body = abs(c_p - o)
            full_range = h - l
            body_pct = round(body / full_range * 100, 0) if full_range else 0
            candle_type = "bullish" if c_p > o else "bearish"
            if body_pct < 20:
                candle_type = "doji"
            candles.append(f"{candle_type} (body {body_pct:.0f}% of range)")
        result["recent_candles"] = candles

    # ── Support / Resistance ───────────────────────────────────────────────────
    sr = detect_support_resistance(df)
    if sr:
        result["support_resistance"] = sr

    # ── Trendlines ─────────────────────────────────────────────────────────────
    tl = detect_trendlines(df)
    if tl:
        result["trendlines"] = tl

    return result


# ── Formatting for Claude ──────────────────────────────────────────────────────

def format_for_prompt(symbol: str, indicators: dict, timeframe: str) -> str:
    """
    Convert indicator dict to a concise, Claude-readable text block.
    """
    if not indicators.get("ok"):
        return ""

    lines = [f"TECHNICAL INDICATORS — {symbol} {timeframe} ({indicators['candles_used']} candles):"]

    if "rsi" in indicators:
        r = indicators["rsi"]
        lines.append(f"  RSI(14): {r['value']} — {r['signal']}")

    if "macd" in indicators:
        m = indicators["macd"]
        cross = " ← BULLISH CROSSOVER" if m["crossover"] else (" ← BEARISH CROSSOVER" if m["crossunder"] else "")
        lines.append(f"  MACD: {m['trend']}, histogram {m['histogram_trend']}{cross}")

    if "ema" in indicators:
        e = indicators["ema"]
        lines.append(f"  EMAs: {e.get('alignment', '')}")
        if "stack" in e:
            lines.append(f"  EMA stack: {e['stack']}")

    if "bollinger" in indicators:
        b = indicators["bollinger"]
        lines.append(f"  Bollinger Bands: price at {b['position_pct']}th percentile — {b['signal']}")

    if "stoch_rsi" in indicators:
        s = indicators["stoch_rsi"]
        lines.append(f"  Stoch RSI: K={s['k']} D={s['d']} — {s['signal']}")

    if "adx" in indicators:
        a = indicators["adx"]
        direction = f", {a['direction']}" if "direction" in a else ""
        lines.append(f"  ADX(14): {a['value']} — {a['strength']}{direction}")

    if "atr" in indicators:
        a = indicators["atr"]
        lines.append(f"  ATR(14): {a['value']} ({a['pct']}% of price) — {a['comment']}")

    if "volume" in indicators:
        lines.append(f"  Volume: {indicators['volume']['signal']}")

    if "recent_candles" in indicators:
        lines.append(f"  Last 3 candles: {' → '.join(indicators['recent_candles'])}")

    if "support_resistance" in indicators:
        sr   = indicators["support_resistance"]
        sups = sorted([l for l in sr if l["type"] == "support"],    key=lambda x: -x["price"])
        ress = sorted([l for l in sr if l["type"] == "resistance"],  key=lambda x:  x["price"])
        if sups:
            lines.append(f"  Nearest support:    {sups[0]['price']} ({sups[0]['touches']} touches)")
        if ress:
            lines.append(f"  Nearest resistance: {ress[0]['price']} ({ress[0]['touches']} touches)")
        extra = ([f"S:{l['price']}" for l in sups[1:2]] +
                 [f"R:{l['price']}" for l in ress[1:2]])
        if extra:
            lines.append(f"  Other levels: {', '.join(extra)}")

    if "trendlines" in indicators:
        tls  = indicators["trendlines"]
        up   = [t for t in tls if t["type"] == "uptrend"]
        down = [t for t in tls if t["type"] == "downtrend"]
        parts = []
        if up:
            parts.append(f"uptrend support {up[0]['anchor1']}→{up[0]['anchor2']} ({up[0]['touches']} touches)")
        if down:
            parts.append(f"downtrend resistance {down[0]['anchor1']}→{down[0]['anchor2']} ({down[0]['touches']} touches)")
        if parts:
            lines.append(f"  Trendlines: {', '.join(parts)}")

    return "\n".join(lines)


# ── Main entry point ───────────────────────────────────────────────────────────

def get_chart_context(symbol: str, timeframes: list = None) -> dict:
    """
    Fetch candles and compute indicators for one or more timeframes.
    Returns: {timeframe: {indicators, prompt_text}, ...}
    """
    if timeframes is None:
        timeframes = ["4H", "1D"]

    result = {}
    for tf in timeframes:
        limit = 200 if tf in ("1H", "4H") else 100
        df    = get_candles(symbol, tf, limit)
        inds  = compute_indicators(df)
        result[tf] = {
            "indicators":  inds,
            "prompt_text": format_for_prompt(symbol, inds, tf),
        }
    return result


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
    """
    df = get_candles(symbol, timeframe, limit=limit)
    if df is None or df.empty:
        return {"candles": [], "levels": [], "symbol": symbol, "timeframe": timeframe}

    levels     = detect_support_resistance(df)
    trendlines = detect_trendlines(df)

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
        "trendlines":    trendlines,
        "symbol":        symbol,
        "timeframe":     timeframe,
        "current_price": round(float(df["close"].iloc[-1]), 8),
    }
