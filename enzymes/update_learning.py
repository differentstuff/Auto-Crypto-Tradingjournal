"""
enzymes/update_learning.py -- Synthase enzyme: trigger learning engine updates.

Calls the learning engine functions after each trade close:
  - update_signal_accuracy(): per-indicator accuracy tracking
  - update_combination_accuracy(): pairwise signal combination tracking
  - update_trajectory_accuracy(): trajectory pattern accuracy tracking
  - compute_adjusted_weights(): adjust indicator weights based on accuracy

Writes adjusted weights to substrate.learning["adjusted_weights"] so that
ScoreConfluence can read them on the next cycle without re-computing.

This enzyme closes the learning loop:
  RecordTradeOutcome → writes trade data to DB
  UpdateLearning → reads trade data, computes accuracy, writes adjusted weights
  ScoreConfluence → reads adjusted weights, uses them for scoring

Enzyme class: Synthase
Priority: -2 (runs after RecordTradeOutcome at -1)
Activates when: decisions.action == 'trade_closed'

Based on: Gap Analysis 2b (Learning Engine never triggered)
"""

from __future__ import annotations

import logging
from typing import Optional

from core.enzyme import Enzyme, EnzymeClass, register_enzyme
from core.substrate import Substrate

_log = logging.getLogger(__name__)


