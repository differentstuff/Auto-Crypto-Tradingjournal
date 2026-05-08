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

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import pandas as pd
import pandas_ta as ta

import bitget_client

# ── Cache ──────────────────────────────────────────────────────────────────────

_cache: dict = {}
_cache_lock  = threading.Lock()
CACHE_TTL    = 600  # 10 minutes

PRICE_TOLERANCE = 0.004  # 0.4% — shared by S/R clustering and trendline validation


def _cached(key: str, fn, ttl: int = CACHE_TTL):
    now = time.time()
    with _cache_lock:
        if key in _cache:
            ts, data = _cache[key]
            if now - ts < ttl:
                return data
    result = fn()
    with _cache_lock:
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
                               tolerance_pct: float = PRICE_TOLERANCE,
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

# Timeframes to scan for trendlines, highest → lowest.
# weight 4=1W (most prominent) → 1=1H (least prominent).
_TF_TL_CONFIG = [
    {"tf": "1W", "limit": 100, "weight": 4, "n_swing": 3},
    {"tf": "1D", "limit": 200, "weight": 3, "n_swing": 5},
    {"tf": "4H", "limit": 200, "weight": 2, "n_swing": 5},
    {"tf": "1H", "limit": 200, "weight": 1, "n_swing": 5},
]


