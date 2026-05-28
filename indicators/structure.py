"""
indicators/structure.py -- Market structure indicator computations.

Pure functions: accept a DataFrame, return structured dicts.
No API calls, no caching, no side effects.

Port of: chart_sr.py (S/R levels), chart_patterns.py (trendlines, fibonacci)

Public API (used by registry and enzymes):
  detect_sr_levels, nearest_levels, detect_trendlines, detect_fibonacci
"""

from __future__ import annotations

import logging
import time

import pandas as pd

_log = logging.getLogger(__name__)

# Default tolerance for S/R clustering (from constants.py PRICE_TOLERANCE)
DEFAULT_TOLERANCE = 0.004


def detect_sr_levels(
    df: pd.DataFrame,
    window: int = 5,
    max_levels: int = 8,
    cluster_pct: float = 0.005,
    tolerance: float | None = None,
    min_touches: int = 2,
) -> list[dict]:
    """
    Find S/R levels using swing high/low pivots with cluster merging.

    Args:
        df: OHLCV DataFrame
        window: lookback/forward bars for pivot detection
        max_levels: maximum number of S/R levels to return
        cluster_pct: cluster merge tolerance as % of price (legacy, use tolerance instead)
        tolerance: cluster merge tolerance as % of price (overrides cluster_pct if provided)
        min_touches: minimum touches for a level to be included (filters weak levels)

    Returns list of dicts sorted by strength DESC:
    [{"price": float, "type": "support"|"resistance", "touches": int, "strength": float}]
    Empty list if df has < 15 bars.
    """
    # Use tolerance if provided (config key), else fall back to cluster_pct
    if tolerance is not None:
        effective_tolerance = tolerance
    else:
        _log.warning(
            "detect_sr_levels: tolerance not passed from config — "
            "falling back to cluster_pct=%.4f (hardcoded default). "
            "Set indicators[].params.tolerance in YAML to override.",
            cluster_pct,
        )
        effective_tolerance = cluster_pct

    if df.empty or len(df) < 15:
        return []

    pivots: list[dict] = []
    w = max(1, window)

    for i in range(w, len(df) - w):
        hi_window = df["high"].iloc[i - w: i + w + 1]
        lo_window = df["low"].iloc[i - w: i + w + 1]

        if df["high"].iloc[i] == hi_window.max():
            pivots.append({"price": float(df["high"].iloc[i]), "type": "resistance"})
        if df["low"].iloc[i] == lo_window.min():
            pivots.append({"price": float(df["low"].iloc[i]), "type": "support"})

    if not pivots:
        return []

    clusters: list[dict] = []
    for pivot in pivots:
        merged = False
        for cluster in clusters:
            if abs(pivot["price"] - cluster["price"]) / (cluster["price"] or 1) < effective_tolerance:
                cluster["price"] = (cluster["price"] + pivot["price"]) / 2
                cluster["touches"] += 1
                merged = True
                break
        if not merged:
            clusters.append({**pivot, "touches": 1, "strength": 0.0})

    # Filter out levels with too few touches
    clusters = [c for c in clusters if c["touches"] >= min_touches]

    if not clusters:
        return []

    max_touches = max((c["touches"] for c in clusters), default=1)
    for c in clusters:
        c["strength"] = round(c["touches"] / max_touches, 2)

    return sorted(clusters, key=lambda x: -x["strength"])[:max_levels]


def nearest_levels(price: float, sr_levels: list[dict]) -> dict:
    """
    Find the nearest support below and resistance above a given price.

    Returns:
        {"support": float|None, "resistance": float|None,
         "support_dist_pct": float|None, "resistance_dist_pct": float|None}
    """
    prices = [lvl["price"] for lvl in sr_levels]
    supports = [p for p in prices if p < price]
    resistances = [p for p in prices if p >= price]

    sup = max(supports) if supports else None
    res = min(resistances) if resistances else None

    return {
        "support": sup,
        "resistance": res,
        "support_dist_pct": round((price - sup) / price * 100, 2) if sup else None,
        "resistance_dist_pct": round((res - price) / price * 100, 2) if res else None,
    }


