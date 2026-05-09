"""
chart_sr.py — Support/resistance level detection via swing pivots.

Pure functions: accept a DataFrame, return structured dicts.
No API calls, no caching, no side effects.
"""
from __future__ import annotations
import pandas as pd


def detect_sr_levels(
    df: pd.DataFrame,
    window: int = 5,
    max_levels: int = 8,
    cluster_pct: float = 0.005,
) -> list[dict]:
    """
    Find S/R levels using swing high/low pivots with cluster merging.

    Args:
        df:          OHLCV DataFrame
        window:      pivot detection window (bars on each side)
        max_levels:  cap on returned levels
        cluster_pct: merge pivots within this fraction of price

    Returns:
        List of dicts sorted by strength DESC:
        [{"price": float, "type": "support"|"resistance",
          "touches": int, "strength": float}]
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

    # Cluster nearby pivots
    clusters: list[dict] = []
    for pivot in pivots:
        merged = False
        for cluster in clusters:
            if abs(pivot["price"] - cluster["price"]) / (cluster["price"] or 1) < cluster_pct:
                # Merge: average price, keep dominant type
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
    prices = [l["price"] for l in sr_levels]
    supports    = [p for p in prices if p < price]
    resistances = [p for p in prices if p >= price]

    sup = max(supports)    if supports    else None
    res = min(resistances) if resistances else None

    return {
        "support":              sup,
        "resistance":           res,
        "support_dist_pct":     round((price - sup)  / price * 100, 2) if sup else None,
        "resistance_dist_pct":  round((res  - price) / price * 100, 2) if res else None,
    }
