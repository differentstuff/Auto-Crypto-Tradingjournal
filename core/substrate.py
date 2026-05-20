"""
core/substrate.py -- Shared state container for the reaction network.

All enzymes read from and write to this single substrate.
Each enzyme modifies only its designated output fields.
The substrate persists across cycles and is stored in the database.

Based on: docs/reaction-design/substrate-schema.yaml
"""

from __future__ import annotations

import copy
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

_log = logging.getLogger(__name__)


class ISCCheck:
    """A single hard-to-vary condition that must be verified before actions."""

    def __init__(self, isc_id: str, criterion: str, verification: str):
        self.id = isc_id
        self.criterion = criterion
        self.verification = verification
        self.status: str = "pending"  # "pending" | "verified" | "failed"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "criterion": self.criterion,
            "verification": self.verification,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ISCCheck":
        isc = cls(d["id"], d["criterion"], d["verification"])
        isc.status = d.get("status", "pending")
        return isc


class Substrate:
    """
    Single shared state container for all enzymes.

    Provides dot-access to nested structures while keeping everything
    serializable. Enzymes read from and write to this object.

    The substrate holds a reference to the full config dict so that
    ISC checks and enzymes can look up config values (scoring thresholds,
    risk limits, etc.) that are not part of the substrate state itself.

    Sections:
      strategy   - strategy identity and config
      portfolio   - account state and positions
      market      - market data (updated by Sensor enzymes)
      analysis    - analysis results (updated by Oxidoreductase enzymes)
      decisions   - decision state (updated by Regulator enzymes)
      learning    - learning state (updated by Synthase enzymes)
      validity    - ISC conditions (hard-to-vary constraints)
    """

    # Default ISC conditions (from substrate-schema.yaml)
    DEFAULT_ISCS = [
        {
            "id": "ISC-001",
            "criterion": "entry_threshold met before any trade opens",
            "verification": "analysis.candidates not empty AND score >= threshold",
        },
        {
            "id": "ISC-002",
            "criterion": "stop loss always set before position opens",
            "verification": "decisions.trade_approved.sl_price > 0",
        },
        {
            "id": "ISC-003",
            "criterion": "position size within risk limit",
            "verification": "trade_approved.size_usdt <= equity * risk_per_trade_pct / 100",
        },
        {
            "id": "ISC-004",
            "criterion": "max concurrent positions not exceeded",
            "verification": "portfolio.open_positions count < strategy.max_positions",
        },
        {
            "id": "ISC-005",
            "criterion": "no trade when noise_flag is true",
            "verification": "analysis.noise_flag == false OR decisions.action == 'wait'",
        },
        {
            "id": "ISC-006",
            "criterion": "confluence minimum signals aligned",
            "verification": "candidate.indicators_aligned >= strategy.confluence_min_signals",
        },
        {
            "id": "ISC-007",
            "criterion": "pre_trade trajectory not sudden coincidence",
            "verification": "pre_trade_context.coincidence_risk != 'high'",
        },
    ]

    def __init__(self, config: Optional[Dict] = None):
        """Initialize substrate from config dict (from config loader)."""
        now = self._now_iso()
        cfg = config or {}

        # Store config reference for ISC lookups and enzyme access
        self._config: Dict = cfg

        # Strategy section
        strategy_cfg = cfg.get("strategy", {})
        self.strategy = {
            "name": strategy_cfg.get("name", ""),
            "description": cfg.get("description", ""),
            "timeframe": strategy_cfg.get("timeframe", "4H"),
            "confirmation_tf": strategy_cfg.get("confirmation_tf", "1H"),
            "cycle_interval_minutes": strategy_cfg.get(
                "cycle_interval_minutes", 15
            ),
            "max_positions": strategy_cfg.get("max_positions", 3),
            "last_loaded_at": now,
        }

        # Portfolio section
        portfolio_cfg = cfg.get("portfolio", {})
        self.portfolio = {
            "equity": 0.0,
            "available_margin": 0.0,
            "open_positions": [],
            "max_positions": portfolio_cfg.get("max_positions", 3),
            "risk_per_trade_pct": portfolio_cfg.get("risk_per_trade_pct", 1.0),
            "leverage": portfolio_cfg.get("leverage", 5),
            "max_total_risk_pct": portfolio_cfg.get("max_total_risk_pct", 3.0),
            "fallback_equity_usdt": portfolio_cfg.get("fallback_equity_usdt", 1000.0),
            "correlation_check": portfolio_cfg.get("correlation_check", True),
            "total_risk_exposure_pct": 0.0,
            "correlation_matrix": {},
        }

        # Market section (populated by Sensor enzymes)
        symbols_cfg = cfg.get("symbols", {})
        self.market = {
            "symbols_watched": symbols_cfg.get("always_watch", []),
            "last_scan_at": "",
            "indicators": {},
            "pre_trade_context": {},
            "macro": {},
            "liquidations": {},
            "onchain": {},
            "sentiment": {},
        }

        # Analysis section (populated by Oxidoreductase enzymes)
        self.analysis = {
            "candidates": [],
            "entry_zones": {},
            "noise_flag": False,
            "noise_reason": "",
            "signal_states": {},
        }

        # Decisions section (populated by Regulator enzymes)
        self.decisions = {
            "action": "wait",
            "trade_approved": None,
            "exit_request": None,
            "exit_approved": None,
            "exit_reason": "",
        }

        # Learning section (populated by Synthase enzymes)
        learning_cfg = cfg.get("learning", {})
        self.learning = {
            "idle_cycles": 0,
            "idle_reasons": [],
            "rulebook": "",
            "rulebook_generated_at": "",
            "signal_accuracy": {},
            "combination_accuracy": {},
            "suppressed_signals": [],
            "highlight_signals": [],
            "total_trades_recorded": 0,
            "total_idle_cycles_recorded": 0,
            "last_retrain_at": "",
        }

        # Validity section (ISC conditions)
        self.validity = [
            ISCCheck.from_dict(isc) for isc in self.DEFAULT_ISCS
        ]
        self.pending = []

        # Internal metadata
        self._cycle_count = 0
        self._created_at = now
        self._updated_at = now

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    # --- Config access --------------------------------------------------------

    @property
    def config(self) -> Dict:
        """Return the full config dict (from ConfigLoader)."""
        return self._config

    def cfg(self, dotted_path: str, default: Any = None) -> Any:
        """
        Get a value from the *config* (not substrate state) by dotted path.

        This is for ISC checks and enzymes that need config values like
        scoring thresholds, risk limits, etc. that are not stored in
        substrate state but in the strategy YAML.
        """
        parts = dotted_path.split(".")
        obj = self._config
        for part in parts:
            if isinstance(obj, dict):
                obj = obj.get(part)
            else:
                return default
            if obj is None:
                return default
        return obj

    # --- Dot-access helpers ---------------------------------------------------

    def get(self, dotted_path: str, default: Any = None) -> Any:
        """
        Get a value by dotted path from substrate state.

        Searches substrate dicts first. Falls back to config if not found
        in substrate state. This allows enzymes and ISC checks to transparently
        access both state and config values.

        Examples:
            substrate.get("strategy.name")  -> "momentum_rising"
            substrate.get("scoring.entry_threshold")  -> 6.5 (from config)
            substrate.get("portfolio.equity")  -> 0.0 (from state)
        """
        # First try substrate state
        parts = dotted_path.split(".")
        obj = self
        for part in parts:
            if isinstance(obj, dict):
                obj = obj.get(part)
            elif hasattr(obj, part):
                obj = getattr(obj, part)
            else:
                obj = None
                break
            if obj is None:
                break

        if obj is not None:
            return obj

        # Fall back to config
        return self.cfg(dotted_path, default)

    def set(self, dotted_path: str, value: Any) -> None:
        """Set a value by dotted path in substrate state, e.g. 'decisions.action', 'wait'."""
        parts = dotted_path.split(".")
        obj = self
        for part in parts[:-1]:
            if isinstance(obj, dict):
                obj = obj.setdefault(part, {})
            elif hasattr(obj, part):
                obj = getattr(obj, part)
            else:
                raise KeyError(f"Cannot traverse {part} in {dotted_path}")
        last = parts[-1]
        if isinstance(obj, dict):
            obj[last] = value
        elif hasattr(obj, last):
            setattr(obj, last, value)
        else:
            raise KeyError(f"Cannot set {last} in {dotted_path}")
        self._updated_at = self._now_iso()

    # --- ISC verification -----------------------------------------------------

    def verify_iscs(self) -> Dict[str, str]:
        """
        Verify all ISC conditions. Returns dict of {isc_id: status}.
        Updates each ISCCheck.status and self.pending list.
        """
        results = {}
        self.pending = []

        for isc in self.validity:
            try:
                verified = self._evaluate_isc(isc)
                isc.status = "verified" if verified else "failed"
            except Exception as e:
                _log.warning("ISC %s evaluation error: %s", isc.id, e)
                isc.status = "failed"

            results[isc.id] = isc.status
            if isc.status == "pending":
                self.pending.append(isc.id)

        return results

    def _evaluate_isc(self, isc: ISCCheck) -> bool:
        """Evaluate a single ISC condition against substrate state + config."""
        vid = isc.id

        if vid == "ISC-001":
            # entry_threshold met before any trade opens
            candidates = self.analysis.get("candidates", [])
            if not candidates:
                return False
            # Look up threshold from config (scoring.entry_threshold)
            threshold = self.cfg("scoring.entry_threshold", 6.5)
            return any(
                c.get("score", 0) >= threshold for c in candidates
            )

        elif vid == "ISC-002":
            # stop loss always set before position opens
            approved = self.decisions.get("trade_approved")
            if approved is None:
                return True  # no trade pending, condition vacuously true
            return approved.get("sl_price", 0) > 0

        elif vid == "ISC-003":
            # position size within risk limit
            approved = self.decisions.get("trade_approved")
            if approved is None:
                return True
            equity = self.portfolio.get("equity", 0)
            # risk_per_trade_pct from portfolio state (set from config)
            risk_pct = self.portfolio.get("risk_per_trade_pct", 1.0)
            max_size = equity * risk_pct / 100
            return approved.get("size_usdt", 0) <= max_size

        elif vid == "ISC-004":
            # max concurrent positions not exceeded
            n_open = len(self.portfolio.get("open_positions", []))
            max_pos = self.strategy.get("max_positions", 3)
            return n_open < max_pos

        elif vid == "ISC-005":
            # no trade when noise_flag is true
            noise = self.analysis.get("noise_flag", False)
            action = self.decisions.get("action", "wait")
            return (not noise) or (action == "wait")

        elif vid == "ISC-006":
            # confluence minimum signals aligned
            candidates = self.analysis.get("candidates", [])
            # Look up from config (scoring.confluence_min_signals)
            min_signals = self.cfg("scoring.confluence_min_signals", 3)
            return all(
                c.get("indicators_aligned", 0) >= min_signals
                for c in candidates
            )

        elif vid == "ISC-007":
            # pre_trade trajectory not sudden coincidence
            ptc = self.market.get("pre_trade_context", {})
            for symbol, ctx in ptc.items():
                if ctx.get("coincidence_risk") == "high":
                    return False
            return True

        return True  # unknown ISC, pass by default

    def all_iscs_pass(self) -> bool:
        """Check if all ISC conditions are verified (or vacuously true)."""
        results = self.verify_iscs()
        return all(s == "verified" for s in results.values())

    # --- Reset helpers --------------------------------------------------------

    def reset_cycle(self) -> None:
        """Reset per-cycle fields for a new daemon cycle."""
        self.analysis["candidates"] = []
        self.analysis["entry_zones"] = {}
        self.analysis["noise_flag"] = False
        self.analysis["noise_reason"] = ""
        self.analysis["signal_states"] = {}
        self.decisions["action"] = "wait"
        self.decisions["trade_approved"] = None
        self.decisions["exit_request"] = None
        self.decisions["exit_approved"] = None
        self.decisions["exit_reason"] = ""
        self.learning["idle_reasons"] = []

        # Reset ISC statuses to pending
        for isc in self.validity:
            isc.status = "pending"
        self.pending = [isc.id for isc in self.validity]

        self._cycle_count += 1
        self._updated_at = self._now_iso()

    def mark_idle(self, reason: str) -> None:
        """Record an idle cycle with reason."""
        self.decisions["action"] = "wait"
        self.learning["idle_cycles"] += 1
        self.learning["idle_reasons"].append(reason)
        self.learning["total_idle_cycles_recorded"] += 1

    # --- Serialization --------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialize substrate to dict for database storage."""
        return {
            "strategy": copy.deepcopy(self.strategy),
            "portfolio": copy.deepcopy(self.portfolio),
            "market": copy.deepcopy(self.market),
            "analysis": copy.deepcopy(self.analysis),
            "decisions": copy.deepcopy(self.decisions),
            "learning": copy.deepcopy(self.learning),
            "validity": [isc.to_dict() for isc in self.validity],
            "pending": list(self.pending),
            "_cycle_count": self._cycle_count,
            "_created_at": self._created_at,
            "_updated_at": self._updated_at,
        }

    def to_json(self) -> str:
        """Serialize substrate to JSON string."""
        return json.dumps(self.to_dict(), default=str, indent=2)

    @classmethod
    def from_dict(cls, d: dict, config: Optional[Dict] = None) -> "Substrate":
        """Reconstruct substrate from dict (e.g. from database)."""
        sub = cls(config=config)
        sub.strategy = d.get("strategy", sub.strategy)
        sub.portfolio = d.get("portfolio", sub.portfolio)
        sub.market = d.get("market", sub.market)
        sub.analysis = d.get("analysis", sub.analysis)
        sub.decisions = d.get("decisions", sub.decisions)
        sub.learning = d.get("learning", sub.learning)
        sub.validity = [
            ISCCheck.from_dict(isc)
            for isc in d.get("validity", cls.DEFAULT_ISCS)
        ]
        sub.pending = d.get("pending", [])
        sub._cycle_count = d.get("_cycle_count", 0)
        sub._created_at = d.get("_created_at", sub._created_at)
        sub._updated_at = d.get("_updated_at", sub._updated_at)
        return sub

    @classmethod
    def from_json(cls, json_str: str, config: Optional[Dict] = None) -> "Substrate":
        """Reconstruct substrate from JSON string."""
        return cls.from_dict(json.loads(json_str), config=config)

    def __repr__(self) -> str:
        action = self.decisions.get("action", "wait")
        n_pos = len(self.portfolio.get("open_positions", []))
        n_cand = len(self.analysis.get("candidates", []))
        return (
            f"Substrate(strategy={self.strategy.get('name', '?')}, "
            f"action={action}, positions={n_pos}, "
            f"candidates={n_cand}, cycle={self._cycle_count})"
        )