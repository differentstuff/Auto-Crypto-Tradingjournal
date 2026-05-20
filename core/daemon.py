"""
core/daemon.py -- 24/7 daemon loop for the reaction network.

The daemon:
  1. Loads config (hot-reload on every cycle)
  2. Builds a secrets-free strategy config slice for the substrate
  3. Initializes substrate from config (or restores from DB)
  4. Runs the reaction network (find activatable enzymes, fire best one)
  5. Persists substrate state to database
  6. Sleeps until next cycle

Security note:
    The substrate receives only the strategy-safe config slice (thresholds,
    risk limits, ISC definitions, indicator weights). Exchange credentials
    and LLM API keys are stripped before passing config to the substrate.
    Enzymes that need credentials receive the full ConfigLoader reference
    directly from the daemon, not via the substrate.

Based on: docs/reaction-design/README.md execution loop
"""

from __future__ import annotations

import logging
import signal
import sys
import time
from typing import Dict, List, Optional

from core.config_loader import ConfigLoader
from core.database import init_db, save_substrate, save_cycle_log, db_conn, load_latest_substrate
from core.scheduler import Scheduler
from core.substrate import Substrate

_log = logging.getLogger(__name__)

# Config keys that contain secrets -- stripped before passing to substrate
_SECRET_KEYS = {"exchange", "llm_keys"}


def _strategy_config_slice(full_config: dict) -> dict:
    """
    Return a copy of the config with all secret keys removed.

    The substrate only needs strategy-level config (thresholds, risk limits,
    ISC definitions, indicator weights, module toggles). Exchange credentials
    and LLM API keys must never be stored on the substrate object.
    """
    return {k: v for k, v in full_config.items() if k not in _SECRET_KEYS}