def detect_trendlines(
    df: pd.DataFrame,
    n_swing: int = 5,
    max_lines: int = 4,
    now_time_sec: float = None,
) -> list:
    """
    Detect ascending support trendlines and descending resistance trendlines
    via swing-pivot pair validation.

    Returns list of {type, p1_time, p2_time, p1_price, p2_price, touches,
                     anchor1, anchor2, at_risk}.
    """
    if len(df) < n_swing * 2 + 10:
        return []

    now_sec = now_time_sec if now_time_sec is not None else time.time()
    highs = df["high"].values.astype(float)
    lows = df["low"].values.astype(float)
    times_sec = (
        df["timestamp"].values.astype(float) / 1000.0
        if "timestamp" in df.columns
        else None
    )
    n = len(df)

    if times_sec is None:
        return []

    # Use fixed tolerance (DEFAULT_TOLERANCE = 0.4%). ATR-adaptive tolerance
    # requires pandas_ta which is version-sensitive; fixed tolerance is
    # sufficient for Phase B and avoids the dependency.
    tol = DEFAULT_TOLERANCE

    pivot_h = [
        i for i in range(n_swing, n - n_swing)
        if highs[i] >= highs[i - n_swing: i + n_swing + 1].max()
    ]
    pivot_l = [
        i for i in range(n_swing, n - n_swing)
        if lows[i] <= lows[i - n_swing: i + n_swing + 1].min()
    ]

    def _score(pivots, arr, ltype, must_be_above):
        candidates = []
        for a in range(len(pivots)):
            for b in range(a + 1, len(pivots)):
                i1, i2 = pivots[a], pivots[b]
                p1, p2 = arr[i1], arr[i2]
                if must_be_above and p2 <= p1:
                    continue
                if not must_be_above and p2 >= p1:
                    continue

                ci_slope = (p2 - p1) / (i2 - i1)
                valid = True
                for k in range(i1, i2 + 1):
                    lv = p1 + ci_slope * (k - i1)
                    if must_be_above and arr[k] < lv * (1 - tol):
                        valid = False
                        break
                    if not must_be_above and arr[k] > lv * (1 + tol):
                        valid = False
                        break
                if not valid:
                    continue

                touches = 2
                at_risk = False
                at_risk_threshold = tol * 0.30
                for k in range(i2 + 1, n):
                    lv = p1 + ci_slope * (k - i1)
                    dist = abs(arr[k] - lv) / max(lv, 1e-9)
                    if dist < tol:
                        touches += 1
                    elif dist < (tol + at_risk_threshold):
                        at_risk = True

                t1, t2 = times_sec[i1], times_sec[i2]
                if t2 <= t1:
                    continue
                rt_slope = (p2 - p1) / (t2 - t1)
                end_price = p1 + rt_slope * (now_sec - t1)
                if end_price <= 0:
                    continue

                candidates.append({
                    "type": ltype,
                    "p1_time": int(t1),
                    "p2_time": int(now_sec),
                    "p1_price": round(p1, 8),
                    "p2_price": round(end_price, 8),
                    "touches": touches,
                    "anchor1": round(p1, 8),
                    "anchor2": round(p2, 8),
                    "at_risk": at_risk,
                })

        candidates.sort(key=lambda x: (-x["touches"], -x["p1_time"]))
        seen: list = []
        unique: list = []
        for c in candidates:
            sig = (round(c["anchor1"], 2), round(c["anchor2"], 2))
            if sig not in seen:
                seen.append(sig)
                unique.append(c)
            if len(unique) >= max(1, max_lines // 2):
                break
        return unique

    up = _score(pivot_l, lows, "uptrend", True)
    down = _score(pivot_h, highs, "downtrend", False)
    return up + down


# Fibonacci levels matching TradingView settings
FIB_LEVELS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.66, 0.786, 1.0,
              1.618, 2.618, 3.618, 4.236]

FIB_LABELS = {
    0.0: "0%", 0.236: "23.6%", 0.382: "38.2%", 0.5: "50%",
    0.618: "61.8%", 0.66: "66% OTE", 0.786: "78.6%", 1.0: "100%",
    1.618: "161.8%", 2.618: "261.8%", 3.618: "361.8%", 4.236: "423.6%",
}

FIB_COLORS = {0.66: "#ef5350"}


def detect_fibonacci(df: pd.DataFrame, n_swing: int = 10) -> dict | None:
    """
    Auto-detect the most recent significant swing high and low, then compute
    standard Fibonacci retracement levels between them.

    Returns {swing_high, swing_low, direction, levels: [{ratio, price, label}]}
    or None if insufficient data.
    """
    if df is None or len(df) < n_swing * 2 + 3:
        return None

    highs = df["high"].values.astype(float)
    lows = df["low"].values.astype(float)
    n = len(df)

    pivot_highs = [
        i for i in range(n_swing, n - n_swing)
        if highs[i] == highs[i - n_swing: i + n_swing + 1].max()
    ]
    pivot_lows = [
        i for i in range(n_swing, n - n_swing)
        if lows[i] == lows[i - n_swing: i + n_swing + 1].min()
    ]

    if not pivot_highs or not pivot_lows:
        return None

    last_high_idx = pivot_highs[-1]
    last_low_idx = pivot_lows[-1]
    swing_high = highs[last_high_idx]
    swing_low = lows[last_low_idx]

    if swing_high <= swing_low:
        return None

    direction = "up" if last_low_idx > last_high_idx else "down"

    levels = []
    rng = swing_high - swing_low
    for ratio in FIB_LEVELS:
        if direction == "up":
            price = swing_low + ratio * rng
        else:
            price = swing_high - ratio * rng
        entry: dict = {
            "ratio": ratio,
            "price": round(float(price), 8),
            "label": FIB_LABELS[ratio],
            "extension": ratio > 1.0,
        }
        if ratio in FIB_COLORS:
            entry["color"] = FIB_COLORS[ratio]
        levels.append(entry)

    return {
        "swing_high": round(float(swing_high), 8),
        "swing_low": round(float(swing_low), 8),
        "direction": direction,
        "levels": levels,
    }