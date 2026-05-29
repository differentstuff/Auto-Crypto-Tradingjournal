"""
enzymes/detect_noise.py -- Oxidoreductase enzyme: noise detection.

Checks market conditions for noise that should suppress trading:
  - Kill zone time filter (ICT Asian session = low liquidity)
  - Conflicting signals across indicators
  - Low volume / spread conditions
  - Extreme ADX (no trend or overextended)

Writes: analysis.noise_flag (bool), analysis.noise_reason (str)

Enzyme class: Oxidoreductase
Activates when: market.indicators not empty AND analysis.noise_flag not yet set this cycle

Port of: scanner_criteria.py (kill zone, criteria)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from core.enzyme import Enzyme, EnzymeClass, register_enzyme
from core.substrate import Substrate

_log = logging.getLogger(__name__)


def _is_in_kill_zone(utc_hour: int = None, kill_zone_hours: list | None = None) -> bool:
    """
    Return True if the given UTC hour falls within an institutional kill zone.
    Default: London 07:00-09:59 UTC | NY AM 12:00-14:59 UTC.
    Configurable via noise.kill_zone_hours in YAML (list of [start, end] pairs).
    Outside these windows, liquidity is lower and noise is higher.
    """
    h = utc_hour if utc_hour is not None else datetime.now(timezone.utc).hour
    if kill_zone_hours is None:
        kill_zone_hours = [[7, 10], [12, 15]]
    return any(start <= h < end for start, end in kill_zone_hours)


def _check_conflicting_signals(
    indicators: dict, weight_map: dict, conflict_threshold: int,
    rsi_high: float, rsi_low: float,
) -> list[str]:
    """
    Check if scoring indicators are giving conflicting directional signals.

    conflict_threshold: minimum bullish AND bearish count to flag as conflict.
        Read from noise.conflict_signal_threshold in config.
    rsi_high/low: RSI thresholds for directional signal. Read from scoring.rsi_signal_high/low.
    All parameters are required — they must come from substrate.cfg().
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

    # If we have both bullish and bearish signals, that's a conflict
    if bullish_count >= conflict_threshold and bearish_count >= conflict_threshold:
        conflicts.append(
            f"Conflicting signals: {bullish_count} bullish vs {bearish_count} bearish"
        )

    return conflicts


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

    Sets analysis.noise_flag and analysis.noise_reason when conditions
    suggest avoiding trades. This feeds into ISC-005 (no trade when
    noise_flag is true).

    Checks:
      1. Outside kill zone (lower liquidity = more noise)
      2. Conflicting directional signals
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
        # This fires BEFORE ScoreConfluence so we can skip scoring when noisy.
        return bool(indicators) and not noise_evaluated

    def transform(self, substrate: Substrate) -> Substrate:
        """Evaluate noise conditions and set analysis.noise_flag."""
        indicators = substrate.market.get("indicators", {})
        if not indicators:
            substrate.analysis["noise_flag"] = False
            substrate.analysis["noise_reason"] = ""
            return substrate

        # Build weight map from config
        indicator_configs = substrate.cfg("indicators", [])
        weight_map = {}
        for ind_cfg in indicator_configs:
            name = ind_cfg.get("name", "")
            weight = ind_cfg.get("weight", 0)
            weight_map[name] = weight

        noise_reasons = []

        # 1. Kill zone check (hours from config)
        utc_hour = datetime.now(timezone.utc).hour
        kill_zone_hours = substrate.cfg("noise.kill_zone_hours")
        if not _is_in_kill_zone(utc_hour, kill_zone_hours=kill_zone_hours):
            noise_reasons.append("Outside kill zone (low liquidity window)")

        # 2. Conflicting signals (thresholds from config)
        conflict_threshold = substrate.cfg("noise.conflict_signal_threshold")
        rsi_high = substrate.cfg("scoring.rsi_signal_high")
        rsi_low = substrate.cfg("scoring.rsi_signal_low")
        conflicts = _check_conflicting_signals(
            indicators, weight_map, conflict_threshold,
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
        # Kill zone alone is enough to flag
        if not is_noisy and any("kill zone" in r.lower() for r in noise_reasons):
            # Only flag kill zone as noise if combined with at least one other issue
            # or if the config says to always flag outside kill zone
            kill_zone_strict = substrate.cfg("noise.kill_zone_blocks")
            if kill_zone_strict:
                is_noisy = True

        substrate.analysis["noise_flag"] = is_noisy
        substrate.analysis["noise_reason"] = "; ".join(noise_reasons) if noise_reasons else ""
        substrate.analysis["noise_evaluated"] = True

        if is_noisy:
            self._log.info("Noise detected: %s", substrate.analysis["noise_reason"])
        else:
            self._log.info("No significant noise detected")

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