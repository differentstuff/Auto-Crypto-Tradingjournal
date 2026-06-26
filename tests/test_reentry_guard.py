"""
tests/test_reentry_guard.py -- Tests for Fix 2: Three-layer re-entry guard.

Verify that the re-entry guard blocks entries during cooldown,
after a recent close, when no new candle has formed, and when
signal re-confirmation fails.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import make_full_config
from core.substrate import Substrate
from enzymes.approve_trade import ApproveTrade
from enzymes.execute_exit import ExecuteExit


def _make_substrate(**overrides) -> Substrate:
    cfg = make_full_config(**overrides)
    return Substrate(config=cfg)


def _make_entry_zone(
    symbol="BTCUSDT",
    direction="Long",
    entry_price=50000.0,
    sl_price=49000.0,
    tp1=52000.0,
    tp2=53500.0,
    atr_value=800.0,
    atr_pct=1.6,
    score=7.5,
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


def _make_candidate(
    symbol="BTCUSDT",
    score=7.5,
) -> dict:
    return {
        "symbol": symbol,
        "score": score,
        "max_score": 10.0,
        "pct": 0.75,
        "label": "strong",
        "indicators_aligned": 4,
        "details": [],
        "confirmation_tf_misaligned": False,
    }


class TestReentryGuardRecentlyClosedOnExit:

    def test_recently_closed_populated_on_full_close(self):
        sub = _make_substrate()
        pos = {
            "symbol": "BTCUSDT",
            "direction": "Long",
            "entry_price": 50000.0,
            "sl_price": 49000.0,
            "mark_price": 47000.0,
            "size_usdt": 500.0,
            "atr_value": 800.0,
            "opened_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            "trailing_active": False,
            "trailing_sl": None,
            "peak_price": 51000.0,
        }
        sub.portfolio["open_positions"] = [pos]
        sub.portfolio["equity"] = 10000.0

        candle_close_ts = "2026-06-26T12:00:00+00:00"
        sub.market["last_candle_close_ts"] = {"BTCUSDT_4H": candle_close_ts}

        sub.decisions["exit_approved"] = {
            "symbol": "BTCUSDT",
            "reason": "hard_sl_breach",
        }

        enzyme = ExecuteExit()
        result = enzyme.transform(sub)

        rc = result.market.get("recently_closed", {})
        assert "BTCUSDT" in rc, "recently_closed should contain the closed symbol"
        assert rc["BTCUSDT"] == candle_close_ts, \
            f"recently_closed timestamp should be candle close ts, got {rc['BTCUSDT']}"

    def test_last_traded_candle_idx_populated_on_full_close(self):
        sub = _make_substrate()
        pos = {
            "symbol": "BTCUSDT",
            "direction": "Long",
            "entry_price": 50000.0,
            "sl_price": 49000.0,
            "mark_price": 47000.0,
            "size_usdt": 500.0,
            "atr_value": 800.0,
            "opened_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            "trailing_active": False,
            "trailing_sl": None,
            "peak_price": 51000.0,
        }
        sub.portfolio["open_positions"] = [pos]
        sub.portfolio["equity"] = 10000.0

        candle_close_ts = "2026-06-26T12:00:00+00:00"
        sub.market["last_candle_close_ts"] = {"BTCUSDT_4H": candle_close_ts}

        sub.decisions["exit_approved"] = {
            "symbol": "BTCUSDT",
            "reason": "hard_sl_breach",
        }

        enzyme = ExecuteExit()
        result = enzyme.transform(sub)

        ltci = result.market.get("last_traded_candle_idx", {})
        assert "BTCUSDT" in ltci, "last_traded_candle_idx should contain the closed symbol"
        assert ltci["BTCUSDT"] == candle_close_ts


class TestReentryGuardCooldown:

    def test_reentry_blocked_during_cooldown(self):
        sub = _make_substrate(strategy={"reentry_cooldown_candles": 2})
        sub.portfolio["open_positions"] = []
        sub.portfolio["equity"] = 10000.0

        closed_ts = datetime.now(timezone.utc).isoformat()
        sub.market["recently_closed"] = {"BTCUSDT": closed_ts}

        zone = _make_entry_zone()
        sub.analysis["entry_zones"] = {"BTCUSDT": zone}

        enzyme = ApproveTrade()
        result = enzyme.transform(sub)

        assert result.decisions.get("trade_approved") is None, \
            "Re-entry should be blocked during cooldown (0 candles elapsed)"

    def test_reentry_allowed_after_cooldown_expires(self):
        closed_ts = (datetime.now(timezone.utc) - timedelta(hours=16)).isoformat()
        sub = _make_substrate(strategy={"reentry_cooldown_candles": 2})
        sub.portfolio["open_positions"] = []
        sub.portfolio["equity"] = 10000.0

        sub.market["recently_closed"] = {"BTCUSDT": closed_ts}

        zone = _make_entry_zone()
        sub.analysis["entry_zones"] = {"BTCUSDT": zone}
        sub.analysis["candidates"] = [_make_candidate()]

        enzyme = ApproveTrade()
        result = enzyme.transform(sub)

        approved = result.decisions.get("trade_approved")
        assert approved is not None, \
            "Re-entry should be allowed after cooldown expires (4+ candles on 4h TF)"

    def test_reentry_cooldown_disabled_when_null(self):
        sub = _make_substrate(strategy={
            "reentry_cooldown_candles": None,
            "reentry_require_signal_confirm": None,
        })
        sub.portfolio["open_positions"] = []
        sub.portfolio["equity"] = 10000.0

        closed_ts = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
        sub.market["recently_closed"] = {"BTCUSDT": closed_ts}

        zone = _make_entry_zone()
        sub.analysis["entry_zones"] = {"BTCUSDT": zone}

        enzyme = ApproveTrade()
        result = enzyme.transform(sub)

        approved = result.decisions.get("trade_approved")
        assert approved is not None, \
            "Re-entry should not block when reentry_cooldown_candles and reentry_require_signal_confirm are None"


class TestReentryGuardBarConfirmation:

    def test_no_new_candle_blocks_reentry(self):
        current_candle_ts = datetime.now(timezone.utc).replace(
            minute=0, second=0, microsecond=0
        ).isoformat()

        sub = _make_substrate()
        sub.portfolio["open_positions"] = []
        sub.portfolio["equity"] = 10000.0

        sub.market["last_traded_candle_idx"] = {"BTCUSDT": current_candle_ts}

        zone = _make_entry_zone()
        sub.analysis["entry_zones"] = {"BTCUSDT": zone}

        enzyme = ApproveTrade()
        result = enzyme.transform(sub)

        approved = result.decisions.get("trade_approved")
        assert approved is None, \
            "Re-entry should be blocked when no new candle has formed since last trade"


class TestReentryGuardSignalReconfirm:

    def test_signal_reconfirm_blocks_when_no_candidate(self):
        closed_ts = (datetime.now(timezone.utc) - timedelta(hours=16)).isoformat()
        sub = _make_substrate(strategy={
            "reentry_cooldown_candles": 2,
            "reentry_require_signal_confirm": True,
        })
        sub.portfolio["open_positions"] = []
        sub.portfolio["equity"] = 10000.0

        sub.market["recently_closed"] = {"BTCUSDT": closed_ts}
        sub.analysis["candidates"] = []
        sub.analysis["entry_zones"] = {"BTCUSDT": _make_entry_zone()}

        enzyme = ApproveTrade()
        result = enzyme.transform(sub)

        approved = result.decisions.get("trade_approved")
        assert approved is None, \
            "Re-entry should be blocked when signal re-confirmation required but no candidate exists"

    def test_signal_reconfirm_allows_when_candidate_present(self):
        closed_ts = (datetime.now(timezone.utc) - timedelta(hours=16)).isoformat()
        sub = _make_substrate(strategy={
            "reentry_cooldown_candles": 2,
            "reentry_require_signal_confirm": True,
        })
        sub.portfolio["open_positions"] = []
        sub.portfolio["equity"] = 10000.0

        sub.market["recently_closed"] = {"BTCUSDT": closed_ts}
        sub.analysis["candidates"] = [_make_candidate()]
        sub.analysis["entry_zones"] = {"BTCUSDT": _make_entry_zone()}

        enzyme = ApproveTrade()
        result = enzyme.transform(sub)

        approved = result.decisions.get("trade_approved")
        assert approved is not None, \
            "Re-entry should be allowed when cooldown expired and candidate present for signal re-confirm"

    def test_signal_reconfirm_disabled_when_null(self):
        closed_ts = datetime.now(timezone.utc).isoformat()
        sub = _make_substrate(strategy={
            "reentry_cooldown_candles": None,
            "reentry_require_signal_confirm": None,
        })
        sub.portfolio["open_positions"] = []
        sub.portfolio["equity"] = 10000.0

        sub.market["recently_closed"] = {"BTCUSDT": closed_ts}
        sub.analysis["entry_zones"] = {"BTCUSDT": _make_entry_zone()}

        enzyme = ApproveTrade()
        result = enzyme.transform(sub)

        approved = result.decisions.get("trade_approved")
        assert approved is not None, \
            "When both cooldown and signal re-confirm are disabled, entry should proceed"
