"""
enzymes/score_confluence.py -- Oxidoreductase enzyme: confluence scoring.

Reads indicator data from substrate.market.indicators, computes weighted
confluence scores, and produces candidates for substrate.analysis.candidates.

Weights are config-driven (from config.indicators[*].weight), not hardcoded.

Enzyme class: Oxidoreductase
Activates when: market.indicators not empty AND analysis.candidates is empty

Rewrite of: chart_confluence.py (config-driven weights)
"""

from __future__ import annotations

import logging
from typing import Optional

from core.enzyme import Enzyme, EnzymeClass, register_enzyme
from core.substrate import Substrate

_log = logging.getLogger(__name__)


def _rsi_weight(rsi_val: float) -> float:
    """RSI contribution: ±1 at extremes, 0 at 50. Dead-band ±5 around 50."""
    if rsi_val > 55:
        return min((rsi_val - 50) / 30.0, 1.0)
    if rsi_val < 45:
        return max((rsi_val - 50) / 30.0, -1.0)
    return 0.0


def _macd_weight(macd: dict) -> float:
    """MACD contribution: full ±1 when aligned + growing, ±0.5 when aligned but fading."""
    trend = macd.get("bias", "")
    hist_dir = "growing" if macd.get("histogram_growing", False) else "shrinking"
    if trend == "bullish":
        return 1.0 if hist_dir == "growing" else 0.5
    if trend == "bearish":
        return -1.0 if hist_dir == "growing" else -0.5
    return 0.0


def _ema_weight(ema: dict) -> float:
    """EMA contribution: ±1 fully aligned stack + price, ±0.5 partial."""
    al = ema.get("alignment", "")
    sk = ema.get("stack", "")
    if "bullish" in al and "bullish" in sk:
        return 1.0
    if "bearish" in al and "bearish" in sk:
        return -1.0
    if "bullish" in sk or "bullish" in al:
        return 0.5
    if "bearish" in sk or "bearish" in al:
        return -0.5
    return 0.0


def _adx_weight(adx: dict) -> float:
    """ADX contribution: direction × trend strength (ADX value / 50, capped at 1)."""
    direction = adx.get("direction", "")
    adx_val = adx.get("value", 0)
    strength = min(adx_val / 50.0, 1.0)
    if "bullish" in direction:
        return strength
    if "bearish" in direction:
        return -strength
    return 0.0


def _wavetrend_weight(wt: dict) -> float:
    """WaveTrend contribution: crossover signals in OB/OS zones are strongest."""
    if not wt:
        return 0.0
    signal = wt.get("signal")
    if signal == "gold_buy":
        return 1.0
    if signal == "buy":
        return 0.85
    if signal == "sell":
        return -0.85
    # No fresh cross — use WT1 position scaled to ±0.5
    wt1 = wt.get("wt1", 0.0)
    return max(-0.5, min(0.5, wt1 / 60.0))


def _volume_weight(inds: dict, directional_score: float) -> float:
    """Volume confirms the dominant direction."""
    ratio = inds.get("volume", {}).get("ratio", 1.0)
    sign = 1 if directional_score > 0 else (-1 if directional_score < 0 else 0)
    if ratio > 1.5:
        return 0.5 * sign
    if ratio < 0.7:
        return -0.25 * sign
    return 0.0


def _cvd_weight(cvd: dict) -> float:
    """CVD rising = bullish signal (+0.4), falling = bearish (-0.4)."""
    trend = cvd.get("trend", "flat")
    return 0.4 if trend == "rising" else (-0.4 if trend == "falling" else 0.0)


def _order_flow_weight(of: dict | None) -> float:
    """+0.15 buying pressure, -0.15 selling pressure or divergence."""
    if not of:
        return 0.0
    if of.get("divergence"):
        return -0.15
    sig = of.get("signal", "neutral")
    if sig == "buying_pressure":
        return 0.15
    if sig == "selling_pressure":
        return -0.15
    return 0.0


