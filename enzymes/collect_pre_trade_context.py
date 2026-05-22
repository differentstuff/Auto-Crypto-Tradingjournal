"""
enzymes/collect_pre_trade_context.py -- Sensor enzyme: trajectory analysis.

Computes pre-trade trajectory analysis over the last N bars for each
candidate. Classifies how indicators aligned: gradual alignment vs
sudden coincidence.

Enzyme class: Sensor
Activates when: analysis.candidates not empty AND market.pre_trade_context is empty

NEW (uses chart_indicators.py historical computation concept)
"""

from __future__ import annotations

import logging
from typing import Optional

from core.enzyme import Enzyme, EnzymeClass, register_enzyme
from core.substrate import Substrate

_log = logging.getLogger(__name__)


def _classify_trajectory(indicator_history: list[dict]) -> dict:
    """
    Classify how indicators aligned over the lookback period.

    Trajectory types:
    - "gradual_alignment": indicators progressively aligned over 8+ bars
    - "sudden_coincidence": indicators aligned only in last 2-3 bars (risky)
    - "stable_consensus": indicators have been aligned for 10+ bars (strong)
    - "diverging": indicators were aligned but are now diverging (weakening)

    Returns {trajectory_type, coincidence_risk, bars_aligned, strength}
    """
    if not indicator_history:
        return {
            "trajectory_type": "unknown",
            "coincidence_risk": "high",
            "bars_aligned": 0,
            "strength": 0.0,
        }

    n = len(indicator_history)
    # Count how many recent bars have aligned signals
    aligned_count = 0
    for bar in indicator_history:
        if bar.get("aligned", False):
            aligned_count += 1

    # Check if alignment is recent (last 3 bars) vs sustained
    recent_aligned = sum(
        1 for bar in indicator_history[-3:] if bar.get("aligned", False)
    )
    earlier_aligned = sum(
        1 for bar in indicator_history[:-3] if bar.get("aligned", False)
    ) if n > 3 else 0

    # Classify
    if aligned_count >= 10:
        trajectory_type = "stable_consensus"
        coincidence_risk = "low"
    elif aligned_count >= 8 and earlier_aligned >= 3:
        trajectory_type = "gradual_alignment"
        coincidence_risk = "low"
    elif recent_aligned >= 2 and earlier_aligned < 2:
        trajectory_type = "sudden_coincidence"
        coincidence_risk = "high"
    elif earlier_aligned > recent_aligned:
        trajectory_type = "diverging"
        coincidence_risk = "medium"
    elif aligned_count >= 4:
        trajectory_type = "gradual_alignment"
        coincidence_risk = "low"
    else:
        trajectory_type = "no_alignment"
        coincidence_risk = "high"

    strength = round(aligned_count / n, 2) if n else 0.0

    return {
        "trajectory_type": trajectory_type,
        "coincidence_risk": coincidence_risk,
        "bars_aligned": aligned_count,
        "total_bars": n,
        "strength": strength,
    }


@register_enzyme
class CollectPreTradeContext(Enzyme):
    """
    Sensor enzyme: compute pre-trade trajectory for candidates.

    For each candidate, analyzes how indicators aligned over the last
    N bars (configurable via learning.trajectory_lookback_bars).
    A gradual alignment is a stronger signal than a sudden coincidence.

    Writes to substrate.market.pre_trade_context as:
        {symbol: {trajectory_type, coincidence_risk, bars_aligned, ...}}
    """

    name = "CollectPreTradeContext"
    enzyme_class = EnzymeClass.SENSOR
    priority = 0

    def requires(self) -> list[str]:
        return ["analysis.candidates not empty"]

    def prohibits(self) -> list[str]:
        return ["market.pre_trade_context not empty"]

    def can_activate(self, substrate: Substrate) -> bool:
        candidates = substrate.analysis.get("candidates", [])
        pre_trade_evaluated = substrate.analysis.get("pre_trade_evaluated", False)
        return bool(candidates) and not pre_trade_evaluated

    def transform(self, substrate: Substrate) -> Substrate:
        """Compute pre-trade trajectory for each candidate."""
        candidates = substrate.analysis.get("candidates", [])
        if not candidates:
            return substrate

        lookback = substrate.cfg("learning.trajectory_lookback_bars", 12)
        indicators = substrate.market.get("indicators", {})

        pre_trade_context = {}

        for candidate in candidates:
            symbol = candidate.get("symbol", "")
            sym_data = indicators.get(symbol, {})
            if not sym_data:
                continue

            # Get primary timeframe
            primary_tf = list(sym_data.keys())[0] if sym_data else None
            if not primary_tf:
                continue

            tf_inds = sym_data[primary_tf]
            if not isinstance(tf_inds, dict) or not tf_inds.get("ok"):
                continue

            # Determine current signal direction
            score = candidate.get("score", 0)
            is_bullish = score > 0

            # Build indicator history over lookback bars
            # We use the current indicator state as a snapshot and
            # estimate trajectory from the indicator values themselves
            indicator_history = self._estimate_trajectory(
                tf_inds, is_bullish, lookback
            )

            trajectory = _classify_trajectory(indicator_history)
            pre_trade_context[symbol] = trajectory

        substrate.market["pre_trade_context"] = pre_trade_context
        substrate.analysis["pre_trade_evaluated"] = True

        self._log.info(
            "Pre-trade context: %d symbols analyzed",
            len(pre_trade_context),
        )

        return substrate

    def _estimate_trajectory(
        self, tf_inds: dict, is_bullish: bool, lookback: int
    ) -> list[dict]:
        """
        Estimate trajectory from current indicator state.

        Since we only have the current snapshot (not historical bar-by-bar
        indicator data), we estimate alignment trajectory from indicator
        strength and crossover signals.

        In a full implementation, this would recompute indicators over
        rolling windows. For Phase B, we use a simplified heuristic.
        """
        history = []

        # RSI: if not at extreme, alignment is recent
        rsi = tf_inds.get("rsi", {})
        rsi_val = rsi.get("value", 50) if isinstance(rsi, dict) else 50

        # MACD: crossover suggests recent alignment
        macd = tf_inds.get("macd", {})
        has_crossover = macd.get("crossover", False) if isinstance(macd, dict) else False
        has_crossunder = macd.get("crossunder", False) if isinstance(macd, dict) else False

        # EMA: alignment strength
        ema = tf_inds.get("ema_stack", {})
        ema_alignment = ema.get("alignment", "neutral") if isinstance(ema, dict) else "neutral"
        ema_stack = ema.get("stack", "mixed") if isinstance(ema, dict) else "mixed"

        # Build estimated history
        for i in range(lookback):
            bars_ago = lookback - i
            aligned = False

            # Heuristic: if MACD has crossover, alignment started recently
            if has_crossover and is_bullish:
                aligned = i >= lookback - 3  # Last 3 bars
            elif has_crossunder and not is_bullish:
                aligned = i >= lookback - 3
            elif "fully" in ema_alignment and ema_stack != "mixed":
                # Full EMA alignment suggests sustained trend
                aligned = True
            elif abs(rsi_val - 50) > 20:
                # Strong RSI suggests alignment
                aligned = i >= lookback - 5
            else:
                # Weak signals — sporadic alignment
                aligned = i % 3 == 0  # Roughly 1/3 of bars

            history.append({"bar": i, "bars_ago": bars_ago, "aligned": aligned})

        return history

    def flux_score(self, substrate: Substrate) -> float:
        if self.can_activate(substrate):
            return 0.8
        return 0.0