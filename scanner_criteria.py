"""
scanner_criteria.py — Criteria DSL and kill-zone helpers for the setup scanner.

Provides:
- CRITERIA_DEFAULTS: the default on/off map for each scoring criterion.
- _disabled_criteria_block(): builds the prompt fragment listing disabled checks.
- _is_in_kill_zone() / _annotate_kill_zone(): ICT kill-zone time helpers.
"""

import datetime

# ── Criteria defaults ──────────────────────────────────────────────────────────
# Each key maps to a scoring check. When False the stage-2 gate skips the hard
# filter AND the prompt tells Claude to ignore that criterion.

CRITERIA_DEFAULTS: dict = {
    "rsi":        True,   # Reject overextended RSI (>78 long / <22 short)
    "macd":       True,   # MACD alignment counts as a 4H signal
    "ema_stack":  True,   # EMA stack alignment counts as a 4H signal
    "adx":        True,   # Reject ADX < 15 (flat/choppy)
    "sr_anchor":  True,   # Require ≥2 S/R levels + entry within 4×ATR
    "wavetrend":  True,   # VMC Cipher / WaveTrend signal in scoring
    "volume":     True,   # Volume confirmation in scoring
    "funding":    True,   # Funding rate penalty (-1/-2 score points)
    "fear_greed": True,   # Fear & Greed ±0.5 adjustment
    "atr_sl":     True,   # Cap score ≤ 6 when SL < 1×ATR from entry
    "rr_minimum": True,   # Cap score ≤ 6 when R:R < 2:1
}

_CRITERIA_DISABLED_LABELS: dict = {
    "rsi":        "RSI overbought/oversold — do NOT penalise or filter on RSI extremes",
    "macd":       "MACD alignment — ignore MACD direction entirely",
    "ema_stack":  "EMA stack — ignore EMA alignment entirely",
    "adx":        "ADX trend strength — do NOT require or factor ADX",
    "sr_anchor":  "S/R anchor — entry does NOT need to be near a named level; score purely on momentum/pattern",
    "wavetrend":  "WaveTrend/VMC Cipher — ignore WT signal entirely",
    "volume":     "Volume confirmation — do NOT require or reward volume",
    "funding":    "Funding rate — do NOT apply any funding rate penalties",
    "fear_greed": "Fear & Greed — do NOT apply F&G score adjustments",
    "atr_sl":     "ATR SL floor — do NOT cap score if SL is tight (inside 1×ATR)",
    "rr_minimum": "R:R minimum — do NOT cap score for low R:R; score the setup quality regardless",
}


def _disabled_criteria_block(criteria: dict) -> str:
    """Return a prompt section listing which checks Claude must skip."""
    disabled = [
        f"  - {_CRITERIA_DISABLED_LABELS[k]}"
        for k in _CRITERIA_DISABLED_LABELS
        if not criteria.get(k, True)
    ]
    if not disabled:
        return ""
    return (
        "DISABLED SCORING CRITERIA (user has turned these OFF — do NOT apply them, "
        "do NOT mention them in your rationale):\n" + "\n".join(disabled)
    )


# ── Kill zone helpers ──────────────────────────────────────────────────────────

def _is_in_kill_zone(utc_hour: int = None) -> bool:
    """
    Return True if the given UTC hour falls within an institutional kill zone.
    London: 07:00–09:59 UTC  |  NY AM: 12:00–14:59 UTC
    Pass utc_hour explicitly for testing; defaults to current UTC time.
    """
    h = utc_hour if utc_hour is not None else datetime.datetime.utcnow().hour
    return (7 <= h < 10) or (12 <= h < 15)


def _annotate_kill_zone(result: dict, utc_hour: int = None) -> dict:
    """
    Append '⚠ Outside kill zone' to the urgency field when outside institutional windows.
    No-op when inside a kill zone. Returns the result dict (mutated in place).
    """
    if _is_in_kill_zone(utc_hour):
        return result
    warning = "⚠ Outside kill zone"
    if "urgency" in result:
        existing = result["urgency"]
        result["urgency"] = (existing + " " + warning).strip() if existing else warning
    else:
        result["urgency"] = warning
    return result
