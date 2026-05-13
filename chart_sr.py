"""
chart_sr.py — Support/resistance level detection via swing pivots.

Pure functions: accept a DataFrame, return structured dicts.
No API calls, no caching, no side effects.

Public API:
  detect_sr_levels          — simple pivot clustering (used by tests)
  detect_support_resistance — full implementation with ATR tolerance + recency weighting
                              (used by chart_context.compute_indicators)
  nearest_levels            — nearest support/resistance above/below a price
"""
from __future__ import annotations
import math as _math
import pandas as pd
import pandas_ta as ta

from constants import PRICE_TOLERANCE


def detect_sr_levels(
    df: pd.DataFrame,
    window: int = 5,
    max_levels: int = 8,
    cluster_pct: float = 0.005,
) -> list[dict]:
    """
    Find S/R levels using swing high/low pivots with cluster merging.

    Returns list of dicts sorted by strength DESC:
    [{"price": float, "type": "support"|"resistance", "touches": int, "strength": float}]
    Empty list if df has < 15 bars.
    """
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
            if abs(pivot["price"] - cluster["price"]) / (cluster["price"] or 1) < cluster_pct:
                cluster["price"] = (cluster["price"] + pivot["price"]) / 2
                cluster["touches"] += 1
                merged = True
                break
        if not merged:
            clusters.append({**pivot, "touches": 1, "strength": 0.0})

    max_touches = max((c["touches"] for c in clusters), default=1)
    for c in clusters:
        c["strength"] = round(c["touches"] / max_touches, 2)

    return sorted(clusters, key=lambda x: -x["strength"])[:max_levels]


def detect_support_resistance(
    df: pd.DataFrame,
    n_swing: int = 5,
    tolerance_pct: float = PRICE_TOLERANCE,
    max_levels: int = 8,
) -> list[dict]:
    """
    Full swing-pivot S/R detection with ATR-relative tolerance and recency weighting.

    Features over detect_sr_levels:
    - ATR-relative clustering so cheap alts and BTC both cluster with sensible precision
    - Exponential recency decay: recent touches outweigh stale ones
    - Filters supports below current price and resistances above current price
    - Returns {price, type, strength, touches, w_touches} sorted ascending by price

    n_swing:       pivot detection window (bars each side)
    tolerance_pct: base clustering tolerance; overridden upward by 0.3×ATR%
    max_levels:    cap on returned levels (split evenly between supports/resistances)
    """
    if len(df) < n_swing * 2 + 3:
        return []

    highs = df["high"].values.astype(float)
    lows  = df["low"].values.astype(float)
    close = float(df["close"].iloc[-1])

    # ATR-relative tolerance: use max(tolerance_pct, 0.3×ATR%) so level detection
    # scales with volatility rather than using a fixed 0.4% across all assets.
    try:
        _atr_s   = ta.atr(df["high"], df["low"], df["close"], length=14)
        _atr_v   = float(_atr_s.iloc[-1]) if _atr_s is not None and not _atr_s.empty else 0
        _atr_pct = _atr_v / close if close > 0 else 0
        tolerance_pct = max(tolerance_pct, 0.3 * _atr_pct)
    except Exception:
        pass

    n_bars = len(highs)
    DECAY  = 0.02   # half-life ≈ 35 bars ago carries ~50% weight of the last bar

    pivot_highs, pivot_lows = [], []
    for i in range(n_swing, n_bars - n_swing):
        window_h = highs[i - n_swing: i + n_swing + 1]
        window_l = lows[i  - n_swing: i + n_swing + 1]
        if highs[i] >= window_h.max():
            pivot_highs.append((highs[i], i))
        if lows[i] <= window_l.min():
            pivot_lows.append((lows[i], i))

    def _cluster(pivots, level_type):
        if not pivots:
            return []
        levels = []
        for p, idx in sorted(pivots, key=lambda x: x[0]):
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
            price    = lvl["_sum"] / lvl["_n"]
            weighted = 0.0
            raw      = 0
            for k in range(n_bars):
                bars_ago = n_bars - 1 - k
                if (abs(highs[k] - price) / price < tolerance_pct or
                        abs(lows[k] - price) / price < tolerance_pct):
                    weighted += _math.exp(-DECAY * bars_ago)
                    raw      += 1
            result.append({
                "price":     round(price, 8),
                "type":      lvl["type"],
                "strength":  lvl["_n"],
                "touches":   raw,
                "w_touches": round(weighted, 2),
            })
        return result

    supports    = _cluster(pivot_lows,  "support")
    resistances = _cluster(pivot_highs, "resistance")

    supports    = [l for l in supports    if l["price"] < close * 1.02]
    resistances = [l for l in resistances if l["price"] > close * 0.98]

    supports    = sorted(supports,    key=lambda x: (-x["w_touches"], -x["strength"]))[:max_levels // 2]
    resistances = sorted(resistances, key=lambda x: (-x["w_touches"], -x["strength"]))[:max_levels // 2]

    return sorted(supports + resistances, key=lambda x: x["price"])


def nearest_levels(
    price: float,
    sr_levels: list[dict],
) -> dict:
    """
    Find the nearest support below and resistance above a given price.

    Returns:
        {"support": float|None, "resistance": float|None,
         "support_dist_pct": float|None, "resistance_dist_pct": float|None}
    """
    prices      = [l["price"] for l in sr_levels]
    supports    = [p for p in prices if p < price]
    resistances = [p for p in prices if p >= price]

    sup = max(supports)    if supports    else None
    res = min(resistances) if resistances else None

    return {
        "support":             sup,
        "resistance":          res,
        "support_dist_pct":    round((price - sup)  / price * 100, 2) if sup else None,
        "resistance_dist_pct": round((res  - price) / price * 100, 2) if res else None,
    }
