"""
scripts/time_travel/scoring.py -- Signal extractors + confluence scoring.

Extracted from enzymes/score_confluence.py and enzymes/record_trade_outcome.py.
These are pure functions of indicator data + weights + formula constants.
A bar that scores 6.3 in the live daemon scores 6.3 here.

Used by:
    - time_travel backtest (this package)
    - Any future scoring replay or analysis tool
"""

from __future__ import annotations

from typing import Dict, Tuple


# ── Per-indicator signal extraction ─────────────────────────────────────────
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
        signal = "bullish"
    elif ratio < 0.7:
        signal = "bearish"
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
# Pure functions of indicator data + weights + formula constants.


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
) -> Tuple[float, float]:
    """Score indicators for a single timeframe.

    Mirrors ScoreConfluence._score_timeframe() exactly.
    Returns (score, max_possible).
    """
    vol_weight = modifier_weights.get("volume", 0.15)
    cvd_weight_m = modifier_weights.get("cvd", 0.1)
    of_weight = modifier_weights.get("order_flow", 0.1)
    vol_high_ratio = modifier_weights.get("volume_high_ratio", 1.5)
    vol_low_ratio = modifier_weights.get("volume_low_ratio", 0.7)

    score = 0.0
    max_possible = 0.0

    # RSI
    if "rsi" in tf_inds and weight_map.get("rsi", 0) > 0:
        rsi_val = tf_inds["rsi"].get("value", 50)
        w = _rsi_weight(rsi_val, rsi_high, rsi_low, formula)
        cfg_weight = weight_map["rsi"]
        score += w * cfg_weight
        max_possible += 1.0 * cfg_weight

    # MACD
    if "macd" in tf_inds and weight_map.get("macd", 0) > 0:
        w = _macd_weight(tf_inds["macd"], formula)
        cfg_weight = weight_map["macd"]
        score += w * cfg_weight
        max_possible += 1.0 * cfg_weight

    # EMA stack
    if "ema_stack" in tf_inds and weight_map.get("ema_stack", 0) > 0:
        w = _ema_weight(tf_inds["ema_stack"], formula)
        cfg_weight = weight_map["ema_stack"]
        score += w * cfg_weight
        max_possible += 1.0 * cfg_weight

    # ADX
    if "adx" in tf_inds and weight_map.get("adx", 0) > 0:
        w = _adx_weight(tf_inds["adx"], formula)
        cfg_weight = weight_map["adx"]
        score += w * cfg_weight
        max_possible += 1.0 * cfg_weight

    # WaveTrend (optional)
    if "wavetrend" in tf_inds and weight_map.get("wavetrend", 0) > 0:
        wt_w = _wavetrend_weight(tf_inds["wavetrend"], formula)
        mfi_w = _mfi_weight(tf_inds["wavetrend"], formula)
        cfg_weight = weight_map.get("wavetrend", 0.15)
        oscillator = max(-1.0, min(1.0, wt_w + mfi_w))
        score += oscillator * cfg_weight
        max_possible += 1.0 * cfg_weight

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

    return score, max_possible


def compute_confluence_score(
    indicators: dict,
    weight_map: dict,
    config: dict,
) -> Tuple[float, float, int, bool]:
    """Compute full confluence score for a symbol across timeframes.

    Mirrors ScoreConfluence.transform() logic:
      - Score each timeframe independently
      - Count aligned indicators via signal-string method (same as live daemon)
      - Normalize score to 0-10 scale (same as live daemon)
      - Check cross-timeframe alignment
      - Neutralize if confirmation TF misaligned

    Args:
        indicators: {tf: {indicator_name: result_dict, ...}, ...}
        weight_map: {indicator_name: weight, ...}
        config: full strategy config dict

    Returns:
        (normalized_score, pct, indicators_aligned, confirmation_misaligned)
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
    tf_scores = {}

    for tf, tf_inds in indicators.items():
        if not isinstance(tf_inds, dict) or not tf_inds.get("ok"):
            continue

        s, m = score_timeframe(
            tf_inds, weight_map, rsi_high, rsi_low,
            momentum_cap, momentum_dampening, modifier_weights, formula,
        )
        tf_scores[tf] = s
        total_score += s
        total_max += m

    # Count aligned indicators — same as ScoreConfluence.transform()
    total_aligned = 0
    for tf, tf_inds in indicators.items():
        if not isinstance(tf_inds, dict) or not tf_inds.get("ok"):
            continue
        for name, w in weight_map.items():
            if w > 0 and name in tf_inds:
                ind = tf_inds[name]
                if isinstance(ind, dict):
                    signal = ind.get("signal", ind.get("bias", ind.get("level", "")))
                    if signal and signal not in ("neutral", "mixed", ""):
                        total_aligned += 1

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

    # Normalize to 0-10 scale
    normalized_score = (total_score / total_max * 10) if total_max else 0.0
    pct = (total_score / total_max) if total_max else 0.0

    return normalized_score, pct, total_aligned, confirmation_misaligned