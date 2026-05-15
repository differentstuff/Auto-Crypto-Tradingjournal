"""
chart_patterns.py — Pure geometric pattern detection on OHLCV DataFrames.
No network calls of its own. Input: DataFrame. Output: lists/dicts.
Extracted from chart_context.py.
"""
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import pandas_ta as ta

from constants import PRICE_TOLERANCE
from chart_candles import get_candles


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
