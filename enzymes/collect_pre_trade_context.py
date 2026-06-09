"""
enzymes/collect_pre_trade_context.py -- Sensor enzyme: trajectory analysis.

Computes pre-trade trajectory analysis for each candidate using REAL
indicator history from substrate.market.indicator_history (rolling window
populated by CollectOHLCV).

P8 (Time-Based Trajectory Sufficiency):
  History sufficiency is measured by time span (trajectory_min_hours), not
  by bar count. This ensures consistent behavior regardless of cycle frequency.

If indicator history is insufficient (time span < trajectory_min_hours), falls
back to empty trajectory data, which sets coincidence_risk='high' and applies
a trajectory soft penalty (instead of blocking via former ISC-007).
This is intentional: no trades until sufficient trajectory data exists,
but once data exists, high coincidence_risk only reduces the effective score
rather than blocking entirely.

Writes:
  market.pre_trade_context (dict per symbol)
  analysis.trajectory_penalty_ratio (float 0.0-1.0)

Enzyme class: Sensor
Activates when: analysis.candidates not empty AND pre_trade_evaluated is False
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from core.enzyme import Enzyme, EnzymeClass, register_enzyme
from core.substrate import Substrate

_log = logging.getLogger(__name__)


def _classify_trajectory(indicator_history: list[dict], thresholds: dict) -> dict:
    """
    Classify how indicators aligned over the lookback period.

    Uses REAL indicator history (not a heuristic estimate). Each history
    entry has a "signal" field ("bullish"/"bearish"/"neutral") computed
    by CollectOHLCV._compute_signal_direction().

    thresholds: dict from config (learning.trajectory_thresholds) with keys:
      stable_consensus, gradual_alignment, earlier_min, recent_min,
      earlier_low, min_alignment

    Trajectory types:
    - "gradual_alignment": indicators progressively aligned over 8+ bars
    - "sudden_coincidence": indicators aligned only in last 2-3 bars (risky)
    - "stable_consensus": indicators have been aligned for 10+ bars (strong)
    - "diverging": indicators were aligned but are now diverging (weakening)
    - "no_alignment": no consistent direction detected

    Returns {trajectory_type, coincidence_risk, bars_aligned, strength}
    """
    t_stable = thresholds.get("stable_consensus", 10)
    t_gradual = thresholds.get("gradual_alignment", 8)
    t_earlier_min = thresholds.get("earlier_min", 3)
    t_recent_min = thresholds.get("recent_min", 2)
    t_earlier_low = thresholds.get("earlier_low", 2)
    t_min_align = thresholds.get("min_alignment", 4)
    if not indicator_history:
        return {
            "trajectory_type": "unknown",
            "coincidence_risk": "high",
            "bars_aligned": 0,
            "strength": 0.0,
        }

    n = len(indicator_history)

    # Count how many bars have aligned signals in the final direction
    # Determine the final direction from the most recent bars
    recent_signals = [
        entry.get("signal", "neutral")
        for entry in indicator_history[-3:]
        if entry.get("signal", "neutral") != "neutral"
    ]

    if not recent_signals:
        return {
            "trajectory_type": "no_alignment",
            "coincidence_risk": "high",
            "bars_aligned": 0,
            "strength": 0.0,
        }

    # Determine final direction from majority of recent signals
    bullish_count = sum(1 for s in recent_signals if s == "bullish")
    bearish_count = sum(1 for s in recent_signals if s == "bearish")
    final_direction = "bullish" if bullish_count >= bearish_count else "bearish"

    # Count bars aligned with the final direction
    aligned_count = 0
    for entry in indicator_history:
        signal = entry.get("signal", "neutral")
        if signal == final_direction:
            aligned_count += 1

    # Check if alignment is recent (last 3 bars) vs sustained
    recent_aligned = sum(
        1 for entry in indicator_history[-3:]
        if entry.get("signal") == final_direction
    )
    earlier_aligned = sum(
        1 for entry in indicator_history[:-3]
        if entry.get("signal") == final_direction
    ) if n > 3 else 0

    # Classify
    if aligned_count >= t_stable:
        trajectory_type = "stable_consensus"
        coincidence_risk = "low"
    elif aligned_count >= t_gradual and earlier_aligned >= t_earlier_min:
        trajectory_type = "gradual_alignment"
        coincidence_risk = "low"
    elif recent_aligned >= t_recent_min and earlier_aligned < t_earlier_low:
        trajectory_type = "sudden_coincidence"
        coincidence_risk = "high"
    elif earlier_aligned > recent_aligned:
        trajectory_type = "diverging"
        coincidence_risk = "medium"
    elif aligned_count >= t_min_align:
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
        "final_direction": final_direction,
    }


def _compute_history_span_hours(history: list[dict]) -> float:
    """
    Compute the time span in hours covered by the indicator history.

    P8: Uses timestamps from history entries to determine real elapsed time,
    not bar count. Returns 0.0 if timestamps are missing or unparseable.
    """
    if not history or len(history) < 2:
        return 0.0

    first_ts = history[0].get("timestamp", "")
    last_ts = history[-1].get("timestamp", "")

    if not first_ts or not last_ts:
        return 0.0

    try:
        first_dt = datetime.fromisoformat(first_ts)
        last_dt = datetime.fromisoformat(last_ts)
        if first_dt.tzinfo is None:
            first_dt = first_dt.replace(tzinfo=timezone.utc)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        span_seconds = (last_dt - first_dt).total_seconds()
        return max(0.0, span_seconds / 3600.0)
    except (ValueError, TypeError):
        return 0.0


@register_enzyme
class CollectPreTradeContext(Enzyme):
    """
    Sensor enzyme: compute pre-trade trajectory for candidates.

    For each candidate, analyzes how indicators aligned over the lookback
    period (configurable via learning.trajectory_lookback_hours).
    Uses REAL indicator history from substrate.market.indicator_history,
    which is populated by CollectOHLCV on each candle close.

    P8: History sufficiency is measured by time span (trajectory_min_hours),
    not bar count. If the history spans less than trajectory_min_hours,
    coincidence_risk is set to 'high' and a trajectory soft penalty is applied
    (instead of blocking via former ISC-007).

    Writes to substrate.market.pre_trade_context as:
        {symbol: {trajectory_type, coincidence_risk, bars_aligned, ...}}

    Also writes analysis.trajectory_penalty_ratio (0.0-1.0) for the
    best candidate, used by ApproveTrade via compute_effective_score().
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
        """Compute pre-trade trajectory for each candidate using real history."""
        candidates = substrate.analysis.get("candidates", [])
        if not candidates:
            return substrate

        # P8: Use time-based sufficiency check (trajectory_min_hours)
        min_hours = substrate.cfg("learning.trajectory_min_hours")
        trajectory_thresholds = substrate.cfg("learning.trajectory_thresholds")
        indicator_history = substrate.market.get("indicator_history", {})
        pre_trade_context = {}

        # Read penalty ratios from config
        trajectory_penalty_high = substrate.cfg("soft_penalties.trajectory_penalty_ratio", 0.5)
        trajectory_penalty_medium = substrate.cfg("soft_penalties.trajectory_medium_ratio", 0.2)

        # Track the worst coincidence risk across all candidates for the penalty
        worst_risk = "low"

        for candidate in candidates:
            symbol = candidate.get("symbol", "")

            # Get real indicator history for this symbol
            symbol_history = indicator_history.get(symbol, [])

            # P8: Check time span, not bar count
            span_hours = _compute_history_span_hours(symbol_history)

            if span_hours < min_hours:
                # Insufficient time span — apply high trajectory penalty
                _log.info(
                    "Insufficient trajectory history for %s: %.1fh / %.1fh required",
                    symbol, span_hours, min_hours,
                )
                pre_trade_context[symbol] = {
                    "trajectory_type": "insufficient_data",
                    "coincidence_risk": "high",
                    "bars_aligned": 0,
                    "total_bars": len(symbol_history),
                    "span_hours": round(span_hours, 1),
                    "strength": 0.0,
                }
                worst_risk = "high"
                continue

            # Use real history for trajectory classification
            trajectory = _classify_trajectory(symbol_history, trajectory_thresholds)
            # P8: Add span_hours to trajectory data for observability
            trajectory["span_hours"] = round(span_hours, 1)
            pre_trade_context[symbol] = trajectory

            # Track worst risk for penalty computation
            risk = trajectory.get("coincidence_risk", "low")
            if risk == "high":
                worst_risk = "high"
            elif risk == "medium" and worst_risk != "high":
                worst_risk = "medium"

        # Compute trajectory_penalty_ratio based on worst coincidence risk
        if worst_risk == "high":
            trajectory_penalty_ratio = trajectory_penalty_high
        elif worst_risk == "medium":
            trajectory_penalty_ratio = trajectory_penalty_medium
        else:
            trajectory_penalty_ratio = 0.0

        substrate.market["pre_trade_context"] = pre_trade_context
        substrate.analysis["trajectory_penalty_ratio"] = trajectory_penalty_ratio
        substrate.analysis["pre_trade_evaluated"] = True

        self._log.info(
            "Pre-trade context: %d symbols analyzed (using real history, min %.1fh, penalty=%.2f)",
            len(pre_trade_context), min_hours, trajectory_penalty_ratio,
        )

        return substrate

    def flux_score(self, substrate: Substrate) -> float:
        """Higher flux when candidates are strong — trajectory matters more."""
        if not self.can_activate(substrate):
            return 0.0
        candidates = substrate.analysis.get("candidates", [])
        if candidates:
            top_score = abs(candidates[0].get("score", 0))
            entry_threshold = substrate.cfg("scoring.entry_threshold")
            if top_score >= entry_threshold:
                return 1.5  # Strong candidate — trajectory analysis is important
        return 0.8  # Candidates exist but weak