"""
core/substrate.py -- Shared state container for the reaction network.

All enzymes read from and write to this single substrate.
Each enzyme modifies only its designated output fields.
The substrate persists across cycles and is stored in the database.

Based on: docs/reaction-design/substrate-schema.yaml

Security note:
    The substrate stores only strategy config (thresholds, risk limits,
    ISC definitions). Exchange credentials and LLM API keys are NEVER
    stored on the substrate. Those are handled by KeyManager and accessed
    directly from ConfigLoader by enzymes that need them.

Serialization:
    to_persistent_dict() -- durable state (survives restart). Stored in DB.
        Contains: strategy, portfolio, learning, validity, cycle metadata.
        Does NOT contain: market (stale on restart), analysis (recomputed),
        per-cycle decisions (cleared on reset).
    to_cycle_snapshot() -- full cycle state for debugging/audit.
        Can be pruned aggressively (last 50 cycles).
    from_persistent_dict() -- restore from DB on daemon restart.
        Market and analysis start empty; sensors repopulate on first cycle.
"""

from __future__ import annotations

import copy
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.virtual_clock import VirtualClock

_log = logging.getLogger(__name__)

# Sentinel for "key not found" -- distinguishes missing from None/False/0/""
_MISSING = object()


class SubstrateConfigError(ValueError):
    """Raised when required keys are missing from the strategy config."""
    pass


class ISCCheck:
    """
    A single hard-to-vary condition that must be verified before actions.

    Conditions are config-driven: field, operator, and value_ref are read
    from the strategy YAML. No ISC IDs are hardcoded in evaluation logic.

    Supported operators:
        any_score_gte   -- any item in list has field_key >= value_ref
        sl_set_or_no_trade -- SL > 0 if trade pending, vacuous if not
        size_within_risk -- size_usdt <= equity * risk_pct / 100
        count_lt        -- len(list) < threshold (reads threshold from live config)
        false_or_action_wait -- field is falsy OR resolved action == 'wait'
        best_field_gte  -- best (first) item in list has field_key >= threshold
        all_field_gte   -- all items in list have field_key >= threshold
        none_field_eq   -- no item in dict/list has field_key == value
    """

    def __init__(
        self,
        isc_id: str,
        criterion: str,
        verification: str,
        field: str = "",
        operator: str = "",
        value_ref: str = "",
        field_key: str = "",
    ):
        self.id = isc_id
        self.criterion = criterion
        self.verification = verification
        # Config-driven evaluation fields
        self.field = field          # dotted path into substrate state
        self.operator = operator    # evaluation operator
        self.value_ref = value_ref  # dotted config path or literal value
        self.field_key = field_key  # for list operators: key within each item
        self.status: str = "pending"  # "pending" | "verified" | "failed"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "criterion": self.criterion,
            "verification": self.verification,
            "field": self.field,
            "operator": self.operator,
            "value_ref": self.value_ref,
            "field_key": self.field_key,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ISCCheck":
        isc = cls(
            isc_id=d["id"],
            criterion=d["criterion"],
            verification=d.get("verification", ""),
            field=d.get("field", ""),
            operator=d.get("operator", ""),
            value_ref=d.get("value_ref", ""),
            field_key=d.get("field_key", ""),
        )
        isc.status = d.get("status", "pending")
        return isc


