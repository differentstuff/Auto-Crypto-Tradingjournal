"""
tests/test_sl_fill.py -- Tests for Fix 1: SL fill semantics.

Verify that hard_sl_breach and trailing_stop_hit fill at the stop price
(not mark_price/candle close), and TP/soft exits still fill at mark_price.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import make_full_config
from core.substrate import Substrate
from enzymes.execute_exit import ExecuteExit


def _make_substrate(**overrides) -> Substrate:
    cfg = make_full_config(**overrides)
    return Substrate(config=cfg)


def _make_position(
    symbol="BTCUSDT",
    direction="Long",
    entry_price=50000.0,
    sl_price=49000.0,
    mark_price=47000.0,
    trailing_sl=None,
    size_usdt=500.0,
    atr_value=800.0,
) -> dict:
    return {
        "symbol": symbol,
        "direction": direction,
        "entry_price": entry_price,
        "sl_price": sl_price,
        "mark_price": mark_price,
        "trailing_active": trailing_sl is not None,
        "trailing_sl": trailing_sl,
        "peak_price": mark_price if trailing_sl is None else max(mark_price, trailing_sl + 500),
        "size_usdt": size_usdt,
        "atr_value": atr_value,
        "opened_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
    }


class TestSLFillSemantics:

    def test_hard_sl_breach_fills_at_sl_price(self):
        sub = _make_substrate()
        pos = _make_position(
            direction="Long",
            entry_price=50000.0,
            sl_price=49000.0,
            mark_price=47000.0,
        )
        sub.portfolio["open_positions"] = [pos]
        sub.portfolio["equity"] = 10000.0
        sub.decisions["exit_approved"] = {
            "symbol": "BTCUSDT",
            "reason": "hard_sl_breach",
        }

        enzyme = ExecuteExit()
        result = enzyme.transform(sub)

        exit_approved = result.decisions["exit_approved"]
        assert exit_approved["exit_price"] == 49000.0, \
            f"hard_sl_breach should fill at sl_price (49000), got {exit_approved['exit_price']}"

    def test_hard_sl_breach_short_fills_at_sl_price(self):
        sub = _make_substrate()
        pos = _make_position(
            direction="Short",
            entry_price=50000.0,
            sl_price=51000.0,
            mark_price=53000.0,
        )
        sub.portfolio["open_positions"] = [pos]
        sub.portfolio["equity"] = 10000.0
        sub.decisions["exit_approved"] = {
            "symbol": "BTCUSDT",
            "reason": "hard_sl_breach",
        }

        enzyme = ExecuteExit()
        result = enzyme.transform(sub)

        exit_approved = result.decisions["exit_approved"]
        assert exit_approved["exit_price"] == 51000.0, \
            f"hard_sl_breach short should fill at sl_price (51000), got {exit_approved['exit_price']}"

    def test_trailing_stop_hit_fills_at_trailing_sl(self):
        sub = _make_substrate()
        pos = _make_position(
            direction="Long",
            entry_price=50000.0,
            sl_price=49000.0,
            mark_price=47000.0,
            trailing_sl=49500.0,
        )
        pos["trailing_active"] = True
        pos["peak_price"] = 51000.0
        sub.portfolio["open_positions"] = [pos]
        sub.portfolio["equity"] = 10000.0
        sub.decisions["exit_approved"] = {
            "symbol": "BTCUSDT",
            "reason": "trailing_stop_hit",
        }

        enzyme = ExecuteExit()
        result = enzyme.transform(sub)

        exit_approved = result.decisions["exit_approved"]
        assert exit_approved["exit_price"] == 49500.0, \
            f"trailing_stop_hit should fill at trailing_sl (49500), got {exit_approved['exit_price']}"

    def test_trailing_stop_hit_short_fills_at_trailing_sl(self):
        sub = _make_substrate()
        pos = _make_position(
            direction="Short",
            entry_price=50000.0,
            sl_price=51000.0,
            mark_price=53000.0,
            trailing_sl=50500.0,
        )
        pos["trailing_active"] = True
        pos["peak_price"] = 49000.0
        sub.portfolio["open_positions"] = [pos]
        sub.portfolio["equity"] = 10000.0
        sub.decisions["exit_approved"] = {
            "symbol": "BTCUSDT",
            "reason": "trailing_stop_hit",
        }

        enzyme = ExecuteExit()
        result = enzyme.transform(sub)

        exit_approved = result.decisions["exit_approved"]
        assert exit_approved["exit_price"] == 50500.0, \
            f"trailing_stop_hit short should fill at trailing_sl (50500), got {exit_approved['exit_price']}"

    def test_tp1_exit_uses_mark_price(self):
        sub = _make_substrate()
        pos = _make_position(
            direction="Long",
            entry_price=50000.0,
            sl_price=49000.0,
            mark_price=52000.0,
        )
        sub.portfolio["open_positions"] = [pos]
        sub.portfolio["equity"] = 10000.0
        sub.decisions["exit_approved"] = {
            "symbol": "BTCUSDT",
            "reason": "tp1_hit",
            "partial": True,
            "sell_pct": 40.0,
        }

        enzyme = ExecuteExit()
        result = enzyme.transform(sub)

        exit_approved = result.decisions["exit_approved"]
        assert exit_approved["exit_price"] == 52000.0, \
            f"tp1_hit should fill at mark_price (52000), got {exit_approved['exit_price']}"

    def test_soft_exit_uses_mark_price(self):
        sub = _make_substrate()
        pos = _make_position(
            direction="Long",
            entry_price=50000.0,
            sl_price=49000.0,
            mark_price=50800.0,
        )
        sub.portfolio["open_positions"] = [pos]
        sub.portfolio["equity"] = 10000.0
        sub.decisions["exit_approved"] = {
            "symbol": "BTCUSDT",
            "reason": "signal_reversal",
        }

        enzyme = ExecuteExit()
        result = enzyme.transform(sub)

        exit_approved = result.decisions["exit_approved"]
        assert exit_approved["exit_price"] == 50800.0, \
            f"signal_reversal should fill at mark_price (50800), got {exit_approved['exit_price']}"

    def test_sl_fill_long_pnl_less_severe_than_mark_price(self):
        sub = _make_substrate()
        pos = _make_position(
            direction="Long",
            entry_price=50000.0,
            sl_price=49000.0,
            mark_price=35000.0,
        )
        sub.portfolio["open_positions"] = [pos]
        sub.portfolio["equity"] = 10000.0
        sub.decisions["exit_approved"] = {
            "symbol": "BTCUSDT",
            "reason": "hard_sl_breach",
        }

        enzyme = ExecuteExit()
        result = enzyme.transform(sub)

        exit_approved = result.decisions["exit_approved"]
        pnl_pct_sl = exit_approved["pnl_pct"]
        pnl_pct_mark = ((35000.0 - 50000.0) / 50000.0) * 100

        assert pnl_pct_sl > pnl_pct_mark, \
            f"SL fill PnL ({pnl_pct_sl:.2f}%) should be less severe than mark_price PnL ({pnl_pct_mark:.2f}%)"

    def test_sl_fill_fallback_when_sl_price_missing(self):
        sub = _make_substrate()
        pos = _make_position(mark_price=47000.0)
        del pos["sl_price"]
        sub.portfolio["open_positions"] = [pos]
        sub.portfolio["equity"] = 10000.0
        sub.decisions["exit_approved"] = {
            "symbol": "BTCUSDT",
            "reason": "hard_sl_breach",
        }

        enzyme = ExecuteExit()
        result = enzyme.transform(sub)

        exit_approved = result.decisions["exit_approved"]
        assert exit_approved["exit_price"] == 47000.0, \
            "When sl_price is missing, fallback to mark_price"
