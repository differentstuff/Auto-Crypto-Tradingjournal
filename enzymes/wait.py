"""
enzymes/wait.py -- Default enzyme: no action, record idle cycle.

The healthy resting state of the system. When no other enzyme can
improve the substrate, Wait fires and records an idle cycle.

Enzyme class: Isomerase (lowest priority, always activatable)
Activates when: always (no conditions)
Writes to: decisions.action = 'wait'

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
    """

    name = "Wait"
    enzyme_class = EnzymeClass.ISOMERASE
    priority = -1

    def can_activate(self, substrate: Substrate) -> bool:
        # Always activatable -- the healthy resting state of the system
        return True

    def transform(self, substrate: Substrate) -> Substrate:
        substrate.decisions["action"] = "wait"
        substrate._updated_at = substrate._now_iso()
        self._log.info("Wait: no actionable signal, staying idle")
        return substrate

    def flux_score(self, substrate: Substrate) -> float:
        # Neutral -- only chosen when all other scores <= 0
        return 0.0