def _mfi_weight(wt: dict) -> float:
    """MFI contribution from WaveTrend data."""
    mfi = wt.get("mfi", 0.0) if wt else 0.0
    if mfi > 10:
        return 0.3
    if mfi < -10:
        return -0.3
    return 0.0


@register_enzyme
class ScoreConfluence(Enzyme):
    """
    Oxidoreductase enzyme: compute confluence scores for all symbols.

    Reads indicator data from substrate.market.indicators, applies
    config-driven weights, and produces a ranked list of candidates
    in substrate.analysis.candidates.

    Each candidate: {symbol, score, max_score, label, indicators_aligned, details}
    """

    name = "ScoreConfluence"
    enzyme_class = EnzymeClass.OXIDOREDUCTASE
    priority = 3

    def requires(self) -> list[str]:
        return ["market.indicators not empty"]

    def prohibits(self) -> list[str]:
        return ["analysis.candidates not empty"]

    def can_activate(self, substrate: Substrate) -> bool:
        indicators = substrate.market.get("indicators", {})
        candidates = substrate.analysis.get("candidates", [])
        return bool(indicators) and not candidates

    def transform(self, substrate: Substrate) -> Substrate:
        """Compute confluence scores for all symbols with indicator data."""
        indicators = substrate.market.get("indicators", {})
        if not indicators:
            self._log.info("No indicator data to score")
            return substrate

        # Get config values
        confluence_min = substrate.cfg("scoring.confluence_min_signals", 3)

        # Build weight map from config
        indicator_configs = substrate.cfg("indicators", [])
        weight_map = {}
        for ind_cfg in indicator_configs:
            name = ind_cfg.get("name", "")
            weight = ind_cfg.get("weight", 0)
            weight_map[name] = weight

        candidates = []
        for symbol, sym_data in indicators.items():
            total_score = 0.0
            total_max = 0.0
            all_details = []
            indicators_aligned = 0

            for tf, tf_inds in sym_data.items():
                if not isinstance(tf_inds, dict) or not tf_inds.get("ok"):
                    continue

                tf_score, tf_max, tf_details = self._score_timeframe(
                    tf_inds, weight_map
                )
                total_score += tf_score
                total_max += tf_max
                all_details.extend(tf_details)

                # Count aligned indicators
                for name, w in weight_map.items():
                    if w > 0 and name in tf_inds:
                        ind = tf_inds[name]
                        if isinstance(ind, dict):
                            # Check if indicator has a directional signal
                            signal = ind.get("signal", ind.get("bias", ind.get("level", "")))
                            if signal and signal not in ("neutral", "mixed", ""):
                                indicators_aligned += 1

            # Compute percentage and label
            pct = total_score / total_max if total_max else 0.0
            label = self._pct_to_label(pct)

            # Only include as candidate if above minimum threshold
            if indicators_aligned >= confluence_min or abs(pct) >= 0.20:
                candidates.append({
                    "symbol": symbol,
                    "score": round(total_score, 2),
                    "max_score": round(total_max, 2),
                    "pct": round(pct, 3),
                    "label": label,
                    "indicators_aligned": indicators_aligned,
                    "details": all_details,
                })

        # Sort by absolute score descending (strongest signals first)
        candidates.sort(key=lambda c: abs(c["score"]), reverse=True)

        substrate.analysis["candidates"] = candidates
        substrate.analysis["signal_states"] = {
            c["symbol"]: c["label"] for c in candidates
        }

        self._log.info(
            "Scored confluence: %d candidates, top=%s",
            len(candidates),
            candidates[0]["symbol"] if candidates else "none",
        )

        return substrate

    def _score_timeframe(
        self, tf_inds: dict, weight_map: dict
    ) -> tuple[float, float, list[str]]:
        """
        Score indicators for a single timeframe.

        Returns (score, max_possible, details_list).
        """
        score = 0.0
        max_possible = 0.0
        details = []

        # RSI
        if "rsi" in tf_inds and weight_map.get("rsi", 0) > 0:
            rsi_val = tf_inds["rsi"].get("value", 50)
            w = _rsi_weight(rsi_val)
            cfg_weight = weight_map["rsi"]
            score += w * cfg_weight
            max_possible += 1.0 * cfg_weight
            details.append(f"RSI {rsi_val:.0f}")

        # MACD
        if "macd" in tf_inds and weight_map.get("macd", 0) > 0:
            w = _macd_weight(tf_inds["macd"])
            cfg_weight = weight_map["macd"]
            score += w * cfg_weight
            max_possible += 1.0 * cfg_weight
            details.append(f"MACD {tf_inds['macd'].get('bias', '?')}")

        # EMA stack
        if "ema_stack" in tf_inds and weight_map.get("ema_stack", 0) > 0:
            w = _ema_weight(tf_inds["ema_stack"])
            cfg_weight = weight_map["ema_stack"]
            score += w * cfg_weight
            max_possible += 1.0 * cfg_weight
            details.append(f"EMA {tf_inds['ema_stack'].get('alignment', '?')}")

        # ADX
        if "adx" in tf_inds and weight_map.get("adx", 0) > 0:
            w = _adx_weight(tf_inds["adx"])
            cfg_weight = weight_map["adx"]
            score += w * cfg_weight
            max_possible += 1.0 * cfg_weight
            details.append(f"ADX {tf_inds['adx'].get('value', 0):.0f}")

        # WaveTrend (optional)
        if "wavetrend" in tf_inds and weight_map.get("wavetrend", 0) > 0:
            wt_w = _wavetrend_weight(tf_inds["wavetrend"])
            mfi_w = _mfi_weight(tf_inds["wavetrend"])
            cfg_weight = weight_map.get("wavetrend", 0.15)
            # Cap correlated oscillator group
            oscillator = max(-1.0, min(1.0, wt_w + mfi_w))
            score += oscillator * cfg_weight
            max_possible += 1.0 * cfg_weight

        # Volume (confirms direction)
        if "volume" in tf_inds:
            vol_w = _volume_weight(tf_inds, score)
            score += vol_w * 0.15  # Volume is always a modifier
            max_possible += 0.5 * 0.15

        # CVD (optional)
        if "cvd" in tf_inds:
            cvd_w = _cvd_weight(tf_inds["cvd"])
            score += cvd_w * 0.1
            max_possible += 0.4 * 0.1

        # Order flow (optional)
        if "order_flow" in tf_inds:
            of_w = _order_flow_weight(tf_inds["order_flow"])
            score += of_w * 0.1
            max_possible += 0.15 * 0.1

        # Cap correlated momentum group (RSI + MACD)
        momentum_raw = 0.0
        if "rsi" in tf_inds and weight_map.get("rsi", 0) > 0:
            momentum_raw += _rsi_weight(tf_inds["rsi"].get("value", 50))
        if "macd" in tf_inds and weight_map.get("macd", 0) > 0:
            momentum_raw += _macd_weight(tf_inds["macd"])
        if abs(momentum_raw) > 1.5:
            excess = abs(momentum_raw) - 1.5
            # Dampen the excess
            score -= (excess * 0.5) * (1 if momentum_raw > 0 else -1)

        return score, max_possible, details

    @staticmethod
    def _pct_to_label(pct: float) -> str:
        """Convert percentage to confluence label."""
        if pct >= 0.60:
            return "Strong Bullish"
        if pct >= 0.33:
            return "Bullish"
        if pct <= -0.60:
            return "Strong Bearish"
        if pct <= -0.33:
            return "Bearish"
        return "Neutral"

    def flux_score(self, substrate: Substrate) -> float:
        """High flux when indicators are available but not yet scored."""
        if self.can_activate(substrate):
            return 1.5
        return 0.0