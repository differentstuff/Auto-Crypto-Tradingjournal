"""
core/daemon.py -- 24/7 daemon loop for the reaction network.

The daemon:
  1. Loads config (hot-reload on every cycle)
  2. Builds a secrets-free strategy config slice for the substrate
  3. Initializes substrate from config (ALWAYS fresh — no DB restore)
  4. Reconciles positions from exchange (live mode only)
  5. Runs the reaction network (find activatable enzymes, fire best one)
  6. Pushes trailing stop updates to exchange (live mode only)
  7. Sleeps until next cycle

Exchange-as-truth architecture:
  - Substrate is ALWAYS built fresh — never loaded from DB
  - Positions are reconciled from exchange on startup AND every cycle
  - SL/TP are pushed to exchange at trade open
  - Trailing stop updates are pushed to exchange when they change
  - Paper mode: positions are runtime-only, no exchange calls
  - Daemon aborts if exchange is unreachable on startup (live mode)

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
from core.enzyme import Enzyme
from core.substrate import Substrate
from core.database import init_db, db_conn

_log = logging.getLogger(__name__)

# Config keys that contain secrets -- stripped before passing to substrate
_SECRET_KEYS = {"llm_keys"}

# Enzymes that open new trades — blocked by ISC gate when any ISC fails.
# NOTE: ExecuteExit is intentionally excluded — ISC must never block exits,
# only entries. Blocking exits is dangerous (can't close losing positions).
_ENTRY_ENZYMES = {"ApproveTrade", "ExecuteTrade"}


def _strategy_config_slice(full_config: dict) -> dict:
    """
    Return a copy of the config with all secret keys removed.

    The substrate only needs strategy-level config (thresholds, risk limits,
    ISC definitions, indicator weights). Exchange credentials and LLM API
    keys must never be stored on the substrate object.
    """
    return {k: v for k, v in full_config.items() if k not in _SECRET_KEYS}


class Daemon:
    """
    24/7 reaction network daemon.

    Exchange-as-truth: substrate is always fresh, positions reconciled from exchange.
    """

    def __init__(
        self,
        strategy_name: str = "momentum_rising",
        paper_mode: bool = False,
        config_dir: Optional[str] = None,
        replay_mode: bool = False,
    ):
        self.strategy_name = strategy_name
        self.paper_mode = paper_mode
        self.replay_mode = replay_mode  # NEW: skip DB/persistence/learning in replay
        self._config_dir = config_dir  # None = use default project config/
        self.config: Optional[ConfigLoader] = None
        self.substrate: Optional[Substrate] = None
        self.scheduler = None
        self.enzymes: List = []
        self._running = False
        self._shutdown_requested = False
        self.exchange = None  # Set by main.py after initialization

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

        # Initialize substrate — ALWAYS fresh (exchange-as-truth)
        self._init_substrate()

        # Load learning data from DB into substrate cache
        self._load_learning_from_db()

        # Initialize scheduler
        interval = self.config.get("strategy.cycle_interval_minutes")
        jitter = self.config.get("daemon.jitter_seconds")
        from core.scheduler import Scheduler
        self.scheduler = Scheduler(interval_minutes=interval, jitter_seconds=jitter)

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
        """
        Initialize substrate — ALWAYS fresh, never from DB.

        Exchange-as-truth: the substrate is a cache of exchange state.
        On startup, we build a fresh substrate and reconcile positions
        from the exchange (live mode only). Paper mode positions are
        runtime-only — they don't persist across restarts.
        """
        # Build secrets-free config slice for the substrate
        safe_config = _strategy_config_slice(self.config.config)

        # ALWAYS build fresh substrate — never load from DB
        _log.info("Creating fresh substrate (exchange-as-truth)")
        self.substrate = Substrate(config=safe_config)

        _log.info("Substrate: %s", self.substrate)

    def _load_learning_from_db(self) -> None:
        """
        Load learning data from DB into substrate.learning cache.

        Exchange-as-truth: substrate is ephemeral (rebuilt fresh every startup).
        Learning data persists in dedicated DB tables, not in substrate.
        On startup, we load from DB into substrate.learning so that
        ScoreConfluence and other enzymes can use adjusted weights/thresholds
        immediately, without waiting for the first trade close to trigger UpdateLearning.
        """
        if self.replay_mode:
            return

        try:
            from core.database import db_conn
            strategy_uid = self.substrate.strategy.get("uid", "legacy")

            with db_conn() as conn:
                # Load adjusted_weights
                rows = conn.execute(
                    "SELECT indicator_name, weight FROM adjusted_weights WHERE strategy_uid = ?",
                    (strategy_uid,),
                ).fetchall()
                if rows:
                    self.substrate.learning["adjusted_weights"] = {
                        r["indicator_name"]: r["weight"] for r in rows
                    }
                    _log.info("Loaded %d adjusted weights from DB", len(rows))

                # Load adjusted_thresholds
                rows = conn.execute(
                    "SELECT threshold_name, value FROM adjusted_thresholds WHERE strategy_uid = ?",
                    (strategy_uid,),
                ).fetchall()
                if rows:
                    self.substrate.learning["adjusted_thresholds"] = {
                        r["threshold_name"]: r["value"] for r in rows
                    }
                    _log.info("Loaded %d adjusted thresholds from DB", len(rows))

                # Load suppressed_signals
                rows = conn.execute(
                    "SELECT indicator_name, reason FROM suppressed_signals WHERE strategy_uid = ?",
                    (strategy_uid,),
                ).fetchall()
                if rows:
                    self.substrate.learning["suppressed_signals"] = [
                        {"name": r["indicator_name"], "reason": r["reason"]} for r in rows
                    ]
                    _log.info("Loaded %d suppressed signals from DB", len(rows))

                # Load highlight_signals
                rows = conn.execute(
                    "SELECT indicator_name, reason FROM highlight_signals WHERE strategy_uid = ?",
                    (strategy_uid,),
                ).fetchall()
                if rows:
                    self.substrate.learning["highlight_signals"] = [
                        {"name": r["indicator_name"], "reason": r["reason"]} for r in rows
                    ]
                    _log.info("Loaded %d highlight signals from DB", len(rows))

                # Load challenger_state
                row = conn.execute(
                    "SELECT state_json FROM challenger_state WHERE strategy_uid = ?",
                    (strategy_uid,),
                ).fetchone()
                if row:
                    import json
                    challenger_state = json.loads(row["state_json"])
                    self.substrate.learning["challenger"] = challenger_state
                    _log.info("Loaded challenger state from DB")

                # Load trade count
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM trade_learning WHERE strategy_uid = ? AND exit_time IS NOT NULL",
                    (strategy_uid,),
                ).fetchone()
                if row:
                    self.substrate.learning["total_trades_recorded"] = row["cnt"]

                # Load latest rulebook
                row = conn.execute(
                    "SELECT rulebook_text, version FROM rulebook_versions WHERE strategy_uid = ? ORDER BY id DESC LIMIT 1",
                    (strategy_uid,),
                ).fetchone()
                if row:
                    self.substrate.learning["rulebook"] = row["rulebook_text"]
                    self.substrate.learning["rulebook_version"] = row["version"]

        except Exception as e:
            _log.warning("Could not load learning data from DB: %s (starting with empty cache)", e)

    def reconcile_from_exchange(self) -> None:
        """
        Rebuild substrate.portfolio.open_positions from exchange data.

        LIVE MODE ONLY. Paper mode positions are runtime-only.

        This method is called:
          1. On startup (after exchange is initialized)
          2. Every cycle (after SyncPositions runs)

        For each position on the exchange:
          - Rebuild position dict from exchange data
          - Recalculate TP1/TP2 from strategy config + entry price + stored atr_pct
          - Determine tp1_taken from achievedProfits > 0
          - Determine trailing state from exchange SL vs. original SL
          - Store exchange order IDs for modify-tpsl-order calls

        If exchange is unreachable on startup, the daemon aborts.
        During cycle execution, reconciliation failures are logged but
        substrate state is preserved (next successful cycle will reconcile).
        """
        if self.paper_mode or self.replay_mode:
            return  # Paper mode: positions are runtime-only

        if self.exchange is None:
            _log.warning("No Exchange instance — cannot reconcile")
            return

        try:
            exchange_positions = self.exchange.fetch_positions()
        except Exception as e:
            _log.error("Exchange fetch failed during reconciliation: %s", e)
            return

        # Get balance
        try:
            balance = self.exchange.fetch_balance()
            if balance:
                self.substrate.portfolio["equity"] = balance.get("equity", 0)
                self.substrate.portfolio["available_margin"] = balance.get("available", 0)
        except Exception as e:
            _log.warning("Balance fetch failed during reconciliation: %s", e)

        # Load position metadata from DB (atr_pct for TP1/TP2 recalculation)
        position_meta = self._load_position_metadata()

        # Rebuild positions from exchange data
        rebuilt_positions = []
        for ex_pos in exchange_positions:
            symbol = ex_pos.get("symbol", "")
            direction = ex_pos.get("direction", "Long")
            entry_price = ex_pos.get("entry_price", 0)
            mark_price = ex_pos.get("mark_price", 0)
            size_usdt = ex_pos.get("size_usdt", 0)
            pos_id = ex_pos.get("pos_id", "")
            achieved_profits = ex_pos.get("achieved_profits", 0)
            ex_sl = ex_pos.get("sl_price", 0)
            ex_tp = ex_pos.get("tp_price", 0)
            sl_order_id = ex_pos.get("sl_order_id", "")
            tp_order_id = ex_pos.get("tp_order_id", "")

            # Look up stored metadata for this position
            meta = position_meta.get(f"{symbol}:{direction}:{entry_price:.2f}", {})

            # Recalculate TP1/TP2 from config + entry price + stored atr_pct
            atr_value = meta.get("atr_value", 0)
            atr_pct = meta.get("atr_pct", 0)
            original_sl = meta.get("sl_price", 0)
            original_tp1 = meta.get("tp1", 0)
            original_tp2 = meta.get("tp2", 0)
            original_size = meta.get("size_usdt", 0)

            # If we have stored TP levels, use them. Otherwise, recalculate.
            tp1 = original_tp1
            tp2 = original_tp2
            sl_price = original_sl if original_sl else ex_sl

            if not tp1 and atr_value and atr_pct:
                # Recalculate from config
                tp1, tp2, sl_price = self._recalc_tp_from_config(
                    direction, entry_price, atr_value, atr_pct
                )

            # Determine TP1 status from exchange (system is sole actor)
            tp1_taken = achieved_profits > 0

            # Activate native trailing stop on exchange if TP1 just detected
            native_trail_order_id = meta.get("native_trail_order_id", "")
            if tp1_taken and not native_trail_order_id and self.exchange is not None:
                native_trail_order_id = self._place_native_trailing_stop(
                    symbol, direction, atr_value, mark_price, tp1
                )

            # Determine trailing state from exchange SL
            trailing_active = False
            trailing_sl = None
            peak_price = entry_price

            if ex_sl and original_sl and ex_sl != original_sl:
                # Exchange SL differs from original → trailing is active
                trailing_active = True
                trailing_sl = ex_sl
                # Estimate peak price from trailing SL
                if direction.lower() == "long" and ex_sl > original_sl:
                    peak_price = max(mark_price, entry_price)
                elif direction.lower() == "short" and ex_sl < original_sl:
                    peak_price = min(mark_price, entry_price)

            # Adjust position size if TP1 was taken (40% sold)
            if tp1_taken and original_size:
                # TP1 sells 40% of original position
                remaining_size = original_size * 0.6
                if size_usdt < original_size * 0.95:  # Allow 5% tolerance
                    size_usdt = remaining_size

            rebuilt = {
                "symbol": symbol,
                "direction": direction,
                "entry_price": entry_price,
                "mark_price": mark_price,
                "sl_price": sl_price or ex_sl,
                "tp1": tp1,
                "tp2": tp2,
                "size_usdt": size_usdt,
                "atr_value": atr_value,
                "atr_pct": atr_pct,
                "opened_at": meta.get("opened_at", ""),
                # Trailing stop state
                "trailing_active": trailing_active,
                "trailing_sl": trailing_sl,
                "peak_price": peak_price,
                # Partial exit tracking
                "tp1_taken": tp1_taken,
                "tp2_taken": False,  # If TP2 was hit, position would be closed
                # Exchange order IDs (for modify-tpsl-order)
                "pos_id": pos_id,
                "sl_order_id": sl_order_id,
                "tp_order_id": tp_order_id,
                "tp1_order_id": meta.get("tp1_order_id", ""),
                "tp2_order_id": meta.get("tp2_order_id", ""),
                "native_trail_order_id": native_trail_order_id,
                "max_profit_atr": meta.get("max_profit_atr", 0.0),
            }

            rebuilt_positions.append(rebuilt)

        # Log reconciliation results with orphan distinction
        old_positions = self.substrate.portfolio.get("open_positions", [])
        old_symbols = {p.get("symbol") + ":" + p.get("direction", "Long") for p in old_positions}
        new_symbols = {p.get("symbol") + ":" + p.get("direction", "Long") for p in rebuilt_positions}
        confirmed = len(old_symbols & new_symbols)
        orphans = len(new_symbols - old_symbols)
        removed = len(old_symbols - new_symbols)

        _log.info(
            "Reconciliation: %d confirmed, %d new (orphan), %d removed (total: %d → %d)",
            confirmed, orphans, removed, len(old_positions), len(rebuilt_positions),
        )

        # Replace substrate positions with exchange data
        self.substrate.portfolio["open_positions"] = rebuilt_positions

    def _recalc_tp_from_config(
        self, direction: str, entry_price: float, atr_value: float, atr_pct: float
    ) -> tuple:
        """
        Recalculate TP1/TP2 from strategy config + stored ATR values.

        Used when position metadata is missing (orphan positions from
        before exchange-as-truth was implemented).

        Returns: (tp1, tp2, sl_price)
        """
        try:
            from enzymes.validate_entry_zone import _compute_sl_tp

            # Get config values for recalculation
            atr_sl_multiplier = self.substrate.cfg("exit_rules.hard_stop.width_atr_multiplier")
            tp1_rr = self.substrate.cfg("exit_rules.tp1_rr", 0.0)
            tp2_rr = self.substrate.cfg("exit_rules.tp2_rr")
            rr_minimum = self.substrate.cfg("scoring.rr_minimum")

            sl_tp = _compute_sl_tp(
                direction=direction,
                entry_price=entry_price,
                atr_value=atr_value,
                atr_pct=atr_pct,
                sr_levels=[],  # No S/R data available during reconciliation
                rr_minimum=rr_minimum,
                atr_sl_multiplier=atr_sl_multiplier,
                tp2_rr=tp2_rr,
                tp1_rr=tp1_rr,
            )
            return sl_tp["tp1"], sl_tp["tp2"], sl_tp["sl_price"]
        except Exception as e:
            _log.warning("TP recalculation failed for %s: %s", direction, e)
            return 0, 0, 0

    def _place_native_trailing_stop(
        self, symbol: str, direction: str, atr_value: float,
        mark_price: float, tp1: float,
    ) -> str:
        """
        Place native trailing stop on exchange after TP1 detection.

        Called during reconciliation when achievedProfits > 0 indicates TP1 hit
        but no native_trail_order_id exists (trail not yet placed on exchange).

        Percentage = atr_multiplier × ATR / current_price × 100
        Fallback: configurable default_pct if ATR unavailable.

        Returns: native_trail_order_id (empty string on failure)
        """
        atr_multiplier = self.substrate.cfg("exit_rules.native_trail.atr_multiplier", 2.0)

        if atr_value and mark_price:
            trail_pct = (atr_multiplier * atr_value / mark_price) * 100
        else:
            trail_pct = self.substrate.cfg("exit_rules.native_trail.default_pct", 5.0)

        trigger_price = tp1 if tp1 else mark_price

        try:
            result = self.exchange.place_trailing_stop(
                symbol=symbol,
                direction=direction,
                trigger_price=trigger_price,
                trail_pct=trail_pct,
            )
            if result:
                order_id = result.get("order_id", "")
                _log.info(
                    "Native trailing stop placed during reconciliation: %s trail_pct=%.2f%% trigger=%.2f order_id=%s",
                    symbol, trail_pct, trigger_price, order_id,
                )
                return order_id
            else:
                _log.warning("Native trailing stop placement returned None for %s", symbol)
                return ""
        except Exception as e:
            _log.warning("Could not place native trailing stop during reconciliation for %s: %s", symbol, e)
            return ""

    def _load_position_metadata(self) -> dict:
        """
        Load position metadata from the position_metadata DB table.

        This table stores atr_pct, original SL/TP, and order IDs
        needed for reconciliation after daemon restart.

        Returns: dict keyed by "symbol:direction:entry_price" → metadata dict
        """
        try:
            from core.database import db_conn
            strategy_uid = self.substrate.strategy.get("uid", "legacy")

            with db_conn() as conn:
                rows = conn.execute(
                    """SELECT symbol, direction, entry_price, atr_value, atr_pct,
                              sl_price, tp1, tp2, size_usdt, opened_at,
                              sl_order_id, tp1_order_id, tp2_order_id,
                              native_trail_order_id, max_profit_atr
                       FROM position_metadata
                       WHERE strategy_uid = ? AND closed_at IS NULL""",
                    (strategy_uid,),
                ).fetchall()

            meta = {}
            for row in rows:
                key = f"{row['symbol']}:{row['direction']}:{row['entry_price']:.2f}"
                meta[key] = dict(row)

            return meta

        except Exception as e:
            _log.debug("Could not load position metadata: %s", e)
            return {}

    def check_exchange_reachable(self) -> bool:
        """
        Check if the exchange is reachable. Aborts daemon if not.

        LIVE MODE ONLY. Paper mode doesn't need exchange connectivity.

        Returns True if exchange is reachable.
        Calls sys.exit(1) if exchange is unreachable.
        """
        if self.paper_mode or self.replay_mode:
            return True  # Paper/replay mode doesn't need exchange

        if self.exchange is None:
            _log.error(
                "Exchange unreachable — no Exchange instance configured. "
                "Cannot reconcile positions. Fix connectivity and restart."
            )
            sys.exit(1)

        try:
            result = self.exchange.test_connection()
            if not result.get("trade_ok"):
                _log.error(
                    "Exchange unreachable — trade connection failed. "
                    "Cannot reconcile positions. Fix connectivity and restart. "
                    "Details: data_ok=%s, trade_ok=%s",
                    result.get("data_ok"), result.get("trade_ok"),
                )
                sys.exit(1)

            _log.info(
                "Exchange reachable: data_ok=%s, trade_ok=%s",
                result.get("data_ok"), result.get("trade_ok"),
            )
            return True

        except Exception as e:
            _log.error(
                "Exchange unreachable — test_connection raised: %s. "
                "Cannot reconcile positions. Fix connectivity and restart.",
                e,
            )
            sys.exit(1)

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
            "description": "Position monitored, exit request evaluated, or partial close executed",
            "terminal_actions": {"manage", "trade_managed"},
        },
        "trade_closed": {
            "description": "Position closed, outcome recorded",
            "terminal_actions": {"trade_closed"},
        },
        "learning_updated": {
            "description": "Cycle complete, learning data recorded",
            "terminal_actions": set(),
        },
    }

    def _at_attractor(self, substrate: Substrate) -> bool:
        """
        Check if the substrate has reached an attractor state.

        An attractor is reached when the action matches a terminal state.
        The 'watching' attractor is special — it's reached when the Wait
        enzyme fires (action == 'wait').
        """
        action = substrate.decisions.get("action", "")
        for attr_name, attr_def in self.ATTRACTORS.items():
            if action in attr_def["terminal_actions"]:
                return True
        return False

    def _find_wait_enzyme(self) -> Optional[Enzyme]:
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
            _log.warning("Wait enzyme not found — using substrate.mark_idle() fallback")
            self.substrate.mark_idle(reason)

    def run_cycle(self) -> Dict:
        """
        Run one cycle of the reaction network.

        1. Hot-reload config
        2. Reset per-cycle substrate fields
        3. Reconcile from exchange (live mode only)
        4. Find activatable enzymes
        5. Fire the best one (regulators first)
        6. Verify ISC conditions after each step
        7. Check attractor state after each step
        8. Push trailing stop updates to exchange (live mode only)
        9. Log cycle

        Returns dict with cycle results.
        """
        self.scheduler.start_cycle()
        cycle_start = time.time()

        # 1. Hot-reload config — SKIP in replay mode
        if not self.replay_mode:
            config_changed = self.config.reload()
            if config_changed:
                interval = self.config.get("strategy.cycle_interval_minutes")
                self.scheduler.update_interval(interval)
                # Refresh secrets-free config slice in substrate
                self.substrate._config = _strategy_config_slice(self.config.config)
                _log.info("Config reloaded, interval updated to %dm", interval)

        # 2. Reset per-cycle fields
        self.substrate.reset_cycle()

        # 3. Reconcile from exchange (live mode only, every cycle)
        #    This ensures substrate always reflects exchange state.
        #    Paper mode: skip (positions are runtime-only).
        #    Replay mode: skip (backtest engine manages positions).
        if not self.paper_mode and not self.replay_mode:
            self.reconcile_from_exchange()

        # 4. Run the reaction network
        enzymes_fired = []
        isc_results = {}
        max_steps = self.config.get("daemon.max_cycle_steps")
        last_enzyme_name = None
        consecutive_count = 0
        fired_this_cycle = set()

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
            if self.substrate.isc_blocks_trade():
                failed_ids = self.substrate.failed_isc_ids()
                entry_blocked = [e for e in activatable if e.name in _ENTRY_ENZYMES]
                if entry_blocked:
                    _log.info(
                        "ISC gate: blocking entry enzymes %s — failed ISCs: %s",
                        [e.name for e in entry_blocked], failed_ids,
                    )
                activatable = [e for e in activatable if e.name not in _ENTRY_ENZYMES]

            if not activatable:
                self._fire_wait("no enzyme can activate")
                enzymes_fired.append("Wait")
                break

            # Regulators always have priority
            regulators = [e for e in activatable if e.is_regulator]
            if regulators:
                regulators.sort(key=lambda e: e.priority, reverse=True)
                best = regulators[0]
            else:
                scores = {
                    e: e.flux_score(self.substrate) for e in activatable
                }
                max_score = max(scores.values()) if scores else 0

                if max_score <= 0:
                    self._fire_wait("no enzyme improves position")
                    enzymes_fired.append("Wait")
                    break

                best = max(activatable, key=lambda e: scores.get(e, 0))

            # Consecutive-fire guard
            if best.name == last_enzyme_name:
                consecutive_count += 1
            else:
                consecutive_count = 1
                last_enzyme_name = best.name

            if consecutive_count >= 3:
                _log.warning(
                    "Enzyme %s fired %d times consecutively -- likely loop, breaking cycle early",
                    best.name, consecutive_count,
                )
                self._fire_wait(
                    f"enzyme loop detected: {best.name} x{consecutive_count}"
                )
                enzymes_fired.append("Wait")
                break

            _log.info(
                "Step %d: firing %s (class=%s, priority=%d)",
                step,
                best.name,
                best.enzyme_class.value,
                best.priority,
            )

            # Fire the selected enzyme (shallow-copy safety)
            substrate_copy = self.substrate.shallow_copy()
            try:
                self.substrate = best.transform(substrate_copy)
                fired_this_cycle.add(best.name)
                enzymes_fired.append(best.name)
            except Exception as e:
                _log.error("Enzyme %s failed: %s", best.name, e, exc_info=True)
                # Substrate remains unchanged (shallow copy was modified, not original)
                continue

            # Evaluate ISC conditions after each step
            self._evaluate_isc(self.substrate, isc_results)

        # ── Post-enzyme: Trailing stop maintenance + exchange push ──────
        from core.trailing_stop import maintain_trailing_stops
        maintain_trailing_stops(self.substrate)

        # Push trailing stop updates to exchange (live mode only)
        if not self.paper_mode and not self.replay_mode:
            self._push_trailing_stops_to_exchange()

        # Save position metadata to DB (for reconciliation after restart)
        if not self.replay_mode:
            self._save_position_metadata()

        # Log cycle
        cycle_end = time.time()
        duration_ms = int((cycle_end - cycle_start) * 1000)

        self.scheduler.end_cycle()

        _log.info(
            "Cycle %d complete: action=%s, enzymes=%s, duration=%dms",
            self.scheduler.cycle_count,
            self.substrate.decisions.get("action", ""),
            enzymes_fired or ["none"],
            duration_ms,
        )

        # ── Post-cycle: Challenger branch (non-blocking) ──────────────────
        if not self.replay_mode and self.config.get("challenger.enabled", False):
            try:
                self._run_challenger_branch()
            except Exception as e:
                _log.error(
                    "Challenger branch failed (production unaffected): %s",
                    e, exc_info=True,
                )

        # ── Post-cycle: Karpathy experiment loop (non-blocking) ───────────
        if not self.replay_mode and self.config.get("karpathy.enabled", False):
            try:
                from learning.karpathy_method import KarpathyMethod
                KarpathyMethod.run_experiment_cycle(self.substrate)
            except Exception as e:
                _log.error(
                    "Karpathy experiment cycle failed (production unaffected): %s",
                    e, exc_info=True,
                )

        # ── Post-cycle: Hyperopt prefilter (non-blocking) ─────────────────
        if not self.replay_mode and self.config.get("hyperopt.enabled", False):
            try:
                from learning.hyperopt_prefilter import HyperoptPrefilter
                HyperoptPrefilter.run_search(self.substrate)
            except Exception as e:
                _log.error(
                    "Hyperopt search failed (production unaffected): %s",
                    e, exc_info=True,
                )

        return {
            "cycle": self.scheduler.cycle_count,
            "action": self.substrate.decisions.get("action", ""),
            "enzymes_fired": enzymes_fired,
            "isc_results": isc_results,
            "duration_ms": duration_ms,
        }

    def _push_trailing_stops_to_exchange(self) -> None:
        """
        Push trailing stop updates to exchange for all open positions.

        LIVE MODE ONLY. Only pushes when trailing_sl actually changes
        (avoid API spam). Uses modify_tpsl_order with the sl_order_id
        from the position dict.
        """
        if self.paper_mode or self.replay_mode:
            return

        if self.exchange is None:
            return

        positions = self.substrate.portfolio.get("open_positions", [])
        for pos in positions:
            trailing_sl = pos.get("trailing_sl")
            trailing_active = pos.get("trailing_active", False)
            sl_order_id = pos.get("sl_order_id", "")
            symbol = pos.get("symbol", "")

            if not trailing_active or not trailing_sl or not sl_order_id:
                continue

            # Check if trailing_sl has changed since last push
            # We compare against the exchange SL (stored in pos_id reconciliation)
            # If they match, no push needed
            ex_sl = pos.get("_exchange_sl_last_pushed")
            if ex_sl is not None and abs(trailing_sl - ex_sl) < 0.01:
                continue  # No change — skip API call

            # Push to exchange
            success = self.exchange.modify_tpsl_order(
                symbol=symbol,
                order_id=sl_order_id,
                new_sl_price=trailing_sl,
            )

            if success:
                pos["_exchange_sl_last_pushed"] = trailing_sl
                _log.info(
                    "Trailing SL pushed to exchange: %s sl=%.2f",
                    symbol, trailing_sl,
                )
            else:
                _log.error(
                    "Failed to push trailing SL to exchange: %s sl=%.2f "
                    "(daemon SL is primary, exchange SL is backup)",
                    symbol, trailing_sl,
                )

    def _save_position_metadata(self) -> None:
        """
        Save position metadata to DB for reconciliation after restart.

        Stores atr_pct, original SL/TP, and exchange order IDs.
        This is NOT position state — that comes from the exchange.
        This is supplementary data needed for TP1/TP2 recalculation
        and order management after daemon restart.
        """
        try:
            from core.database import db_conn
            strategy_uid = self.substrate.strategy.get("uid", "legacy")

            with db_conn() as conn:
                # Mark all existing open positions as closed (stale)
                conn.execute(
                    "UPDATE position_metadata SET closed_at = datetime('now') "
                    "WHERE strategy_uid = ? AND closed_at IS NULL",
                    (strategy_uid,),
                )

                # Insert current open positions
                positions = self.substrate.portfolio.get("open_positions", [])
                for pos in positions:
                    conn.execute(
                        """INSERT INTO position_metadata
                           (symbol, direction, entry_price, strategy_uid,
                            atr_value, atr_pct, sl_price, tp1, tp2,
                            size_usdt, opened_at,
                            sl_order_id, tp1_order_id, tp2_order_id,
                            native_trail_order_id, max_profit_atr)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            pos.get("symbol", ""),
                            pos.get("direction", ""),
                            pos.get("entry_price", 0),
                            strategy_uid,
                            pos.get("atr_value", 0),
                            pos.get("atr_pct", 0),
                            pos.get("sl_price", 0),
                            pos.get("tp1", 0),
                            pos.get("tp2", 0),
                            pos.get("size_usdt", 0),
                            pos.get("opened_at", ""),
                            pos.get("sl_order_id", ""),
                            pos.get("tp1_order_id", ""),
                            pos.get("tp2_order_id", ""),
                            pos.get("native_trail_order_id", ""),
                            pos.get("max_profit_atr", 0.0),
                        ),
                    )

        except Exception as e:
            _log.debug("Could not save position metadata: %s", e)

    def _evaluate_isc(self, substrate: Substrate, isc_results: dict) -> None:
        """Evaluate ISC conditions and record results."""
        for isc in substrate.validity:
            if isc.id not in substrate.pending:
                continue  # Already evaluated

            try:
                passed = self._check_isc(substrate, isc)
                isc.status = "verified" if passed else "failed"
                substrate.pending.remove(isc.id)
                isc_results[isc.id] = isc.status
            except Exception as e:
                _log.error("ISC %s evaluation failed: %s", isc.id, e)
                isc_results[isc.id] = "error"

    def _check_isc(self, substrate: Substrate, isc) -> bool:
        """Check a single ISC condition against substrate state."""
        from core.substrate import ISCCheck

        field = isc.field
        operator = isc.operator
        value_ref = isc.value_ref
        field_key = isc.field_key

        if not field:
            return True

        # Get the field value from substrate
        obj = substrate.get(field)

        if operator == "any_score_gte":
            if not isinstance(obj, list) or not obj:
                return False
            threshold = substrate.cfg(value_ref) if value_ref else 0
            return any(item.get(field_key, 0) >= threshold for item in obj)

        elif operator == "sl_set_or_no_trade":
            trade_approved = substrate.decisions.get("trade_approved")
            if trade_approved is None:
                return True  # No trade pending — vacuous
            return trade_approved.get(field_key, 0) > 0

        elif operator == "size_within_risk":
            trade_approved = substrate.decisions.get("trade_approved")
            if trade_approved is None:
                return True
            size = trade_approved.get(field_key, 0)
            equity = substrate.portfolio.get("equity", 0)
            risk_pct = substrate.cfg("portfolio.risk_per_trade_pct")
            return size <= equity * risk_pct / 100

        elif operator == "count_lt":
            if not isinstance(obj, list):
                return False
            threshold = substrate.cfg(value_ref) if value_ref else 999
            return len(obj) < threshold

        elif operator == "false_or_action_wait":
            val = substrate.get(field)
            action = substrate.decisions.get("action", "")
            return not val or action == "wait"

        elif operator == "best_field_gte":
            if not isinstance(obj, list) or not obj:
                return False
            threshold = substrate.cfg(value_ref) if value_ref else 0
            return obj[0].get(field_key, 0) >= threshold

        elif operator == "all_field_gte":
            if not isinstance(obj, list) or not obj:
                return True
            threshold = substrate.cfg(value_ref) if value_ref else 0
            return all(item.get(field_key, 0) >= threshold for item in obj)

        elif operator == "none_field_eq":
            if isinstance(obj, dict):
                return all(v != value_ref for v in obj.values())
            elif isinstance(obj, list):
                return all(item.get(field_key) != value_ref for item in obj)
            return True

        return True

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
        """Run the challenger paper-trading branch after the production cycle."""
        from learning.challenger import WeightChallenger, CandidateQueue
        from learning.hypothetical_tracker import HypotheticalTracker
        from learning.comparator import ChallengerComparator

        challenger = self.substrate.learning.get("challenger", {})
        challenger_weights = challenger.get("weights")

        if not challenger_weights:
            activated = WeightChallenger.activate_next_candidate(self.substrate)
            if not activated:
                return
            challenger_weights = self.substrate.learning["challenger"]["weights"]

        HypotheticalTracker.run_cycle(self.substrate, challenger_weights)

        verdict = ChallengerComparator.evaluate(self.substrate)

        if verdict == "promote":
            metrics = ChallengerComparator.get_metrics(self.substrate)
            WeightChallenger.promote(self.substrate, "profit_factor_improvement", metrics)
            WeightChallenger.activate_next_candidate(self.substrate)

        elif verdict == "discard":
            metrics = ChallengerComparator.get_metrics(self.substrate)
            WeightChallenger.discard(self.substrate, "insufficient_improvement", metrics)
            WeightChallenger.activate_next_candidate(self.substrate)

    def register_enzyme(self, enzyme) -> None:
        """Register an enzyme with the daemon."""
        self.enzymes.append(enzyme)
        _log.info("Registered enzyme: %s (class=%s)", enzyme.name, enzyme.enzyme_class.value)

    def register_enzymes(self, enzymes: list) -> None:
        """Register multiple enzymes."""
        for e in enzymes:
            self.register_enzyme(e)
