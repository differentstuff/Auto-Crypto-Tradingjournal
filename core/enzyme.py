"""
core/enzyme.py -- Enzyme base class for the reaction network.

Each enzyme has:
  - class: Sensor, Oxidoreductase, Regulator, Synthase, Transporter, Isomerase
  - activation conditions (requires/prohibits)
  - transform() method that modifies the substrate
  - flux_score() method measuring progress toward attractor
  - priority: Regulator enzymes always fire first

Based on: docs/reaction-design/enzyme-definitions.yaml
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from core.substrate import Substrate


class EnzymeClass(Enum):
    SENSOR = "Sensor"
    OXIDOREDUCTASE = "Oxidoreductase"
    SYNTHASE = "Synthase"
    REGULATOR = "Regulator"
    TRANSPORTER = "Transporter"
    ISOMERASE = "Isomerase"


class Enzyme(ABC):
    """
    Abstract base class for all enzymes in the reaction network.

    Enzymes fire based on activation conditions. Regulator enzymes
    have priority over all others. The daemon loop selects the
    highest-flux-score enzyme that can activate each step.
    """

    # Subclasses override these
    name: str = "UnnamedEnzyme"
    enzyme_class: EnzymeClass = EnzymeClass.ISOMERASE
    priority: int = 0
    llm_required: bool = False

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self._log = logging.getLogger(f"enzyme.{self.name}")

    # --- Activation conditions -------------------------------------------------

    def can_activate(self, substrate: Substrate) -> bool:
        """
        Check if this enzyme's activation conditions are met.

        Default implementation checks requires/prohibits lists.
        Subclasses can override for custom logic.
        """
        # Check requires: all must be True
        for req in self.requires():
            if not self._evaluate_condition(req, substrate):
                return False

        # Check prohibits: none must be True
        for pro in self.prohibits():
            if self._evaluate_condition(pro, substrate):
                return False

        return True

    def requires(self) -> List[str]:
        """List of conditions that must be True for activation."""
        return []

    def prohibits(self) -> List[str]:
        """List of conditions that must be False for activation."""
        return []

    def _evaluate_condition(self, condition: str, substrate: Substrate) -> bool:
        """
        Evaluate a condition string against the substrate.

        Supported conditions:
          - "substrate.section.field is set" -- field is not empty/None/False
          - "substrate.section.field == 'value'" -- equality check
          - "substrate.section.field not empty" -- list/dict is not empty
          - "substrate.section.field fresh" -- timestamp is recent (within TTL)
          - Custom conditions handled by subclass overrides
        """
        s = substrate

        # Handle common conditions
        if "is set" in condition:
            path = condition.replace(" is set", "").strip()
            val = s.get(path)
            return val is not None and val != "" and val != 0 and val != []

        if "not empty" in condition:
            path = condition.split("not empty")[0].strip()
            val = s.get(path)
            return bool(val)

        if "is empty" in condition or "is empty or stale" in condition:
            path = condition.split("is empty")[0].strip()
            val = s.get(path)
            return not bool(val)

        if "==" in condition:
            parts = condition.split("==")
            path = parts[0].strip()
            expected = parts[1].strip().strip("'\"")
            actual = s.get(path)
            return str(actual) == expected

        if "!=" in condition:
            parts = condition.split("!=")
            path = parts[0].strip()
            expected = parts[1].strip().strip("'\"")
            actual = s.get(path)
            return str(actual) != expected

        if "fresh" in condition:
            # For freshness checks, we assume True for now
            # (TTL checks implemented in subclasses with cache awareness)
            return True

        self._log.warning("Unknown condition format: %s", condition)
        return False

    # --- Core methods ----------------------------------------------------------

    @abstractmethod
    def transform(self, substrate: Substrate) -> Substrate:
        """
        Execute this enzyme's transformation on the substrate.

        This is the main work method. Each enzyme modifies only
        its designated output fields and returns the updated substrate.
        """
        ...

    def flux_score(self, substrate: Substrate) -> float:
        """
        Calculate how much this enzyme moves the substrate toward
        the attractor. Higher = more progress.

        Default: 1.0 for activatable enzymes, 0.0 otherwise.
        Regulators always get priority regardless of flux score.
        """
        if self.can_activate(substrate):
            return 1.0
        return 0.0

    # --- Class hierarchy helpers -----------------------------------------------

    @property
    def is_regulator(self) -> bool:
        return self.enzyme_class == EnzymeClass.REGULATOR

    @property
    def is_sensor(self) -> bool:
        return self.enzyme_class == EnzymeClass.SENSOR

    @property
    def class_priority(self) -> int:
        """Regulators get priority 10, everything else uses instance priority."""
        if self.is_regulator:
            return 10
        return self.priority

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"name={self.name!r}, "
            f"class={self.enzyme_class.value}, "
            f"priority={self.priority})"
        )


# --- Enzyme Registry ----------------------------------------------------------

_enzyme_registry: dict[str, type[Enzyme]] = {}


def register_enzyme(cls: type[Enzyme]) -> type[Enzyme]:
    """Decorator to register an enzyme class by name."""
    instance = cls()
    _enzyme_registry[instance.name] = cls
    return cls


def get_enzyme(name: str) -> Optional[type[Enzyme]]:
    """Look up an enzyme class by name."""
    return _enzyme_registry.get(name)


def list_enzymes() -> List[str]:
    """List all registered enzyme names."""
    return sorted(_enzyme_registry.keys())


def create_enzyme(name: str, config: Optional[dict] = None) -> Optional[Enzyme]:
    """Instantiate an enzyme by name with optional config."""
    cls = get_enzyme(name)
    if cls is None:
        return None
    return cls(config=config)


# --- Wait Enzyme (default, always activatable) --------------------------------

class WaitEnzyme(Enzyme):
    """
    Default enzyme. No strong signal detected, no actionable setup found.
    Keep watching. The market owes us nothing.
    """

    name = "Wait"
    enzyme_class = EnzymeClass.ISOMERASE
    priority = -1

    def can_activate(self, substrate: Substrate) -> bool:
        # Always activatable
        return True

    def transform(self, substrate: Substrate) -> Substrate:
        substrate.decisions["action"] = "wait"
        substrate._updated_at = Substrate._now_iso()
        self._log.info("Wait: no actionable signal, staying idle")
        return substrate

    def flux_score(self, substrate: Substrate) -> float:
        # Neutral -- only chosen when all other scores <= 0
        return 0.0