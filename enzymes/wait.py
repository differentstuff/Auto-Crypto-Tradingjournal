"""
enzymes/wait.py -- Default enzyme: no action, record idle cycle.

The healthy resting state of the system. When no other enzyme can
improve the substrate, Wait fires and records an idle cycle.

The daemon explicitly fires this enzyme as the fallback when no other
enzyme can activate or when all flux scores are <= 0. This keeps the
idle-cycle recording logic in the enzyme (where it belongs) rather
than duplicating it in the daemon.

Enzyme class: Isomerase (lowest priority, always activatable)
Activates when: always (no conditions)
Writes to: decisions.action = 'wait', learning.idle_cycles += 1

Per the reaction network schema: every enzyme is one file in enzymes/.
"""

from __future__ import annotations

import logging

from core.enzyme import Enzyme, EnzymeClass, register_enzyme
from core.substrate import Substrate

_log = logging.getLogger(__name__)


@register_enzyme
class WaitEnzyme(Enzyme):
    """
    Default enzyme. No strong signal detected, no actionable setup found.
    Keep watching. The market owes us nothing.

    Records idle cycles with reasons so the learning engine can later
    validate that waiting was the correct decision.
    """

    name = "Wait"
    enzyme_class = EnzymeClass.ISOMERASE
    priority = -1

    def __init__(self, config=None, idle_reason: str = ""):
        super().__init__(config=config)
        self._idle_reason = idle_reason

    def can_activate(self, substrate: Substrate) -> bool:
        # Always activatable -- the healthy resting state of the system
        return True

    def transform(self, substrate: Substrate) -> Substrate:
        reason = self._idle_reason or "no actionable signal"
        substrate.decisions["action"] = "wait"
        substrate.learning["idle_cycles"] += 1
        # Shallow-copy safe: create new list instead of mutating shared reference
        substrate.learning["idle_reasons"] = substrate.learning.get("idle_reasons", []) + [reason]
        substrate.learning["total_idle_cycles_recorded"] += 1
        substrate._updated_at = substrate._now_iso()
        self._log.info("Wait: %s, staying idle", reason)
        # Reset reason for next cycle
        self._idle_reason = ""
        return substrate

    def set_idle_reason(self, reason: str) -> None:
        """Set the reason for this idle cycle (called by daemon before firing)."""
        self._idle_reason = reason

    def flux_score(self, substrate: Substrate) -> float:
        # Neutral -- only chosen when all other scores <= 0
        return 0.0
