"""
tests/test_challenger.py -- Unit tests for the Challenger system.

Tests CandidateQueue, WeightChallenger, ChallengerComparator, and
HypotheticalTracker with mocked substrate (no live DB/exchange calls).
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from tests.conftest import make_full_config
from core.substrate import Substrate


def _make_substrate(**config_overrides) -> Substrate:
    config = make_full_config(
        challenger={"enabled": True, "min_trades": 3, "min_improvement": 0.1},
        **config_overrides,
    )
    return Substrate(config=config)


class TestCandidateQueue:
    """Tests for the CandidateQueue FIFO queue."""

    def test_push_and_pop(self):
        """Push a candidate, then pop it — FIFO order."""
        substrate = _make_substrate()
        weights = {"rsi": 0.3, "macd": 0.2}
        with patch("learning.challenger._log_challenger_event"):
            from learning.challenger import CandidateQueue
            CandidateQueue.push(weights, source="test", substrate=substrate)
            entry = CandidateQueue.pop_next(substrate)
        assert entry is not None
        assert entry["weights"] == weights
        assert entry["source"] == "test"

    def test_pop_empty_queue(self):
        """Popping from an empty queue returns None."""
        substrate = _make_substrate()
        from learning.challenger import CandidateQueue
        result = CandidateQueue.pop_next(substrate)
        assert result is None

    def test_fifo_order(self):
        """Multiple pushes come out in FIFO order."""
        substrate = _make_substrate()
        from learning.challenger import CandidateQueue
        with patch("learning.challenger._log_challenger_event"):
            CandidateQueue.push({"rsi": 0.1}, source="first", substrate=substrate)
            CandidateQueue.push({"rsi": 0.2}, source="second", substrate=substrate)
            CandidateQueue.push({"rsi": 0.3}, source="third", substrate=substrate)
        assert CandidateQueue.pop_next(substrate)["source"] == "first"
        assert CandidateQueue.pop_next(substrate)["source"] == "second"
        assert CandidateQueue.pop_next(substrate)["source"] == "third"

    def test_queue_max_size_eviction(self):
        """When queue exceeds max size, oldest entry is evicted."""
        substrate = _make_substrate()
        from learning.challenger import CandidateQueue, _MAX_QUEUE_SIZE
        with patch("learning.challenger._log_challenger_event"):
            for i in range(_MAX_QUEUE_SIZE + 2):
                CandidateQueue.push({"rsi": i / 100}, source=f"src_{i}", substrate=substrate)
        queue = substrate.learning["challenger"]["candidate_queue"]
        assert len(queue) == _MAX_QUEUE_SIZE
        assert queue[0]["source"] == "src_2"


class TestWeightChallenger:
    """Tests for WeightChallenger activation, promotion, and discard."""

    def test_create_from_weights(self):
        """Creating a challenger sets weights, source, and trade_count=0."""
        substrate = _make_substrate()
        weights = {"rsi": 0.3, "macd": 0.2}
        with patch("learning.challenger._log_challenger_event"):
            from learning.challenger import WeightChallenger
            WeightChallenger.create_from_weights(weights, source="weight_adjuster", substrate=substrate)
        assert substrate.learning["challenger"]["weights"] == weights
        assert substrate.learning["challenger"]["source"] == "weight_adjuster"
        assert substrate.learning["challenger"]["trade_count"] == 0

    def test_promote_replaces_production_weights(self):
        """Promoting challenger weights replaces adjusted_weights in substrate."""
        substrate = _make_substrate()
        substrate.learning["adjusted_weights"] = {"rsi": 0.25, "macd": 0.25}
        with patch("learning.challenger._log_challenger_event"):
            from learning.challenger import WeightChallenger
            WeightChallenger.create_from_weights({"rsi": 0.3, "macd": 0.2}, source="test", substrate=substrate)
            WeightChallenger.promote(substrate, "test_promote", metrics={
                "production_profit_factor": 1.0, "challenger_profit_factor": 1.5,
            })
        assert substrate.learning["adjusted_weights"] == {"rsi": 0.3, "macd": 0.2}
        assert substrate.learning["challenger"]["weights"] is None

    def test_discard_clears_challenger(self):
        """Discarding clears the challenger without changing production weights."""
        substrate = _make_substrate()
        substrate.learning["adjusted_weights"] = {"rsi": 0.25, "macd": 0.25}
        with patch("learning.challenger._log_challenger_event"):
            from learning.challenger import WeightChallenger
            WeightChallenger.create_from_weights({"rsi": 0.3, "macd": 0.2}, source="test", substrate=substrate)
            WeightChallenger.discard(substrate, "test_discard", metrics={
                "production_profit_factor": 1.5, "challenger_profit_factor": 1.0,
            })
        assert substrate.learning["adjusted_weights"] == {"rsi": 0.25, "macd": 0.25}
        assert substrate.learning["challenger"]["weights"] is None

    def test_activate_next_candidate(self):
        """Activating next candidate from queue creates a new challenger."""
        substrate = _make_substrate()
        with patch("learning.challenger._log_challenger_event"):
            from learning.challenger import CandidateQueue, WeightChallenger
            CandidateQueue.push({"rsi": 0.3}, source="queue_test", substrate=substrate)
            result = WeightChallenger.activate_next_candidate(substrate)
        assert result is True
        assert substrate.learning["challenger"]["weights"] == {"rsi": 0.3}

    def test_activate_next_candidate_empty_queue(self):
        """Activating from empty queue returns False."""
        substrate = _make_substrate()
        from learning.challenger import WeightChallenger
        assert WeightChallenger.activate_next_candidate(substrate) is False

    def test_promote_without_active_challenger(self):
        """Promoting with no active challenger is a no-op with warning."""
        substrate = _make_substrate()
        from learning.challenger import WeightChallenger
        WeightChallenger.promote(substrate, "no_challenger")
        assert substrate.learning.get("adjusted_weights") == {}

    def test_create_replaces_existing_challenger(self):
        """Creating a new challenger replaces any existing one."""
        substrate = _make_substrate()
        with patch("learning.challenger._log_challenger_event"):
            from learning.challenger import WeightChallenger
            WeightChallenger.create_from_weights({"rsi": 0.1}, source="old", substrate=substrate)
            WeightChallenger.create_from_weights({"rsi": 0.2}, source="new", substrate=substrate)
        assert substrate.learning["challenger"]["weights"] == {"rsi": 0.2}
        assert substrate.learning["challenger"]["source"] == "new"


class TestChallengerComparator:
    """Tests for the ChallengerComparator profit factor evaluation."""

    def test_compute_profit_factor_basic(self):
        """Profit factor = gross_wins / gross_losses."""
        from learning.comparator import ChallengerComparator
        trades = [{"exit_pnl_pct": 2.0}, {"exit_pnl_pct": -1.0}, {"exit_pnl_pct": 3.0}, {"exit_pnl_pct": -0.5}]
        pf = ChallengerComparator.compute_profit_factor(trades)
        assert abs(pf - 5.0 / 1.5) < 0.001

    def test_compute_profit_factor_no_losses(self):
        """No losses → infinite profit factor (if wins exist)."""
        from learning.comparator import ChallengerComparator
        assert ChallengerComparator.compute_profit_factor([{"exit_pnl_pct": 2.0}]) == float("inf")

    def test_compute_profit_factor_empty(self):
        """No trades → profit factor is 0."""
        from learning.comparator import ChallengerComparator
        assert ChallengerComparator.compute_profit_factor([]) == 0.0

    def test_should_evaluate_below_min(self):
        """Not enough trades → should_evaluate returns False."""
        substrate = _make_substrate()
        substrate.learning.setdefault("challenger", {})["trade_count"] = 2
        from learning.comparator import ChallengerComparator
        assert ChallengerComparator.should_evaluate(substrate) is False

    def test_should_evaluate_at_min(self):
        """Enough trades → should_evaluate returns True."""
        substrate = _make_substrate()
        substrate.learning.setdefault("challenger", {})["trade_count"] = 3
        from learning.comparator import ChallengerComparator
        assert ChallengerComparator.should_evaluate(substrate) is True

    def test_evaluate_accumulating_no_weights(self):
        """No active challenger → accumulating."""
        substrate = _make_substrate()
        from learning.comparator import ChallengerComparator
        assert ChallengerComparator.evaluate(substrate) == "accumulating"

    def test_evaluate_accumulating_below_min_trades(self):
        """Active challenger but not enough trades → accumulating."""
        substrate = _make_substrate()
        substrate.learning.setdefault("challenger", {})["weights"] = {"rsi": 0.3}
        substrate.learning["challenger"]["trade_count"] = 1
        from learning.comparator import ChallengerComparator
        assert ChallengerComparator.evaluate(substrate) == "accumulating"


class TestHypotheticalTracker:
    """Tests for the HypotheticalTracker paper-trade tracker."""

    def _base_position(self, **overrides):
        pos = {
            "symbol": "BTCUSDT", "direction": "Long", "entry_price": 100000,
            "sl_price": 95000, "tp1": 110000, "mark_price": 100000,
            "atr_value": 2000, "trailing_active": False, "trailing_sl": None,
            "peak_price": 100000,
        }
        pos.update(overrides)
        return pos

    def test_check_exits_hard_sl_breach(self):
        """Position exits when hard SL is breached."""
        substrate = _make_substrate()
        substrate.market["last_prices"] = {"BTCUSDT": 90000}
        with patch("learning.challenger._log_challenger_event"), \
             patch("learning.hypothetical_tracker._update_trailing_stop", side_effect=lambda p, s: p):
            from learning.hypothetical_tracker import HypotheticalTracker
            remaining = HypotheticalTracker._check_exits(substrate, [self._base_position()])
        assert len(remaining) == 0

    def test_check_exits_tp1_hit(self):
        """Position exits when TP1 is hit."""
        substrate = _make_substrate()
        substrate.market["last_prices"] = {"BTCUSDT": 110000}
        with patch("learning.challenger._log_challenger_event"), \
             patch("learning.hypothetical_tracker._update_trailing_stop", side_effect=lambda p, s: p):
            from learning.hypothetical_tracker import HypotheticalTracker
            remaining = HypotheticalTracker._check_exits(substrate, [self._base_position(tp1=108000)])
        assert len(remaining) == 0

    def test_check_exits_no_exit(self):
        """Position stays open when no exit condition is met."""
        substrate = _make_substrate()
        substrate.market["last_prices"] = {"BTCUSDT": 102000}
        substrate.market["indicators"] = {}
        with patch("learning.hypothetical_tracker._update_trailing_stop", side_effect=lambda p, s: p):
            from learning.hypothetical_tracker import HypotheticalTracker
            remaining = HypotheticalTracker._check_exits(substrate, [self._base_position()])
        assert len(remaining) == 1

    def test_check_exits_short_sl_breach(self):
        """Short position exits when SL (above entry) is breached."""
        substrate = _make_substrate()
        substrate.market["last_prices"] = {"BTCUSDT": 105000}
        with patch("learning.challenger._log_challenger_event"), \
             patch("learning.hypothetical_tracker._update_trailing_stop", side_effect=lambda p, s: p):
            from learning.hypothetical_tracker import HypotheticalTracker
            remaining = HypotheticalTracker._check_exits(substrate, [
                self._base_position(direction="Short", sl_price=103000, tp1=92000)
            ])
        assert len(remaining) == 0


class TestTrailingStop:
    """Tests for the trailing stop logic in hypothetical_tracker."""

    def test_trailing_activates_at_threshold(self):
        """Trailing stop activates when profit exceeds activation_pct."""
        substrate = _make_substrate()
        from learning.hypothetical_tracker import _update_trailing_stop
        result = _update_trailing_stop({
            "entry_price": 100000, "mark_price": 102000, "direction": "Long",
            "atr_value": 2000, "trailing_active": False, "trailing_sl": None, "peak_price": 100000,
        }, substrate)
        assert result["trailing_active"] is True
        assert result["trailing_sl"] == 100000

    def test_trailing_not_active_below_threshold(self):
        """Trailing stop does NOT activate below profit threshold."""
        substrate = _make_substrate()
        from learning.hypothetical_tracker import _update_trailing_stop
        result = _update_trailing_stop({
            "entry_price": 100000, "mark_price": 101000, "direction": "Long",
            "atr_value": 2000, "trailing_active": False, "trailing_sl": None, "peak_price": 100000,
        }, substrate)
        assert result["trailing_active"] is False

    def test_trailing_ratchets_for_long(self):
        """Trailing SL only moves up for long positions."""
        substrate = _make_substrate()
        from learning.hypothetical_tracker import _update_trailing_stop
        result = _update_trailing_stop({
            "entry_price": 100000, "mark_price": 103000, "direction": "Long",
            "atr_value": 2000, "trailing_active": True, "trailing_sl": 100000, "peak_price": 100000,
        }, substrate)
        assert result["trailing_sl"] > 100000

    def test_trailing_does_not_mutate_original(self):
        """_update_trailing_stop returns a new dict, does not mutate original."""
        substrate = _make_substrate()
        pos = {
            "entry_price": 100000, "mark_price": 102000, "direction": "Long",
            "atr_value": 2000, "trailing_active": False, "trailing_sl": None, "peak_price": 100000,
        }
        from learning.hypothetical_tracker import _update_trailing_stop
        result = _update_trailing_stop(pos, substrate)
        assert pos["trailing_sl"] is None
        assert result["trailing_sl"] is not None


class TestChallengerIntegration:
    """Integration test: queue → activate → accumulate → promote/discard."""

    def test_full_promote_flow(self):
        """Weight adjuster pushes → queue → activate → accumulate → promote."""
        substrate = _make_substrate()
        substrate.learning["adjusted_weights"] = {"rsi": 0.25, "macd": 0.25}
        new_weights = {"rsi": 0.30, "macd": 0.20}
        with patch("learning.challenger._log_challenger_event"):
            from learning.challenger import WeightChallenger, CandidateQueue
            CandidateQueue.push(new_weights, source="weight_adjuster", substrate=substrate)
            WeightChallenger.activate_next_candidate(substrate)
        substrate.learning["challenger"]["trade_count"] = 5
        with patch("learning.challenger._log_challenger_event"), \
             patch("learning.comparator.ChallengerComparator.evaluate", return_value="promote"), \
             patch("learning.comparator.ChallengerComparator.get_metrics", return_value={
                 "production_profit_factor": 1.0, "challenger_profit_factor": 1.5,
             }):
            from learning.comparator import ChallengerComparator
            assert ChallengerComparator.evaluate(substrate) == "promote"
            WeightChallenger.promote(substrate, "profit_factor_improvement",
                                     metrics=ChallengerComparator.get_metrics(substrate))
        assert substrate.learning["adjusted_weights"] == new_weights
        assert substrate.learning["challenger"]["weights"] is None

    def test_full_discard_flow(self):
        """Weight adjuster pushes → queue → activate → accumulate → discard."""
        substrate = _make_substrate()
        substrate.learning["adjusted_weights"] = {"rsi": 0.25, "macd": 0.25}
        new_weights = {"rsi": 0.30, "macd": 0.20}
        with patch("learning.challenger._log_challenger_event"):
            from learning.challenger import WeightChallenger, CandidateQueue
            CandidateQueue.push(new_weights, source="weight_adjuster", substrate=substrate)
            WeightChallenger.activate_next_candidate(substrate)
        substrate.learning["challenger"]["trade_count"] = 5
        with patch("learning.challenger._log_challenger_event"), \
             patch("learning.comparator.ChallengerComparator.evaluate", return_value="discard"), \
             patch("learning.comparator.ChallengerComparator.get_metrics", return_value={
                 "production_profit_factor": 1.5, "challenger_profit_factor": 1.0,
             }):
            from learning.comparator import ChallengerComparator
            assert ChallengerComparator.evaluate(substrate) == "discard"
            WeightChallenger.discard(substrate, "insufficient_improvement",
                                     metrics=ChallengerComparator.get_metrics(substrate))
        assert substrate.learning["adjusted_weights"] == {"rsi": 0.25, "macd": 0.25}
        assert substrate.learning["challenger"]["weights"] is None


class TestChallengerDB:
    """Tests for the challenger_log DB table."""

    def test_challenger_log_table_exists(self, temp_db):
        """Migration 47 creates the challenger_log table."""
        import sqlite3
        conn = sqlite3.connect(temp_db)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='challenger_log'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1

    def test_challenger_log_columns(self, temp_db):
        """challenger_log has all required columns."""
        import sqlite3
        conn = sqlite3.connect(temp_db)
        cursor = conn.execute("PRAGMA table_info(challenger_log)")
        columns = {row[1] for row in cursor.fetchall()}
        conn.close()
        required = {
            "id", "strategy_uid", "event_type", "source", "timestamp",
            "challenger_weights_json", "current_weights_json", "reason",
            "production_profit_factor", "challenger_profit_factor",
            "promoted", "trade_count", "symbol", "entry_score",
            "exit_pnl_pct", "exit_reason", "signal_states_json",
        }
        assert required.issubset(columns)

    def test_insert_and_read_challenger_log(self, temp_db):
        """Can insert and read a challenger_log entry."""
        import sqlite3
        conn = sqlite3.connect(temp_db)
        conn.execute(
            "INSERT INTO challenger_log (strategy_uid, event_type, source, promoted, trade_count) VALUES (?, ?, ?, ?, ?)",
            ("test-uid", "activated", "weight_adjuster", 0, 0),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM challenger_log WHERE strategy_uid = 'test-uid'").fetchone()
        conn.close()
        assert row is not None
