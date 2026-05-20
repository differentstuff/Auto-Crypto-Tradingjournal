"""
core/enzyme.py -- Enzyme base class for the reaction network.

Each enzyme has:
  - class: Sensor, Oxidoreductase, Regulator, Synthase, Transporter, Isomerase
  - activation conditions (requires/prohibits)
  - transform() method that modifies the substrate
  - flux_score() method measuring progress toward attractor
  - priority: Regulator enzymes always fire first

Based on: docs/reaction-design/enzyme-definitions.yaml

Condition syntax for requires()/prohibits():
  Paths must NOT include the "substrate." prefix -- the evaluator strips it.
  Examples:
    "strategy.name is set"
    "analysis.candidates not empty"
    "decisions.action == 'wait'"
    "analysis.noise_flag != 'true'"
    "market.indicators fresh"
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

    # Subclasses override these as class attributes
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

        The "substrate." prefix is stripped automatically so condition
        strings can be written either way:
            "substrate.strategy.name is set"  -- from enzyme-definitions.yaml
            "strategy.name is set"            -- shorthand in code

        Supported condition forms:
          "<path> is set"          -- value is not None/empty/0/False
          "<path> not empty"       -- list or dict is non-empty
          "<path> is empty"        -- list or dict is empty (or missing)
          "<path> is empty or stale" -- same as is empty (stale handled by subclass)
          "<path> == 'value'"      -- string equality
          "<path> != 'value'"      -- string inequality
          "<path> fresh"           -- TTL check (always True here; subclasses override)
        """
        # Strip "substrate." prefix so both forms work identically
        cond = condition.strip()
        if cond.startswith("substrate."):
            cond = cond[len("substrate."):]

        s = substrate

        if "is set" in cond:
            path = cond.replace(" is set", "").strip()
            val = s.get(path)
            return val is not None and val != "" and val != 0 and val != []

        if "not empty" in cond:
            path = cond.split("not empty")[0].strip()
            val = s.get(path)
            return bool(val)

        if "is empty or stale" in cond:
            path = cond.split("is empty or stale")[0].strip()
            val = s.get(path)
            return not bool(val)

        if "is empty" in cond:
            path = cond.split("is empty")[0].strip()
            val = s.get(path)
            return not bool(val)

        if "==" in cond:
            parts = cond.split("==")
            path = parts[0].strip()
            expected = parts[1].strip().strip("'\"")
            actual = s.get(path)
            return str(actual) == expected

        if "!=" in cond:
            parts = cond.split("!=")
            path = parts[0].strip()
            expected = parts[1].strip().strip("'\"")
            actual = s.get(path)
            return str(actual) != expected

        if "fresh" in cond:
            # TTL freshness checks are implemented in subclasses with
            # cache awareness. Default: assume fresh (do not block activation).
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
    """
    Decorator to register an enzyme class by name.

    Reads the name from the class attribute directly -- does NOT
    instantiate the class (which would require a config argument
    and create a throwaway object just to read a class-level string).
    """
    _enzyme_registry[cls.name] = cls
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


# --- WaitEnzyme has moved to enzymes/wait.py ---
# It is registered via @register_enzyme when enzymes/ is imported.
# This keeps the schema: every enzyme = one file in enzymes/.
