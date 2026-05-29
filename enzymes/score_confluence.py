"""
enzymes/score_confluence.py -- Oxidoreductase enzyme: confluence scoring.

Reads indicator data from substrate.market.indicators, computes weighted
confluence scores, and produces candidates for substrate.analysis.candidates.

P1: Cross-timeframe alignment. If confirmation_tf is configured and both
timeframes have indicator data, the candidate is neutralized (score=0)
when the primary and confirmation timeframes disagree in direction.
confirmation_tf acts as a trend filter — only trade when the higher
timeframe confirms the primary timeframe's direction.

Weights are config-driven (from config.indicators[*].weight), not hardcoded.

Enzyme class: Oxidoreductase
Activates when: market.indicators not empty AND analysis.candidates is empty
"""

from __future__ import annotations

import logging
from typing import Optional

from core.enzyme import Enzyme, EnzymeClass, register_enzyme
from core.substrate import Substrate

_log = logging.getLogger(__name__)


def _rsi_weight(rsi_val: float, rsi_high: float, rsi_low: float, formula: dict) -> float:
    """RSI contribution: ±1 at extremes, 0 at midpoint. Dead-band around midpoint.
    Formula constants from config: scoring.formula.rsi_midpoint, scoring.formula.rsi_scale."""
    midpoint = formula["rsi_midpoint"]
    scale = formula["rsi_scale"]
    if rsi_val > rsi_high:
        return min((rsi_val - midpoint) / scale, 1.0)
    if rsi_val < rsi_low:
        return max((rsi_val - midpoint) / scale, -1.0)
    return 0.0


def _macd_weight(macd: dict, formula: dict) -> float:
    """MACD contribution: full ±aligned_growing when aligned + growing, ±aligned_fading when aligned but fading.
    Formula constants from config: scoring.formula.macd_aligned_growing, scoring.formula.macd_aligned_fading."""
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
    """EMA contribution: full_alignment when fully aligned, partial_alignment when partially aligned.
    Formula constants from config: scoring.formula.ema_full_alignment, scoring.formula.ema_partial_alignment."""
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
    """ADX contribution: direction × trend strength (ADX value / adx_scale, capped at 1).
    Formula constant from config: scoring.formula.adx_scale."""
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
    """WaveTrend contribution: crossover signals in OB/OS zones are strongest.
    Formula constants from config: scoring.formula.wavetrend_gold_signal,
    scoring.formula.wavetrend_signal, scoring.formula.wavetrend_wt1_scale,
    scoring.formula.wavetrend_no_signal_cap."""
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
    # No fresh cross — use WT1 position scaled to ±no_signal_cap
    wt1 = wt.get("wt1", 0.0)
    return max(-no_signal_cap, min(no_signal_cap, wt1 / wt1_scale))


def _volume_weight(inds: dict, directional_score: float,
                   vol_high_ratio: float, vol_low_ratio: float,
                   formula: dict) -> float:
    """Volume confirms or weakens the dominant direction.
    Thresholds from scoring.modifier_weights.volume_high_ratio/low_ratio.
    Contribution from scoring.formula.volume_confirm/volume_weaken."""
    ratio = inds.get("volume", {}).get("ratio", 1.0)
    sign = 1 if directional_score > 0 else (-1 if directional_score < 0 else 0)
    if ratio > vol_high_ratio:
        return formula["volume_confirm"] * sign
    if ratio < vol_low_ratio:
        return formula["volume_weaken"] * sign
    return 0.0


def _cvd_weight(cvd: dict, formula: dict) -> float:
    """CVD contribution: rising = +cvd_trend, falling = -cvd_trend.
    Formula constant from config: scoring.formula.cvd_trend."""
    trend = cvd.get("trend", "flat")
    cvd_trend = formula["cvd_trend"]
    return cvd_trend if trend == "rising" else (-cvd_trend if trend == "falling" else 0.0)


