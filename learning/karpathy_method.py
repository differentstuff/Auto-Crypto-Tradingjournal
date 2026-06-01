"""
learning/karpathy_method.py -- Karpathy experiment loop (stub).

Proposes one parameter change at a time, backtests it, and pushes
improvements to the CandidateQueue for Challenger validation.

This is a stub — the interface is defined but the implementation
is deferred to the AutoTraderKarpathyMethod plan.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

_log = logging.getLogger(__name__)


class KarpathyMethod:
    """Karpathy experiment loop: propose one change, backtest, push if better.

    Stub: interface defined. Full implementation in AutoTraderKarpathyMethod plan.
    """

    @staticmethod
    def run_experiment_cycle(substrate: Any) -> None:
        """Run one experiment cycle: propose, backtest, push if improved.

        Stub — not yet implemented.
        """
        enabled = substrate.cfg("karpathy.enabled", False)
        if not enabled:
            return
        _log.debug("KarpathyMethod: experiment cycle not yet implemented")

    @staticmethod
    def push_candidate_if_improved(
        new_weights: Dict[str, float],
        backtest_score: float,
        substrate: Any,
        param_changed: str = "",
    ) -> None:
        """Push a candidate to the CandidateQueue if backtest improved.

        Stub — not yet implemented.
        """
        _log.debug("KarpathyMethod: candidate push not yet implemented")