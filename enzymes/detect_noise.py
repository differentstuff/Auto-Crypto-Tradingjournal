"""
enzymes/detect_noise.py -- Oxidoreductase enzyme: noise detection.

Checks market conditions for noise and computes a soft penalty ratio:
  - Liquidity filter (outside high-liquidity windows = lower liquidity)
  - Conflicting signals across indicators (relative ratio)
  - Low volume / spread conditions
  - Extreme ADX (no trend or overextended)

Writes:
  analysis.noise_flag (bool)           -- kept for logging/backward compat
  analysis.noise_reason (str)          -- human-readable reasons
  analysis.noise_penalty_ratio (float) -- soft penalty 0.0-1.0 (replaces hard gate)

Enzyme class: Oxidoreductase
Activates when: market.indicators not empty AND analysis.noise_evaluated is False

Port of: scanner_criteria.py (liquidity filter, criteria)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from core.enzyme import Enzyme, EnzymeClass, register_enzyme
from core.substrate import Substrate

_log = logging.getLogger(__name__)


def _is_in_liquidity_window(utc_hour: int = None, liquidity_hours: list | None = None) -> bool:
    """
    Return True if the given UTC hour falls within a high-liquidity window.
    Default: London 07:00-09:59 UTC | NY AM 12:00-14:59 UTC.
    Configurable via noise.liquidity_filter_hours in YAML (list of [start, end] pairs).
    Outside these windows, liquidity is lower and noise penalty increases.
    """
    h = utc_hour if utc_hour is not None else datetime.now(timezone.utc).hour
    if liquidity_hours is None:
        liquidity_hours = [[7, 10], [12, 15]]
    return any(start <= h < end for start, end in liquidity_hours)


def _check_conflicting_signals(
    indicators: dict, weight_map: dict, conflict_max_ratio: float,
    rsi_high: float, rsi_low: float,
) -> tuple[list[str], float]:
    """
    Check if scoring indicators are giving conflicting directional signals.

    conflict_max_ratio: max ratio of min(bullish,bearish)/total to tolerate.
        Read from noise.conflict_max_ratio in config.
        If the conflict ratio exceeds this, a conflict is flagged.
    rsi_high/low: RSI thresholds for directional signal. Read from scoring.rsi_signal_high/low.
    All parameters are required — they must come from substrate.cfg().

    Returns: (list of conflict reason strings, conflict_ratio 0.0-1.0)
    """
    conflicts = []
    bullish_count = 0
    bearish_count = 0

    for symbol, sym_data in indicators.items():
        for tf, tf_inds in sym_data.items():
            if not isinstance(tf_inds, dict) or not tf_inds.get("ok"):
                continue

            # RSI
            rsi = tf_inds.get("rsi", {})
            if isinstance(rsi, dict) and weight_map.get("rsi", 0) > 0:
                rsi_val = rsi.get("value", 50)
                if rsi_val > rsi_high:
                    bullish_count += 1
                elif rsi_val < rsi_low:
                    bearish_count += 1

            # MACD
            macd = tf_inds.get("macd", {})
            if isinstance(macd, dict) and weight_map.get("macd", 0) > 0:
                bias = macd.get("bias", "")
                if "bullish" in bias:
                    bullish_count += 1
                elif "bearish" in bias:
                    bearish_count += 1

            # EMA stack
            ema = tf_inds.get("ema_stack", {})
            if isinstance(ema, dict) and weight_map.get("ema_stack", 0) > 0:
                alignment = ema.get("alignment", "")
                if "bullish" in alignment:
                    bullish_count += 1
                elif "bearish" in alignment:
                    bearish_count += 1

    # Compute conflict ratio: how much the minority direction divides the total
    total = bullish_count + bearish_count
    conflict_ratio = 0.0
    if total > 0:
        conflict_ratio = min(bullish_count, bearish_count) / total

    # If conflict ratio exceeds threshold, flag as conflict
    if conflict_ratio > conflict_max_ratio and total > 0:
        conflicts.append(
            f"Conflicting signals: {bullish_count} bullish vs {bearish_count} bearish (ratio={conflict_ratio:.2f})"
        )

    return conflicts, conflict_ratio


def _check_volume(indicators: dict, vol_low: float, vol_very_low: float) -> list[str]:
    """
    Check volume conditions for noise.

    vol_low:       volume ratio below this → "low volume" warning.
                   Read from noise.volume_low_ratio in config.
    vol_very_low:  volume ratio below this → "very low volume" warning.
                   Read from noise.volume_very_low_ratio in config.

    Returns list of noise reasons (empty if volume is healthy).
    """
    reasons = []

    for symbol, sym_data in indicators.items():
        for tf, tf_inds in sym_data.items():
            if not isinstance(tf_inds, dict) or not tf_inds.get("ok"):
                continue

            vol = tf_inds.get("volume", {})
            if isinstance(vol, dict):
                ratio = vol.get("ratio", 1.0)
                if ratio < vol_very_low:
                    reasons.append(f"{symbol}: very low volume ratio ({ratio:.1f})")
                elif ratio < vol_low:
                    reasons.append(f"{symbol}: low volume ratio ({ratio:.1f})")

    return reasons


def _check_adx_extremes(
    indicators: dict, weight_map: dict, adx_no_trend: float, adx_overextended: float,
) -> list[str]:
    """
    Check for ADX extremes: no trend or overextended.

    adx_no_trend:      ADX below this → no trend (noise). Read from noise.adx_no_trend.
    adx_overextended:   ADX above this → overextended (noise). Read from noise.adx_overextended.

    Returns list of noise reasons.
    """
    reasons = []

    for symbol, sym_data in indicators.items():
        for tf, tf_inds in sym_data.items():
            if not isinstance(tf_inds, dict) or not tf_inds.get("ok"):
                continue

            adx = tf_inds.get("adx", {})
            if isinstance(adx, dict) and weight_map.get("adx", 0) > 0:
                adx_val = adx.get("value", 0)
                if adx_val < adx_no_trend:
                    reasons.append(f"{symbol}: no trend (ADX={adx_val:.0f})")
                elif adx_val > adx_overextended:
                    reasons.append(f"{symbol}: overextended (ADX={adx_val:.0f})")

    return reasons


@register_enzyme
class DetectNoise(Enzyme):
    """
    Oxidoreductase enzyme: detect noisy market conditions.

    Computes analysis.noise_penalty_ratio (0.0-1.0) based on noise severity.
    Also sets analysis.noise_flag (bool) for logging/backward compat, but
    noise_flag no longer blocks trades — the penalty ratio is used by
    ApproveTrade via substrate.compute_effective_score() instead.

    Checks:
      1. Outside liquidity window (lower liquidity = more noise)
      2. Conflicting directional signals (relative ratio)
      3. Low volume
      4. ADX extremes (no trend or overextended)
    """

    name = "DetectNoise"
    enzyme_class = EnzymeClass.OXIDOREDUCTASE
    priority = 4  # Runs after CollectOHLCV (5) but before ScoreConfluence (3)

    def requires(self) -> list[str]:
        return ["market.indicators not empty"]

    def prohibits(self) -> list[str]:
        return []

    def can_activate(self, substrate: Substrate) -> bool:
        indicators = substrate.market.get("indicators", {})
        noise_evaluated = substrate.analysis.get("noise_evaluated", False)
        # Activate when indicators exist and noise hasn't been evaluated yet.
        return bool(indicators) and not noise_evaluated

    def transform(self, substrate: Substrate) -> Substrate:
        """Evaluate noise conditions and compute noise_penalty_ratio."""
        indicators = substrate.market.get("indicators", {})
        if not indicators:
            substrate.analysis["noise_flag"] = False
            substrate.analysis["noise_reason"] = ""
            substrate.analysis["noise_penalty_ratio"] = 0.0
            return substrate

        # Build weight map from config
        indicator_configs = substrate.cfg("indicators", [])
        weight_map = {}
        for ind_cfg in indicator_configs:
            name = ind_cfg.get("name", "")
            weight = ind_cfg.get("weight", 0)
            weight_map[name] = weight

        noise_reasons = []

        # 1. Liquidity filter check (hours from config)
        utc_hour = datetime.now(timezone.utc).hour
        liquidity_hours = substrate.cfg("noise.liquidity_filter_hours", [[7, 10], [12, 15]])
        if not _is_in_liquidity_window(utc_hour, liquidity_hours=liquidity_hours):
            noise_reasons.append("Outside liquidity window (lower liquidity)")

        # 2. Conflicting signals (relative ratio from config)
        conflict_max_ratio = substrate.cfg("noise.conflict_max_ratio", 0.5)
        rsi_high = substrate.cfg("scoring.rsi_signal_high")
        rsi_low = substrate.cfg("scoring.rsi_signal_low")
        conflicts, conflict_ratio = _check_conflicting_signals(
            indicators, weight_map, conflict_max_ratio,
            rsi_high=rsi_high, rsi_low=rsi_low,
        )
        noise_reasons.extend(conflicts)

        # 3. Volume check (thresholds from config)
        vol_low = substrate.cfg("noise.volume_low_ratio")
        vol_very_low = substrate.cfg("noise.volume_very_low_ratio")
        volume_issues = _check_volume(indicators, vol_low, vol_very_low)
        noise_reasons.extend(volume_issues)

        # 4. ADX extremes (thresholds from config)
        adx_no_trend = substrate.cfg("noise.adx_no_trend")
        adx_overextended = substrate.cfg("noise.adx_overextended")
        adx_issues = _check_adx_extremes(indicators, weight_map, adx_no_trend, adx_overextended)
        noise_reasons.extend(adx_issues)

        # Set noise flag based on severity (threshold from config)
        # Only flag as noisy if we have enough reasons (avoid false positives)
        min_reasons = substrate.cfg("noise.noise_severity_min_reasons")
        is_noisy = len(noise_reasons) >= min_reasons

        # Compute noise_penalty_ratio: scale by number of reasons vs min_reasons
        # More reasons = higher penalty, capped at the configured max ratio
        noise_penalty_max = substrate.cfg("soft_penalties.noise_penalty_ratio", 0.3)
        if len(noise_reasons) > 0:
            # Scale: 1 reason = 50% of max, 2+ reasons = full max
            scale = min(1.0, len(noise_reasons) / max(min_reasons, 1))
            noise_penalty_ratio = round(noise_penalty_max * scale, 3)
        else:
            noise_penalty_ratio = 0.0

        substrate.analysis["noise_flag"] = is_noisy
        substrate.analysis["noise_reason"] = "; ".join(noise_reasons) if noise_reasons else ""
        substrate.analysis["noise_penalty_ratio"] = noise_penalty_ratio
        substrate.analysis["noise_evaluated"] = True

        if is_noisy:
            self._log.info(
                "Noise detected (penalty=%.2f): %s",
                noise_penalty_ratio, substrate.analysis["noise_reason"],
            )
        else:
            self._log.info("No significant noise detected (penalty=%.2f)", noise_penalty_ratio)

        return substrate

    def flux_score(self, substrate: Substrate) -> float:
        """Dynamic flux: high when noise conditions are present."""
        if not self.can_activate(substrate):
            return 0.0
        # Outside kill zone with no other noise = less urgent
        # Inside kill zone or multiple noise signals = more urgent
        indicators = substrate.market.get("indicators", {})
        if not indicators:
            return 0.5
        # Multiple symbols with potential noise = higher urgency to check
        n_symbols = len(indicators)
        if n_symbols >= 3:
            return 1.5
        return 1.2