class Substrate:
    """
    Single shared state container for all enzymes.

    Provides dot-access to nested structures while keeping everything
    serializable. Enzymes read from and write to this object.

    The substrate holds a reference to the STRATEGY config slice only
    (scoring thresholds, risk limits, ISC definitions, indicator weights).
    Exchange credentials and LLM keys are NOT stored here.

    Sections:
      strategy   - strategy identity and config
      portfolio  - account state and positions
      market     - market data (updated by Sensor enzymes)
      analysis   - analysis results (updated by Oxidoreductase enzymes)
      decisions  - decision state (updated by Regulator enzymes)
      learning   - learning state (updated by Synthase enzymes)
      validity   - ISC conditions (hard-to-vary constraints)
    """


    def __init__(self, config: Optional[Dict] = None):
        """
        Initialize substrate from strategy config dict.

        The config passed here should be the strategy-safe slice only
        (no exchange credentials, no LLM keys). The daemon is responsible
        for stripping secrets before passing config to the substrate.
        """
        # Virtual clock for replay mode — delegates to VirtualClock instance.
        # In live mode (clock inactive), returns real datetime.now().
        self._clock = VirtualClock()

        now = self._now_iso()
        cfg = config or {}

        # Store strategy config reference for ISC lookups and enzyme access.
        # This must NOT contain exchange credentials or LLM API keys.
        self._config: Dict = cfg

        # Strategy section — all values from config, no hardcoded defaults.
        # If a key is missing from config, cfg() raises ValueError immediately.
        strategy_cfg = cfg.get("strategy", {})
        _REQUIRED_STRATEGY_KEYS = (
            "name", "uid", "timeframe", "confirmation_tf",
            "cycle_interval_minutes", "max_positions",
        )
        missing = [k for k in _REQUIRED_STRATEGY_KEYS if k not in strategy_cfg]
        if missing:
            raise SubstrateConfigError(
                f"Missing required strategy config key(s): {', '.join(missing)}. "
                f"Add them to config/default.yaml or your strategy YAML."
            )
        self.strategy = {
            "name": strategy_cfg["name"],
            "uid": strategy_cfg["uid"],
            "description": cfg.get("description", ""),
            "timeframe": strategy_cfg["timeframe"],
            "confirmation_tf": strategy_cfg["confirmation_tf"],
            "cycle_interval_minutes": strategy_cfg["cycle_interval_minutes"],
            "max_positions": strategy_cfg["max_positions"],
            "last_loaded_at": now,
        }

        # Portfolio section — all config values from config, no hardcoded defaults.
        # Runtime state (equity, positions) starts at zero/empty.
        portfolio_cfg = cfg.get("portfolio", {})
        self.portfolio = {
            "equity": 0.0,
            "available_margin": 0.0,
            "open_positions": [],
            "max_positions": portfolio_cfg["max_positions"],
            "risk_per_trade_pct": portfolio_cfg["risk_per_trade_pct"],
            "leverage": portfolio_cfg["leverage"],
            "max_total_risk_pct": portfolio_cfg["max_total_risk_pct"],
            "fallback_equity_usdt": portfolio_cfg["fallback_equity_usdt"],
            "correlation_check": portfolio_cfg["correlation_check"],
            "max_same_direction": portfolio_cfg["max_same_direction"],
            "total_risk_exposure_pct": 0.0,
            "correlation_matrix": {},
        }

        # Market section (populated by Sensor enzymes each cycle)
        # NOT persisted across restarts -- sensors repopulate on first cycle.
        # indicator_history survives reset_cycle() (not cleared) but is NOT persisted
        # to DB. After restart, the first N cycles will have incomplete history.
        # CollectPreTradeContext falls back to empty history in this case,
        # which sets coincidence_risk='high' and blocks trades via ISC-007.
        # This is intentional: no trades until sufficient trajectory data exists.
        symbols_cfg = cfg.get("symbols", {})
        # Filter never_trade from always_watch at init time
        _always = symbols_cfg.get("always_watch", [])
        _never = symbols_cfg.get("never_trade", [])
        self.market = {
            "symbols_watched": [s for s in _always if s not in _never],
            "last_scan_at": "",
            "last_dynamic_filter_at": "",  # ISO timestamp of last dynamic filter run
            "indicators": {},
            "indicator_history": {},  # {symbol: [{timestamp, indicators: {...}}, ...]
            "last_candle_close_ts": {},  # {symbol_tf: ISO_timestamp} — survives reset_cycle
            "last_prices": {},         # {symbol: float} — last close price per symbol
            "ohlcv": {},               # {symbol: {tf: {high: [], low: [], close: []}}} — raw price arrays for MarketGeometry
            "geometry": {},             # {symbol: {trend_direction, phase, pullback_depth, structure_break, ...}} — from MarketGeometry enzyme
            "pre_trade_context": {},
            "macro": {},
            "liquidations": {},
            "onchain": {},
            "sentiment": {},
        }

        # Confluence section (populated by Sensor enzymes, read by Oxidoreductase enzymes)
        # Persists across cycles within a run — regime_normal is valid until
        # next sensor run. NOT persisted across restarts (sensors repopulate).
        self.confluence = {
            "regime_normal": True,  # HMM regime filter (default: True = fail-open)
        }

        # Analysis section (populated by Oxidoreductase enzymes each cycle)
        # NOT persisted across restarts -- evaluators recompute on first cycle.
        self.analysis = {
            "candidates": [],
            "entry_zones": {},
            "noise_flag": False,          # kept for backward compat / logging; no longer blocks trades
            "noise_reason": "",
            "noise_penalty_ratio": 0.0,   # soft penalty: 0.0 = no noise, 1.0 = full noise penalty
            "confluence_penalty_ratio": 0.0,  # soft penalty: 0.0 = sufficient alignment, up to config max
            "trajectory_penalty_ratio": 0.0,  # soft penalty: 0.0 = safe trajectory, up to config max
            "signal_states": {},
            # Evaluation markers: distinguish "not yet evaluated" from
            # "evaluated and found nothing".  Without these, an empty list
            # ([]) is indistinguishable from "never been set", causing
            # enzymes to re-fire indefinitely in the daemon loop.
            "confluence_scored": False,
            "noise_evaluated": False,
            "entry_zones_evaluated": False,
            "pre_trade_evaluated": False,
            "macro_evaluated": False,
        }

        # Decisions section (populated by Regulator enzymes)
        # Per-cycle fields are cleared by reset_cycle().
        self.decisions = {
            "action": "",
            "trade_approved": None,
            "exit_request": None,
            "exit_approved": None,
            "exit_reason": "",
        }

        # Learning section (populated by Synthase enzymes)
        # Persisted across restarts -- accumulated over hundreds of trades.
        self.learning = {
            "idle_cycles": 0,
            "idle_reasons": [],
            "rulebook": "",
            "rulebook_version": "",
            "rulebook_generated_at": "",
            "signal_accuracy": {},
            "combination_accuracy": {},
            "suppressed_signals": [],
            "highlight_signals": [],
            "adjusted_weights": {},    # Learning-adjusted indicator weights (written by UpdateLearning)
            "adjusted_thresholds": {}, # Learning-adjusted penalty thresholds (written by UpdateLearning)
            "total_trades_recorded": 0,
            "total_idle_cycles_recorded": 0,
            "last_retrain_at": "",
        }

        # Validity section (ISC conditions — hard-to-vary constraints)
        # ISC definitions MUST come from config (default.yaml or strategy YAML).
        # No hardcoded fallback — missing validity is a fatal config error.
        # This ensures config is the single source of truth (CLAUDE.md principle).
        isc_defs = cfg.get("validity")
        if not isc_defs:
            raise SubstrateConfigError(
                "Missing required config key: 'validity'. "
                "Add a validity section to config/default.yaml (ISC definitions)."
            )
        self.validity = [ISCCheck.from_dict(isc) for isc in isc_defs]
        self.pending = []

        # Internal metadata
        self._cycle_count = 0
        self._created_at = now
        self._updated_at = now

    @staticmethod
    def _now_iso() -> str:
        """Static method: return real UTC time as ISO string.

        Kept for backward compatibility. New code should use the
        instance method now_iso() which respects the virtual clock.
        """
        return datetime.now(timezone.utc).isoformat()

    def now_iso(self) -> str:
        """Instance method: return current time as ISO string.

        Uses virtual clock if active (replay mode), otherwise real time.
        """
        return self._clock.now_iso()

    def now_as_datetime(self) -> datetime:
        """Return current time as datetime.

        Uses virtual clock if active (replay mode), otherwise real time.
        """
        return self._clock.now()

    def now_timestamp(self) -> float:
        """Return current time as Unix timestamp.

        Uses virtual clock if active (replay mode), otherwise real time.
        """
        return self._clock.now_timestamp()

    # --- Config access --------------------------------------------------------

    @property
    def config(self) -> Dict:
        """Return the strategy config dict (no secrets)."""
        return self._config

    def cfg(self, dotted_path: str, default: Any = _MISSING) -> Any:
        """
        Get a value from the *config* (not substrate state) by dotted path.

        Used by ISC checks and enzymes that need config values like
        scoring thresholds, risk limits, etc.

        If the key starts with "soft_penalties.", checks learning-adjusted
        thresholds first (substrate.learning["adjusted_thresholds"]).
        This allows the learning engine to tune penalty ratios at runtime
        without modifying the config file.

        Raises ValueError if the key is not found and no default is provided.
        This ensures config is the single source of truth — missing keys
        are caught immediately rather than silently falling back to hardcoded
        defaults that may be wrong.
        """
        # Check learning-adjusted thresholds first for soft_penalties keys
        if dotted_path.startswith("soft_penalties."):
            adjusted = self.learning.get("adjusted_thresholds", {})
            penalty_key = dotted_path.split(".", 1)[1]  # e.g. "noise_penalty_ratio"
            if penalty_key in adjusted:
                return adjusted[penalty_key]

        parts = dotted_path.split(".")
        obj = self._config
        for part in parts:
            if isinstance(obj, dict):
                obj = obj.get(part, _MISSING)
            else:
                if default is _MISSING:
                    raise ValueError(
                        f"Config key '{dotted_path}' not found (non-dict at '{part}')"
                    )
                return default
            if obj is _MISSING:
                if default is _MISSING:
                    raise ValueError(
                        f"Config key '{dotted_path}' not found in strategy config. "
                        f"Add it to config/strategies/_template.yaml and your strategy YAML."
                    )
                return default
        return obj if obj is not _MISSING else default

    # --- Dot-access helpers ---------------------------------------------------

    def get(self, dotted_path: str, default: Any = None) -> Any:
        """
        Get a value by dotted path from substrate state.

        Uses a sentinel (_MISSING) to distinguish "not found" from
        legitimate None/False/0/"" values. Falls back to config only
        when the key is truly absent from substrate state.

        Examples:
            substrate.get("strategy.name")           -> "momentum_rising"
            substrate.get("scoring.entry_threshold") -> 6.5 (from config)
            substrate.get("portfolio.equity")        -> 0.0 (from state)
            substrate.get("decisions.trade_approved") -> None (set by enzyme)
        """
        parts = dotted_path.split(".")
        obj: Any = self
        for part in parts:
            if isinstance(obj, dict):
                obj = obj.get(part, _MISSING)
            elif hasattr(obj, part):
                obj = getattr(obj, part)
            else:
                obj = _MISSING
                break
            if obj is _MISSING:
                break

        if obj is not _MISSING:
            return obj

        # Fall back to config (for scoring thresholds, risk limits, etc.)
        try:
            return self.cfg(dotted_path, default)
        except ValueError:
            return default

    def set(self, dotted_path: str, value: Any) -> None:
        """Set a value by dotted path in substrate state."""
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

    # --- ISC verification (config-driven, no hardcoded IDs) -------------------

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

    def _resolve_value_ref(self, value_ref: str) -> Any:
        """
        Resolve a value_ref string to a concrete value.

        value_ref can be:
          - A dotted substrate state path: "decisions.action" -> "wait"
          - A dotted config path: "scoring.entry_threshold" -> 6.5
          - A literal string: "high", "wait"
          - Empty string: returns None
        """
        if not value_ref:
            return None
        # Try substrate state first (decisions.action, portfolio.equity, etc.)
        val = self.get(value_ref, _MISSING)
        if val is not _MISSING:
            return val
        # Then try config (scoring thresholds, risk limits, etc.)
        try:
            val = self.cfg(value_ref, _MISSING)
            if val is not _MISSING:
                return val
        except ValueError:
            pass
        # Treat as literal string
        return value_ref

    def _evaluate_isc(self, isc: ISCCheck) -> bool:
        """
        Evaluate a single ISC condition using its config-driven operator.

        All logic is driven by isc.field, isc.operator, isc.value_ref,
        and isc.field_key. No ISC IDs are referenced here.
        """
        op = isc.operator
        field_val = self.get(isc.field, _MISSING)
        resolved = self._resolve_value_ref(isc.value_ref)

        # --- any_score_gte: any item in list has field_key >= threshold -------
        if op == "any_score_gte":
            if field_val is _MISSING or not field_val:
                return False  # empty list = condition not met
            threshold = resolved if resolved is not None else 0
            return any(
                item.get(isc.field_key, 0) >= threshold
                for item in field_val
                if isinstance(item, dict)
            )

        # --- sl_set_or_no_trade: SL > 0 if trade pending, vacuous if not -----
        elif op == "sl_set_or_no_trade":
            if field_val is _MISSING or field_val is None:
                return True  # no trade pending, vacuously true
            if not isinstance(field_val, dict):
                return False
            return field_val.get(isc.field_key, 0) > 0

        # --- size_within_risk: size_usdt <= equity * risk_pct / 100 ----------
        elif op == "size_within_risk":
            if field_val is _MISSING or field_val is None:
                return True  # no trade pending, vacuously true
            if not isinstance(field_val, dict):
                return False
            equity = self.get("portfolio.equity", 0)
            risk_pct = self.get("portfolio.risk_per_trade_pct", 1.0)
            max_size = equity * risk_pct / 100
            return field_val.get(isc.field_key, 0) <= max_size

        # --- count_lt: len(list) < threshold ----------------------------------
        # Reads threshold from live config (self.cfg()) to avoid stale state.
        elif op == "count_lt":
            items = field_val if field_val is not _MISSING else []
            count = len(items) if isinstance(items, (list, dict)) else 0
            # Always read limit from live config, never stale substrate state
            try:
                limit = self.cfg(isc.value_ref) if isc.value_ref else 0
            except ValueError:
                limit = resolved if resolved is not None else 0
            return count < limit

        # --- false_or_action_wait: field is falsy OR action == 'wait' --------
        elif op == "false_or_action_wait":
            noise = field_val if field_val is not _MISSING else False
            action = resolved if resolved is not None else ""
            return (not noise) or (action == "")

        # --- best_field_gte: best (first) item has field_key >= threshold ----
        # Used by ISC-006: only the best candidate must meet the threshold,
        # not all candidates (weak symbols shouldn't block the best one).
        elif op == "best_field_gte":
            if field_val is _MISSING or not field_val:
                return False  # empty list = condition not met
            threshold = resolved if resolved is not None else 0
            best = field_val[0] if isinstance(field_val, list) else next(iter(field_val.values()), {})
            if not isinstance(best, dict):
                return False
            return best.get(isc.field_key, 0) >= threshold

        # --- all_field_gte: all items in list have field_key >= threshold -----
        elif op == "all_field_gte":
            if field_val is _MISSING or not field_val:
                return False  # empty list = condition not met (not vacuously true)
            threshold = resolved if resolved is not None else 0
            return all(
                item.get(isc.field_key, 0) >= threshold
                for item in field_val
                if isinstance(item, dict)
            )

        # --- none_field_eq: no item in dict/list has field_key == value -------
        elif op == "none_field_eq":
            if field_val is _MISSING or not field_val:
                return True  # nothing to check, vacuously true
            items = field_val.values() if isinstance(field_val, dict) else field_val
            return not any(
                item.get(isc.field_key) == resolved
                for item in items
                if isinstance(item, dict)
            )

        # --- Unknown operator: log and pass (fail-safe) -----------------------
        else:
            _log.warning(
                "ISC %s: unknown operator %r, passing by default", isc.id, op
            )
            return True

    def all_iscs_pass(self) -> bool:
        """Check if all ISC conditions are verified (or vacuously true)."""
        results = self.verify_iscs()
        return all(s == "verified" for s in results.values())

    def isc_blocks_trade(self) -> bool:
        """
        Returns True if any ISC condition has failed, blocking trade execution.

        Called by the daemon before selecting trade-executing enzymes.
        This is the enforcement point — ISC is not just audit, it's a hard gate.
        Pending ISCs (not yet evaluated this cycle) do NOT block trades;
        only explicitly failed ISCs block.
        """
        return any(isc.status == "failed" for isc in self.validity)

    def failed_isc_ids(self) -> list[str]:
        """Return IDs of all ISC conditions that have failed."""
        return [isc.id for isc in self.validity if isc.status == "failed"]

    # --- Soft penalties (replace former hard-gate ISCs 005/006/007) -----------

    def soft_penalties(self) -> Dict[str, float]:
        """
        Return current soft penalty ratios from analysis state.

        Penalties are written by enzymes (DetectNoise, ScoreConfluence,
        CollectPreTradeContext) and read by ApproveTrade to compute
        effective_score. Each ratio is 0.0 (no penalty) to 1.0 (full block).

        Returns:
            dict with keys: noise, confluence, trajectory
        """
        return {
            "noise": self.analysis.get("noise_penalty_ratio", 0.0),
            "confluence": self.analysis.get("confluence_penalty_ratio", 0.0),
            "trajectory": self.analysis.get("trajectory_penalty_ratio", 0.0),
        }

    def compute_effective_score(self, raw_score: float) -> float:
        """
        Apply multiplicative soft penalties to a raw confluence score.

        Formula: effective_score = raw_score
            × (1 - noise_penalty)
            × (1 - confluence_penalty)
            × (1 - trajectory_penalty)

        This replaces the former hard-gate ISCs (005/006/007) that blocked
        ALL trades when noise_flag=True, confluence_min not met, or
        coincidence_risk=high. Now these conditions reduce the score
        instead of blocking entirely, allowing the learning engine to
        collect data from penalized trades.

        A trade with penalties applied still needs to meet
        scoring.approval_threshold to proceed.
        """
        penalties = self.soft_penalties()
        effective = raw_score
        for name, ratio in penalties.items():
            effective *= (1.0 - ratio)
        return effective

    # --- Reset helpers --------------------------------------------------------

    def reset_cycle(self) -> None:
        """Reset per-cycle fields for a new daemon cycle."""
        # Market section: clear transient fields for re-sensing.
        # NOTE: indicators and last_candle_close_ts are NOT cleared here.
        # CollectOHLCV manages indicators: it refreshes them only when a new
        # candle has closed. Between candle closes, indicators persist because
        # they represent the last completed candle's data — still valid.
        # This eliminates redundant API calls (P7: smart OHLCV activation).
        self.market["last_scan_at"] = ""
        self.market["macro"] = {}
        self.market["pre_trade_context"] = {}
        # NOTE: indicator_history and last_candle_close_ts are NOT cleared.
        # They accumulate across cycles and survive reset_cycle().
        # indicator_history is trimmed by CollectOHLCV to the configured time span.
        # last_candle_close_ts tracks when each symbol/tf last had a candle close.
        # After restart, _bootstrap_indicator_history() computes history from
        # OHLCV data so trades are not blocked by ISC-007.
        # Analysis section: clear for re-evaluation
        self.analysis["candidates"] = []
        self.analysis["entry_zones"] = {}
        self.analysis["noise_flag"] = False
        self.analysis["noise_reason"] = ""
        self.analysis["noise_penalty_ratio"] = 0.0
        self.analysis["confluence_penalty_ratio"] = 0.0
        self.analysis["trajectory_penalty_ratio"] = 0.0
        self.analysis["signal_states"] = {}
        # Reset evaluation markers so enzymes can fire again
        self.analysis["confluence_scored"] = False
        self.analysis["noise_evaluated"] = False
        self.analysis["entry_zones_evaluated"] = False
        self.analysis["pre_trade_evaluated"] = False
        self.analysis["macro_evaluated"] = False
        # Decisions section: clear for new decisions
        self.decisions["action"] = ""
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
        self.learning["idle_reasons"] = [*self.learning["idle_reasons"], reason]
        self.learning["total_idle_cycles_recorded"] += 1

    # --- Serialization --------------------------------------------------------

    def shallow_copy(self) -> "Substrate":
        """
        Create a shallow copy for enzyme execution safety.

        Top-level dicts are shallow-copied so field reassignment is safe.
        Nested values (lists, dicts inside those dicts) are shared references.
        Enzymes must NOT mutate nested values in-place; they must create
        new values and reassign entire fields.

        This replaces copy.deepcopy() in the daemon loop. It's ~1-2MB
        cheaper per cycle and makes the "no partial mutation" invariant
        explicit by design: if an enzyme can't mutate in-place, it can't
        leave the substrate in a partially-modified state.

        If an enzyme raises an exception, self.substrate remains unchanged
        because only the shallow copy was modified — the same guarantee
        as deep copy, but without the cost.
        """
        new = Substrate.__new__(Substrate)
        new._config = self._config  # config is never mutated by enzymes
        new._clock = self._clock  # clock is shared — same virtual time for all copies
        new.strategy = self.strategy.copy()
        new.portfolio = self.portfolio.copy()
        new.market = self.market.copy()
        new.confluence = self.confluence.copy()
        new.analysis = self.analysis.copy()
        new.decisions = self.decisions.copy()
        new.learning = self.learning.copy()
        new.validity = list(self.validity)  # new list, same ISCCheck objects
        new.pending = list(self.pending)
        new._cycle_count = self._cycle_count
        new._created_at = self._created_at
        new._updated_at = self._updated_at
        return new

    def to_persistent_dict(self) -> dict:
        """
        Serialize durable substrate state for database storage.

        Contains only what must survive a daemon restart:
          - strategy: which strategy is running
          - portfolio: open positions, equity (critical for restart recovery)
          - learning: accumulated accuracy data, rulebook, suppressed signals
          - validity: ISC definitions and last-known statuses
          - cycle metadata

        Does NOT contain:
          - market: stale on restart; sensors repopulate on first cycle
          - analysis: stale on restart; evaluators recompute on first cycle
          - per-cycle decisions: cleared by reset_cycle() anyway
        """
        return {
            "strategy": copy.deepcopy(self.strategy),
            "portfolio": copy.deepcopy(self.portfolio),
            "learning": copy.deepcopy(self.learning),
            "validity": [isc.to_dict() for isc in self.validity],
            "pending": list(self.pending),
            "_cycle_count": self._cycle_count,
            "_created_at": self._created_at,
            "_updated_at": self._updated_at,
        }

    def to_cycle_snapshot(self) -> dict:
        """
        Full cycle snapshot for debugging and audit trail.

        Includes market and analysis data. Pruned aggressively in DB
        (last N cycles only, configured by substrate_state_max_rows).
        """
        return {
            "strategy": copy.deepcopy(self.strategy),
            "portfolio": copy.deepcopy(self.portfolio),
            "market": copy.deepcopy(self.market),
            "confluence": copy.deepcopy(self.confluence),
            "analysis": copy.deepcopy(self.analysis),
            "decisions": copy.deepcopy(self.decisions),
            "learning": copy.deepcopy(self.learning),
            "validity": [isc.to_dict() for isc in self.validity],
            "pending": list(self.pending),
            "_cycle_count": self._cycle_count,
            "_created_at": self._created_at,
            "_updated_at": self._updated_at,
        }

    def to_dict(self) -> dict:
        """Alias for to_cycle_snapshot() -- full state for compatibility."""
        return self.to_cycle_snapshot()

    def to_json(self) -> str:
        """Serialize substrate to JSON string (full cycle snapshot)."""
        return json.dumps(self.to_cycle_snapshot(), default=str, indent=2)

    def to_persistent_json(self) -> str:
        """Serialize durable substrate state to JSON string."""
        return json.dumps(self.to_persistent_dict(), default=str, indent=2)

    @classmethod
    def from_persistent_dict(cls, d: dict, config: Optional[Dict] = None) -> "Substrate":
        """
        Reconstruct substrate from persistent dict (e.g. from database on restart).

        Market and analysis sections start empty -- sensors and evaluators
        will repopulate them on the first cycle. This is correct: you never
        want to trade on stale market data from before a restart.
        """
        sub = cls(config=config)
        sub.strategy = d.get("strategy", sub.strategy)
        sub.portfolio = d.get("portfolio", sub.portfolio)
        sub.learning = d.get("learning", sub.learning)
        validity_data = d.get("validity", [])
        if validity_data:
            sub.validity = [ISCCheck.from_dict(isc) for isc in validity_data]
        else:
            # DB has no ISCs — use live config (hot-reload may have changed them)
            isc_defs = (config or {}).get("validity")
            if not isc_defs:
                raise SubstrateConfigError(
                    "No ISC definitions in DB or config — cannot restore safely. "
                    "Add a validity section to config/default.yaml."
                )
            sub.validity = [ISCCheck.from_dict(isc) for isc in isc_defs]
        sub.pending = d.get("pending", [])
        sub._cycle_count = d.get("_cycle_count", 0)
        sub._created_at = d.get("_created_at", sub._created_at)
        sub._updated_at = d.get("_updated_at", sub._updated_at)
        return sub

    @classmethod
    def from_dict(cls, d: dict, config: Optional[Dict] = None) -> "Substrate":
        """
        Reconstruct substrate from dict.

        Handles both persistent dicts (from DB) and full cycle snapshots.
        If market/analysis are present, they are restored (used in tests).
        """
        sub = cls.from_persistent_dict(d, config=config)
        # Restore transient sections if present (e.g. in test roundtrips)
        if "market" in d:
            sub.market = d["market"]
        if "confluence" in d:
            sub.confluence = d["confluence"]
        if "analysis" in d:
            sub.analysis = d["analysis"]
        if "decisions" in d:
            sub.decisions = d["decisions"]
        return sub

    @classmethod
    def from_json(cls, json_str: str, config: Optional[Dict] = None) -> "Substrate":
        """Reconstruct substrate from JSON string."""
        return cls.from_dict(json.loads(json_str), config=config)

    def __repr__(self) -> str:
        action = self.decisions.get("action", "")
        n_pos = len(self.portfolio.get("open_positions", []))
        n_cand = len(self.analysis.get("candidates", []))
        return (
            f"Substrate(strategy={self.strategy.get('name', '?')}, "
            f"action={action}, positions={n_pos}, "
            f"candidates={n_cand}, cycle={self._cycle_count})"
        )