class Daemon:
    """
    24/7 reaction network daemon.

    Runs cycles of: load config -> init substrate -> run network -> persist state -> sleep.
    """

    def __init__(
        self,
        strategy_name: str = "momentum_rising",
        paper_mode: bool = False,
        config_dir: Optional[str] = None,
    ):
        self.strategy_name = strategy_name
        self.paper_mode = paper_mode
        self._config_dir = config_dir  # None = use default project config/
        self.config: Optional[ConfigLoader] = None
        self.substrate: Optional[Substrate] = None
        self.scheduler: Optional[Scheduler] = None
        self.enzymes: List = []  # Will be populated in Phase B
        self._running = False
        self._shutdown_requested = False

    def initialize(self) -> None:
        """Initialize database, config, substrate, scheduler."""
        _log.info("Initializing daemon with strategy: %s", self.strategy_name)

        # Initialize database (creates tables if needed)
        init_db()

        # Load configuration (config_dir=None uses default project config/)
        self.config = ConfigLoader(
            strategy_name=self.strategy_name,
            config_dir=self._config_dir,
        )

        # Override paper mode if specified on command line
        if self.paper_mode:
            self.config.config.setdefault("daemon", {})["paper_mode"] = True

        # Initialize substrate from config (or restore from DB)
        self._init_substrate()

        # Initialize scheduler
        interval = self.config.get("strategy.cycle_interval_minutes", 15)
        self.scheduler = Scheduler(interval_minutes=interval)

        # Register shutdown handlers
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)

        _log.info(
            "Daemon initialized: strategy=%s, paper=%s, interval=%dm",
            self.strategy_name,
            self.paper_mode,
            interval,
        )

    def _init_substrate(self) -> None:
        """Initialize substrate from config or restore from database."""
        # Build secrets-free config slice for the substrate
        safe_config = _strategy_config_slice(self.config.config)

        last_state = load_latest_substrate(self.strategy_name)
        if last_state:
            _log.info("Restoring substrate from database")
            self.substrate = Substrate.from_persistent_dict(last_state, config=safe_config)
        else:
            _log.info("Creating new substrate from config")
            self.substrate = Substrate(config=safe_config)

        _log.info("Substrate: %s", self.substrate)

    def run_cycle(self) -> Dict:
        """
        Run one cycle of the reaction network.

        1. Hot-reload config
        2. Reset per-cycle substrate fields
        3. Find activatable enzymes
        4. Fire the best one (regulators first)
        5. Verify ISC conditions after each step
        6. Persist state
        7. Log cycle

        Returns dict with cycle results.
        """
        self.scheduler.start_cycle()
        cycle_start = time.time()

        # 1. Hot-reload config
        config_changed = self.config.reload()
        if config_changed:
            interval = self.config.get("strategy.cycle_interval_minutes", 15)
            self.scheduler.update_interval(interval)
            # Refresh secrets-free config slice in substrate
            self.substrate._config = _strategy_config_slice(self.config.config)
            _log.info("Config reloaded, interval updated to %dm", interval)

        # 2. Reset per-cycle fields
        self.substrate.reset_cycle()

        # 3. Run the reaction network
        enzymes_fired = []
        isc_results = {}
        max_steps = self.config.get("daemon.max_cycle_steps", 20)

        for step in range(max_steps):
            # Find activatable enzymes
            activatable = [
                e for e in self.enzymes if e.can_activate(self.substrate)
            ]

            if not activatable:
                # No enzyme can fire -- idle cycle
                self.substrate.mark_idle("no enzyme can activate")
                break

            # Regulators always have priority
            regulators = [e for e in activatable if e.is_regulator]
            if regulators:
                # Fire regulators in priority order
                regulators.sort(key=lambda e: e.priority, reverse=True)
                best = regulators[0]
            else:
                # Calculate flux scores (progress toward attractor)
                scores = {
                    e: e.flux_score(self.substrate) for e in activatable
                }
                max_score = max(scores.values()) if scores else 0

                if max_score <= 0:
                    # No enzyme improves our position -- wait
                    self.substrate.mark_idle("no enzyme improves position")
                    break

                best = max(activatable, key=lambda e: scores.get(e, 0))

            # Fire the selected enzyme
            _log.info(
                "Step %d: firing %s (class=%s, priority=%d)",
                step,
                best.name,
                best.enzyme_class.value,
                best.priority,
            )
            self.substrate = best.transform(self.substrate)
            enzymes_fired.append(best.name)

            # Verify ISC conditions after each step
            isc_results = self.substrate.verify_iscs()

            # Check if we've reached a terminal state
            action = self.substrate.decisions.get("action", "wait")
            if action in ("enter", "exit", "manage", "halt_all"):
                break

        # If no enzymes were registered yet (Phase A), just log
        if not self.enzymes:
            _log.info("No enzymes registered yet (skeleton mode)")
            self.substrate.mark_idle("skeleton mode - no enzymes registered")
            isc_results = self.substrate.verify_iscs()

        # Persist substrate (using max_rows from config)
        max_rows = self.config.get("daemon.substrate_state_max_rows", 200)
        save_substrate(self.substrate, max_rows=max_rows)

        # Log cycle
        cycle_end = time.time()
        duration_ms = int((cycle_end - cycle_start) * 1000)

        save_cycle_log(
            strategy_name=self.strategy_name,
            cycle_count=self.scheduler.cycle_count,
            action=self.substrate.decisions.get("action", "wait"),
            enzymes_fired=enzymes_fired,
            isc_results=isc_results,
            duration_ms=duration_ms,
        )

        self.scheduler.end_cycle()

        _log.info(
            "Cycle %d complete: action=%s, enzymes=%s, duration=%dms",
            self.scheduler.cycle_count,
            self.substrate.decisions.get("action", "wait"),
            enzymes_fired or ["none"],
            duration_ms,
        )

        return {
            "cycle": self.scheduler.cycle_count,
            "action": self.substrate.decisions.get("action", "wait"),
            "enzymes_fired": enzymes_fired,
            "isc_results": isc_results,
            "duration_ms": duration_ms,
        }

    def run(self) -> None:
        """Main daemon loop: run cycles forever."""
        self._running = True
        _log.info("Daemon starting (strategy=%s, paper=%s)", self.strategy_name, self.paper_mode)

        while self._running and not self._shutdown_requested:
            try:
                self.run_cycle()
            except Exception as e:
                _log.error("Cycle error: %s", e, exc_info=True)

            if self._shutdown_requested:
                break

            self.scheduler.sleep_until_next_cycle()

        _log.info("Daemon stopped after %d cycles", self.scheduler.cycle_count)

    def _handle_shutdown(self, signum, frame) -> None:
        """Handle SIGTERM/SIGINT for graceful shutdown."""
        _log.info("Shutdown signal received (signum=%d)", signum)
        self._shutdown_requested = True
        self._running = False
        self.scheduler.stop()

    def register_enzyme(self, enzyme) -> None:
        """Register an enzyme with the daemon."""
        self.enzymes.append(enzyme)
        _log.info("Registered enzyme: %s (class=%s)", enzyme.name, enzyme.enzyme_class.value)

    def register_enzymes(self, enzymes: list) -> None:
        """Register multiple enzymes."""
        for e in enzymes:
            self.register_enzyme(e)