def _order_flow_weight(of: dict | None, formula: dict) -> float:
    """+order_flow_pressure for buying, -order_flow_pressure for selling or divergence.
    Formula constant from config: scoring.formula.order_flow_pressure."""
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
    """MFI contribution from WaveTrend data.
    Formula constants from config: scoring.formula.mfi_threshold, scoring.formula.mfi_contribution."""
    mfi = wt.get("mfi", 0.0) if wt else 0.0
    threshold = formula["mfi_threshold"]
    contribution = formula["mfi_contribution"]
    if mfi > threshold:
        return contribution
    if mfi < -threshold:
        return -contribution
    return 0.0


@register_enzyme
class ScoreConfluence(Enzyme):
    """
    Oxidoreductase enzyme: compute confluence scores for all symbols.

    P1: Cross-timeframe alignment. If confirmation_tf is configured and
    both timeframes have indicator data, the candidate is neutralized
    (score=0, pct=0) when the primary and confirmation timeframes
    disagree in direction. The confirmation_tf acts as a trend filter.

    Each candidate: {symbol, score, max_score, label, indicators_aligned,
                     details, confirmation_tf_misaligned}
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
        confluence_scored = substrate.analysis.get("confluence_scored", False)
        noise_flag = substrate.analysis.get("noise_flag", False)
        return bool(indicators) and not candidates and not confluence_scored and not noise_flag

    def transform(self, substrate: Substrate) -> Substrate:
        """Compute confluence scores for all symbols with indicator data."""
        indicators = substrate.market.get("indicators", {})
        if not indicators:
            self._log.info("No indicator data to score")
            return substrate

        confluence_min = substrate.cfg("scoring.confluence_min_signals")
        min_candidate_pct = substrate.cfg("scoring.min_candidate_pct")
        rsi_high = substrate.cfg("scoring.rsi_signal_high")
        rsi_low = substrate.cfg("scoring.rsi_signal_low")
        momentum_cap = substrate.cfg("scoring.momentum_cap")
        momentum_dampening = substrate.cfg("scoring.momentum_dampening")
        modifier_weights = substrate.cfg("scoring.modifier_weights")
        label_thresholds = substrate.cfg("scoring.label_thresholds")
        formula = substrate.cfg("scoring.formula")

        # Build weight map from config
        indicator_configs = substrate.cfg("indicators", [])
        weight_map = {}
        for ind_cfg in indicator_configs:
            name = ind_cfg.get("name", "")
            weight = ind_cfg.get("weight", 0)
            weight_map[name] = weight

        # Apply learning-adjusted weights
        strategy_name = substrate.strategy.get("name", "")
        strategy_uid = substrate.strategy.get("uid", "legacy")
        adjusted = substrate.learning.get("adjusted_weights", {})
        if adjusted and isinstance(adjusted, dict) and any(v != 0 for v in adjusted.values()):
            weight_map = adjusted
            self._log.debug("Using pre-computed adjusted weights from substrate.learning")
        else:
            try:
                from learning.weight_adjuster import compute_adjusted_weights
                min_trades = substrate.cfg("learning.min_trades_before_adjusting")
                adjusted = compute_adjusted_weights(
                    weight_map, strategy_name, strategy_uid=strategy_uid,
                    min_trades=min_trades,
                )
                if adjusted != weight_map:
                    changed = [k for k in adjusted if adjusted.get(k) != weight_map.get(k)]
                    self._log.info("Computed %d adjusted weights from learning engine: %s",
                                   len(changed), changed)
                    weight_map = adjusted
            except Exception as e:
                self._log.warning("Could not compute adjusted weights: %s", e)

        # P1: Read confirmation TF from strategy config
        confirmation_tf = substrate.strategy.get("confirmation_tf")
        primary_tf = substrate.strategy.get("timeframe", "")

        candidates = []
        for symbol, sym_data in indicators.items():
            total_score = 0.0
            total_max = 0.0
            all_details = []
            indicators_aligned = 0
            tf_scores = {}  # P1: per-TF scores for alignment check

            for tf, tf_inds in sym_data.items():
                if not isinstance(tf_inds, dict) or not tf_inds.get("ok"):
                    continue

                tf_score, tf_max, tf_details = self._score_timeframe(
                    tf_inds, weight_map, rsi_high, rsi_low,
                    momentum_cap, momentum_dampening, modifier_weights, formula,
                )
                tf_scores[tf] = tf_score
                total_score += tf_score
                total_max += tf_max
                all_details.extend(tf_details)

                # Count aligned indicators
                for name, w in weight_map.items():
                    if w > 0 and name in tf_inds:
                        ind = tf_inds[name]
                        if isinstance(ind, dict):
                            signal = ind.get("signal", ind.get("bias", ind.get("level", "")))
                            if signal and signal not in ("neutral", "mixed", ""):
                                indicators_aligned += 1

            # P1: Cross-timeframe alignment check
            # If confirmation_tf is configured and both TFs have data,
            # neutralize the candidate when directions disagree.
            # confirmation_tf acts as a trend filter: only trade when the
            # higher timeframe confirms the primary timeframe's direction.
            confirmation_misaligned = False
            if confirmation_tf and primary_tf and confirmation_tf != primary_tf:
                primary_score = tf_scores.get(primary_tf, 0)
                confirm_score = tf_scores.get(confirmation_tf, 0)
                primary_dir = self._direction_from_score(primary_score)
                confirm_dir = self._direction_from_score(confirm_score)

                if (primary_dir != "neutral" and confirm_dir != "neutral"
                        and primary_dir != confirm_dir):
                    total_score = 0.0
                    total_max = 0.0
                    confirmation_misaligned = True
                    self._log.info(
                        "Confirmation TF misaligned for %s: "
                        "%s=%s (%.2f), %s=%s (%.2f) — neutralized",
                        symbol, primary_tf, primary_dir, primary_score,
                        confirmation_tf, confirm_dir, confirm_score,
                    )

            # Compute percentage and label
            pct = total_score / total_max if total_max else 0.0
            label = self._pct_to_label(pct, label_thresholds)

            # Only include as candidate if above minimum threshold
            if indicators_aligned >= confluence_min or abs(pct) >= min_candidate_pct:
                candidates.append({
                    "symbol": symbol,
                    "score": round(total_score, 2),
                    "max_score": round(total_max, 2),
                    "pct": round(pct, 3),
                    "label": label,
                    "indicators_aligned": indicators_aligned,
                    "details": all_details,
                    "confirmation_tf_misaligned": confirmation_misaligned,
                })

        # Sort by absolute score descending (strongest signals first)
        candidates.sort(key=lambda c: abs(c["score"]), reverse=True)

        substrate.analysis["candidates"] = candidates
        substrate.analysis["signal_states"] = {
            c["symbol"]: c["label"] for c in candidates
        }
        substrate.analysis["confluence_scored"] = True

        self._log.info(
            "Scored confluence: %d candidates, top=%s",
            len(candidates),
            candidates[0]["symbol"] if candidates else "none",
        )

        return substrate

    def _score_timeframe(
        self, tf_inds: dict, weight_map: dict,
        rsi_high: float, rsi_low: float,
        momentum_cap: float, momentum_dampening: float,
        modifier_weights: dict, formula: dict,
    ) -> tuple[float, float, list[str]]:
        """
        Score indicators for a single timeframe.

        Returns (score, max_possible, details_list).
        """
        vol_weight = modifier_weights.get("volume", 0.15)
        cvd_weight = modifier_weights.get("cvd", 0.1)
        of_weight = modifier_weights.get("order_flow", 0.1)
        score = 0.0
        max_possible = 0.0
        details = []

        # RSI
        if "rsi" in tf_inds and weight_map.get("rsi", 0) > 0:
            rsi_val = tf_inds["rsi"].get("value", 50)
            w = _rsi_weight(rsi_val, rsi_high, rsi_low, formula)
            cfg_weight = weight_map["rsi"]
            score += w * cfg_weight
            max_possible += 1.0 * cfg_weight
            details.append(f"RSI {rsi_val:.0f}")

        # MACD
        if "macd" in tf_inds and weight_map.get("macd", 0) > 0:
            w = _macd_weight(tf_inds["macd"], formula)
            cfg_weight = weight_map["macd"]
            score += w * cfg_weight
            max_possible += 1.0 * cfg_weight
            details.append(f"MACD {tf_inds['macd'].get('bias', '?')}")

        # EMA stack
        if "ema_stack" in tf_inds and weight_map.get("ema_stack", 0) > 0:
            w = _ema_weight(tf_inds["ema_stack"], formula)
            cfg_weight = weight_map["ema_stack"]
            score += w * cfg_weight
            max_possible += 1.0 * cfg_weight
            details.append(f"EMA {tf_inds['ema_stack'].get('alignment', '?')}")

        # ADX
        if "adx" in tf_inds and weight_map.get("adx", 0) > 0:
            w = _adx_weight(tf_inds["adx"], formula)
            cfg_weight = weight_map["adx"]
            score += w * cfg_weight
            max_possible += 1.0 * cfg_weight
            details.append(f"ADX {tf_inds['adx'].get('value', 0):.0f}")

        # WaveTrend (optional)
        if "wavetrend" in tf_inds and weight_map.get("wavetrend", 0) > 0:
            wt_w = _wavetrend_weight(tf_inds["wavetrend"], formula)
            mfi_w = _mfi_weight(tf_inds["wavetrend"], formula)
            cfg_weight = weight_map.get("wavetrend", 0.15)
            # Cap correlated oscillator group
            oscillator = max(-1.0, min(1.0, wt_w + mfi_w))
            score += oscillator * cfg_weight
            max_possible += 1.0 * cfg_weight

        # Volume (confirms direction)
        if "volume" in tf_inds:
            vol_high_ratio = modifier_weights.get("volume_high_ratio", 1.5)
            vol_low_ratio = modifier_weights.get("volume_low_ratio", 0.7)
            vol_w = _volume_weight(tf_inds, score, vol_high_ratio, vol_low_ratio, formula)
            score += vol_w * vol_weight
            max_possible += 0.5 * vol_weight

        # CVD (optional)
        if "cvd" in tf_inds:
            cvd_w = _cvd_weight(tf_inds["cvd"], formula)
            score += cvd_w * cvd_weight
            max_possible += 0.4 * cvd_weight

        # Order flow (optional)
        if "order_flow" in tf_inds:
            of_w = _order_flow_weight(tf_inds["order_flow"], formula)
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
            # Dampen the excess
            score -= (excess * momentum_dampening) * (1 if momentum_raw > 0 else -1)

        return score, max_possible, details

    @staticmethod
    def _direction_from_score(score: float) -> str:
        """Determine direction from a confluence score.

        Positive score = bullish, negative = bearish, zero = neutral.
        Used for cross-timeframe alignment checks (P1).
        """
        if score > 0:
            return "bullish"
        elif score < 0:
            return "bearish"
        return "neutral"

    @staticmethod
    def _pct_to_label(pct: float, label_thresholds: dict) -> str:
        """Convert percentage to confluence label."""
        strong = label_thresholds.get("strong", 0.60)
        weak = label_thresholds.get("weak", 0.33)
        if pct >= strong:
            return "Strong Bullish"
        if pct >= weak:
            return "Bullish"
        if pct <= -strong:
            return "Strong Bearish"
        if pct <= -weak:
            return "Bearish"
        return "Neutral"

    def flux_score(self, substrate: Substrate) -> float:
        """Dynamic flux: high when hunting for entries (no positions),
        lower when positions are full."""
        if not self.can_activate(substrate):
            return 0.0
        positions = substrate.portfolio.get("open_positions", [])
        max_positions = substrate.cfg("strategy.max_positions")
        if len(positions) >= max_positions:
            return 0.5
        if not positions:
            return 2.5
        return 1.5