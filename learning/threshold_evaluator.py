"""
learning/threshold_evaluator.py -- Compare production vs exploration bucket accuracy.

Reads signal_accuracy_by_threshold for a strategy, compares production
and exploration bucket stats, and proposes threshold changes to
CandidateQueue when exploration outperforms production with statistical
significance.

Three conditions must ALL be met for a proposal:
  1. exploration PF > production PF * (1 + min_improvement_pct/100)
  2. exploration has >= min_trades trades
  3. Wilson score intervals for win_rate don't overlap (statistically significant)

Reuses:
  - wilson_score_interval() from analyzer.py
  - CandidateQueue.push() from challenger.py
  - db_conn() from database.py

Decision D4: No duplication — reuses existing code.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

_log = logging.getLogger(__name__)


def evaluate_thresholds(
    strategy_name: str,
    strategy_uid: str,
    entry_threshold: float,
    min_trades: int = 30,
    min_improvement_pct: float = 20.0,
    min_confidence_gap: float = 0.10,
    substrate: Any = None,
) -> Optional[Dict]:
    """Compare production vs exploration bucket accuracy and propose threshold changes.

    Args:
        strategy_name: Strategy name for logging.
        strategy_uid: Strategy UID for DB queries.
        entry_threshold: Current production entry_threshold.
        min_trades: Minimum trades in exploration bucket to consider.
        min_improvement_pct: Minimum PF improvement percentage to propose.
        min_confidence_gap: Minimum gap between Wilson intervals for significance.
        substrate: Substrate object (needed for CandidateQueue.push).

    Returns:
        Proposal dict if conditions are met, None otherwise.
        Proposal contains: {entry_threshold, source, reason, production_pf,
        exploration_pf, trade_count}
    """
    from core.database import db_conn
    from learning.analyzer import wilson_score_interval

    try:
        with db_conn() as conn:
            # Read production bucket stats
            prod_rows = conn.execute(
                """SELECT indicator_name, profit_factor, win_rate, trade_count,
                          total_fired, correct, threshold_value
                   FROM signal_accuracy_by_threshold
                   WHERE strategy_uid = ? AND threshold_bucket = 'production'""",
                (strategy_uid,),
            ).fetchall()

            # Read exploration bucket stats
            expl_rows = conn.execute(
                """SELECT indicator_name, profit_factor, win_rate, trade_count,
                          total_fired, correct, threshold_value
                   FROM signal_accuracy_by_threshold
                   WHERE strategy_uid = ? AND threshold_bucket = 'exploration'""",
                (strategy_uid,),
            ).fetchall()

        if not prod_rows or not expl_rows:
            _log.debug(
                "Threshold evaluator: insufficient data for '%s' "
                "(prod=%d rows, expl=%d rows)",
                strategy_name, len(prod_rows), len(expl_rows),
            )
            return None

        # Aggregate stats across all indicators per bucket
        prod_pf_values = [r["profit_factor"] for r in prod_rows if r["profit_factor"] is not None and r["trade_count"] > 0]
        expl_pf_values = [r["profit_factor"] for r in expl_rows if r["profit_factor"] is not None and r["trade_count"] > 0]

        # Weight by trade_count for aggregate PF
        prod_total_trades = sum(r["trade_count"] for r in prod_rows)
        expl_total_trades = sum(r["trade_count"] for r in expl_rows)

        # Aggregate win_rate using total correct / total fired
        prod_total_correct = sum(r["correct"] for r in prod_rows)
        prod_total_fired = sum(r["total_fired"] for r in prod_rows)

        expl_total_correct = sum(r["correct"] for r in expl_rows)
        expl_total_fired = sum(r["total_fired"] for r in expl_rows)

        # Compute aggregate profit factor (weighted average)
        prod_pf = (
            sum(pf * r["trade_count"] for pf, r in zip(prod_pf_values, [r for r in prod_rows if r["profit_factor"] is not None and r["trade_count"] > 0]))
            / max(prod_total_trades, 1)
            if prod_pf_values
            else 0.0
        )
        expl_pf = (
            sum(pf * r["trade_count"] for pf, r in zip(expl_pf_values, [r for r in expl_rows if r["profit_factor"] is not None and r["trade_count"] > 0]))
            / max(expl_total_trades, 1)
            if expl_pf_values
            else 0.0
        )

        # Get average threshold_value from exploration rows
        expl_thresholds = [r["threshold_value"] for r in expl_rows if r["threshold_value"] > 0]
        avg_expl_threshold = sum(expl_thresholds) / len(expl_thresholds) if expl_thresholds else entry_threshold

        # ── Condition 1: exploration PF > production PF * (1 + min_improvement_pct/100)
        if prod_pf <= 0:
            _log.debug("Threshold evaluator: production PF is 0, cannot compare")
            return None

        improvement = (expl_pf - prod_pf) / prod_pf * 100
        if improvement < min_improvement_pct:
            _log.debug(
                "Threshold evaluator: exploration PF improvement %.1f%% < %.1f%% minimum",
                improvement, min_improvement_pct,
            )
            return None

        # ── Condition 2: exploration has >= min_trades trades
        if expl_total_trades < min_trades:
            _log.debug(
                "Threshold evaluator: exploration trades %d < %d minimum",
                expl_total_trades, min_trades,
            )
            return None

        # ── Condition 3: Wilson score intervals for win_rate don't overlap
        prod_wr_low, prod_wr_high = wilson_score_interval(prod_total_correct, prod_total_fired)
        expl_wr_low, expl_wr_high = wilson_score_interval(expl_total_correct, expl_total_fired)

        # Intervals overlap if the lower bound of one is below the upper bound of the other
        intervals_overlap = not (prod_wr_high < expl_wr_low or expl_wr_high < prod_wr_low)

        if intervals_overlap:
            _log.debug(
                "Threshold evaluator: Wilson intervals overlap "
                "(prod: [%.3f, %.3f], expl: [%.3f, %.3f])",
                prod_wr_low, prod_wr_high, expl_wr_low, expl_wr_high,
            )
            return None

        # Intervals don't overlap — check gap is large enough for confidence
        gap = max(0, expl_wr_low - prod_wr_high) if expl_wr_low > prod_wr_high else max(0, prod_wr_low - expl_wr_high)
        if gap < min_confidence_gap:
            _log.debug(
                "Threshold evaluator: Wilson gap too small "
                "(prod: [%.3f, %.3f], expl: [%.3f, %.3f], gap: %.3f < %.3f)",
                prod_wr_low, prod_wr_high, expl_wr_low, expl_wr_high, gap, min_confidence_gap,
            )
            return None

        # ── All conditions met: propose threshold change
        proposal = {
            "entry_threshold": avg_expl_threshold,
            "source": "threshold_evaluator",
            "reason": (
                f"Exploration outperforms production: "
                f"PF {expl_pf:.2f} vs {prod_pf:.2f} (+{improvement:.1f}%), "
                f"trades={expl_total_trades}, "
                f"WR intervals non-overlapping (gap={gap:.3f})"
            ),
            "production_pf": round(prod_pf, 2),
            "exploration_pf": round(expl_pf, 2),
            "trade_count": expl_total_trades,
        }

        # Push to CandidateQueue if substrate is available
        if substrate is not None:
            try:
                from learning.challenger import CandidateQueue
                CandidateQueue.push(
                    weights={"entry_threshold": avg_expl_threshold},
                    source="threshold_evaluator",
                    substrate=substrate,
                    metadata=proposal,
                )
                _log.info("Threshold evaluator pushed proposal to CandidateQueue: %s", proposal)
            except Exception as e:
                _log.error("Failed to push threshold proposal to CandidateQueue: %s", e, exc_info=True)
        else:
            _log.info("Threshold evaluator proposal (no substrate, not pushed): %s", proposal)

        return proposal

    except Exception as e:
        _log.error("Threshold evaluator failed for '%s': %s", strategy_name, e, exc_info=True)
        return None