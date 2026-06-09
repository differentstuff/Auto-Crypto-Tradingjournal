#!/usr/bin/env python3
"""
scripts/time_travel.py -- Fast-forward daemon: replay scoring logic on historical data.

Replays the daemon's indicator computation + confluence scoring on historical
OHLCV data, simulates entries at multiple thresholds, walks forward to find
exits, and writes results to trade_learning — exactly as if the daemon had
run live during that period.

Purpose:
  Populate trade_learning with trades the daemon WOULD have taken (and missed)
  at various entry_threshold values. This gives Karpathy/Hyperopt the data
  they need to discover optimal thresholds — killing Karpathy v1's
  "blocked but shouldn't be" blind spot.

  80:20 approach: backtest fills trade_learning fast (80%), live trading
  finetunes (20%). Backtest trades have no slippage/latency — that's
  acceptable because the learning engine re-scores from signals_at_entry_json,
  not from raw P&L.

Usage:
  python scripts/time_travel.py --start 2025-01-01 --symbols BTCUSDT ETHUSDT
  python scripts/time_travel.py --start 2025-06-01 --end 2025-12-01 --thresholds 3,4,5,6.5
  python scripts/time_travel.py --start 2025-01-01 --strategy momentum_rising

Architecture:
  Reuses daemon components directly:
    - indicators/registry.py → compute_indicator()
    - enzymes/score_confluence.py → scoring functions (_rsi_weight, _macd_weight, etc.)
    - enzymes/record_trade_outcome.py → signal extractors (for signals_at_entry_json)
    - core/exchange.py → fetch_ohlcv()
    - core/config_loader.py → ConfigLoader
    - core/database.py → trade_learning writes

  Does NOT build a full Substrate per bar (too heavy). Instead, extracts
  the scoring logic into a standalone function that mirrors ScoreConfluence
  exactly — same weights, same formula constants, same alignment checks.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time as _time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

# ── Project path setup ──────────────────────────────────────────────────────
import os
from dotenv import load_dotenv

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

from core.config_loader import ConfigLoader
from core.database import db_conn, init_db
from core.exchange import Exchange
from indicators.registry import compute_indicator

_log = logging.getLogger("time_travel")

# ── Signal extractors (from record_trade_outcome.py) ────────────────────────
# These produce the same signals_at_entry_json format as live trading,
# ensuring Karpathy/Hyperopt can re-score consistently.


def _extract_rsi_signal(rsi: dict, rsi_high: float, rsi_low: float) -> dict:
    value = rsi.get("value", 50)
    level = rsi.get("level", "neutral")
    if value > rsi_high:
        signal = "bullish"
    elif value < rsi_low:
        signal = "bearish"
    else:
        signal = "neutral"
    return {"signal": signal, "value": value, "level": level}


def _extract_macd_signal(macd: dict) -> dict:
    bias = macd.get("bias", "")
    histogram_growing = macd.get("histogram_growing", False)
    crossover = macd.get("crossover", False)
    crossunder = macd.get("crossunder", False)
    if "bullish" in bias:
        signal = "bullish"
    elif "bearish" in bias:
        signal = "bearish"
    else:
        signal = "neutral"
    return {
        "signal": signal,
        "bias": bias,
        "histogram_growing": histogram_growing,
        "crossover": crossover,
        "crossunder": crossunder,
    }


def _extract_ema_signal(ema: dict) -> dict:
    alignment = ema.get("alignment", "")
    stack = ema.get("stack", "")
    if "bullish" in alignment and "bullish" in stack:
        signal = "bullish"
    elif "bearish" in alignment and "bearish" in stack:
        signal = "bearish"
    elif "bullish" in alignment or "bullish" in stack:
        signal = "bullish"
    elif "bearish" in alignment or "bearish" in stack:
        signal = "bearish"
    else:
        signal = "neutral"
    return {"signal": signal, "alignment": alignment, "stack": stack}


def _extract_adx_signal(adx: dict) -> dict:
    direction = adx.get("direction", "")
    value = adx.get("value", 0)
    trend_strength = adx.get("trend_strength", "weak")
    if "bullish" in direction:
        signal = "bullish"
    elif "bearish" in direction:
        signal = "bearish"
    else:
        signal = "neutral"
    return {"signal": signal, "value": value, "trend_strength": trend_strength}


def _extract_wavetrend_signal(wt: dict) -> dict:
    if not wt or not isinstance(wt, dict):
        return {"signal": "neutral"}
    wt_signal = wt.get("signal")
    if wt_signal == "gold_buy":
        signal = "bullish"
    elif wt_signal == "buy":
        signal = "bullish"
    elif wt_signal == "sell":
        signal = "bearish"
    else:
        wt1 = wt.get("wt1", 0)
        if wt1 > 0:
            signal = "bullish"
        elif wt1 < 0:
            signal = "bearish"
        else:
            signal = "neutral"
    return {
        "signal": signal,
        "wt1": wt.get("wt1"),
        "wt2": wt.get("wt2"),
        "cross": wt.get("cross"),
        "zone": wt.get("zone"),
    }


def _extract_volume_signal(vol: dict) -> dict:
    if not vol or not isinstance(vol, dict):
        return {"signal": "neutral"}
    ratio = vol.get("ratio", 1.0)
    if ratio > 1.5:
        signal = "bullish"  # high volume = confirms direction
    elif ratio < 0.7:
        signal = "bearish"  # low volume = weakens direction
    else:
        signal = "neutral"
    return {"signal": signal, "ratio": ratio}


def _extract_cvd_signal(cvd: dict) -> dict:
    if not cvd or not isinstance(cvd, dict):
        return {"signal": "neutral"}
    trend = cvd.get("trend", "flat")
    if trend == "rising":
        return {"signal": "bullish", "trend": trend}
    elif trend == "falling":
        return {"signal": "bearish", "trend": trend}
    return {"signal": "neutral", "trend": trend}


def _extract_order_flow_signal(of: dict) -> dict:
    if not of or not isinstance(of, dict):
        return {"signal": "neutral"}
    sig = of.get("signal", "neutral")
    if sig == "buying_pressure":
        return {"signal": "bullish"}
    elif sig == "selling_pressure":
        return {"signal": "bearish"}
    if of.get("divergence"):
        return {"signal": "bearish"}
    return {"signal": "neutral"}


def build_signals_at_entry(
    tf_indicators: dict,
    rsi_high: float,
    rsi_low: float,
) -> dict:
    """Build signals_at_entry_json dict from computed indicators.

    Mirrors record_trade_outcome.py's extraction logic exactly.
    Only includes indicators that have data in tf_indicators.
    """
    signals = {}

    if "rsi" in tf_indicators and isinstance(tf_indicators["rsi"], dict):
        signals["rsi"] = _extract_rsi_signal(tf_indicators["rsi"], rsi_high, rsi_low)

    if "macd" in tf_indicators and isinstance(tf_indicators["macd"], dict):
        signals["macd"] = _extract_macd_signal(tf_indicators["macd"])

    if "ema_stack" in tf_indicators and isinstance(tf_indicators["ema_stack"], dict):
        signals["ema_stack"] = _extract_ema_signal(tf_indicators["ema_stack"])

    if "adx" in tf_indicators and isinstance(tf_indicators["adx"], dict):
        signals["adx"] = _extract_adx_signal(tf_indicators["adx"])

    if "wavetrend" in tf_indicators and isinstance(tf_indicators["wavetrend"], dict):
        signals["wavetrend"] = _extract_wavetrend_signal(tf_indicators["wavetrend"])

    if "volume" in tf_indicators and isinstance(tf_indicators["volume"], dict):
        signals["volume"] = _extract_volume_signal(tf_indicators["volume"])

    if "cvd" in tf_indicators and isinstance(tf_indicators["cvd"], dict):
        signals["cvd"] = _extract_cvd_signal(tf_indicators["cvd"])

    if "order_flow" in tf_indicators and isinstance(tf_indicators["order_flow"], dict):
        signals["order_flow"] = _extract_order_flow_signal(tf_indicators["order_flow"])

    return signals


# ── Confluence scoring (mirrors ScoreConfluence exactly) ────────────────────
# These functions are extracted from enzymes/score_confluence.py.
# They are pure functions of indicator data + weights + formula constants.

def _rsi_weight(rsi_val: float, rsi_high: float, rsi_low: float, formula: dict) -> float:
    midpoint = formula["rsi_midpoint"]
    scale = formula["rsi_scale"]
    if rsi_val > rsi_high:
        return min((rsi_val - midpoint) / scale, 1.0)
    if rsi_val < rsi_low:
        return max((rsi_val - midpoint) / scale, -1.0)
    return 0.0


def _macd_weight(macd: dict, formula: dict) -> float:
    aligned_growing = formula["macd_aligned_growing"]
    aligned_fading = formula["macd_aligned_fading"]
    trend = macd.get("bias", "")
    hist_dir = "growing" if macd.get("histogram_growing", False) else "shrinking"
    if trend == "bullish":
        return aligned_growing if hist_dir == "growing" else aligned_fading
    if trend == "bearish":
        return -aligned_growing if hist_dir == "growing" else -aligned_fading
    return 0.0


def _ema_weight(ema: dict, formula: dict) -> float:
    full = formula["ema_full_alignment"]
    partial = formula["ema_partial_alignment"]
    al = ema.get("alignment", "")
    sk = ema.get("stack", "")
    if "bullish" in al and "bullish" in sk:
        return full
    if "bearish" in al and "bearish" in sk:
        return -full
    if "bullish" in sk or "bullish" in al:
        return partial
    if "bearish" in sk or "bearish" in al:
        return -partial
    return 0.0


def _adx_weight(adx: dict, formula: dict) -> float:
    adx_scale = formula["adx_scale"]
    direction = adx.get("direction", "")
    adx_val = adx.get("value", 0)
    strength = min(adx_val / adx_scale, 1.0)
    if "bullish" in direction:
        return strength
    if "bearish" in direction:
        return -strength
    return 0.0


def _wavetrend_weight(wt: dict, formula: dict) -> float:
    if not wt:
        return 0.0
    gold_signal = formula["wavetrend_gold_signal"]
    wt_signal = formula["wavetrend_signal"]
    wt1_scale = formula["wavetrend_wt1_scale"]
    no_signal_cap = formula["wavetrend_no_signal_cap"]
    signal = wt.get("signal")
    if signal == "gold_buy":
        return gold_signal
    if signal == "buy":
        return wt_signal
    if signal == "sell":
        return -wt_signal
    wt1 = wt.get("wt1", 0.0)
    return max(-no_signal_cap, min(no_signal_cap, wt1 / wt1_scale))


def _volume_weight(inds: dict, directional_score: float,
                   vol_high_ratio: float, vol_low_ratio: float,
                   formula: dict) -> float:
    ratio = inds.get("volume", {}).get("ratio", 1.0)
    sign = 1 if directional_score > 0 else (-1 if directional_score < 0 else 0)
    if ratio > vol_high_ratio:
        return formula["volume_confirm"] * sign
    if ratio < vol_low_ratio:
        return formula["volume_weaken"] * sign
    return 0.0


def _cvd_weight(cvd: dict, formula: dict) -> float:
    trend = cvd.get("trend", "flat")
    cvd_trend = formula["cvd_trend"]
    return cvd_trend if trend == "rising" else (-cvd_trend if trend == "falling" else 0.0)


def _order_flow_weight(of: dict | None, formula: dict) -> float:
    if not of:
        return 0.0
    pressure = formula["order_flow_pressure"]
    if of.get("divergence"):
        return -pressure
    sig = of.get("signal", "neutral")
    if sig == "buying_pressure":
        return pressure
    if sig == "selling_pressure":
        return -pressure
    return 0.0


def _mfi_weight(wt: dict, formula: dict) -> float:
    mfi = wt.get("mfi", 0.0) if wt else 0.0
    threshold = formula["mfi_threshold"]
    contribution = formula["mfi_contribution"]
    if mfi > threshold:
        return contribution
    if mfi < -threshold:
        return -contribution
    return 0.0


def score_timeframe(
    tf_inds: dict,
    weight_map: dict,
    rsi_high: float,
    rsi_low: float,
    momentum_cap: float,
    momentum_dampening: float,
    modifier_weights: dict,
    formula: dict,
) -> Tuple[float, float, int]:
    """Score indicators for a single timeframe.

    Mirrors ScoreConfluence._score_timeframe() exactly.
    Returns (score, max_possible, indicators_aligned).
    """
    vol_weight = modifier_weights.get("volume", 0.15)
    cvd_weight_m = modifier_weights.get("cvd", 0.1)
    of_weight = modifier_weights.get("order_flow", 0.1)
    vol_high_ratio = modifier_weights.get("volume_high_ratio", 1.5)
    vol_low_ratio = modifier_weights.get("volume_low_ratio", 0.7)

    score = 0.0
    max_possible = 0.0
    indicators_aligned = 0

    # RSI
    if "rsi" in tf_inds and weight_map.get("rsi", 0) > 0:
        rsi_val = tf_inds["rsi"].get("value", 50)
        w = _rsi_weight(rsi_val, rsi_high, rsi_low, formula)
        cfg_weight = weight_map["rsi"]
        score += w * cfg_weight
        max_possible += 1.0 * cfg_weight
        if w > 0:
            indicators_aligned += 1
        elif w < 0:
            indicators_aligned += 1

    # MACD
    if "macd" in tf_inds and weight_map.get("macd", 0) > 0:
        w = _macd_weight(tf_inds["macd"], formula)
        cfg_weight = weight_map["macd"]
        score += w * cfg_weight
        max_possible += 1.0 * cfg_weight
        if w != 0:
            indicators_aligned += 1

    # EMA stack
    if "ema_stack" in tf_inds and weight_map.get("ema_stack", 0) > 0:
        w = _ema_weight(tf_inds["ema_stack"], formula)
        cfg_weight = weight_map["ema_stack"]
        score += w * cfg_weight
        max_possible += 1.0 * cfg_weight
        if w != 0:
            indicators_aligned += 1

    # ADX
    if "adx" in tf_inds and weight_map.get("adx", 0) > 0:
        w = _adx_weight(tf_inds["adx"], formula)
        cfg_weight = weight_map["adx"]
        score += w * cfg_weight
        max_possible += 1.0 * cfg_weight
        if w != 0:
            indicators_aligned += 1

    # WaveTrend (optional)
    if "wavetrend" in tf_inds and weight_map.get("wavetrend", 0) > 0:
        wt_w = _wavetrend_weight(tf_inds["wavetrend"], formula)
        mfi_w = _mfi_weight(tf_inds["wavetrend"], formula)
        cfg_weight = weight_map.get("wavetrend", 0.15)
        oscillator = max(-1.0, min(1.0, wt_w + mfi_w))
        score += oscillator * cfg_weight
        max_possible += 1.0 * cfg_weight
        if wt_w != 0:
            indicators_aligned += 1

    # Volume (confirms direction)
    if "volume" in tf_inds:
        vol_w = _volume_weight(tf_inds, score, vol_high_ratio, vol_low_ratio, formula)
        score += vol_w * vol_weight
        max_possible += 0.5 * vol_weight

    # CVD (optional)
    if "cvd" in tf_inds:
        cvd_w = _cvd_weight(tf_inds["cvd"], formula)
        score += cvd_w * cvd_weight_m
        max_possible += 0.4 * cvd_weight_m

    # Order flow (optional)
    if "order_flow" in tf_inds:
        of_data = tf_inds.get("order_flow")
        of_w = _order_flow_weight(of_data, formula)
        score += of_w * of_weight
        max_possible += 0.15 * of_weight

    # Cap correlated momentum group (RSI + MACD)
    momentum_raw = 0.0
    if "rsi" in tf_inds and weight_map.get("rsi", 0) > 0:
        momentum_raw += _rsi_weight(tf_inds["rsi"].get("value", 50), rsi_high, rsi_low, formula)
    if "macd" in tf_inds and weight_map.get("macd", 0) > 0:
        momentum_raw += _macd_weight(tf_inds["macd"], formula)
    if abs(momentum_raw) > momentum_cap:
        excess = abs(momentum_raw) - momentum_cap
        score -= (excess * momentum_dampening) * (1 if momentum_raw > 0 else -1)

    return score, max_possible, indicators_aligned


def compute_confluence_score(
    indicators: dict,
    weight_map: dict,
    config: dict,
) -> Tuple[float, float, int, bool]:
    """Compute full confluence score for a symbol across timeframes.

    Mirrors ScoreConfluence.transform() logic:
      - Score each timeframe independently
      - Check cross-timeframe alignment
      - Neutralize if confirmation TF misaligned

    Args:
        indicators: {tf: {indicator_name: result_dict, ...}, ...}
        weight_map: {indicator_name: weight, ...}
        config: full strategy config dict

    Returns:
        (total_score, max_possible, indicators_aligned, confirmation_misaligned)
    """
    scoring = config.get("scoring", {})
    formula = scoring.get("formula", {})
    rsi_high = scoring.get("rsi_signal_high", 55)
    rsi_low = scoring.get("rsi_signal_low", 45)
    momentum_cap = scoring.get("momentum_cap", 1.5)
    momentum_dampening = scoring.get("momentum_dampening", 0.5)
    modifier_weights = scoring.get("modifier_weights", {})

    strategy = config.get("strategy", {})
    primary_tf = strategy.get("timeframe", "1h")
    confirmation_tf = strategy.get("confirmation_tf", "4h")

    total_score = 0.0
    total_max = 0.0
    total_aligned = 0
    tf_scores = {}

    for tf, tf_inds in indicators.items():
        if not isinstance(tf_inds, dict) or not tf_inds.get("ok"):
            continue

        s, m, a = score_timeframe(
            tf_inds, weight_map, rsi_high, rsi_low,
            momentum_cap, momentum_dampening, modifier_weights, formula,
        )
        tf_scores[tf] = s
        total_score += s
        total_max += m
        total_aligned += a

    # Cross-timeframe alignment check
    confirmation_misaligned = False
    if confirmation_tf and primary_tf and confirmation_tf != primary_tf:
        primary_score = tf_scores.get(primary_tf, 0)
        confirm_score = tf_scores.get(confirmation_tf, 0)

        def _direction(score: float) -> str:
            if score > 0:
                return "bullish"
            elif score < 0:
                return "bearish"
            return "neutral"

        primary_dir = _direction(primary_score)
        confirm_dir = _direction(confirm_score)

        if primary_dir != "neutral" and confirm_dir != "neutral" and primary_dir != confirm_dir:
            confirmation_misaligned = True
            total_score = 0.0
            total_max = 0.0

    return total_score, total_max, total_aligned, confirmation_misaligned


# ── Exit simulation ─────────────────────────────────────────────────────────


def simulate_exit(
    df: pd.DataFrame,
    entry_idx: int,
    entry_price: float,
    direction: str,
    atr_value: float,
    config: dict,
) -> Optional[dict]:
    """Walk forward from entry to determine exit.

    Uses ATR-based SL/TP from config exit_rules.
    Returns dict with exit info or None if no exit found within data range.
    """
    exit_rules = config.get("exit_rules", {})
    hard_stop = exit_rules.get("hard_stop", {})
    trailing_stop = exit_rules.get("trailing_stop", {})

    atr_mult = hard_stop.get("width_atr_multiplier", 1.5)
    rr_minimum = config.get("scoring", {}).get("rr_minimum", 2.0)

    is_long = direction.lower() in ("long", "buy")

    # SL distance in price
    sl_distance = atr_value * atr_mult
    # TP distance = SL * RR ratio
    tp_distance = sl_distance * rr_minimum

    if is_long:
        sl_price = entry_price - sl_distance
        tp_price = entry_price + tp_distance
    else:
        sl_price = entry_price + sl_distance
        tp_price = entry_price - tp_distance

    # Trailing stop parameters
    trail_enabled = trailing_stop.get("enabled", True)
    trail_activation_pct = trailing_stop.get("activation_profit_pct", 0.5) / 100
    trail_atr_mult = trailing_stop.get("trail_atr_multiplier", 1.0)
    breakeven_at_activation = trailing_stop.get("breakeven_at_activation", True)

    # Walk forward
    highest_favorable = entry_price  # for longs: highest price reached
    lowest_adverse = entry_price     # for longs: lowest price reached
    trail_activated = False
    trail_price = sl_price  # current trailing stop level

    max_bars = len(df) - entry_idx - 1
    # Cap walk-forward at 200 bars (reasonable holding period)
    max_bars = min(max_bars, 200)

    for i in range(1, max_bars + 1):
        bar_idx = entry_idx + i
        bar = df.iloc[bar_idx]

        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar["close"])

        if is_long:
            highest_favorable = max(highest_favorable, high)

            # Check trailing stop activation
            if trail_enabled and not trail_activated:
                if high >= entry_price * (1 + trail_activation_pct):
                    trail_activated = True
                    trail_price = max(sl_price, entry_price if breakeven_at_activation else sl_price)

            # Update trailing stop
            if trail_activated:
                new_trail = high - atr_value * trail_atr_mult
                trail_price = max(trail_price, new_trail)

            # Check exits: SL first, then trailing, then TP
            if low <= sl_price:
                exit_price = sl_price
                exit_reason = "hard_stop"
                pnl_pct = ((exit_price - entry_price) / entry_price) * 100
                return {
                    "exit_bar": bar_idx,
                    "exit_price": exit_price,
                    "exit_reason": exit_reason,
                    "pnl_pct": round(pnl_pct, 3),
                    "duration_bars": i,
                    "sl_hit": 1,
                    "trailing_stop_hit": 0,
                    "mfe_pct": round(((highest_favorable - entry_price) / entry_price) * 100, 3),
                    "mae_pct": round(((low - entry_price) / entry_price) * 100, 3) if low < entry_price else 0.0,
                }

            if trail_activated and low <= trail_price:
                exit_price = trail_price
                exit_reason = "trailing_stop"
                pnl_pct = ((exit_price - entry_price) / entry_price) * 100
                return {
                    "exit_bar": bar_idx,
                    "exit_price": exit_price,
                    "exit_reason": exit_reason,
                    "pnl_pct": round(pnl_pct, 3),
                    "duration_bars": i,
                    "sl_hit": 0,
                    "trailing_stop_hit": 1,
                    "mfe_pct": round(((highest_favorable - entry_price) / entry_price) * 100, 3),
                    "mae_pct": 0.0,
                }

            if high >= tp_price:
                exit_price = tp_price
                exit_reason = "take_profit"
                pnl_pct = ((exit_price - entry_price) / entry_price) * 100
                return {
                    "exit_bar": bar_idx,
                    "exit_price": exit_price,
                    "exit_reason": exit_reason,
                    "pnl_pct": round(pnl_pct, 3),
                    "duration_bars": i,
                    "sl_hit": 0,
                    "trailing_stop_hit": 0,
                    "mfe_pct": round(((high - entry_price) / entry_price) * 100, 3),
                    "mae_pct": 0.0,
                }

        else:  # Short
            lowest_adverse = min(lowest_adverse, low)

            # Check trailing stop activation
            if trail_enabled and not trail_activated:
                if low <= entry_price * (1 - trail_activation_pct):
                    trail_activated = True
                    trail_price = min(sl_price, entry_price if breakeven_at_activation else sl_price)

            # Update trailing stop
            if trail_activated:
                new_trail = low + atr_value * trail_atr_mult
                trail_price = min(trail_price, new_trail)

            # Check exits
            if high >= sl_price:
                exit_price = sl_price
                exit_reason = "hard_stop"
                pnl_pct = ((entry_price - exit_price) / entry_price) * 100
                return {
                    "exit_bar": bar_idx,
                    "exit_price": exit_price,
                    "exit_reason": exit_reason,
                    "pnl_pct": round(pnl_pct, 3),
                    "duration_bars": i,
                    "sl_hit": 1,
                    "trailing_stop_hit": 0,
                    "mfe_pct": round(((entry_price - lowest_adverse) / entry_price) * 100, 3),
                    "mae_pct": round(((high - entry_price) / entry_price) * 100, 3) if high > entry_price else 0.0,
                }

            if trail_activated and high >= trail_price:
                exit_price = trail_price
                exit_reason = "trailing_stop"
                pnl_pct = ((entry_price - exit_price) / entry_price) * 100
                return {
                    "exit_bar": bar_idx,
                    "exit_price": exit_price,
                    "exit_reason": exit_reason,
                    "pnl_pct": round(pnl_pct, 3),
                    "duration_bars": i,
                    "sl_hit": 0,
                    "trailing_stop_hit": 1,
                    "mfe_pct": round(((entry_price - lowest_adverse) / entry_price) * 100, 3),
                    "mae_pct": 0.0,
                }

            if low <= tp_price:
                exit_price = tp_price
                exit_reason = "take_profit"
                pnl_pct = ((entry_price - exit_price) / entry_price) * 100
                return {
                    "exit_bar": bar_idx,
                    "exit_price": exit_price,
                    "exit_reason": exit_reason,
                    "pnl_pct": round(pnl_pct, 3),
                    "duration_bars": i,
                    "sl_hit": 0,
                    "trailing_stop_hit": 0,
                    "mfe_pct": round(((entry_price - low) / entry_price) * 100, 3),
                    "mae_pct": 0.0,
                }

    # No exit found within data range — trade still open
    return None


# ── Trade deduplication ─────────────────────────────────────────────────────


class TradeCooldown:
    """Prevent re-entering the same signal at the same threshold.

    After entering at threshold T for symbol S with direction D:
    - Don't re-enter until the confluence score drops below T
    - Or the direction reverses
    - Or a configurable number of bars pass (cooldown_bars)

    This prevents entering the same signal 50 times on consecutive bars.
    """

    def __init__(self, cooldown_bars: int = 3):
        self._cooldown_bars = cooldown_bars
        # {symbol: {threshold: {direction, bar_idx}}}
        self._active: Dict[str, Dict[float, dict]] = {}

    def can_enter(self, symbol: str, threshold: float, direction: str, bar_idx: int,
                  current_score: float) -> bool:
        """Check if we can enter at this threshold for this symbol."""
        sym = self._active.get(symbol, {})
        entry = sym.get(threshold)
        if entry is None:
            return True

        # Direction reversed → allow
        if entry["direction"] != direction:
            return True

        # Score dropped below threshold → allow (signal faded and came back)
        if abs(current_score) < threshold:
            return True

        # Cooldown expired → allow
        if bar_idx - entry["bar_idx"] >= self._cooldown_bars:
            return True

        return False

    def record_entry(self, symbol: str, threshold: float, direction: str, bar_idx: int):
        """Record that we entered at this threshold."""
        if symbol not in self._active:
            self._active[symbol] = {}
        self._active[symbol][threshold] = {
            "direction": direction,
            "bar_idx": bar_idx,
        }


# ── Main time-travel loop ──────────────────────────────────────────────────


def time_travel(
    symbols: List[str],
    start_date: str,
    end_date: str,
    thresholds: List[float],
    strategy_name: str = "momentum_rising",
    cooldown_bars: int = 3,
    batch_size: int = 100,
    dry_run: bool = False,
) -> dict:
    """Run the time-travel backtest.

    Args:
        symbols: List of symbols to backtest (e.g., ["BTCUSDT", "ETHUSDT"])
        start_date: ISO date string (e.g., "2025-01-01")
        end_date: ISO date string (e.g., "2025-12-01") or "now"
        thresholds: Entry thresholds to sweep (e.g., [3.0, 4.0, 5.0, 6.5])
        strategy_name: Strategy config to use
        cooldown_bars: Bars to wait before re-entering same signal
        batch_size: Number of bars to fetch per API call
        dry_run: If True, don't write to DB

    Returns:
        Summary dict with trade counts and stats.
    """
    # Initialize
    init_db()
    config_loader = ConfigLoader(strategy_name=strategy_name)
    config = config_loader.config
    exchange = Exchange(config_loader)

    strategy_cfg = config.get("strategy", {})
    strategy_uid = strategy_cfg.get("uid", "legacy")
    primary_tf = strategy_cfg.get("timeframe", "1h")
    confirmation_tf = strategy_cfg.get("confirmation_tf", "4h")
    scoring = config.get("scoring", {})
    rsi_high = scoring.get("rsi_signal_high", 55)
    rsi_low = scoring.get("rsi_signal_low", 45)

    # Build weight map from config (same as ScoreConfluence)
    indicator_configs = config.get("indicators", [])
    weight_map = {}
    compute_configs = []
    for ind_cfg in indicator_configs:
        name = ind_cfg.get("name", "")
        weight = ind_cfg.get("weight", 0)
        weight_map[name] = weight
        # Compute indicators with weight > 0 (scoring) + infrastructure (atr, sr_levels, momentum_quality)
        if weight > 0 or name in ("atr", "sr_levels", "momentum_quality"):
            compute_configs.append(ind_cfg)

    # Try to apply learning-adjusted weights (same as live daemon)
    try:
        from learning.weight_adjuster import compute_adjusted_weights
        adjusted = compute_adjusted_weights(
            weight_map, strategy_name,
            strategy_uid=strategy_uid,
            min_trades=config.get("learning", {}).get("min_trades_before_adjusting", 30),
            adjustment_boost=config.get("learning", {}).get("adjustment_boost", 1.2),
            adjustment_review_reduce=config.get("learning", {}).get("adjustment_review_reduce", 0.9),
        )
        if adjusted != weight_map:
            _log.info("Using learning-adjusted weights (changed: %s)",
                      [k for k in adjusted if adjusted.get(k) != weight_map.get(k)])
            weight_map = adjusted
    except Exception as e:
        _log.info("No learning-adjusted weights available, using config defaults: %s", e)

    # Parse dates
    start_dt = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
    if end_date.lower() == "now":
        end_dt = datetime.now(timezone.utc)
    else:
        end_dt = datetime.fromisoformat(end_date).replace(tzinfo=timezone.utc)

    _log.info("=" * 70)
    _log.info("TIME TRAVEL — Fast-forward daemon")
    _log.info("  Strategy: %s (uid: %s)", strategy_name, strategy_uid)
    _log.info("  Symbols: %s", symbols)
    _log.info("  Period: %s → %s", start_date, end_date)
    _log.info("  Thresholds: %s", thresholds)
    _log.info("  Primary TF: %s, Confirmation TF: %s", primary_tf, confirmation_tf)
    _log.info("  Weights: %s", {k: round(v, 3) for k, v in weight_map.items() if v != 0})
    _log.info("  Dry run: %s", dry_run)
    _log.info("=" * 70)

    total_trades = 0
    total_wins = 0
    total_losses = 0
    trades_by_threshold = {t: 0 for t in thresholds}
    trades_by_symbol = {s: 0 for s in symbols}

    for symbol in symbols:
        _log.info("-" * 50)
        _log.info("Processing %s", symbol)

        cooldown = TradeCooldown(cooldown_bars=cooldown_bars)

        # Fetch OHLCV for primary TF
        _log.info("  Fetching %s OHLCV (%s)...", symbol, primary_tf)
        df_primary = _fetch_historical_ohlcv(exchange, symbol, primary_tf, start_dt, end_dt, batch_size)
        if df_primary is None or df_primary.empty:
            _log.warning("  No data for %s %s, skipping", symbol, primary_tf)
            continue

        # Fetch OHLCV for confirmation TF
        df_confirm = None
        if confirmation_tf and confirmation_tf != primary_tf:
            _log.info("  Fetching %s OHLCV (%s)...", symbol, confirmation_tf)
            df_confirm = _fetch_historical_ohlcv(exchange, symbol, confirmation_tf, start_dt, end_dt, batch_size)

        _log.info("  Primary bars: %d, Confirmation bars: %d",
                  len(df_primary), len(df_confirm) if df_confirm is not None else 0)

        # Minimum bars needed for indicator computation
        min_bars = 30  # Most indicators need at least 30 bars

        symbol_trades = 0

        for bar_idx in range(min_bars, len(df_primary)):
            # Slice data up to current bar (simulates "what the daemon saw at this point")
            df_slice = df_primary.iloc[:bar_idx + 1]
            bar = df_primary.iloc[bar_idx]
            bar_time = bar.name if hasattr(bar, 'name') else df_primary.index[bar_idx]

            # Compute indicators for primary TF
            primary_indicators = {"ok": True, "candles_used": len(df_slice)}
            for ind_cfg in compute_configs:
                ind_name = ind_cfg.get("name", "")
                ind_params = ind_cfg.get("params", {})
                try:
                    result = compute_indicator(ind_name, df_slice, **ind_params)
                    if result is not None:
                        primary_indicators[ind_name] = result
                except Exception as e:
                    _log.debug("  Indicator %s failed at bar %d: %s", ind_name, bar_idx, e)

            # Compute indicators for confirmation TF
            confirm_indicators = {}
            if df_confirm is not None and len(df_confirm) > min_bars:
                # Find the last confirmation bar that closed before this primary bar
                confirm_idx = _find_confirmation_bar(df_confirm, bar_time, bar_idx)
                if confirm_idx is not None and confirm_idx >= min_bars:
                    df_confirm_slice = df_confirm.iloc[:confirm_idx + 1]
                    confirm_indicators = {"ok": True, "candles_used": len(df_confirm_slice)}
                    for ind_cfg in compute_configs:
                        ind_name = ind_cfg.get("name", "")
                        ind_params = ind_cfg.get("params", {})
                        try:
                            result = compute_indicator(ind_name, df_confirm_slice, **ind_params)
                            if result is not None:
                                confirm_indicators[ind_name] = result
                        except Exception:
                            pass

            # Build indicator dict for scoring
            indicators = {primary_tf: primary_indicators}
            if confirm_indicators.get("ok"):
                indicators[confirmation_tf] = confirm_indicators

            # Compute confluence score
            total_score, total_max, indicators_aligned, confirmation_misaligned = \
                compute_confluence_score(indicators, weight_map, config)

            if total_max == 0 or confirmation_misaligned:
                continue

            # Determine direction from score
            if total_score > 0:
                direction = "Long"
            elif total_score < 0:
                direction = "Short"
            else:
                continue  # Neutral — no entry

            # Check each threshold
            for threshold in thresholds:
                if abs(total_score) < threshold:
                    continue  # Below this threshold

                # Check cooldown
                if not cooldown.can_enter(symbol, threshold, direction, bar_idx, total_score):
                    continue

                # ENTRY — simulate the trade
                entry_price = float(bar["close"])
                atr_result = primary_indicators.get("atr")
                atr_value = atr_result.get("value", 0) if isinstance(atr_result, dict) else 0

                # Fallback: compute ATR from the slice if not in indicators
                if atr_value == 0:
                    try:
                        atr_result = compute_indicator("atr", df_slice, period=14)
                        atr_value = atr_result.get("value", 0) if isinstance(atr_result, dict) else 0
                    except Exception:
                        pass

                if atr_value == 0:
                    _log.debug("  No ATR at bar %d, skipping entry", bar_idx)
                    continue

                # Simulate exit
                exit_info = simulate_exit(
                    df_primary, bar_idx, entry_price, direction, atr_value, config
                )

                if exit_info is None:
                    # Trade still open at end of data — skip (incomplete)
                    continue

                # Build signals_at_entry_json
                signals = build_signals_at_entry(primary_indicators, rsi_high, rsi_low)

                # Determine outcome
                pnl = exit_info["pnl_pct"]
                if pnl > 0.01:
                    outcome = "win"
                elif pnl < -0.01:
                    outcome = "loss"
                else:
                    outcome = "breakeven"

                # Calculate timestamps
                entry_time = _bar_to_iso(bar_time)
                exit_bar_idx = exit_info["exit_bar"]
                exit_bar = df_primary.iloc[exit_bar_idx]
                exit_time = _bar_to_iso(exit_bar.name if hasattr(exit_bar, 'name') else df_primary.index[exit_bar_idx])
                duration_minutes = exit_info["duration_bars"] * _tf_to_minutes(primary_tf)

                # Write to trade_learning
                if not dry_run:
                    _write_trade(
                        symbol=symbol,
                        direction=direction,
                        strategy_name=strategy_name,
                        strategy_uid=strategy_uid,
                        entry_time=entry_time,
                        exit_time=exit_time,
                        outcome=outcome,
                        pnl_pct=pnl,
                        duration_minutes=duration_minutes,
                        confluence_score=round(total_score, 2),
                        max_score=round(total_max, 2),
                        signals_at_entry=signals,
                        indicators_aligned=indicators_aligned,
                        entry_price=entry_price,
                        exit_price=exit_info["exit_price"],
                        exit_reason=exit_info["exit_reason"],
                        sl_hit=exit_info["sl_hit"],
                        trailing_stop_hit=exit_info["trailing_stop_hit"],
                        mfe_pct=exit_info.get("mfe_pct", 0),
                        mae_pct=exit_info.get("mae_pct", 0),
                        threshold_used=threshold,
                    )

                # Record entry for cooldown
                cooldown.record_entry(symbol, threshold, direction, bar_idx)

                # Stats
                total_trades += 1
                if outcome == "win":
                    total_wins += 1
                elif outcome == "loss":
                    total_losses += 1
                trades_by_threshold[threshold] = trades_by_threshold.get(threshold, 0) + 1
                trades_by_symbol[symbol] = trades_by_symbol.get(symbol, 0) + 1
                symbol_trades += 1

        _log.info("  %s: %d trades generated", symbol, symbol_trades)

    # Summary
    win_rate = (total_wins / total_trades * 100) if total_trades > 0 else 0
    summary = {
        "total_trades": total_trades,
        "wins": total_wins,
        "losses": total_losses,
        "win_rate_pct": round(win_rate, 1),
        "by_threshold": trades_by_threshold,
        "by_symbol": trades_by_symbol,
    }

    _log.info("=" * 70)
    _log.info("TIME TRAVEL COMPLETE")
    _log.info("  Total trades: %d", total_trades)
    _log.info("  Wins: %d, Losses: %d, Win rate: %.1f%%", total_wins, total_losses, win_rate)
    _log.info("  By threshold: %s", trades_by_threshold)
    _log.info("  By symbol: %s", trades_by_symbol)
    if dry_run:
        _log.info("  (DRY RUN — no trades written to DB)")
    _log.info("=" * 70)

    return summary


# ── Helpers ─────────────────────────────────────────────────────────────────


def _fetch_historical_ohlcv(
    exchange: Exchange,
    symbol: str,
    timeframe: str,
    start_dt: datetime,
    end_dt: datetime,
    batch_size: int = 500,
) -> Optional[pd.DataFrame]:
    """Fetch historical OHLCV data, paginating if needed.

    CCXT's fetch_ohlcv supports 'since' parameter but may not return
    all data in one call for large date ranges. This method paginates
    by fetching batch_size bars at a time, advancing the 'since' parameter.
    """
    all_bars = []
    since_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    ccxt_symbol = symbol.replace("USDT", "/USDT:USDT")

    while since_ms < end_ms:
        try:
            exchange_obj = exchange._get_data_exchange()
            bars = exchange_obj.fetch_ohlcv(
                ccxt_symbol,
                timeframe=timeframe,
                since=since_ms,
                limit=batch_size,
            )
        except Exception as e:
            _log.error("Failed to fetch %s %s since %s: %s", symbol, timeframe, since_ms, e)
            break

        if not bars:
            break

        all_bars.extend(bars)

        # Advance since to the last bar's timestamp + 1
        last_ts = bars[-1][0]
        if last_ts <= since_ms:
            break  # No progress — stop
        since_ms = last_ts + 1

        if last_ts >= end_ms:
            break

        # Rate limit
        _time.sleep(0.2)

    if not all_bars:
        return None

    # Convert to DataFrame
    df = pd.DataFrame(all_bars, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp")

    # Filter to requested date range
    df = df[df.index >= start_dt]
    df = df[df.index <= end_dt]

    # Deduplicate (in case of overlapping fetches)
    df = df[~df.index.duplicated(keep="first")]
    df = df.sort_index()

    return df


def _find_confirmation_bar(
    df_confirm: pd.DataFrame,
    primary_bar_time,
    primary_bar_idx: int,
) -> Optional[int]:
    """Find the last closed confirmation bar before a primary bar.

    For each 1h bar, find the most recent 4h bar that had already closed.
    """
    if df_confirm is None or df_confirm.empty:
        return None

    # Find the last confirmation bar with timestamp <= primary_bar_time
    mask = df_confirm.index <= primary_bar_time
    if not mask.any():
        return None

    # Return the index (iloc position) of the last matching bar
    last_matching = df_confirm.index[mask][-1]
    return df_confirm.index.get_loc(last_matching)


def _tf_to_minutes(timeframe: str) -> int:
    """Convert timeframe string to minutes."""
    tf = timeframe.strip().upper()
    if tf.endswith("H"):
        return int(tf[:-1]) * 60
    if tf.endswith("M"):
        return int(tf[:-1])
    if tf.endswith("D"):
        return int(tf[:-1]) * 1440
    return 60


def _bar_to_iso(bar_time) -> str:
    """Convert a bar timestamp to ISO format string."""
    if hasattr(bar_time, 'isoformat'):
        return bar_time.isoformat()
    return str(bar_time)


def _write_trade(
    symbol: str,
    direction: str,
    strategy_name: str,
    strategy_uid: str,
    entry_time: str,
    exit_time: str,
    outcome: str,
    pnl_pct: float,
    duration_minutes: int,
    confluence_score: float,
    max_score: float,
    signals_at_entry: dict,
    indicators_aligned: int,
    entry_price: float,
    exit_price: float,
    exit_reason: str,
    sl_hit: int,
    trailing_stop_hit: int,
    mfe_pct: float,
    mae_pct: float,
    threshold_used: float,
) -> None:
    """Write a simulated trade to the trade_learning table.

    Same schema as the live daemon uses, so Karpathy/Hyperopt can read it.
    """
    try:
        with db_conn() as conn:
            conn.execute(
                """INSERT INTO trade_learning
                   (symbol, direction, strategy_name, strategy_uid,
                    entry_time, exit_time, outcome, pnl_pct,
                    duration_minutes, confluence_score_at_entry,
                    signals_at_entry_json, indicators_aligned,
                    entry_price, exit_price, exit_reason,
                    sl_hit, trailing_stop_hit,
                    max_favorable_excursion_pct, max_adverse_excursion_pct,
                    effective_score)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    symbol,
                    direction,
                    strategy_name,
                    strategy_uid,
                    entry_time,
                    exit_time,
                    outcome,
                    pnl_pct,
                    duration_minutes,
                    confluence_score,
                    json.dumps(signals_at_entry),
                    indicators_aligned,
                    entry_price,
                    exit_price,
                    exit_reason,
                    sl_hit,
                    trailing_stop_hit,
                    mfe_pct,
                    mae_pct,
                    confluence_score,  # effective_score = raw score (no penalties in backtest)
                ),
            )
    except Exception as e:
        _log.error("Failed to write trade to DB: %s", e, exc_info=True)


