"""
indicators/volume.py -- Volume indicator computations.

Pure functions: accept a DataFrame, return structured dicts.
No API calls, no caching, no side effects.

Port of: chart_indicators.py (volume analysis)
"""

from __future__ import annotations

import pandas as pd


def compute_volume(df: pd.DataFrame) -> dict | None:
    """Volume vs 20-bar average. Returns {"current","avg_20","ratio","signal"} or None."""
    if len(df["volume"]) < 20:
        return None
    vol_now = float(df["volume"].iloc[-1])
    vol_avg = float(df["volume"].iloc[-20:].mean())
    ratio = round(vol_now / vol_avg, 2) if vol_avg else 1.0
    return {
        "current": round(vol_now, 2),
        "avg_20": round(vol_avg, 2),
        "ratio": ratio,
        "signal": (
            f"high volume ({ratio}x avg)" if ratio > 1.5 else
            f"low volume ({ratio}x avg)" if ratio < 0.7 else
            f"average volume ({ratio}x avg)"
        ),
    }