@register_enzyme
class UpdateLearning(Enzyme):
    """
    Synthase enzyme: update learning data after each trade close.

    Fires after RecordTradeOutcome has written the trade data to the DB.
    Calls all learning update functions and writes adjusted weights back
    to the substrate for ScoreConfluence to use on the next cycle.
    """

    name = "UpdateLearning"
    enzyme_class = EnzymeClass.SYNTHASE
    priority = -2  # After RecordTradeOutcome (-1)

    def requires(self) -> list[str]:
        return []

    def prohibits(self) -> list[str]:
        return []

    def can_activate(self, substrate: Substrate) -> bool:
        """Activate after a trade has been closed."""
        action = substrate.decisions.get("action", "wait")
        return action == "trade_closed"

    def transform(self, substrate: Substrate) -> Substrate:
        """Run all learning updates and write adjusted weights to substrate."""
        strategy_name = substrate.strategy.get("name", "")
        strategy_uid = substrate.strategy.get("uid", "legacy")

        # Read ALL learning config values from substrate.cfg() every cycle.
        # No hardcoded defaults — config is the single source of truth.
        min_trades_per_signal = substrate.cfg("learning.min_trades_per_signal")
        min_trades_before_adj = substrate.cfg("learning.min_trades_before_adjusting")
        significance_level = substrate.cfg("learning.significance_level")
        contrarian_win_rate = substrate.cfg("learning.contrarian_win_rate")
        highlight_threshold = substrate.cfg("learning.highlight_threshold")
        monitor_low_threshold = substrate.cfg("learning.monitor_low_threshold")
        suppress_range = tuple(substrate.cfg("learning.suppress_range"))
        contrarian_threshold = substrate.cfg("learning.contrarian_threshold")

        # 1. Update signal accuracy (per-indicator win/loss tracking)
        try:
            from learning.analyzer import update_signal_accuracy
            update_signal_accuracy(
                strategy_name,
                strategy_uid=strategy_uid,
                min_trades_per_signal=min_trades_per_signal,
                highlight_threshold=highlight_threshold,
                monitor_low_threshold=monitor_low_threshold,
                suppress_range=suppress_range,
                contrarian_threshold=contrarian_threshold,
            )
            _log.info("Updated signal accuracy for '%s'", strategy_name)
        except Exception as e:
            _log.error("Failed to update signal accuracy: %s", e, exc_info=True)

        # 2. Update combination accuracy (pairwise signal tracking)
        try:
            from learning.combination import update_combination_accuracy
            update_combination_accuracy(
                strategy_name,
                strategy_uid=strategy_uid,
                min_trades=min_trades_per_signal,
                significance_level=significance_level,
                contrarian_win_rate=contrarian_win_rate,
            )
            _log.info("Updated combination accuracy for '%s'", strategy_name)
        except Exception as e:
            _log.error("Failed to update combination accuracy: %s", e, exc_info=True)

        # 3. Update trajectory accuracy (pattern tracking)
        try:
            from learning.trajectory import update_trajectory_accuracy
            update_trajectory_accuracy(
                strategy_name,
                strategy_uid=strategy_uid,
                min_trades=min_trades_per_signal,
                highlight_threshold=highlight_threshold,
                monitor_low_threshold=monitor_low_threshold,
                suppress_range=suppress_range,
                contrarian_threshold=contrarian_threshold,
            )
            _log.info("Updated trajectory accuracy for '%s'", strategy_name)
        except Exception as e:
            _log.error("Failed to update trajectory accuracy: %s", e, exc_info=True)

        # 4. Compute adjusted weights and write to substrate
        #    If challenger is enabled, push to CandidateQueue instead of
        #    writing directly to production. The challenger validates before
        #    weights reach substrate.learning["adjusted_weights"].
        #    If challenger is disabled, write directly (legacy behavior).
        try:
            from learning.weight_adjuster import compute_adjusted_weights
            indicator_configs = substrate.cfg("indicators", [])
            weight_map = {
                cfg.get("name", ""): cfg.get("weight", 0)
                for cfg in indicator_configs
                if cfg.get("weight", 0) > 0
            }

            adjusted = compute_adjusted_weights(
                weight_map,
                strategy_name,
                strategy_uid=strategy_uid,
                min_trades=min_trades_before_adj,
                adjustment_boost=substrate.cfg("learning.adjustment_boost"),
                adjustment_review_reduce=substrate.cfg("learning.adjustment_review_reduce"),
            )

            if adjusted and adjusted != weight_map:
                challenger_enabled = substrate.cfg("challenger.enabled", False)

                if challenger_enabled:
                    from learning.challenger import CandidateQueue
                    CandidateQueue.push(adjusted, source="weight_adjuster", substrate=substrate)
                    _log.info(
                        "Adjusted weights pushed to CandidateQueue for '%s' (challenger enabled)",
                        strategy_name,
                    )
                else:
                    substrate.learning["adjusted_weights"] = adjusted
                    changed = [
                        k for k in adjusted
                        if adjusted.get(k) != weight_map.get(k)
                    ]
                    _log.info(
                        "Adjusted weights for '%s': %d indicators changed: %s",
                        strategy_name, len(changed), changed,
                    )
            else:
                _log.debug("No weight adjustments needed for '%s'", strategy_name)

        except Exception as e:
            _log.error("Failed to compute adjusted weights: %s", e, exc_info=True)

        # 5. Compute adjusted soft penalty thresholds based on trade outcomes
        #    If penalized trades are winning, reduce penalties (they're too aggressive).
        #    If penalized trades are losing, increase penalties (they're too lenient).
        try:
            from learning.weight_adjuster import compute_adjusted_thresholds
            current_penalties = {
                "noise_penalty_ratio": substrate.cfg("soft_penalties.noise_penalty_ratio", 0.3),
                "confluence_penalty_ratio": substrate.cfg("soft_penalties.confluence_penalty_ratio", 0.3),
                "trajectory_penalty_ratio": substrate.cfg("soft_penalties.trajectory_penalty_ratio", 0.5),
                "trajectory_medium_ratio": substrate.cfg("soft_penalties.trajectory_medium_ratio", 0.2),
            }

            adjusted_thresholds = compute_adjusted_thresholds(
                current_penalties,
                strategy_name,
                strategy_uid=strategy_uid,
                min_trades=min_trades_before_adj,
            )

            if adjusted_thresholds != current_penalties:
                substrate.learning["adjusted_thresholds"] = adjusted_thresholds
                changed_keys = [
                    k for k in adjusted_thresholds
                    if adjusted_thresholds.get(k) != current_penalties.get(k)
                ]
                _log.info(
                    "Adjusted penalty thresholds for '%s': %d changed: %s",
                    strategy_name, len(changed_keys), changed_keys,
                )
            else:
                _log.debug("No penalty threshold adjustments needed for '%s'", strategy_name)

        except Exception as e:
            _log.error("Failed to compute adjusted thresholds: %s", e, exc_info=True)

        # 6. Update trade count in substrate
        try:
            from core.database import db_conn
            with db_conn() as conn:
                row = conn.execute(
                    """SELECT COUNT(*) as cnt FROM trade_learning
                       WHERE strategy_name = ? AND exit_time IS NOT NULL""",
                    (strategy_name,),
                ).fetchone()
                if row:
                    substrate.learning["total_trades_recorded"] = row["cnt"]
        except Exception as e:
            _log.debug("Could not update trade count: %s", e)

        _log.info("UpdateLearning complete for '%s'", strategy_name)
        return substrate

    def flux_score(self, substrate: Substrate) -> float:
        """Dynamic flux: high urgency — learning must update after trade close."""
        if not self.can_activate(substrate):
            return 0.0
        return 4.0  # Must fire promptly after trade close