# ── CLI ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Time-travel daemon: replay scoring on historical data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Backtest BTCUSDT from Jan 2025 with default thresholds
  python scripts/time_travel.py --start 2025-01-01 --symbols BTCUSDT

  # Backtest multiple symbols with custom thresholds
  python scripts/time_travel.py --start 2025-06-01 --end 2025-12-01 \\
      --symbols BTCUSDT ETHUSDT SOLUSDT --thresholds 3,4,5,6.5

  # Dry run (don't write to DB)
  python scripts/time_travel.py --start 2025-01-01 --symbols BTCUSDT --dry-run

  # Use config symbols (from strategy YAML)
  python scripts/time_travel.py --start 2025-01-01
        """,
    )
    parser.add_argument(
        "--start", required=True,
        help="Start date (ISO format, e.g., 2025-01-01)",
    )
    parser.add_argument(
        "--end", default="now",
        help="End date (ISO format or 'now', default: now)",
    )
    parser.add_argument(
        "--symbols", nargs="+", default=None,
        help="Symbols to backtest (e.g., BTCUSDT ETHUSDT). Default: from strategy config.",
    )
    parser.add_argument(
        "--thresholds", default="3.0,4.0,5.0,6.5",
        help="Comma-separated entry thresholds to sweep (default: 3.0,4.0,5.0,6.5)",
    )
    parser.add_argument(
        "--strategy", default="momentum_rising",
        help="Strategy name (default: momentum_rising)",
    )
    parser.add_argument(
        "--cooldown", type=int, default=3,
        help="Bars to wait before re-entering same signal (default: 3)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Don't write trades to DB (preview only)",
    )
    parser.add_argument(
        "--batch-size", type=int, default=500,
        help="OHLCV bars per API call (default: 500)",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level (default: INFO)",
    )

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Parse thresholds
    thresholds = [float(t.strip()) for t in args.thresholds.split(",")]

    # Resolve symbols
    symbols = args.symbols
    if symbols is None:
        # Load from strategy config
        config_loader = ConfigLoader(strategy_name=args.strategy)
        symbols_cfg = config_loader.config.get("symbols", {})
        always_watch = symbols_cfg.get("always_watch", [])
        if always_watch:
            symbols = always_watch
            _log.info("Using symbols from strategy config: %s", symbols)
        else:
            _log.error("No symbols specified and strategy config has no always_watch list")
            sys.exit(1)

    # Run time travel
    summary = time_travel(
        symbols=symbols,
        start_date=args.start,
        end_date=args.end,
        thresholds=thresholds,
        strategy_name=args.strategy,
        cooldown_bars=args.cooldown,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
    )

    # Exit code
    sys.exit(0 if summary["total_trades"] > 0 else 1)


if __name__ == "__main__":
    main()
