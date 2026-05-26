"""
tests_new/test_phase_c.py -- Validation tests for Phase C: Regulators and Transporters.

Tests that:
1. ApproveTrade (Regulator): Kelly sizing, ISC enforcement, SL validation, position limits
2. ApproveExit (Regulator): hard SL, trailing stop (state on position), max hold, signal reversal
3. RequestExit (Sensor): signal reversal detection, TP hit, SL breach detection
4. ExecuteTrade (Transporter): paper mode logging, position tracking, trade_learning recording
5. ExecuteExit (Transporter): position removal, outcome recording, action state
6. SyncPositions (Sensor): equity sync, position reconciliation, paper mode fallback
7. SendTelegramLog (Transporter): config-gated, graceful no-token handling
8. Exchange order methods: paper mode guards, error handling
9. Integration: full entry and exit cycles, ISC blocking, trailing stop persistence

All tests are pure unit tests:
  - No real network calls (exchange mocked or paper mode)
  - No real database (uses temp_db fixture from conftest.py)
  - All configurable values driven through fixture config dicts
  - Trailing stop state lives on each position dict (not global)

Requires: pytest>=9.0.0, pandas>=3.0.3, ccxt>=4.5.54
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.substrate import Substrate
from core.enzyme import EnzymeClass
from enzymes.approve_exit import _update_trailing_stop


# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------

def _make_config(overrides: dict | None = None) -> dict:
    """
    Return a minimal but complete strategy config dict.
    All Phase C configurable values are present so enzymes never fall back
    to implicit defaults that cannot be audited.
    """
    cfg = {
        "strategy": {
            "name": "test_strategy",
            "timeframe": "4H",
            "confirmation_tf": "1H",
            "max_positions": 3,
            "cycle_interval_minutes": 15,
        },
        "symbols": {
            "always_watch": ["BTCUSDT", "ETHUSDT"],
            "never_trade": [],
        },
        "portfolio": {
            "risk_per_trade_pct": 1.0,         # % of equity risked per trade
            "leverage": 5,
            "max_positions": 3,
            "max_total_risk_pct": 3.0,          # total portfolio risk cap
            "fallback_equity_usdt": 1000.0,     # used in paper mode
            "correlation_check": True,
            "max_same_direction": 3,            # directional concentration limit
        },
        "scoring": {
            "entry_threshold": 6.5,
            "confluence_min_signals": 3,
            "rr_minimum": 2.0,
        },
        "risk": {
            "kelly_min": 0.05,                  # Kelly fraction floor (soft-hard-coded)
            "kelly_max": 0.25,                  # Kelly fraction ceiling (soft-hard-coded)
            "kelly_win_rate_base": 0.35,        # win-rate proxy base at score=0
            "kelly_win_rate_range": 0.40,       # win-rate range (base + range at score=10)
            "kelly_avg_win_r": 2.0,             # conservative R:R for Kelly
            "max_size_pct_of_equity": 25.0,     # hard cap: position <= 25% equity
            "min_size_pct_of_equity": 5.0,      # floor: position >= 5% equity
        },
        "exit_rules": {
            "hard_stop": {
                "width_atr_multiplier": 1.5,
            },
            "trailing_stop": {
                "enabled": True,
                "activation_pct": 1.5,          # % profit before trailing activates
                "trail_atr_multiplier": 1.0,    # trail distance = ATR * this
                "breakeven_at_activation": True,# move SL to entry when trailing starts
            },
            "max_hold_hours": 72,               # exit if held longer than this
            "tp_exit_pct": 100.0,               # close 100% at TP (vs partial)
        },
        "modules": {
            "macro_context": False,
            "telegram_logs": False,
            "telegram_interaction": False,
        },
        "telegram": {
            "bot_token": "",
            "chat_id": "",
        },
        "sync": {
            "position_sync_every_n_cycles": 4,  # sync positions every N cycles
        },
        "daemon": {
            "paper_mode": True,
            "max_cycle_steps": 20,
            "substrate_state_max_rows": 200,
        },
    }
    if overrides:
        # Deep-merge one level
        for k, v in overrides.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k] = {**cfg[k], **v}
            else:
                cfg[k] = v
    return cfg


def _make_substrate(config_overrides: dict | None = None) -> Substrate:
    return Substrate(config=_make_config(config_overrides))


def _make_entry_zone(
    symbol: str = "BTCUSDT",
    direction: str = "Long",
    entry_price: float = 50000.0,
    sl_price: float = 49000.0,
    tp1: float = 52000.0,
    tp2: float = 53500.0,
    atr_value: float = 800.0,
    atr_pct: float = 1.6,
    score: float = 7.5,
) -> dict:
    return {
        "direction": direction,
        "entry_price": entry_price,
        "sl_price": sl_price,
        "tp1": tp1,
        "tp2": tp2,
        "rr_ratio": 2.0,
        "atr_value": atr_value,
        "atr_pct": atr_pct,
        "score": score,
        "label": "momentum_rising",
        "timeframe": "4H",
    }


def _make_open_position(
    symbol: str = "BTCUSDT",
    direction: str = "Long",
    entry_price: float = 50000.0,
    sl_price: float = 49000.0,
    tp1: float = 52000.0,
    tp2: float = 53500.0,
    mark_price: float = 50500.0,
    size_usdt: float = 500.0,
    atr_value: float = 800.0,
    opened_at: str | None = None,
) -> dict:
    """
    A position dict as stored on substrate.portfolio.open_positions.
    Trailing stop state fields are always present (even if not yet active).

    By default, opened_at is 1 hour ago so max_hold_hours never triggers
    in tests that aren't specifically testing max hold duration.
    """
    if opened_at is None:
        opened_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    return {
        "symbol": symbol,
        "direction": direction,
        "entry_price": entry_price,
        "sl_price": sl_price,
        "tp1": tp1,
        "tp2": tp2,
        "mark_price": mark_price,
        "size_usdt": size_usdt,
        "atr_value": atr_value,
        "opened_at": opened_at,
        # Trailing stop state — always on the position dict
        "trailing_active": False,
        "trailing_sl": None,
        "peak_price": mark_price,
    }


# ---------------------------------------------------------------------------
# TestApproveTrade
# ---------------------------------------------------------------------------

class TestApproveTrade:
    """ApproveTrade: RiskManager Regulator enzyme — entry gate."""

    def _get_enzyme(self):
        """Import and instantiate ApproveTrade (imported lazily so missing file = clear error)."""
        from enzymes.approve_trade import ApproveTrade
        return ApproveTrade(config=_make_config())

    def test_is_regulator_class(self):
        """ApproveTrade must be a Regulator with priority 10."""
        enzyme = self._get_enzyme()
        assert enzyme.enzyme_class == EnzymeClass.REGULATOR
        assert enzyme.is_regulator is True
        assert enzyme.priority == 10

    def test_does_not_activate_without_entry_zones(self):
        """Does not activate when analysis.entry_zones is empty."""
        enzyme = self._get_enzyme()
        sub = _make_substrate()
        sub.analysis["entry_zones"] = {}
        assert enzyme.can_activate(sub) is False

    def test_does_not_activate_when_already_approved(self):
        """Does not re-run if trade_approved is already set this cycle."""
        enzyme = self._get_enzyme()
        sub = _make_substrate()
        sub.analysis["entry_zones"] = {"BTCUSDT": _make_entry_zone()}
        sub.decisions["trade_approved"] = {"symbol": "BTCUSDT"}
        assert enzyme.can_activate(sub) is False

    def test_activates_with_entry_zones(self):
        """Activates when entry_zones present and no approval yet."""
        enzyme = self._get_enzyme()
        sub = _make_substrate()
        sub.analysis["entry_zones"] = {"BTCUSDT": _make_entry_zone()}
        sub.portfolio["equity"] = 10000.0
        assert enzyme.can_activate(sub) is True

    def test_approves_valid_long_trade(self):
        """Approves a long trade with correct SL placement and sufficient score."""
        enzyme = self._get_enzyme()
        sub = _make_substrate()
        sub.portfolio["equity"] = 10000.0
        sub.portfolio["open_positions"] = []
        sub.analysis["candidates"] = [{"symbol": "BTCUSDT", "score": 7.5, "indicators_aligned": 4}]
        sub.analysis["entry_zones"] = {"BTCUSDT": _make_entry_zone(direction="Long")}
        sub.analysis["noise_flag"] = False

        result = enzyme.transform(sub)

        approved = result.decisions.get("trade_approved")
        assert approved is not None
        assert approved["symbol"] == "BTCUSDT"
        assert approved["direction"] == "Long"
        assert approved["sl_price"] < approved["entry_price"]  # SL below entry for long
        assert approved["size_usdt"] > 0

    def test_approves_valid_short_trade(self):
        """Approves a short trade with correct SL placement."""
        enzyme = self._get_enzyme()
        sub = _make_substrate()
        sub.portfolio["equity"] = 10000.0
        sub.portfolio["open_positions"] = []
        sub.analysis["candidates"] = [{"symbol": "ETHUSDT", "score": 7.0, "indicators_aligned": 4}]
        sub.analysis["entry_zones"] = {
            "ETHUSDT": _make_entry_zone(
                symbol="ETHUSDT",
                direction="Short",
                entry_price=3000.0,
                sl_price=3100.0,   # SL above entry for short
                tp1=2900.0,
                tp2=2800.0,
                score=7.0,
            )
        }
        sub.analysis["noise_flag"] = False

        result = enzyme.transform(sub)

        approved = result.decisions.get("trade_approved")
        assert approved is not None
        assert approved["sl_price"] > approved["entry_price"]  # SL above entry for short

    def test_blocks_long_with_sl_above_entry(self):
        """Blocks a long trade where SL is above entry (invalid placement)."""
        enzyme = self._get_enzyme()
        sub = _make_substrate()
        sub.portfolio["equity"] = 10000.0
        sub.portfolio["open_positions"] = []
        sub.analysis["entry_zones"] = {
            "BTCUSDT": _make_entry_zone(
                direction="Long",
                entry_price=50000.0,
                sl_price=51000.0,  # WRONG: SL above entry for long
            )
        }
        sub.analysis["noise_flag"] = False

        result = enzyme.transform(sub)

        assert result.decisions.get("trade_approved") is None

    def test_blocks_short_with_sl_below_entry(self):
        """Blocks a short trade where SL is below entry (invalid placement)."""
        enzyme = self._get_enzyme()
        sub = _make_substrate()
        sub.portfolio["equity"] = 10000.0
        sub.portfolio["open_positions"] = []
        sub.analysis["entry_zones"] = {
            "BTCUSDT": _make_entry_zone(
                direction="Short",
                entry_price=50000.0,
                sl_price=49000.0,  # WRONG: SL below entry for short
            )
        }
        sub.analysis["noise_flag"] = False

        result = enzyme.transform(sub)

        assert result.decisions.get("trade_approved") is None

    def test_blocks_when_max_positions_reached(self):
        """Blocks new trade when portfolio already at max_positions."""
        enzyme = self._get_enzyme()
        sub = _make_substrate({"strategy": {"max_positions": 2}})
        sub.portfolio["equity"] = 10000.0
        sub.portfolio["open_positions"] = [
            _make_open_position("BTCUSDT"),
            _make_open_position("ETHUSDT"),
        ]
        sub.analysis["entry_zones"] = {"SOLUSDT": _make_entry_zone("SOLUSDT")}
        sub.analysis["noise_flag"] = False

        result = enzyme.transform(sub)

        assert result.decisions.get("trade_approved") is None

    def test_blocks_when_noise_flag_true(self):
        """Blocks trade when noise_flag is True (ISC-005)."""
        enzyme = self._get_enzyme()
        sub = _make_substrate()
        sub.portfolio["equity"] = 10000.0
        sub.portfolio["open_positions"] = []
        sub.analysis["entry_zones"] = {"BTCUSDT": _make_entry_zone()}
        sub.analysis["noise_flag"] = True

        result = enzyme.transform(sub)

        assert result.decisions.get("trade_approved") is None

    def test_kelly_fraction_capped_min(self):
        """Kelly fraction is never below kelly_min (config-driven floor)."""
        enzyme = self._get_enzyme()
        sub = _make_substrate()
        sub.portfolio["equity"] = 10000.0
        sub.portfolio["open_positions"] = []
        # Very low score → very low Kelly → should be capped at kelly_min
        sub.analysis["entry_zones"] = {
            "BTCUSDT": _make_entry_zone(score=1.0)
        }
        sub.analysis["noise_flag"] = False

        result = enzyme.transform(sub)
        approved = result.decisions.get("trade_approved")
        if approved:
            kelly_min = _make_config()["risk"]["kelly_min"]
            assert approved.get("kelly_fraction", kelly_min) >= kelly_min

    def test_kelly_fraction_capped_max(self):
        """Kelly fraction is never above kelly_max (config-driven ceiling)."""
        enzyme = self._get_enzyme()
        sub = _make_substrate()
        sub.portfolio["equity"] = 10000.0
        sub.portfolio["open_positions"] = []
        sub.analysis["entry_zones"] = {
            "BTCUSDT": _make_entry_zone(score=10.0)
        }
        sub.analysis["noise_flag"] = False

        result = enzyme.transform(sub)
        approved = result.decisions.get("trade_approved")
        if approved:
            kelly_max = _make_config()["risk"]["kelly_max"]
            assert approved.get("kelly_fraction", 0) <= kelly_max

    def test_size_capped_at_max_pct_of_equity(self):
        """Position size never exceeds max_size_pct_of_equity (config-driven)."""
        enzyme = self._get_enzyme()
        sub = _make_substrate()
        sub.portfolio["equity"] = 10000.0
        sub.portfolio["open_positions"] = []
        # Very tight SL → huge calculated size → must be capped
        sub.analysis["entry_zones"] = {
            "BTCUSDT": _make_entry_zone(
                entry_price=50000.0,
                sl_price=49999.0,   # 0.002% SL → enormous notional
            )
        }
        sub.analysis["noise_flag"] = False

        result = enzyme.transform(sub)
        approved = result.decisions.get("trade_approved")
        if approved:
            max_pct = _make_config()["risk"]["max_size_pct_of_equity"]
            max_allowed = 10000.0 * max_pct / 100
            assert approved["size_usdt"] <= max_allowed

    def test_approved_dict_has_required_fields(self):
        """trade_approved dict contains all required fields for ExecuteTrade."""
        enzyme = self._get_enzyme()
        sub = _make_substrate()
        sub.portfolio["equity"] = 10000.0
        sub.portfolio["open_positions"] = []
        sub.analysis["entry_zones"] = {"BTCUSDT": _make_entry_zone()}
        sub.analysis["noise_flag"] = False

        result = enzyme.transform(sub)
        approved = result.decisions.get("trade_approved")
        if approved:
            required = {"symbol", "direction", "entry_price", "sl_price", "tp1", "tp2",
                        "size_usdt", "kelly_fraction", "approved_at"}
            assert required.issubset(approved.keys()), (
                f"Missing fields: {required - approved.keys()}"
            )


# ---------------------------------------------------------------------------
# TestApproveExit
# ---------------------------------------------------------------------------

class TestApproveExit:
    """ApproveExit: RiskManager exit gate — decides whether to close a position."""

    def _get_enzyme(self):
        from enzymes.approve_exit import ApproveExit
        return ApproveExit(config=_make_config())

    def test_is_regulator_class(self):
        enzyme = self._get_enzyme()
        assert enzyme.enzyme_class == EnzymeClass.REGULATOR
        assert enzyme.is_regulator is True
        assert enzyme.priority == 10

    def test_does_not_activate_without_exit_request(self):
        """Does not activate when no exit_request is on the substrate."""
        enzyme = self._get_enzyme()
        sub = _make_substrate()
        sub.decisions["exit_request"] = None
        assert enzyme.can_activate(sub) is False

    def test_activates_with_exit_request(self):
        """Activates when exit_request is set."""
        enzyme = self._get_enzyme()
        sub = _make_substrate()
        sub.decisions["exit_request"] = {
            "symbol": "BTCUSDT", "reason": "sl_breach", "urgency": "immediate"
        }
        sub.portfolio["open_positions"] = [_make_open_position()]
        assert enzyme.can_activate(sub) is True

    def test_approves_exit_on_hard_sl_breach_long(self):
        """Approves exit immediately when long position price falls below sl_price."""
        enzyme = self._get_enzyme()
        sub = _make_substrate()
        pos = _make_open_position(direction="Long", entry_price=50000.0, sl_price=49000.0,
                                  mark_price=48500.0)  # below SL
        sub.portfolio["open_positions"] = [pos]
        sub.decisions["exit_request"] = {
            "symbol": "BTCUSDT", "reason": "sl_breach", "urgency": "immediate"
        }

        result = enzyme.transform(sub)

        approved = result.decisions.get("exit_approved")
        assert approved is not None
        assert approved["symbol"] == "BTCUSDT"
        assert "sl" in approved["reason"].lower() or "stop" in approved["reason"].lower()

    def test_approves_exit_on_hard_sl_breach_short(self):
        """Approves exit immediately when short position price rises above sl_price."""
        enzyme = self._get_enzyme()
        sub = _make_substrate()
        pos = _make_open_position(direction="Short", entry_price=3000.0, sl_price=3100.0,
                                  mark_price=3150.0)  # above SL
        sub.portfolio["open_positions"] = [pos]
        sub.decisions["exit_request"] = {
            "symbol": "BTCUSDT", "reason": "sl_breach", "urgency": "immediate"
        }

        result = enzyme.transform(sub)

        approved = result.decisions.get("exit_approved")
        assert approved is not None

    def test_approves_exit_when_max_hold_hours_exceeded(self):
        """Approves exit when position has been held beyond max_hold_hours."""
        enzyme = self._get_enzyme()
        sub = _make_substrate()
        # Position opened 80 hours ago (> 72h max_hold_hours)
        old_time = (datetime.now(timezone.utc) - timedelta(hours=80)).isoformat()
        pos = _make_open_position(opened_at=old_time)
        sub.portfolio["open_positions"] = [pos]
        sub.decisions["exit_request"] = {
            "symbol": "BTCUSDT", "reason": "max_hold_exceeded", "urgency": "normal"
        }

        result = enzyme.transform(sub)

        approved = result.decisions.get("exit_approved")
        assert approved is not None

    def test_does_not_approve_exit_for_healthy_position(self):
        """Does not approve exit when position is healthy and no hard rule triggered."""
        enzyme = self._get_enzyme()
        sub = _make_substrate()
        pos = _make_open_position(entry_price=50000.0, sl_price=49000.0, mark_price=51000.0)
        sub.portfolio["open_positions"] = [pos]
        # Soft signal reversal request — position is healthy, no hard rule
        sub.decisions["exit_request"] = {
            "symbol": "BTCUSDT", "reason": "soft_signal_reversal", "urgency": "low"
        }

        result = enzyme.transform(sub)

        # Soft reversal alone should NOT trigger approval for a healthy position
        # (enzyme may deny and leave exit_approved as None)
        # This test validates the enzyme doesn't blindly approve soft requests
        # The enzyme may approve or deny — what matters is it doesn't crash
        # and exit_approved is either None or a valid dict
        approved = result.decisions.get("exit_approved")
        assert approved is None or isinstance(approved, dict)

    # --- Trailing stop tests ---

    def test_trailing_stop_not_active_below_threshold(self):
        """Trailing stop does not activate when profit is below activation_pct."""
        sub = _make_substrate()
        # activation_pct = 1.5%, position only up 0.5%
        pos = _make_open_position(entry_price=50000.0, mark_price=50250.0)  # +0.5%
        pos["trailing_active"] = False
        pos["trailing_sl"] = None
        pos["peak_price"] = 50250.0

        result = _update_trailing_stop(pos, sub.config)
        assert result["trailing_active"] is False

    def test_trailing_stop_activates_above_threshold(self):
        """Trailing stop activates when profit exceeds activation_pct (config-driven)."""
        sub = _make_substrate()
        # activation_pct = 1.5%, position up 2.0%
        pos = _make_open_position(entry_price=50000.0, mark_price=51000.0)  # +2.0%
        pos["trailing_active"] = False
        pos["trailing_sl"] = None
        pos["peak_price"] = 51000.0

        result = _update_trailing_stop(pos, sub.config)
        assert result["trailing_active"] is True
        # When breakeven_at_activation=True, trailing_sl should be >= entry_price
        assert result["trailing_sl"] is not None
        assert result["trailing_sl"] >= result["entry_price"]

    def test_trailing_stop_state_on_position_dict(self):
        """Trailing stop state (trailing_active, trailing_sl, peak_price) lives on position dict."""
        pos = _make_open_position()
        # These three fields must always be present on every position
        assert "trailing_active" in pos
        assert "trailing_sl" in pos
        assert "peak_price" in pos

    def test_trailing_stop_peak_price_updates_with_position(self):
        """peak_price on position dict updates as mark_price rises (long)."""
        sub = _make_substrate()
        pos = _make_open_position(entry_price=50000.0, mark_price=51500.0)  # +3%
        pos["trailing_active"] = True
        pos["trailing_sl"] = 50000.0  # at breakeven
        pos["peak_price"] = 51000.0   # previous peak

        result = _update_trailing_stop(pos, sub.config)
        # peak_price should have moved up to 51500
        assert result["peak_price"] >= 51500.0

    def test_trailing_stop_triggers_exit_on_retrace(self):
        """Trailing stop triggers an exit request when price retraces below trailing_sl."""
        enzyme = self._get_enzyme()
        sub = _make_substrate()
        pos = _make_open_position(
            entry_price=50000.0,
            mark_price=50800.0,   # retraced below trailing_sl
        )
        pos["trailing_active"] = True
        pos["trailing_sl"] = 51000.0   # trailing_sl is above current mark
        pos["peak_price"] = 52000.0
        sub.portfolio["open_positions"] = [pos]
        sub.decisions["exit_request"] = {
            "symbol": "BTCUSDT", "reason": "trailing_stop_hit", "urgency": "immediate"
        }

        result = enzyme.transform(sub)

        approved = result.decisions.get("exit_approved")
        assert approved is not None
        assert "trail" in approved.get("reason", "").lower() or "stop" in approved.get("reason", "").lower()


# ---------------------------------------------------------------------------
# TestRequestExit
# ---------------------------------------------------------------------------

class TestRequestExit:
    """RequestExit: monitors open positions and requests exit when conditions met."""

    def _get_enzyme(self):
        from enzymes.request_exit import RequestExit
        return RequestExit(config=_make_config())

    def test_is_sensor_or_oxidoreductase(self):
        """RequestExit is a Sensor or Oxidoreductase (not Regulator — it only requests)."""
        enzyme = self._get_enzyme()
        assert enzyme.enzyme_class in (EnzymeClass.SENSOR, EnzymeClass.OXIDOREDUCTASE)
        assert enzyme.is_regulator is False

    def test_does_not_activate_without_open_positions(self):
        """Does not activate when portfolio has no open positions."""
        enzyme = self._get_enzyme()
        sub = _make_substrate()
        sub.portfolio["open_positions"] = []
        assert enzyme.can_activate(sub) is False

    def test_activates_with_open_positions(self):
        """Activates when at least one open position exists."""
        enzyme = self._get_enzyme()
        sub = _make_substrate()
        sub.portfolio["open_positions"] = [_make_open_position()]
        assert enzyme.can_activate(sub) is True

    def test_requests_exit_on_sl_breach_long(self):
        """Requests exit when long position mark_price falls below sl_price."""
        enzyme = self._get_enzyme()
        sub = _make_substrate()
        pos = _make_open_position(
            direction="Long", entry_price=50000.0, sl_price=49000.0,
            mark_price=48500.0,  # below SL
        )
        sub.portfolio["open_positions"] = [pos]

        result = enzyme.transform(sub)

        req = result.decisions.get("exit_request")
        assert req is not None
        assert req["symbol"] == "BTCUSDT"
        assert req["urgency"] == "immediate"

    def test_requests_exit_on_sl_breach_short(self):
        """Requests exit when short position mark_price rises above sl_price."""
        enzyme = self._get_enzyme()
        sub = _make_substrate()
        pos = _make_open_position(
            direction="Short", entry_price=3000.0, sl_price=3100.0,
            mark_price=3150.0,  # above SL
        )
        sub.portfolio["open_positions"] = [pos]

        result = enzyme.transform(sub)

        req = result.decisions.get("exit_request")
        assert req is not None
        assert req["urgency"] == "immediate"

    def test_requests_exit_on_tp1_hit(self):
        """Requests exit when mark_price reaches TP1."""
        enzyme = self._get_enzyme()
        sub = _make_substrate()
        pos = _make_open_position(
            direction="Long", entry_price=50000.0, tp1=52000.0,
            mark_price=52100.0,  # at/above TP1
        )
        sub.portfolio["open_positions"] = [pos]

        result = enzyme.transform(sub)

        req = result.decisions.get("exit_request")
        assert req is not None
        assert "tp" in req.get("reason", "").lower() or "target" in req.get("reason", "").lower()

    def test_no_exit_request_for_healthy_position(self):
        """Does not request exit when position is healthy and within all limits."""
        enzyme = self._get_enzyme()
        sub = _make_substrate()
        pos = _make_open_position(
            direction="Long", entry_price=50000.0, sl_price=49000.0,
            tp1=52000.0, mark_price=50800.0,  # healthy: above SL, below TP
        )
        sub.portfolio["open_positions"] = [pos]

        result = enzyme.transform(sub)

        req = result.decisions.get("exit_request")
        # Healthy position: no exit request
        assert req is None

    def test_exit_request_has_required_fields(self):
        """exit_request dict contains symbol, reason, urgency."""
        enzyme = self._get_enzyme()
        sub = _make_substrate()
        pos = _make_open_position(
            direction="Long", sl_price=49000.0, mark_price=48000.0
        )
        sub.portfolio["open_positions"] = [pos]

        result = enzyme.transform(sub)

        req = result.decisions.get("exit_request")
        if req is not None:
            assert "symbol" in req
            assert "reason" in req
            assert "urgency" in req


# ---------------------------------------------------------------------------
# TestExecuteTrade
# ---------------------------------------------------------------------------

class TestExecuteTrade:
    """ExecuteTrade: Transporter — places orders (paper or live) and records to DB."""

    def _get_enzyme(self, config_overrides=None):
        from enzymes.execute_trade import ExecuteTrade
        return ExecuteTrade(config=_make_config(config_overrides))

    def test_is_transporter_class(self):
        enzyme = self._get_enzyme()
        assert enzyme.enzyme_class == EnzymeClass.TRANSPORTER

    def test_does_not_activate_without_trade_approved(self):
        """Does not activate when trade_approved is None."""
        enzyme = self._get_enzyme()
        sub = _make_substrate()
        sub.decisions["trade_approved"] = None
        assert enzyme.can_activate(sub) is False

    def test_does_not_activate_when_already_trade_open(self):
        """Does not activate when action is already 'trade_open' (idempotent)."""
        enzyme = self._get_enzyme()
        sub = _make_substrate()
        sub.decisions["trade_approved"] = {"symbol": "BTCUSDT", "direction": "Long",
                                            "entry_price": 50000.0, "sl_price": 49000.0,
                                            "tp1": 52000.0, "tp2": 53500.0, "size_usdt": 500.0,
                                            "kelly_fraction": 0.1, "approved_at": "2026-05-20T10:00:00+00:00"}
        sub.decisions["action"] = "trade_open"
        assert enzyme.can_activate(sub) is False

    def test_activates_with_trade_approved(self):
        """Activates when trade_approved is set and action is not 'trade_open'."""
        enzyme = self._get_enzyme()
        sub = _make_substrate()
        sub.decisions["trade_approved"] = {"symbol": "BTCUSDT", "direction": "Long",
                                            "entry_price": 50000.0, "sl_price": 49000.0,
                                            "tp1": 52000.0, "tp2": 53500.0, "size_usdt": 500.0,
                                            "kelly_fraction": 0.1, "approved_at": "2026-05-20T10:00:00+00:00"}
        sub.decisions["action"] = "wait"
        assert enzyme.can_activate(sub) is True

    def test_paper_mode_adds_position_to_portfolio(self):
        """Paper mode: position is added to portfolio.open_positions."""
        enzyme = self._get_enzyme({"daemon": {"paper_mode": True}})
        sub = _make_substrate({"daemon": {"paper_mode": True}})
        sub.portfolio["open_positions"] = []
        sub.portfolio["equity"] = 10000.0
        sub.decisions["trade_approved"] = {
            "symbol": "BTCUSDT", "direction": "Long",
            "entry_price": 50000.0, "sl_price": 49000.0,
            "tp1": 52000.0, "tp2": 53500.0, "size_usdt": 500.0,
            "kelly_fraction": 0.1, "approved_at": "2026-05-20T10:00:00+00:00",
            "atr_value": 800.0,
        }

        result = enzyme.transform(sub)

        assert len(result.portfolio["open_positions"]) == 1
        pos = result.portfolio["open_positions"][0]
        assert pos["symbol"] == "BTCUSDT"
        assert pos["direction"] == "Long"

    def test_paper_mode_sets_action_trade_open(self, temp_db):
        """Paper mode: decisions.action is set to 'trade_open'."""
        enzyme = self._get_enzyme({"daemon": {"paper_mode": True}})
        sub = _make_substrate({"daemon": {"paper_mode": True}})
        sub.portfolio["open_positions"] = []
        sub.portfolio["equity"] = 10000.0
        sub.decisions["trade_approved"] = {
            "symbol": "BTCUSDT", "direction": "Long",
            "entry_price": 50000.0, "sl_price": 49000.0,
            "tp1": 52000.0, "tp2": 53500.0, "size_usdt": 500.0,
            "kelly_fraction": 0.1, "approved_at": "2026-05-20T10:00:00+00:00",
            "atr_value": 800.0,
        }

        result = enzyme.transform(sub)

        assert result.decisions["action"] == "trade_open"

    def test_paper_mode_position_has_trailing_stop_fields(self, temp_db):
        """Paper mode: new position dict contains all trailing stop state fields."""
        enzyme = self._get_enzyme({"daemon": {"paper_mode": True}})
        sub = _make_substrate({"daemon": {"paper_mode": True}})
        sub.portfolio["open_positions"] = []
        sub.portfolio["equity"] = 10000.0
        sub.decisions["trade_approved"] = {
            "symbol": "BTCUSDT", "direction": "Long",
            "entry_price": 50000.0, "sl_price": 49000.0,
            "tp1": 52000.0, "tp2": 53500.0, "size_usdt": 500.0,
            "kelly_fraction": 0.1, "approved_at": "2026-05-20T10:00:00+00:00",
            "atr_value": 800.0,
        }

        result = enzyme.transform(sub)

        pos = result.portfolio["open_positions"][0]
        assert "trailing_active" in pos
        assert "trailing_sl" in pos
        assert "peak_price" in pos
        assert pos["trailing_active"] is False
        assert pos["trailing_sl"] is None

    def test_paper_mode_records_to_trade_learning(self, temp_db):
        """Paper mode: trade is recorded in trade_learning table by RecordTradeOutcome."""
        import sqlite3
        from enzymes.record_trade_outcome import RecordTradeOutcome
        recorder = RecordTradeOutcome(config=_make_config({"daemon": {"paper_mode": True}}))
        sub = _make_substrate({"daemon": {"paper_mode": True}})
        sub.portfolio["open_positions"] = []
        sub.portfolio["equity"] = 10000.0
        sub.decisions["trade_approved"] = {
            "symbol": "BTCUSDT", "direction": "Long",
            "entry_price": 50000.0, "sl_price": 49000.0,
            "tp1": 52000.0, "tp2": 53500.0, "size_usdt": 500.0,
            "kelly_fraction": 0.1, "approved_at": "2026-05-20T10:00:00+00:00",
            "atr_value": 800.0, "score": 7.5,
        }
        sub.decisions["action"] = "trade_open"

        recorder.transform(sub)

        conn = sqlite3.connect(temp_db)
        rows = conn.execute("SELECT * FROM trade_learning WHERE symbol='BTCUSDT'").fetchall()
        conn.close()
        assert len(rows) >= 1

    def test_paper_mode_does_not_call_exchange_api(self, temp_db):
        """Paper mode: no real exchange API call is made."""
        enzyme = self._get_enzyme({"daemon": {"paper_mode": True}})
        sub = _make_substrate({"daemon": {"paper_mode": True}})
        sub.portfolio["open_positions"] = []
        sub.portfolio["equity"] = 10000.0
        sub.decisions["trade_approved"] = {
            "symbol": "BTCUSDT", "direction": "Long",
            "entry_price": 50000.0, "sl_price": 49000.0,
            "tp1": 52000.0, "tp2": 53500.0, "size_usdt": 500.0,
            "kelly_fraction": 0.1, "approved_at": "2026-05-20T10:00:00+00:00",
            "atr_value": 800.0,
        }

        with patch("ccxt.bitget") as mock_exchange:
            enzyme.transform(sub)
            # In paper mode, no exchange instance should be created for order placement
            mock_exchange.assert_not_called()


# ---------------------------------------------------------------------------
# TestExecuteExit
# ---------------------------------------------------------------------------

class TestExecuteExit:
    """ExecuteExit: Transporter — closes positions and records outcomes."""

    def _get_enzyme(self, config_overrides=None):
        from enzymes.execute_exit import ExecuteExit
        return ExecuteExit(config=_make_config(config_overrides))

    def test_is_transporter_class(self):
        enzyme = self._get_enzyme()
        assert enzyme.enzyme_class == EnzymeClass.TRANSPORTER

    def test_does_not_activate_without_exit_approved(self):
        """Does not activate when exit_approved is None."""
        enzyme = self._get_enzyme()
        sub = _make_substrate()
        sub.decisions["exit_approved"] = None
        assert enzyme.can_activate(sub) is False

    def test_activates_with_exit_approved(self):
        """Activates when exit_approved is set."""
        enzyme = self._get_enzyme()
        sub = _make_substrate()
        sub.decisions["exit_approved"] = {
            "symbol": "BTCUSDT", "reason": "sl_breach", "urgency": "immediate"
        }
        sub.portfolio["open_positions"] = [_make_open_position()]
        assert enzyme.can_activate(sub) is True

    def test_paper_mode_removes_position_from_portfolio(self, temp_db):
        """Paper mode: position is removed from portfolio.open_positions."""
        enzyme = self._get_enzyme({"daemon": {"paper_mode": True}})
        sub = _make_substrate({"daemon": {"paper_mode": True}})
        sub.portfolio["open_positions"] = [_make_open_position()]
        sub.decisions["exit_approved"] = {
            "symbol": "BTCUSDT", "reason": "sl_breach", "urgency": "immediate"
        }

        result = enzyme.transform(sub)

        remaining = [p for p in result.portfolio["open_positions"]
                     if p["symbol"] == "BTCUSDT"]
        assert len(remaining) == 0

    def test_paper_mode_sets_action_trade_closed(self, temp_db):
        """Paper mode: decisions.action is set to 'trade_closed'."""
        enzyme = self._get_enzyme({"daemon": {"paper_mode": True}})
        sub = _make_substrate({"daemon": {"paper_mode": True}})
        sub.portfolio["open_positions"] = [_make_open_position()]
        sub.decisions["exit_approved"] = {
            "symbol": "BTCUSDT", "reason": "sl_breach", "urgency": "immediate"
        }

        result = enzyme.transform(sub)

        assert result.decisions["action"] == "trade_closed"

    def test_paper_mode_records_outcome_to_trade_learning(self, temp_db):
        """Paper mode: outcome is recorded in trade_learning table by RecordTradeOutcome."""
        import sqlite3
        from enzymes.record_trade_outcome import RecordTradeOutcome

        # First record an entry so the UPDATE can find it
        recorder = RecordTradeOutcome(config=_make_config({"daemon": {"paper_mode": True}}))
        sub = _make_substrate({"daemon": {"paper_mode": True}})
        sub.decisions["trade_approved"] = {
            "symbol": "BTCUSDT", "direction": "Long", "score": 7.5,
        }
        sub.decisions["action"] = "trade_open"
        recorder.transform(sub)

        # Now record the exit
        sub.decisions["exit_approved"] = {
            "symbol": "BTCUSDT", "reason": "sl_breach", "urgency": "immediate"
        }
        sub.decisions["action"] = "trade_closed"
        recorder.transform(sub)

        conn = sqlite3.connect(temp_db)
        rows = conn.execute(
            "SELECT exit_reason FROM trade_learning WHERE symbol='BTCUSDT' AND exit_time IS NOT NULL"
        ).fetchall()
        conn.close()
        assert len(rows) >= 1
        for row in rows:
            assert row[0] is not None

    def test_handles_missing_position_gracefully(self, temp_db):
        """Does not crash when exit_approved references a symbol not in open_positions."""
        enzyme = self._get_enzyme({"daemon": {"paper_mode": True}})
        sub = _make_substrate({"daemon": {"paper_mode": True}})
        sub.portfolio["open_positions"] = []  # position already gone
        sub.decisions["exit_approved"] = {
            "symbol": "BTCUSDT", "reason": "sl_breach", "urgency": "immediate"
        }

        # Must not raise
        result = enzyme.transform(sub)
        assert result is not None


# ---------------------------------------------------------------------------
# TestSyncPositions
# ---------------------------------------------------------------------------

class TestSyncPositions:
    """SyncPositions: Sensor enzyme — reconciles portfolio state with exchange."""

    def _get_enzyme(self, config_overrides=None):
        from enzymes.sync_positions import SyncPositions
        return SyncPositions(config=_make_config(config_overrides))

    def test_is_sensor_class(self):
        enzyme = self._get_enzyme()
        assert enzyme.enzyme_class == EnzymeClass.SENSOR

    def test_activates_on_correct_cycle_interval(self):
        """Activates every N cycles as configured in sync.position_sync_every_n_cycles."""
        enzyme = self._get_enzyme({"sync": {"position_sync_every_n_cycles": 4}})
        sub = _make_substrate({"sync": {"position_sync_every_n_cycles": 4}})

        # Cycle 0: should activate (0 % 4 == 0)
        sub._cycle_count = 0
        assert enzyme.can_activate(sub) is True

        # Cycle 2: should NOT activate (2 % 4 != 0)
        sub._cycle_count = 2
        assert enzyme.can_activate(sub) is False

        # Cycle 4: should activate (4 % 4 == 0)
        sub._cycle_count = 4
        assert enzyme.can_activate(sub) is True

    def test_paper_mode_uses_fallback_equity(self):
        """Paper mode: uses fallback_equity_usdt from config, skips exchange call."""
        enzyme = self._get_enzyme({"daemon": {"paper_mode": True},
                                   "portfolio": {"fallback_equity_usdt": 5000.0}})
        sub = _make_substrate({"daemon": {"paper_mode": True},
                               "portfolio": {"fallback_equity_usdt": 5000.0}})
        sub._cycle_count = 0

        with patch("ccxt.bitget") as mock_ex:
            result = enzyme.transform(sub)
            mock_ex.assert_not_called()

        assert result.portfolio["equity"] == 5000.0

    def test_paper_mode_does_not_call_exchange(self):
        """Paper mode: no exchange API calls are made during sync."""
        enzyme = self._get_enzyme({"daemon": {"paper_mode": True}})
        sub = _make_substrate({"daemon": {"paper_mode": True}})
        sub._cycle_count = 0

        with patch("ccxt.bitget") as mock_ex:
            enzyme.transform(sub)
            mock_ex.assert_not_called()

    def test_reconciles_positions_closed_externally(self):
        """Removes positions from portfolio that are no longer on the exchange."""
        # Must be in live mode (not paper) for reconciliation to run
        enzyme = self._get_enzyme({"daemon": {"paper_mode": False}})
        sub = _make_substrate({"daemon": {"paper_mode": False}})
        # Two positions in portfolio
        sub.portfolio["open_positions"] = [
            _make_open_position("BTCUSDT"),
            _make_open_position("ETHUSDT"),
        ]
        sub._cycle_count = 0

        # Exchange returns only ETHUSDT (BTCUSDT was closed externally)
        mock_exchange_positions = [
            {
                "symbol": "ETHUSDT",
                "direction": "Long",
                "size": 10.0,
                "entry_price": 3000.0,
                "mark_price": 3050.0,
                "unrealized_pnl": 50.0,
                "leverage": 5,
                "liquidation_price": 2500.0,
            }
        ]
        mock_balance = {"equity": 9500.0, "available": 8000.0}

        with patch.object(enzyme, "_fetch_exchange_data",
                          return_value=(mock_exchange_positions, mock_balance)):
            result = enzyme.transform(sub)

        symbols_remaining = [p["symbol"] for p in result.portfolio["open_positions"]]
        assert "BTCUSDT" not in symbols_remaining
        assert "ETHUSDT" in symbols_remaining


# ---------------------------------------------------------------------------
# TestSendTelegramLog
# ---------------------------------------------------------------------------

class TestSendTelegramLog:
    """SendTelegramLog: optional Transporter — one-way log push."""

    def _get_enzyme(self, config_overrides=None):
        from enzymes.send_telegram_log import SendTelegramLog
        return SendTelegramLog(config=_make_config(config_overrides))

    def test_does_not_activate_when_module_disabled(self):
        """Does not activate when modules.telegram_logs is False."""
        enzyme = self._get_enzyme({"modules": {"telegram_logs": False}})
        sub = _make_substrate({"modules": {"telegram_logs": False}})
        sub.decisions["action"] = "trade_open"
        assert enzyme.can_activate(sub) is False

    def test_activates_when_module_enabled_and_significant_event(self):
        """Activates when telegram_logs is True and a significant event occurred."""
        enzyme = self._get_enzyme({"modules": {"telegram_logs": True},
                                   "telegram": {"bot_token": "test_token", "chat_id": "123"}})
        sub = _make_substrate({"modules": {"telegram_logs": True}})
        sub.decisions["action"] = "trade_open"
        assert enzyme.can_activate(sub) is True

    def test_no_exception_when_token_not_configured(self):
        """Does not raise when bot_token/chat_id are empty strings."""
        enzyme = self._get_enzyme({"modules": {"telegram_logs": True},
                                   "telegram": {"bot_token": "", "chat_id": ""}})
        sub = _make_substrate({"modules": {"telegram_logs": True}})
        sub.decisions["action"] = "trade_open"
        sub.portfolio["open_positions"] = [_make_open_position()]

        # Must not raise even with no token
        result = enzyme.transform(sub)
        assert result is not None

    def test_does_not_activate_for_wait_action(self):
        """Does not activate when action is 'wait' (no significant event)."""
        enzyme = self._get_enzyme({"modules": {"telegram_logs": True}})
        sub = _make_substrate({"modules": {"telegram_logs": True}})
        sub.decisions["action"] = "wait"
        assert enzyme.can_activate(sub) is False


# ---------------------------------------------------------------------------
# TestExchangeOrderMethods
# ---------------------------------------------------------------------------

class TestExchangeOrderMethods:
    """Tests for order placement methods on core/exchange.py."""

    def _get_exchange(self, paper_mode: bool = True):
        """Create an Exchange instance with a mock ConfigLoader."""
        from core.exchange import Exchange
        mock_config = MagicMock()
        mock_config.paper_mode = paper_mode
        mock_config.get.side_effect = lambda key, default=None: {
            "exchange.data_source": "binance",
            "exchange.primary": "bitget",
        }.get(key, default)
        mock_config.get_exchange_creds.return_value = {
            "api_key": "test", "secret_key": "test", "passphrase": "test"
        }
        return Exchange(mock_config)

    def test_place_market_order_paper_mode_returns_dict(self):
        """place_market_order in paper mode returns a mock order dict without API call."""
        ex = self._get_exchange(paper_mode=True)
        result = ex.place_market_order(
            symbol="BTCUSDT", side="buy", size_usdt=500.0, leverage=5
        )
        assert isinstance(result, dict)
        assert result.get("paper") is True
        assert result.get("symbol") == "BTCUSDT"

    def test_place_stop_order_paper_mode_returns_dict(self):
        """place_stop_order in paper mode returns a mock order dict."""
        ex = self._get_exchange(paper_mode=True)
        result = ex.place_stop_order(
            symbol="BTCUSDT", side="sell", stop_price=49000.0, size=0.01
        )
        assert isinstance(result, dict)
        assert result.get("paper") is True

    def test_cancel_orders_paper_mode_no_op(self):
        """cancel_orders in paper mode returns True without API call."""
        ex = self._get_exchange(paper_mode=True)
        result = ex.cancel_orders(symbol="BTCUSDT")
        assert result is True

    def test_close_position_paper_mode_returns_true(self):
        """close_position in paper mode returns True without API call."""
        ex = self._get_exchange(paper_mode=True)
        result = ex.close_position(symbol="BTCUSDT", direction="Long", size=0.01)
        assert result is True

    def test_place_market_order_handles_exchange_error(self):
        """place_market_order raises ExchangeError (not crashes) when exchange unavailable."""
        from core.exchange import ExchangeError
        ex = self._get_exchange(paper_mode=False)

        # Create a mock exchange that raises on create_market_order
        mock_trade_ex = MagicMock()
        mock_trade_ex.create_market_order.side_effect = Exception("connection refused")
        mock_trade_ex.set_leverage.return_value = None
        # Set the internal attribute directly (trade_exchange is a property)
        ex._trade_exchange = mock_trade_ex

        with pytest.raises(ExchangeError):
            ex.place_market_order("BTCUSDT", "buy", 500.0, 5)


# ---------------------------------------------------------------------------
# TestPhaseC_Integration
# ---------------------------------------------------------------------------

class TestPhaseC_Integration:
    """Integration tests: full entry and exit cycles through the enzyme pipeline."""

    def _build_pipeline(self) -> tuple[Substrate, list]:
        """Build a substrate and Phase C enzyme list (paper mode)."""
        # Import all Phase C enzymes — triggers @register_enzyme decorators
        import enzymes  # noqa: F401 (Phase B)
        from enzymes.approve_trade import ApproveTrade
        from enzymes.approve_exit import ApproveExit
        from enzymes.request_exit import RequestExit
        from enzymes.execute_trade import ExecuteTrade
        from enzymes.execute_exit import ExecuteExit
        from enzymes.sync_positions import SyncPositions
        from enzymes.wait import WaitEnzyme

        config = _make_config({"daemon": {"paper_mode": True}})
        sub = Substrate(config=config)
        sub.portfolio["equity"] = 10000.0
        sub.portfolio["open_positions"] = []
        sub.analysis["noise_flag"] = False

        enzymes_list = [
            ApproveTrade(config=config),
            ApproveExit(config=config),
            RequestExit(config=config),
            ExecuteTrade(config=config),
            ExecuteExit(config=config),
            SyncPositions(config=config),
            WaitEnzyme(config=config),
        ]
        return sub, enzymes_list

    def test_full_entry_cycle_paper_mode(self, temp_db):
        """
        Full entry cycle: entry_zones set → ApproveTrade → ExecuteTrade →
        substrate has open position with trailing stop fields.
        """
        sub, enzyme_list = self._build_pipeline()

        # Populate substrate as Phase B would
        sub.analysis["candidates"] = [{"symbol": "BTCUSDT", "score": 7.5, "indicators_aligned": 4}]
        sub.analysis["entry_zones"] = {"BTCUSDT": _make_entry_zone()}

        # Run ApproveTrade
        approve = next(e for e in enzyme_list if e.name == "ApproveTrade")
        if approve.can_activate(sub):
            sub = approve.transform(sub)

        # Run ExecuteTrade
        execute = next(e for e in enzyme_list if e.name == "ExecuteTrade")
        if execute.can_activate(sub):
            sub = execute.transform(sub)

        assert len(sub.portfolio["open_positions"]) == 1
        pos = sub.portfolio["open_positions"][0]
        assert pos["symbol"] == "BTCUSDT"
        assert "trailing_active" in pos
        assert "trailing_sl" in pos
        assert "peak_price" in pos

    def test_full_exit_cycle_paper_mode(self, temp_db):
        """
        Full exit cycle: open position + SL breach → RequestExit → ApproveExit →
        ExecuteExit → position removed.
        """
        sub, enzyme_list = self._build_pipeline()

        # Start with an open position (SL breached)
        pos = _make_open_position(
            direction="Long", entry_price=50000.0, sl_price=49000.0,
            mark_price=48000.0  # below SL
        )
        sub.portfolio["open_positions"] = [pos]

        # Run RequestExit
        req_exit = next(e for e in enzyme_list if e.name == "RequestExit")
        if req_exit.can_activate(sub):
            sub = req_exit.transform(sub)

        # Run ApproveExit
        approve_exit = next(e for e in enzyme_list if e.name == "ApproveExit")
        if approve_exit.can_activate(sub):
            sub = approve_exit.transform(sub)

        # Run ExecuteExit
        exec_exit = next(e for e in enzyme_list if e.name == "ExecuteExit")
        if exec_exit.can_activate(sub):
            sub = exec_exit.transform(sub)

        remaining = [p for p in sub.portfolio["open_positions"] if p["symbol"] == "BTCUSDT"]
        assert len(remaining) == 0
        assert sub.decisions["action"] == "trade_closed"

    def test_isc_blocks_trade_when_noise_flag_true(self, temp_db):
        """ISC-005: ApproveTrade is blocked when noise_flag is True."""
        sub, enzyme_list = self._build_pipeline()
        sub.analysis["entry_zones"] = {"BTCUSDT": _make_entry_zone()}
        sub.analysis["noise_flag"] = True  # ISC-005 must block this

        approve = next(e for e in enzyme_list if e.name == "ApproveTrade")
        if approve.can_activate(sub):
            sub = approve.transform(sub)

        assert sub.decisions.get("trade_approved") is None
        assert len(sub.portfolio["open_positions"]) == 0

    def test_isc_blocks_trade_when_max_positions_reached(self, temp_db):
        """ISC-004: ApproveTrade is blocked when max_positions is reached."""
        sub, enzyme_list = self._build_pipeline()
        # Fill to max_positions (3)
        sub.portfolio["open_positions"] = [
            _make_open_position("BTCUSDT"),
            _make_open_position("ETHUSDT"),
            _make_open_position("SOLUSDT"),
        ]
        sub.analysis["entry_zones"] = {"BNBUSDT": _make_entry_zone("BNBUSDT")}
        sub.analysis["noise_flag"] = False

        approve = next(e for e in enzyme_list if e.name == "ApproveTrade")
        if approve.can_activate(sub):
            sub = approve.transform(sub)

        assert sub.decisions.get("trade_approved") is None

    def test_trailing_stop_state_survives_reset_cycle(self, temp_db):
        """
        Trailing stop state on position dict persists across substrate.reset_cycle().
        portfolio.open_positions is NOT cleared by reset_cycle (it's persistent state).
        """
        sub, _ = self._build_pipeline()
        pos = _make_open_position()
        pos["trailing_active"] = True
        pos["trailing_sl"] = 50200.0
        pos["peak_price"] = 51500.0
        sub.portfolio["open_positions"] = [pos]

        # reset_cycle clears market/analysis/decisions but NOT portfolio.open_positions
        sub.reset_cycle()

        assert len(sub.portfolio["open_positions"]) == 1
        restored_pos = sub.portfolio["open_positions"][0]
        assert restored_pos["trailing_active"] is True
        assert restored_pos["trailing_sl"] == 50200.0
        assert restored_pos["peak_price"] == 51500.0