def detect_trendlines(df: pd.DataFrame, n_swing: int = 5, max_lines: int = 4,
                      now_time_sec: float = None) -> list:
    """
    Detect ascending support trendlines (uptrend) and descending resistance
    trendlines (downtrend) via swing-pivot pair validation.

    Validation uses candle-index slope to check no candle violates the line
    between the two anchors (0.5% tolerance). The line is then extended to
    now_time_sec using a real-time slope so it displays correctly across
    any viewing timeframe.

    Returns list of {type, p1_time, p2_time, p1_price, p2_price, touches,
                     anchor1, anchor2} — p2_time is always current time.
    """
    if len(df) < n_swing * 2 + 10:
        return []

    now_sec   = now_time_sec if now_time_sec is not None else time.time()
    highs     = df["high"].values.astype(float)
    lows      = df["low"].values.astype(float)
    times_sec = df["timestamp"].values.astype(float) / 1000.0
    n         = len(df)

    # ATR-relative tolerance: 0.5× ATR as % of price, floored at PRICE_TOLERANCE.
    # BTC 4H candles wick 1-2%; a fixed 0.4% tolerance rejected valid trendlines.
    try:
        atr_s   = ta.atr(df["high"], df["low"], df["close"], length=14)
        atr_val = float(atr_s.iloc[-1]) if atr_s is not None and not atr_s.empty else 0
        cur_p   = float(df["close"].iloc[-1])
        atr_pct = atr_val / cur_p if cur_p > 0 else 0
        tol     = max(PRICE_TOLERANCE, 0.5 * atr_pct)
    except Exception:
        tol = PRICE_TOLERANCE

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
                if must_be_above     and p2 <= p1: continue
                if not must_be_above and p2 >= p1: continue

                # Candle-index slope — used only for validation
                ci_slope = (p2 - p1) / (i2 - i1)

                valid = True
                for k in range(i1, i2 + 1):
                    lv = p1 + ci_slope * (k - i1)
                    if must_be_above     and arr[k] < lv * (1 - tol): valid = False; break
                    if not must_be_above and arr[k] > lv * (1 + tol): valid = False; break
                if not valid:
                    continue

                touches  = 2
                at_risk  = False
                # at_risk: a post-anchor candle came within 30% of the tolerance band
                at_risk_threshold = tol * 0.30
                for k in range(i2 + 1, n):
                    lv      = p1 + ci_slope * (k - i1)
                    dist    = abs(arr[k] - lv) / max(lv, 1e-9)
                    if dist < tol:
                        touches += 1
                    elif dist < (tol + at_risk_threshold):
                        at_risk = True

                # Real-time slope to extend the line to now
                t1, t2 = times_sec[i1], times_sec[i2]
                if t2 <= t1:
                    continue
                rt_slope  = (p2 - p1) / (t2 - t1)
                end_price = p1 + rt_slope * (now_sec - t1)
                if end_price <= 0:
                    continue

                candidates.append({
                    "type":     ltype,
                    "p1_time":  int(t1),
                    "p2_time":  int(now_sec),
                    "p1_price": round(p1, 8),
                    "p2_price": round(end_price, 8),
                    "touches":  touches,
                    "anchor1":  round(p1, 8),
                    "anchor2":  round(p2, 8),
                    "at_risk":  at_risk,
                })

        candidates.sort(key=lambda x: (-x["touches"], -x["p1_time"]))
        seen, unique = [], []
        for c in candidates:
            sig = (round(c["anchor1"], 2), round(c["anchor2"], 2))
            if sig not in seen:
                seen.append(sig)
                unique.append(c)
            if len(unique) >= max(1, max_lines // 2):
                break
        return unique

    up   = _score(pivot_l, lows,  "uptrend",   True)
    down = _score(pivot_h, highs, "downtrend", False)
    return up + down


def detect_all_trendlines(symbol: str) -> list:
    """
    Detect trendlines on 1W, 1D, 4H, 1H in parallel and return them all tagged
    with 'timeframe' and 'weight' (4=1W → 1=1H). Sorted highest-TF first.
    """
    now_sec = time.time()

    def _fetch_tf(cfg):
        df = get_candles(symbol, cfg["tf"], limit=cfg["limit"])
        if df is None or df.empty or len(df) < cfg["n_swing"] * 2 + 10:
            return []
        tls = detect_trendlines(df, n_swing=cfg["n_swing"],
                                 max_lines=2, now_time_sec=now_sec)
        for tl in tls:
            tl["timeframe"] = cfg["tf"]
            tl["weight"]    = cfg["weight"]
        return tls

    result = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        for tls in ex.map(_fetch_tf, _TF_TL_CONFIG):
            result.extend(tls)
    result.sort(key=lambda x: -x["weight"])
    return result


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

    if "support_resistance" in indicators:
        sr   = indicators["support_resistance"]
        sups = sorted([l for l in sr if l["type"] == "support"],   key=lambda x: -x["price"])
        ress = sorted([l for l in sr if l["type"] == "resistance"], key=lambda x:  x["price"])
        if sups:
            parts.append(f"S:{sups[0]['price']}")
        if ress:
            parts.append(f"R:{ress[0]['price']}")

    return (f"{symbol} {timeframe}: " + " | ".join(parts)) if parts else ""


# ── Fibonacci retracement detection ───────────────────────────────────────────

FIB_LEVELS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
FIB_LABELS = {0.0: "0%", 0.236: "23.6%", 0.382: "38.2%",
              0.5: "50%", 0.618: "61.8%", 0.786: "78.6%", 1.0: "100%"}


def detect_fibonacci(df: pd.DataFrame, n_swing: int = 10) -> dict | None:
    """
    Auto-detect the most recent significant swing high and low, then compute
    standard Fibonacci retracement levels between them.

    Uses n_swing candles on each side to qualify a pivot.
    Returns {swing_high, swing_low, direction, levels: [{ratio, price, label}]}
    or None if insufficient data.
    """
    if df is None or len(df) < n_swing * 2 + 3:
        return None

    highs = df["high"].values.astype(float)
    lows  = df["low"].values.astype(float)
    n     = len(df)

    # Find pivot highs and lows (most recent qualifying pivot)
    pivot_highs = [i for i in range(n_swing, n - n_swing)
                   if highs[i] == highs[i - n_swing:i + n_swing + 1].max()]
    pivot_lows  = [i for i in range(n_swing, n - n_swing)
                   if lows[i]  == lows[i  - n_swing:i + n_swing + 1].min()]

    if not pivot_highs or not pivot_lows:
        return None

    last_high_idx = pivot_highs[-1]
    last_low_idx  = pivot_lows[-1]
    swing_high    = highs[last_high_idx]
    swing_low     = lows[last_low_idx]

    if swing_high <= swing_low:
        return None

    # Direction: if high is more recent → price fell → retracement is upward (bullish fib)
    # If low is more recent → price rose → retracement is downward (bearish fib)
    direction = "up" if last_low_idx > last_high_idx else "down"

    levels = []
    for ratio in FIB_LEVELS:
        if direction == "up":
            # Measuring from low to high: 0% = low, 100% = high
            price = swing_low + ratio * (swing_high - swing_low)
        else:
            # Measuring from high to low: 0% = high, 100% = low
            price = swing_high - ratio * (swing_high - swing_low)
        levels.append({
            "ratio": ratio,
            "price": round(float(price), 8),
            "label": FIB_LABELS[ratio],
        })

    return {
        "swing_high": round(float(swing_high), 8),
        "swing_low":  round(float(swing_low),  8),
        "direction":  direction,
        "levels":     levels,
    }


# ── Main entry point ───────────────────────────────────────────────────────────

def get_candles_at_time(symbol: str, timeframe: str, end_time_ms: int,
                        limit: int = 200) -> pd.DataFrame:
    """
    Fetch historical candles ending at end_time_ms (Unix ms). NOT cached —
    each call returns the snapshot visible at that specific point in time.
    """
    sym = symbol.upper()
    if not sym.endswith("USDT"):
        sym += "USDT"
    try:
        raw = bitget_client._get("/api/v2/mix/market/candles", {
            "symbol":      sym,
            "productType": "USDT-FUTURES",
            "granularity": timeframe,
            "limit":       str(limit),
            "endTime":     str(end_time_ms),
        })
        if not raw or not isinstance(raw, list):
            return pd.DataFrame()
        df = pd.DataFrame(raw, columns=[
            "timestamp", "open", "high", "low", "close", "volume", "quote_volume"
        ])
        for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["timestamp"] = pd.to_numeric(df["timestamp"])
        return df.sort_values("timestamp").reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


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


def _rsi_weight(rsi_val: float) -> float:
    """RSI contribution: ±1 at extremes, 0 at 50. Dead-band ±5 around 50."""
    if rsi_val > 55:   return min((rsi_val - 50) / 30.0,  1.0)
    if rsi_val < 45:   return max((rsi_val - 50) / 30.0, -1.0)
    return 0.0


def _macd_weight(macd: dict) -> float:
    """MACD contribution: full ±1 when aligned + growing, ±0.5 when aligned but fading."""
    trend    = macd.get("trend", "")
    hist_dir = macd.get("histogram_trend", "")
    if trend == "bullish":
        return 1.0 if hist_dir == "growing" else 0.5
    if trend == "bearish":
        return -1.0 if hist_dir == "growing" else -0.5
    return 0.0


def _ema_weight(ema: dict) -> float:
    """EMA contribution: ±1 fully aligned stack + price, ±0.5 partial."""
    al = ema.get("alignment", "")
    sk = ema.get("stack", "")
    if "fully bullish" in al and "bullish" in sk: return  1.0
    if "fully bearish" in al and "bearish" in sk: return -1.0
    if "bullish" in sk or "fully bullish" in al:  return  0.5
    if "bearish" in sk or "fully bearish" in al:  return -0.5
    return 0.0


def _adx_weight(adx: dict) -> float:
    """ADX contribution: direction × trend strength (ADX value / 50, capped at 1)."""
    direction = adx.get("direction", "")
    adx_val   = adx.get("value", 0)
    strength  = min(adx_val / 50.0, 1.0)
    if "bullish" in direction:  return  strength
    if "bearish" in direction:  return -strength
    return 0.0


def confluence_score(symbol: str, timeframes: list = None, ctx: dict = None) -> dict:
    """
    Aggregate RSI/MACD/EMA/ADX direction signals across timeframes with
    magnitude weighting — strong signals contribute more than weak ones.
    Returns {score, max, bullish, bearish, label, details}.
    Pass ctx to reuse an already-computed get_chart_context() result.
    """
    tfs = timeframes or ["4H", "1D"]
    if ctx is None:
        ctx = get_chart_context(symbol, tfs)

    total_score = 0.0
    details     = []

    for tf in tfs:
        inds = ctx.get(tf, {}).get("indicators", {})
        if not inds.get("ok"):
            continue

        rsi_w  = _rsi_weight(inds.get("rsi",  {}).get("value", 50))
        macd_w = _macd_weight(inds.get("macd", {}))
        ema_w  = _ema_weight(inds.get("ema",   {}))
        adx_w  = _adx_weight(inds.get("adx",   {}))
        base_score = rsi_w + macd_w + ema_w + adx_w
        vol_w  = _volume_weight(inds, base_score)

        tf_score = base_score + vol_w
        total_score += tf_score

        pos = round(sum(w for w in (rsi_w, macd_w, ema_w, adx_w, vol_w) if w > 0), 1)
        neg = round(sum(w for w in (rsi_w, macd_w, ema_w, adx_w, vol_w) if w < 0), 1)
        details.append(f"{tf}: +{pos}/{neg}")

    max_val = float(len(tfs) * 4.5)  # 4 directional signals (max 1.0) + volume bonus (max 0.5)
    pct     = total_score / max_val if max_val else 0.0

    # Thresholds: ±0.33 ≈ net 1/3 of max weight aligned; ±0.60 = strong consensus
    if pct >= 0.60:
        label = "Strong Bullish"
    elif pct >= 0.33:
        label = "Bullish"
    elif pct <= -0.60:
        label = "Strong Bearish"
    elif pct <= -0.33:
        label = "Bearish"
    else:
        label = "Neutral"

    bull_total = round(sum(w for tf in tfs
                           for inds_w in [_get_tf_weights(ctx, tf)]
                           for w in inds_w if w > 0), 1)
    bear_total = round(abs(sum(w for tf in tfs
                               for inds_w in [_get_tf_weights(ctx, tf)]
                               for w in inds_w if w < 0)), 1)

    return {
        "score":   round(total_score, 2),
        "max":     max_val,
        "bullish": bull_total,
        "bearish": bear_total,
        "label":   label,
        "details": details,
    }


def _volume_weight(inds: dict, directional_score: float) -> float:
    """
    Volume confirms the dominant direction.
    High volume (>1.5×) amplifies consensus by ±0.5.
    Low volume (<0.7×) dampens consensus by ∓0.25.
    Direction taken from the four other signals' net score.
    """
    ratio = inds.get("volume", {}).get("ratio", 1.0)
    sign  = 1 if directional_score > 0 else (-1 if directional_score < 0 else 0)
    if ratio > 1.5:
        return  0.5 * sign
    if ratio < 0.7:
        return -0.25 * sign
    return 0.0


def _get_tf_weights(ctx: dict, tf: str) -> list:
    """Return the five signal weights for a single timeframe (RSI/MACD/EMA/ADX/Vol)."""
    inds = ctx.get(tf, {}).get("indicators", {})
    if not inds.get("ok"):
        return []
    base = [
        _rsi_weight(inds.get("rsi",  {}).get("value", 50)),
        _macd_weight(inds.get("macd", {})),
        _ema_weight(inds.get("ema",   {})),
        _adx_weight(inds.get("adx",   {})),
    ]
    base.append(_volume_weight(inds, sum(base)))
    return base


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
        "symbol":        symbol,
        "timeframe":     timeframe,
        "current_price": round(float(df["close"].iloc[-1]), 8),
    }
