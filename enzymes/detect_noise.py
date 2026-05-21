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


def _is_in_kill_zone(utc_hour: int = None) -> bool:
    """
    Return True if the given UTC hour falls within an institutional kill zone.
    London: 07:00-09:59 UTC  |  NY AM: 12:00-14:59 UTC
    Outside these windows, liquidity is lower and noise is higher.
    """
    h = utc_hour if utc_hour is not None else datetime.now(timezone.utc).hour
    return (7 <= h < 10) or (12 <= h < 15)


def _check_conflicting_signals(indicators: dict, weight_map: dict) -> list[str]:
    """
    Check if scoring indicators are giving conflicting directional signals.

    Returns list of conflict descriptions (empty if no conflicts).
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
                if rsi_val > 55:
                    bullish_count += 1
                elif rsi_val < 45:
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
    if bullish_count >= 2 and bearish_count >= 2:
        conflicts.append(
            f"Conflicting signals: {bullish_count} bullish vs {bearish_count} bearish"
        )

    return conflicts


def _check_volume(indicators: dict) -> list[str]:
    """
    Check volume conditions for noise.

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
                if ratio < 0.5:
                    reasons.append(f"{symbol}: very low volume ratio ({ratio:.1f})")
                elif ratio < 0.7:
                    reasons.append(f"{symbol}: low volume ratio ({ratio:.1f})")

    return reasons


def _check_adx_extremes(indicators: dict, weight_map: dict) -> list[str]:
    """
    Check for ADX extremes: no trend (ADX < 15) or overextended (ADX > 40).

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
                if adx_val < 15:
                    reasons.append(f"{symbol}: no trend (ADX={adx_val:.0f})")
                elif adx_val > 40:
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
        candidates = substrate.analysis.get("candidates", [])
        # Activate if candidates exist and noise hasn't been evaluated yet
        # noise_flag defaults to False, so we check if noise_reason is empty
        # (which means we haven't run yet this cycle)
        noise_reason = substrate.analysis.get("noise_reason", "")
        return bool(candidates) and not noise_reason

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

        # 1. Kill zone check
        utc_hour = datetime.now(timezone.utc).hour
        if not _is_in_kill_zone(utc_hour):
            noise_reasons.append("Outside kill zone (low liquidity window)")

        # 2. Conflicting signals
        conflicts = _check_conflicting_signals(indicators, weight_map)
        noise_reasons.extend(conflicts)

        # 3. Volume check
        volume_issues = _check_volume(indicators)
        noise_reasons.extend(volume_issues)

        # 4. ADX extremes
        adx_issues = _check_adx_extremes(indicators, weight_map)
        noise_reasons.extend(adx_issues)

        # Set noise flag based on severity
        # Only flag as noisy if we have 2+ reasons (avoid false positives)
        is_noisy = len(noise_reasons) >= 2
        # Kill zone alone is enough to flag
        if not is_noisy and any("kill zone" in r.lower() for r in noise_reasons):
            # Only flag kill zone as noise if combined with at least one other issue
            # or if the config says to always flag outside kill zone
            kill_zone_strict = substrate.cfg("noise.kill_zone_blocks", False)
            if kill_zone_strict:
                is_noisy = True

        substrate.analysis["noise_flag"] = is_noisy
        substrate.analysis["noise_reason"] = "; ".join(noise_reasons) if noise_reasons else ""

        if is_noisy:
            self._log.info("Noise detected: %s", substrate.analysis["noise_reason"])
        else:
            self._log.info("No significant noise detected")

        return substrate

    def flux_score(self, substrate: Substrate) -> float:
        """High flux when indicators are available but noise not yet checked."""
        if self.can_activate(substrate):
            return 1.2
        return 0.0