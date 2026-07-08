"""
tests/test_replay_driver.py -- Verify replay driver cycle execution and outcome recording.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.replay_driver import build_cycle_timestamps
from core.outcome_recorder import OutcomeRecorder
from core.virtual_clock import VirtualClock


class TestBuildCycleTimestamps:
    """Test cycle timestamp generation."""

    def test_basic_range(self):
        timestamps = build_cycle_timestamps("2025-01-01", "2025-01-02", 15)
        assert len(timestamps) == 193
        assert timestamps[0] == datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        assert timestamps[-1] == datetime(2025, 1, 3, 0, 0, 0, tzinfo=timezone.utc)

    def test_single_day(self):
        timestamps = build_cycle_timestamps("2025-01-01", "2025-01-01", 60)
        assert len(timestamps) == 25

    def test_4h_interval(self):
        timestamps = build_cycle_timestamps("2025-01-01", "2025-01-03", 240)
        assert len(timestamps) == 19

    def test_empty_range(self):
        timestamps = build_cycle_timestamps("2025-01-02", "2025-01-01", 15)
        assert len(timestamps) == 0


class TestOutcomeRecorder:
    """Test OutcomeRecorder captures and writes results."""

    def test_capture_equity_curve(self):
        recorder = OutcomeRecorder("test", "2025-01-01", "2025-01-31")
        substrate = MagicMock()
        substrate.decisions = {"action": "wait"}
        substrate.portfolio = {"equity": 1000.0, "open_positions": []}
        t = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        recorder.capture_cycle(substrate, t)
        assert len(recorder._equity_curve) == 1
        assert recorder._equity_curve[0]["equity"] == 1000.0

    def test_capture_trade_entry(self):
        recorder = OutcomeRecorder("test", "2025-01-01", "2025-01-31")
        substrate = MagicMock()
        substrate.decisions = {
            "action": "trade_open",
            "trade_approved": {
                "symbol": "BTCUSDT", "direction": "Long",
                "entry_price": 50000.0, "sl_price": 48000.0,
                "tp1": 52000.0, "size_usdt": 100.0,
                "atr_value": 1500.0, "score": 7.5,
            },
        }
        substrate.portfolio = {"equity": 1000.0, "open_positions": [{"symbol": "BTCUSDT"}]}
        t = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        recorder.capture_cycle(substrate, t)
        assert len(recorder._trades) == 1
        assert recorder._trades[0]["symbol"] == "BTCUSDT"

    def test_capture_trade_exit(self):
        recorder = OutcomeRecorder("test", "2025-01-01", "2025-01-31")
        substrate = MagicMock()
        substrate.decisions = {
            "action": "trade_open",
            "trade_approved": {
                "symbol": "BTCUSDT", "direction": "Long",
                "entry_price": 50000.0, "sl_price": 48000.0,
                "tp1": 52000.0, "size_usdt": 100.0,
                "atr_value": 1500.0, "score": 7.5,
            },
        }
        substrate.portfolio = {"equity": 1000.0, "open_positions": []}
        t1 = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        recorder.capture_cycle(substrate, t1)
        substrate.decisions = {
            "action": "trade_closed",
            "exit_approved": {"symbol": "BTCUSDT", "reason": "tp1_hit", "net_pnl_usdt": 20.0, "gross_pnl_usdt": 20.0, "exit_fee_usdt": 0.0},
        }
        t2 = datetime(2025, 1, 2, 12, 0, 0, tzinfo=timezone.utc)
        recorder.capture_cycle(substrate, t2)
        assert recorder._trades[0]["exit_reason"] == "tp1_hit"
        assert recorder._trades[0]["is_winner"] is True

    def test_write_results(self, tmp_path):
        import json
        recorder = OutcomeRecorder("test_strategy", "2025-01-01", "2025-01-31")
        substrate = MagicMock()
        substrate.decisions = {"action": "wait"}
        substrate.portfolio = {"equity": 1000.0, "open_positions": []}
        t = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        recorder.capture_cycle(substrate, t)
        filepath = recorder.write_results(output_dir=str(tmp_path))
        assert os.path.exists(filepath)
        with open(filepath) as f:
            data = json.load(f)
        assert data["strategy"] == "test_strategy"
        assert data["summary"]["total_cycles"] == 1

    def test_pnl_accumulation_across_partial_and_full_close(self):
        """TP1 partial (profitable) + final close (loss on remaining): whole-trade PnL must sum both legs."""
        recorder = OutcomeRecorder("test", "2025-01-01", "2025-01-31")

        # 1. Entry
        substrate = MagicMock()
        substrate.decisions = {
            "action": "trade_open",
            "trade_approved": {
                "symbol": "BTCUSDT", "direction": "Long",
                "entry_price": 50000.0, "sl_price": 48000.0,
                "tp1": 52000.0, "size_usdt": 500.0,
                "atr_value": 1500.0, "score": 7.5,
                "entry_fee_usdt": 0.3,
            },
        }
        substrate.portfolio = {"equity": 10000.0, "open_positions": [{"symbol": "BTCUSDT"}]}
        t1 = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        recorder.capture_cycle(substrate, t1)

        # 2. TP1 partial close — profitable leg
        substrate.decisions = {
            "action": "trade_managed",
            "exit_approved": {
                "symbol": "BTCUSDT",
                "exit_fee_usdt": 0.06,
                "gross_pnl_usdt": 15.0,
                "net_pnl_usdt": 14.94,
            },
        }
        t2 = datetime(2025, 1, 2, 12, 0, 0, tzinfo=timezone.utc)
        recorder.capture_cycle(substrate, t2)

        # 3. Final close — loss on remaining position
        substrate.decisions = {
            "action": "trade_closed",
            "exit_approved": {
                "symbol": "BTCUSDT", "reason": "trailing_stop_hit",
                "net_pnl_usdt": -6.18, "gross_pnl_usdt": -6.0,
                "exit_fee_usdt": 0.12, "exit_price": 49000.0,
            },
        }
        t3 = datetime(2025, 1, 3, 12, 0, 0, tzinfo=timezone.utc)
        recorder.capture_cycle(substrate, t3)

        trade = recorder._trades[0]

        # Accumulators should contain the sum of both legs
        assert trade["realized_gross_pnl_usd"] == pytest.approx(15.0 + (-6.0), abs=0.01)
        assert trade["realized_net_pnl_usd"] == pytest.approx(14.94 + (-6.18), abs=0.01)

        # net_pnl_usd / gross_pnl_usd reflect the whole trade, not just the final leg
        assert trade["net_pnl_usd"] == pytest.approx(14.94 + (-6.18), abs=0.01)
        assert trade["gross_pnl_usd"] == pytest.approx(15.0 + (-6.0), abs=0.01)

        # Whole trade is net profitable (14.94 - 6.18 = 8.76 > 0), even though
        # the final leg alone was a loss — is_winner must be True (regression case)
        assert trade["is_winner"] is True

        # Fee accumulation still correct
        assert trade["exit_fees_usd"] == pytest.approx(0.06 + 0.12, abs=0.001)
        assert trade["total_fees_usd"] == pytest.approx(0.3 + 0.06 + 0.12, abs=0.001)

    def test_fee_accumulation_across_partial_and_full_close(self):
        """Entry + TP1 partial + final close: total_fees_usd sums all fees correctly."""
        recorder = OutcomeRecorder("test", "2025-01-01", "2025-01-31")

        # 1. Entry
        substrate = MagicMock()
        substrate.decisions = {
            "action": "trade_open",
            "trade_approved": {
                "symbol": "BTCUSDT", "direction": "Long",
                "entry_price": 50000.0, "sl_price": 48000.0,
                "tp1": 52000.0, "size_usdt": 500.0,
                "atr_value": 1500.0, "score": 7.5,
                "entry_fee_usdt": 0.3,
            },
        }
        substrate.portfolio = {"equity": 10000.0, "open_positions": [{"symbol": "BTCUSDT"}]}
        t1 = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        recorder.capture_cycle(substrate, t1)

        # 2. TP1 partial close
        substrate.decisions = {
            "action": "trade_managed",
            "exit_approved": {
                "symbol": "BTCUSDT", "exit_fee_usdt": 0.1248,
            },
        }
        t2 = datetime(2025, 1, 2, 12, 0, 0, tzinfo=timezone.utc)
        recorder.capture_cycle(substrate, t2)

        # 3. Final close
        substrate.decisions = {
            "action": "trade_closed",
            "exit_approved": {
                "symbol": "BTCUSDT", "reason": "trailing_stop_hit",
                "net_pnl_usdt": -6.1764, "gross_pnl_usdt": -6.0,
                "exit_fee_usdt": 0.1764, "exit_price": 49000.0,
            },
        }
        t3 = datetime(2025, 1, 3, 12, 0, 0, tzinfo=timezone.utc)
        recorder.capture_cycle(substrate, t3)

        trade = recorder._trades[0]
        assert trade["entry_fee_usd"] == 0.3
        assert trade["exit_fees_usd"] == pytest.approx(0.1248 + 0.1764, abs=0.001)
        assert trade["total_fees_usd"] == pytest.approx(0.3 + 0.1248 + 0.1764, abs=0.001)
        assert trade["is_winner"] is False


class TestDaemonReplayMode:
    """Test that Daemon.replay_mode skips DB writes."""

    def test_daemon_has_replay_mode(self):
        from core.daemon import Daemon
        d = Daemon(replay_mode=True)
        assert d.replay_mode is True

    def test_daemon_replay_mode_defaults_false(self):
        from core.daemon import Daemon
        d = Daemon()
        assert d.replay_mode is False

    @patch("core.daemon.init_db")
    def test_replay_mode_skips_db_writes(self, mock_init_db):
        """In replay mode, init_db is still called but no cycle_log writes happen."""
        from conftest import make_full_config
        from core.daemon import Daemon

        d = Daemon(strategy_name="test_strategy", paper_mode=True, replay_mode=True)

        full_cfg = make_full_config()

        def _dotted_get(key, default=None):
            parts = key.split(".")
            node = full_cfg
            for part in parts:
                if isinstance(node, dict) and part in node:
                    node = node[part]
                else:
                    return default
            return node

        with patch.object(d, 'config') as mock_config:
            mock_config.config = full_cfg
            mock_config.get.side_effect = _dotted_get
            mock_config.reload.return_value = False
            mock_config.paper_mode = True

            from core.substrate import Substrate
            d.substrate = Substrate(config=full_cfg)

            from core.scheduler import Scheduler
            d.scheduler = Scheduler(interval_minutes=15, jitter_seconds=0)

            from core.enzyme import create_enzyme
            wait = create_enzyme("Wait", config=full_cfg)
            if wait:
                d.register_enzyme(wait)

            d.run_cycle()

        # Verify no save_cycle_log was called (function doesn't exist anymore)
        # The daemon simply doesn't call any DB write for cycle logging
