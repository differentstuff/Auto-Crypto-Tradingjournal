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

# Enzymes that execute trades — blocked by ISC gate when any ISC fails
_TRADE_ENZYMES = {"ApproveTrade", "ExecuteTrade", "ExecuteExit"}


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
        interval = self.config.get("strategy.cycle_interval_minutes")
        jitter = self.config.get("daemon.jitter_seconds")
        self.scheduler = Scheduler(interval_minutes=interval, jitter_seconds=jitter)

        # Check if strategy UID changed since last run (warning only)
        self._check_strategy_uid()

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

    def _check_strategy_uid(self) -> None:
        """
        Warn if strategy UID has changed since last run.

        The UID is the stable identity for all learning data. If it changes
        (e.g. manual YAML edit removed the uid field), existing learning
        data becomes orphaned. This check queries the DB for the last known
        UID and logs a warning if it differs from the current config.

        Non-critical: logs a warning but does not block startup.
        """
        current_uid = self.config.config.get("strategy", {}).get("uid", "")
        if not current_uid:
            return  # No UID yet — will be generated by ConfigLoader

        try:
            with db_conn() as conn:
                row = conn.execute(
                    """SELECT substrate_json FROM substrate_state
                       WHERE strategy_name = ?
                       ORDER BY id DESC LIMIT 1""",
                    (self.strategy_name,),
                ).fetchone()

                if row:
                    import json
                    state = json.loads(row["substrate_json"])
                    old_uid = state.get("strategy", {}).get("uid", "")
                    if old_uid and old_uid != current_uid:
                        _log.warning(
                            "Strategy UID changed: %s -> %s. "
                            "Learning data tied to the old UID may be orphaned.",
                            old_uid, current_uid,
                        )
        except Exception as e:
            _log.debug("Could not check strategy UID history: %s", e)

    # --- Attractor definitions ------------------------------------------------

    ATTRACTORS = {
        "watching": {
            "description": "Default state: no signal, portfolio loaded, indicators fresh",
            "terminal_actions": {"wait"},
        },
        "trade_opened": {
            "description": "New position created, entry complete",
            "terminal_actions": {"trade_open"},
        },
        "trade_managed": {
            "description": "Position monitored, exit request evaluated",
            "terminal_actions": {"manage"},
        },
        "trade_closed": {
            "description": "Position closed, outcome recorded",
            "terminal_actions": {"trade_closed"},
        },
        "learning_updated": {
            "description": "Cycle complete, learning data recorded",
            "terminal_actions": set(),  # Reached at end of cycle, not via action
        },
    }

    def _at_attractor(self, substrate: Substrate) -> bool:
        """
        Check if the substrate has reached an attractor state.

        An attractor is reached when the action matches a terminal state.
        The 'watching' attractor is special — it's reached when the Wait
        enzyme fires (action == 'wait').
        """
        action = substrate.decisions.get("action", "wait")
        for attr_name, attr_def in self.ATTRACTORS.items():
            if action in attr_def["terminal_actions"]:
                return True
        return False

    def _find_wait_enzyme(self) -> Optional:
        """Find the Wait enzyme from the registered enzymes list."""
        for e in self.enzymes:
            if e.name == "Wait":
                return e
        return None

    def _fire_wait(self, reason: str) -> None:
        """
        Explicitly fire the Wait enzyme with an idle reason.

        This is the daemon's fallback: when no other enzyme can activate
        or all flux scores are <= 0, we fire Wait instead of duplicating
        idle-cycle logic in the daemon.

        Uses shallow copy for the same safety guarantee as the main loop:
        if Wait raises, self.substrate remains unchanged.
        """
        wait = self._find_wait_enzyme()
        if wait is not None:
            wait.set_idle_reason(reason)
            _log.info("Firing Wait enzyme (reason: %s)", reason)
            substrate_copy = self.substrate.shallow_copy()
            try:
                self.substrate = wait.transform(substrate_copy)
            except Exception as e:
                _log.error("Wait enzyme failed: %s — using mark_idle() fallback", e)
                self.substrate.mark_idle(reason)
        else:
            # Fallback: no Wait enzyme registered (shouldn't happen in production)
            _log.warning("Wait enzyme not found — using substrate.mark_idle() fallback")
            self.substrate.mark_idle(reason)

    def run_cycle(self) -> Dict:
        """
        Run one cycle of the reaction network.

        1. Hot-reload config
        2. Reset per-cycle substrate fields
        3. Find activatable enzymes
        4. Fire the best one (regulators first)
        5. Verify ISC conditions after each step
        6. Check attractor state after each step
        7. Persist state
        8. Log cycle

        Returns dict with cycle results.
        """
        self.scheduler.start_cycle()
        cycle_start = time.time()

        # 1. Hot-reload config
        config_changed = self.config.reload()
        if config_changed:
            interval = self.config.get("strategy.cycle_interval_minutes")
            self.scheduler.update_interval(interval)
            # Refresh secrets-free config slice in substrate
            self.substrate._config = _strategy_config_slice(self.config.config)
            _log.info("Config reloaded, interval updated to %dm", interval)

        # 2. Reset per-cycle fields
        self.substrate.reset_cycle()

        # 3. Run the reaction network
        enzymes_fired = []
        isc_results = {}
        max_steps = self.config.get("daemon.max_cycle_steps")
        last_enzyme_name = None
        consecutive_count = 0
        fired_this_cycle = set()  # Prevent any enzyme from firing twice per cycle

        for step in range(max_steps):
            # Check if we've reached an attractor
            if self._at_attractor(self.substrate):
                break

            # Find activatable enzymes (excluding already-fired this cycle)
            activatable = [
                e for e in self.enzymes
                if e.can_activate(self.substrate) and e.name not in fired_this_cycle
            ]

            # ISC gate: if any ISC has failed, exclude trade-executing enzymes.
            # This is the enforcement point — ISC is not just audit, it's a hard gate.
            # Pending ISCs (not yet evaluated this cycle) do NOT block.
            if self.substrate.isc_blocks_trade():
                failed_ids = self.substrate.failed_isc_ids()
                trade_blocked = [e for e in activatable if e.name in _TRADE_ENZYMES]
                if trade_blocked:
                    _log.info(
                        "ISC gate: blocking trade enzymes %s — failed ISCs: %s",
                        [e.name for e in trade_blocked], failed_ids,
                    )
                activatable = [e for e in activatable if e.name not in _TRADE_ENZYMES]

            if not activatable:
                # No enzyme can fire -- fire Wait explicitly
                self._fire_wait("no enzyme can activate")
                enzymes_fired.append("Wait")
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
                    # No enzyme improves our position -- fire Wait explicitly
                    self._fire_wait("no enzyme improves position")
                    enzymes_fired.append("Wait")
                    break

                best = max(activatable, key=lambda e: scores.get(e, 0))

            # Consecutive-fire guard: if the same enzyme fires 3+ times
            # in a row, it's likely stuck in a loop. Break with warning.
            if best.name == last_enzyme_name:
                consecutive_count += 1
            else:
                consecutive_count = 1
                last_enzyme_name = best.name

            if consecutive_count >= 3:
                _log.warning(
                    "Enzyme %s fired %d times consecutively -- "
                    "likely loop, breaking cycle early",
                    best.name, consecutive_count,
                )
                self._fire_wait(
                    f"enzyme loop detected: {best.name} x{consecutive_count}"
                )
                enzymes_fired.append("Wait")
                break

            # Fire the selected enzyme
            #
            # SHALLOW-COPY SAFETY: We pass a shallow copy of the substrate to
            # transform(). The shallow copy has its own top-level dicts
            # (strategy, portfolio, market, etc.) but shares nested values
            # (lists, dicts inside those dicts). Enzymes must NOT mutate
            # nested values in-place; they must create new values and reassign
            # entire fields (e.g. substrate.portfolio["open_positions"] = new_list).
            #
            # If transform() raises an exception, self.substrate remains unchanged
            # because only the shallow copy's top-level dicts were modified —
            # the original substrate's dicts are separate objects.
            #
            # This is cheaper than deep copy (~1-2MB saved per cycle) and makes
            # the "no partial mutation" invariant explicit by design.
            #
            # LIMITATION: External side effects (exchange orders in ExecuteTrade/
            # ExecuteExit, DB writes in RecordTradeOutcome) are NOT rolled back.
            # If an exchange order succeeds but the substrate write fails, the
            # order remains on the exchange. This is acceptable because:
            #   1. Risk management (SL/TP) is set before or at order time.
            #   2. SyncPositions reconciles on the next cycle.
            #   3. ExecuteTrade/ExecuteExit should perform external operations
            #      BEFORE modifying the substrate (fail-fast on external calls).
            _log.info(
                "Step %d: firing %s (class=%s, priority=%d)",
                step,
                best.name,
                best.enzyme_class.value,
                best.priority,
            )
            substrate_copy = self.substrate.shallow_copy()
            try:
                self.substrate = best.transform(substrate_copy)
                enzymes_fired.append(best.name)
                fired_this_cycle.add(best.name)
            except Exception as e:
                _log.error(
                    "Enzyme %s failed in transform(): %s — substrate rolled back",
                    best.name, e, exc_info=True,
                )
                # self.substrate is unchanged (still the pre-copy version)
                # Do NOT add to enzymes_fired, do NOT update consecutive count
                # Continue to next enzyme selection
                continue

            # Consecutive-fire guard: if the same enzyme fires 3+ times
            # in a row, it's likely stuck in a loop. Break with warning.
            # Only counts SUCCESSFUL fires (failed enzymes don't count).

            # Verify ISC conditions after each step
            isc_results = self.substrate.verify_iscs()

        # If no enzymes were registered yet (Phase A), just log
        if not self.enzymes:
            _log.info("No enzymes registered yet (skeleton mode)")
            self._fire_wait("skeleton mode - no enzymes registered")
            isc_results = self.substrate.verify_iscs()

        # Persist substrate (using max_rows from config)
        max_rows = self.config.get("daemon.substrate_state_max_rows")
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

        # ── Post-cycle: Challenger branch (non-blocking) ──────────────────
        if self.config.get("challenger.enabled", False):
            try:
                self._run_challenger_branch()
            except Exception as e:
                _log.error(
                    "Challenger branch failed (production unaffected): %s",
                    e, exc_info=True,
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

    def _run_challenger_branch(self) -> None:
        """Run the challenger paper-trading branch after the production cycle.

        This method runs AFTER the production cycle is complete and state is
        persisted. It uses the same market data as production but scores with
        challenger weights. All operations are non-blocking — if anything
        fails, production is completely unaffected.

        Flow:
          1. If no active challenger, try to activate next from queue
          2. Run hypothetical tracker cycle (exits + entries)
          3. Evaluate challenger (promote / discard / accumulating)
          4. If resolved, try to activate next from queue
        """
        from learning.challenger import WeightChallenger, CandidateQueue
        from learning.hypothetical_tracker import HypotheticalTracker
        from learning.comparator import ChallengerComparator

        challenger = self.substrate.learning.get("challenger", {})
        challenger_weights = challenger.get("weights")

        # Step 1: If no active challenger, try to activate one from the queue
        if not challenger_weights:
            activated = WeightChallenger.activate_next_candidate(self.substrate)
            if not activated:
                return  # No candidates in queue
            challenger_weights = self.substrate.learning["challenger"]["weights"]

        # Step 2: Run hypothetical tracker cycle
        HypotheticalTracker.run_cycle(self.substrate, challenger_weights)

        # Step 3: Evaluate challenger
        verdict = ChallengerComparator.evaluate(self.substrate)

        if verdict == "promote":
            metrics = ChallengerComparator.get_metrics(self.substrate)
            WeightChallenger.promote(self.substrate, "profit_factor_improvement", metrics)

            # Try to activate next candidate from queue
            WeightChallenger.activate_next_candidate(self.substrate)

        elif verdict == "discard":
            metrics = ChallengerComparator.get_metrics(self.substrate)
            WeightChallenger.discard(self.substrate, "insufficient_improvement", metrics)

            # Try to activate next candidate from queue
            WeightChallenger.activate_next_candidate(self.substrate)

        # "accumulating" — do nothing, keep collecting data

    def register_enzyme(self, enzyme) -> None:
        """Register an enzyme with the daemon."""
        self.enzymes.append(enzyme)
        _log.info("Registered enzyme: %s (class=%s)", enzyme.name, enzyme.enzyme_class.value)

    def register_enzymes(self, enzymes: list) -> None:
        """Register multiple enzymes."""
        for e in enzymes:
            self.register_enzyme(e)
