"""
learning/comparator.py -- ChallengerComparator: profit factor evaluation.

Compares challenger vs production performance using profit factor
(gross wins / gross losses) as the metric. Evaluates only after
a configurable minimum number of hypothetical trades have closed.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

_log = logging.getLogger(__name__)


class ChallengerComparator:
    """Evaluates whether the challenger should be promoted or discarded."""

    @staticmethod
    def should_evaluate(substrate: Any) -> bool:
        """Check if enough hypothetical trades have closed for evaluation."""
        challenger = substrate.learning.get("challenger", {})
        min_trades = substrate.cfg("challenger.min_trades")
        trade_count = challenger.get("trade_count", 0)
        return trade_count >= min_trades

    @staticmethod
    def compute_profit_factor(trades: List[Dict]) -> float:
        """Compute profit factor = gross_wins / gross_losses."""
        if not trades:
            return 0.0
        gross_wins = 0.0
        gross_losses = 0.0
        for trade in trades:
            pnl = trade.get("exit_pnl_pct", 0.0)
            if pnl > 0:
                gross_wins += pnl
            elif pnl < 0:
                gross_losses += abs(pnl)
        if gross_losses == 0:
            return float("inf") if gross_wins > 0 else 0.0
        return gross_wins / gross_losses

    @staticmethod
    def evaluate(substrate: Any) -> str:
        """Evaluate the challenger against production.

        Returns "promote", "discard", or "accumulating".
        """
        challenger = substrate.learning.get("challenger", {})
        if not challenger.get("weights"):
            return "accumulating"
        if not ChallengerComparator.should_evaluate(substrate):
            return "accumulating"

        strategy_uid = substrate.strategy.get("uid", "legacy")
        from learning.hypothetical_tracker import HypotheticalTracker
        challenger_trades = HypotheticalTracker.get_closed_trades(strategy_uid)
        challenger_pf = ChallengerComparator.compute_profit_factor(challenger_trades)
        production_pf = ChallengerComparator._get_production_profit_factor(substrate)
        min_improvement = substrate.cfg("challenger.min_improvement")

        if production_pf <= 0:
            if challenger_pf > 0:
                return "promote"
            return "discard"

        improvement = (challenger_pf - production_pf) / production_pf
        if improvement >= min_improvement:
            return "promote"
        return "discard"

    @staticmethod
    def get_metrics(substrate: Any) -> Dict:
        """Return current comparison metrics for logging."""
        challenger = substrate.learning.get("challenger", {})
        strategy_uid = substrate.strategy.get("uid", "legacy")
        from learning.hypothetical_tracker import HypotheticalTracker
        challenger_trades = HypotheticalTracker.get_closed_trades(strategy_uid)
        challenger_pf = ChallengerComparator.compute_profit_factor(challenger_trades)
        production_pf = ChallengerComparator._get_production_profit_factor(substrate)
        return {
            "production_profit_factor": round(production_pf, 3),
            "challenger_profit_factor": round(challenger_pf, 3),
            "trade_count": challenger.get("trade_count", 0),
        }

    @staticmethod
    def _get_production_profit_factor(substrate: Any) -> float:
        """Compute production profit factor from recent trade_learning data."""
        try:
            from core.database import db_conn
            strategy_name = substrate.strategy.get("name", "")
            with db_conn() as conn:
                rows = conn.execute(
                    """SELECT pnl_pct FROM trade_learning
                       WHERE strategy_name = ?
                         AND exit_time IS NOT NULL
                         AND pnl_pct IS NOT NULL
                       ORDER BY exit_time DESC LIMIT 50""",
                    (strategy_name,),
                ).fetchall()
                trades = [{"exit_pnl_pct": row["pnl_pct"]} for row in rows]
                return ChallengerComparator.compute_profit_factor(trades)
        except Exception as e:
            _log.error("Failed to compute production profit factor: %s", e, exc_info=True)
            return 0.0