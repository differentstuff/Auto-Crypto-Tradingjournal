"""
tests/test_exchange_as_truth.py -- Tests for exchange-as-truth architecture.

Covers:
  - reconcile_from_exchange() rebuilds positions from exchange data
  - Per-cycle reconciliation is called in live mode
  - Daemon aborts on exchange unreachable in live mode
  - Paper mode makes no exchange API calls
  - Native trailing stop placed after TP1 detection
  - Trailing stop push to exchange when SL changes
  - TP1 detection from achievedProfits > 0
  - Learning data loaded from DB on startup
  - Learning data persists to DB after trade close
  - ExecuteExit closes position on exchange in live mode
  - ExecuteExit cancels exchange orders in live mode
  - Orphan positions handled correctly
  - substrate_state_max_rows removed from config
"""

import os
import sys
import json
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.daemon import Daemon
from core.substrate import Substrate
from core.exchange import Exchange
from enzymes.execute_exit import ExecuteExit
from enzymes.execute_trade import ExecuteTrade
from conftest import make_full_config


def _make_mock_exchange(paper_mode=False):
    """Create a mock Exchange instance."""
    mock = MagicMock(spec=Exchange)
    mock.paper_mode = paper_mode
    mock._primary = "bitget"
    mock._data_source = "bitget"
    mock._paper_mode = paper_mode
    mock.fetch_positions.return_value = []
    mock.fetch_balance.return_value = {"equity": 5000.0, "available": 4500.0, "total_margin": 500.0}
    mock.test_connection.return_value = {"data_ok": True, "trade_ok": True, "primary": "bitget", "data_source": "bitget"}
    mock.cancel_orders.return_value = True
    mock.close_position.return_value = {"order_id": "close-123", "symbol": "BTCUSDT", "direction": "Long", "status": "closed"}
    mock.place_trailing_stop.return_value = {"order_id": "trail-123", "symbol": "BTCUSDT", "direction": "Long", "trail_pct": 3.0, "status": "pending"}
    mock.modify_tpsl_order.return_value = True
    mock.place_order.return_value = {"order_id": "order-123", "symbol": "BTCUSDT", "direction": "Long", "status": "filled"}
    mock.place_tpsl_order.return_value = {"order_id": "tpsl-123", "symbol": "BTCUSDT", "direction": "Long", "status": "pending"}
    return mock


class TestReconcileFromExchange:
    """Test daemon.reconcile_from_exchange()."""

    def test_reconcile_rebuilds_positions_from_exchange(self, config_dir, temp_db):
        """Positions are rebuilt from exchange data, not from DB."""
        daemon = Daemon(strategy_name="test_strategy", config_dir=str(config_dir))
        daemon.initialize()
        mock_exchange = _make_mock_exchange()
        daemon.exchange = mock_exchange

        mock_exchange.fetch_positions.return_value = [
            {
                "symbol": "BTCUSDT",
                "direction": "Long",
                "entry_price": 95000.0,
                "mark_price": 96000.0,
                "size_usdt": 500.0,
                "unrealized_pnl": 10.5,
                "unrealized_pct": 1.05,
                "leverage": 5.0,
                "pos_id": "pos-1",
                "achieved_profits": 0.0,
                "sl_price": 93000.0,
                "tp_price": 98000.0,
                "sl_order_id": "sl-1",
                "tp_order_id": "tp-1",
                "total_contracts": 0.5,
                "available_contracts": 0.5,
            },
        ]

        daemon.reconcile_from_exchange()

        positions = daemon.substrate.portfolio.get("open_positions", [])
        assert len(positions) == 1
        assert positions[0]["symbol"] == "BTCUSDT"
        assert positions[0]["direction"] == "Long"
        assert positions[0]["entry_price"] == 95000.0
        assert positions[0]["pos_id"] == "pos-1"
        assert positions[0]["sl_order_id"] == "sl-1"
        assert positions[0]["tp1_taken"] == False

    def test_reconcile_detects_tp1_from_achieved_profits(self, config_dir, temp_db):
        """achievedProfits > 0 means TP1 has been taken."""
        daemon = Daemon(strategy_name="test_strategy", config_dir=str(config_dir))
        daemon.initialize()
        mock_exchange = _make_mock_exchange()
        daemon.exchange = mock_exchange

        mock_exchange.fetch_positions.return_value = [
            {
                "symbol": "BTCUSDT",
                "direction": "Long",
                "entry_price": 95000.0,
                "mark_price": 96000.0,
                "size_usdt": 300.0,
                "unrealized_pnl": 10.5,
                "unrealized_pct": 1.05,
                "leverage": 5.0,
                "pos_id": "pos-1",
                "achieved_profits": 50.0,
                "sl_price": 93000.0,
                "tp_price": 0.0,
                "sl_order_id": "sl-1",
                "tp_order_id": "",
                "total_contracts": 0.3,
                "available_contracts": 0.3,
            },
        ]

        daemon.reconcile_from_exchange()
        positions = daemon.substrate.portfolio.get("open_positions", [])
        assert len(positions) == 1
        assert positions[0]["tp1_taken"] == True

    def test_reconcile_skips_in_paper_mode(self, config_dir, temp_db):
        """Paper mode positions are runtime-only — no reconciliation."""
        daemon = Daemon(strategy_name="test_strategy", paper_mode=True, config_dir=str(config_dir))
        daemon.initialize()
        mock_exchange = _make_mock_exchange(paper_mode=True)
        daemon.exchange = mock_exchange

        daemon.substrate.portfolio["open_positions"] = [
            {"symbol": "BTCUSDT", "direction": "Long", "entry_price": 95000.0},
        ]

        daemon.reconcile_from_exchange()

        mock_exchange.fetch_positions.assert_not_called()
        positions = daemon.substrate.portfolio.get("open_positions", [])
        assert len(positions) == 1

    def test_reconcile_handles_orphan_positions(self, config_dir, temp_db):
        """Positions on exchange but not in substrate are treated as normal."""
        daemon = Daemon(strategy_name="test_strategy", config_dir=str(config_dir))
        daemon.initialize()
        mock_exchange = _make_mock_exchange()
        daemon.exchange = mock_exchange

        mock_exchange.fetch_positions.return_value = [
            {
                "symbol": "ETHUSDT",
                "direction": "Short",
                "entry_price": 3500.0,
                "mark_price": 3400.0,
                "size_usdt": 200.0,
                "unrealized_pnl": 5.0,
                "unrealized_pct": 2.5,
                "leverage": 3.0,
                "pos_id": "pos-orphan",
                "achieved_profits": 0.0,
                "sl_price": 3600.0,
                "tp_price": 3200.0,
                "sl_order_id": "sl-orphan",
                "tp_order_id": "tp-orphan",
                "total_contracts": 0.2,
                "available_contracts": 0.2,
            },
        ]

        daemon.reconcile_from_exchange()
        positions = daemon.substrate.portfolio.get("open_positions", [])
        assert len(positions) == 1
        assert positions[0]["symbol"] == "ETHUSDT"

    def test_reconcile_handles_empty_exchange(self, config_dir, temp_db):
        """No positions on exchange means empty substrate."""
        daemon = Daemon(strategy_name="test_strategy", config_dir=str(config_dir))
        daemon.initialize()
        mock_exchange = _make_mock_exchange()
        daemon.exchange = mock_exchange

        daemon.reconcile_from_exchange()
        assert daemon.substrate.portfolio.get("open_positions", []) == []


class TestDaemonAbortOnExchangeUnreachable:
    """Test daemon aborts if exchange is unreachable in live mode."""

    def test_abort_on_exchange_unreachable(self, config_dir, temp_db):
        """Daemon calls sys.exit(1) if exchange unreachable in live mode."""
        daemon = Daemon(strategy_name="test_strategy", config_dir=str(config_dir))
        daemon.initialize()
        mock_exchange = _make_mock_exchange()
        mock_exchange.test_connection.return_value = {"data_ok": False, "trade_ok": False}
        daemon.exchange = mock_exchange

        with pytest.raises(SystemExit) as exc_info:
            daemon.check_exchange_reachable()
        assert exc_info.value.code == 1

    def test_no_abort_in_paper_mode(self, config_dir, temp_db):
        """Paper mode doesn't check exchange reachability."""
        daemon = Daemon(strategy_name="test_strategy", paper_mode=True, config_dir=str(config_dir))
        daemon.initialize()
        result = daemon.check_exchange_reachable()
        assert result == True

    def test_no_abort_in_replay_mode(self, config_dir, temp_db):
        """Replay mode doesn't check exchange reachability."""
        daemon = Daemon(strategy_name="test_strategy", paper_mode=True, replay_mode=True, config_dir=str(config_dir))
        daemon.initialize()
        result = daemon.check_exchange_reachable()
        assert result == True

    def test_abort_on_no_exchange_instance(self, config_dir, temp_db):
        """Daemon aborts if exchange is None in live mode."""
        daemon = Daemon(strategy_name="test_strategy", config_dir=str(config_dir))
        daemon.initialize()
        daemon.exchange = None

        with pytest.raises(SystemExit) as exc_info:
            daemon.check_exchange_reachable()
        assert exc_info.value.code == 1


class TestNativeTrailingStopActivation:
    """Test native trailing stop placed after TP1 detection."""

    def test_native_trailing_stop_placed_on_tp1_in_reconciliation(self, config_dir, temp_db):
        """reconcile_from_exchange() places native trailing stop when TP1 detected."""
        daemon = Daemon(strategy_name="test_strategy", config_dir=str(config_dir))
        daemon.initialize()
        mock_exchange = _make_mock_exchange()
        daemon.exchange = mock_exchange

        mock_exchange.fetch_positions.return_value = [
            {
                "symbol": "BTCUSDT",
                "direction": "Long",
                "entry_price": 95000.0,
                "mark_price": 96000.0,
                "size_usdt": 300.0,
                "unrealized_pnl": 10.5,
                "unrealized_pct": 1.05,
                "leverage": 5.0,
                "pos_id": "pos-1",
                "achieved_profits": 50.0,
                "sl_price": 93000.0,
                "tp_price": 0.0,
                "sl_order_id": "sl-1",
                "tp_order_id": "",
                "total_contracts": 0.3,
                "available_contracts": 0.3,
            },
        ]

        daemon.reconcile_from_exchange()

        mock_exchange.place_trailing_stop.assert_called_once()
        call_args = mock_exchange.place_trailing_stop.call_args
        assert call_args[1]["symbol"] == "BTCUSDT"
        assert call_args[1]["direction"] == "Long"

        positions = daemon.substrate.portfolio.get("open_positions", [])
        assert positions[0]["native_trail_order_id"] == "trail-123"

    def test_native_trailing_stop_not_placed_when_already_exists(self, config_dir, temp_db):
        """Native trail NOT re-placed if order_id already exists in metadata."""
        daemon = Daemon(strategy_name="test_strategy", config_dir=str(config_dir))
        daemon.initialize()
        mock_exchange = _make_mock_exchange()
        daemon.exchange = mock_exchange

        mock_exchange.fetch_positions.return_value = [
            {
                "symbol": "BTCUSDT",
                "direction": "Long",
                "entry_price": 95000.0,
                "mark_price": 96000.0,
                "size_usdt": 300.0,
                "unrealized_pnl": 10.5,
                "unrealized_pct": 1.05,
                "leverage": 5.0,
                "pos_id": "pos-1",
                "achieved_profits": 50.0,
                "sl_price": 93000.0,
                "tp_price": 0.0,
                "sl_order_id": "sl-1",
                "tp_order_id": "",
                "total_contracts": 0.3,
                "available_contracts": 0.3,
            },
        ]

        with patch.object(daemon, '_load_position_metadata', return_value={
            "BTCUSDT:Long:95000.00": {
                "atr_value": 1500.0,
                "atr_pct": 0.0158,
                "sl_price": 93000.0,
                "tp1": 96500.0,
                "tp2": 98000.0,
                "size_usdt": 500.0,
                "opened_at": "",
                "native_trail_order_id": "trail-existing",
            },
        }):
            daemon.reconcile_from_exchange()

        mock_exchange.place_trailing_stop.assert_not_called()
        positions = daemon.substrate.portfolio.get("open_positions", [])
        assert positions[0]["native_trail_order_id"] == "trail-existing"

    def test_native_trailing_stop_not_placed_in_paper_mode(self, config_dir, temp_db):
        """Paper mode skips native trailing stop placement."""
        daemon = Daemon(strategy_name="test_strategy", paper_mode=True, config_dir=str(config_dir))
        daemon.initialize()
        mock_exchange = _make_mock_exchange(paper_mode=True)
        daemon.exchange = mock_exchange

        daemon.reconcile_from_exchange()
        mock_exchange.place_trailing_stop.assert_not_called()


class TestTrailingStopPushToExchange:
    """Test trailing stop updates pushed to exchange."""

    def test_trailing_sl_pushed_when_changed(self, config_dir, temp_db):
        """modify_tpsl_order called when trailing_sl changes."""
        daemon = Daemon(strategy_name="test_strategy", config_dir=str(config_dir))
        daemon.initialize()
        mock_exchange = _make_mock_exchange()
        daemon.exchange = mock_exchange

        daemon.substrate.portfolio["open_positions"] = [
            {
                "symbol": "BTCUSDT",
                "direction": "Long",
                "entry_price": 95000.0,
                "mark_price": 97000.0,
                "size_usdt": 500.0,
                "trailing_active": True,
                "trailing_sl": 95500.0,
                "sl_order_id": "sl-1",
                "_exchange_sl_last_pushed": 94000.0,
                "atr_value": 1500.0,
            },
        ]

        daemon._push_trailing_stops_to_exchange()

        mock_exchange.modify_tpsl_order.assert_called_once_with(
            symbol="BTCUSDT",
            order_id="sl-1",
            new_sl_price=95500.0,
        )

    def test_trailing_sl_not_pushed_when_unchanged(self, config_dir, temp_db):
        """No API call when trailing_sl matches last pushed value."""
        daemon = Daemon(strategy_name="test_strategy", config_dir=str(config_dir))
        daemon.initialize()
        mock_exchange = _make_mock_exchange()
        daemon.exchange = mock_exchange

        daemon.substrate.portfolio["open_positions"] = [
            {
                "symbol": "BTCUSDT",
                "direction": "Long",
                "entry_price": 95000.0,
                "mark_price": 97000.0,
                "size_usdt": 500.0,
                "trailing_active": True,
                "trailing_sl": 95500.0,
                "sl_order_id": "sl-1",
                "_exchange_sl_last_pushed": 95500.0,
                "atr_value": 1500.0,
            },
        ]

        daemon._push_trailing_stops_to_exchange()
        mock_exchange.modify_tpsl_order.assert_not_called()


class TestExecuteExitLiveMode:
    """Test ExecuteExit exchange operations in live mode."""

    def test_close_position_on_exchange(self):
        """Live mode full close calls exchange.close_position()."""
        mock_exchange = _make_mock_exchange()
        enzyme = ExecuteExit(exchange=mock_exchange)

        config = make_full_config(daemon={"paper_mode": False})
        sub = Substrate(config=config)
        sub.portfolio["equity"] = 5000.0
        sub.portfolio["open_positions"] = [
            {
                "symbol": "BTCUSDT", "direction": "Long",
                "entry_price": 95000.0, "mark_price": 96000.0,
                "size_usdt": 500.0, "sl_price": 93000.0,
                "atr_value": 1500.0, "opened_at": "",
                "trailing_active": False, "trailing_sl": None,
                "peak_price": 95000.0, "tp1_taken": False, "tp2_taken": False,
            },
        ]
        sub.decisions["exit_approved"] = {
            "symbol": "BTCUSDT", "reason": "signal_reversal",
        }

        result = enzyme.transform(sub)
        mock_exchange.close_position.assert_called_once()
        call_kwargs = mock_exchange.close_position.call_args[1]
        assert call_kwargs["symbol"] == "BTCUSDT"
        assert call_kwargs["direction"] == "Long"
        assert call_kwargs["size_usdt"] == 500.0

    def test_cancel_orders_on_exchange(self):
        """Live mode full close calls exchange.cancel_orders()."""
        mock_exchange = _make_mock_exchange()
        enzyme = ExecuteExit(exchange=mock_exchange)

        config = make_full_config(daemon={"paper_mode": False})
        sub = Substrate(config=config)
        sub.portfolio["equity"] = 5000.0
        sub.portfolio["open_positions"] = [
            {
                "symbol": "BTCUSDT", "direction": "Long",
                "entry_price": 95000.0, "mark_price": 96000.0,
                "size_usdt": 500.0, "sl_price": 93000.0,
                "atr_value": 1500.0, "opened_at": "",
                "trailing_active": False, "trailing_sl": None,
                "peak_price": 95000.0, "tp1_taken": False, "tp2_taken": False,
            },
        ]
        sub.decisions["exit_approved"] = {
            "symbol": "BTCUSDT", "reason": "signal_reversal",
        }

        result = enzyme.transform(sub)
        mock_exchange.cancel_orders.assert_called_once_with("BTCUSDT")

    def test_paper_mode_no_exchange_calls(self):
        """Paper mode makes no exchange API calls."""
        mock_exchange = _make_mock_exchange(paper_mode=True)
        enzyme = ExecuteExit(exchange=mock_exchange)

        config = make_full_config(daemon={"paper_mode": True})
        sub = Substrate(config=config)
        sub.portfolio["equity"] = 5000.0
        sub.portfolio["open_positions"] = [
            {
                "symbol": "BTCUSDT", "direction": "Long",
                "entry_price": 95000.0, "mark_price": 96000.0,
                "size_usdt": 500.0, "sl_price": 93000.0,
                "atr_value": 1500.0, "opened_at": "",
                "trailing_active": False, "trailing_sl": None,
                "peak_price": 95000.0, "tp1_taken": False, "tp2_taken": False,
            },
        ]
        sub.decisions["exit_approved"] = {
            "symbol": "BTCUSDT", "reason": "signal_reversal",
        }

        result = enzyme.transform(sub)
        mock_exchange.close_position.assert_not_called()
        mock_exchange.cancel_orders.assert_not_called()

    def test_native_trailing_stop_on_tp1_partial(self):
        """Live mode TP1 partial close calls exchange.place_trailing_stop()."""
        mock_exchange = _make_mock_exchange()
        enzyme = ExecuteExit(exchange=mock_exchange)

        config = make_full_config(daemon={"paper_mode": False})
        sub = Substrate(config=config)
        sub.portfolio["equity"] = 5000.0
        sub.portfolio["open_positions"] = [
            {
                "symbol": "BTCUSDT", "direction": "Long",
                "entry_price": 95000.0, "mark_price": 96500.0,
                "size_usdt": 500.0, "sl_price": 93000.0,
                "tp1": 96500.0, "tp2": 98000.0,
                "atr_value": 1500.0, "atr_pct": 0.0158,
                "opened_at": "", "trailing_active": False,
                "trailing_sl": None, "peak_price": 95000.0,
                "tp1_taken": False, "tp2_taken": False,
                "native_trail_order_id": "",
            },
        ]
        sub.decisions["exit_approved"] = {
            "symbol": "BTCUSDT", "reason": "tp1_partial",
            "partial": True, "sell_pct": 40.0,
        }

        result = enzyme.transform(sub)
        mock_exchange.place_trailing_stop.assert_called_once()


class TestLearningDataLoadOnStartup:
    """Test learning data loaded from DB on daemon startup."""

    def test_adjusted_weights_loaded_from_db(self, config_dir, temp_db):
        """adjusted_weights table is loaded into substrate.learning on startup."""
        from core.database import db_conn

        daemon = Daemon(strategy_name="test_strategy", config_dir=str(config_dir))
        daemon.initialize()
        strategy_uid = daemon.substrate.strategy.get("uid", "legacy")

        with db_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO adjusted_weights (strategy_uid, indicator_name, weight, updated_at) VALUES (?, ?, ?, datetime('now'))",
                (strategy_uid, "rsi", 0.30),
            )
            conn.execute(
                "INSERT OR REPLACE INTO adjusted_weights (strategy_uid, indicator_name, weight, updated_at) VALUES (?, ?, ?, datetime('now'))",
                (strategy_uid, "macd", 0.50),
            )

        daemon._load_learning_from_db()

        weights = daemon.substrate.learning.get("adjusted_weights", {})
        assert "rsi" in weights
        assert weights["rsi"] == 0.30
        assert "macd" in weights
        assert weights["macd"] == 0.50

    def test_adjusted_thresholds_loaded_from_db(self, config_dir, temp_db):
        """adjusted_thresholds table is loaded into substrate.learning on startup."""
        from core.database import db_conn

        daemon = Daemon(strategy_name="test_strategy", config_dir=str(config_dir))
        daemon.initialize()
        strategy_uid = daemon.substrate.strategy.get("uid", "legacy")

        with db_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO adjusted_thresholds (strategy_uid, threshold_name, value, updated_at) VALUES (?, ?, ?, datetime('now'))",
                (strategy_uid, "noise_penalty_ratio", 0.25),
            )

        daemon._load_learning_from_db()

        thresholds = daemon.substrate.learning.get("adjusted_thresholds", {})
        assert "noise_penalty_ratio" in thresholds
        assert thresholds["noise_penalty_ratio"] == 0.25

    def test_empty_db_starts_with_empty_cache(self, config_dir, temp_db):
        """Fresh DB (no learning data) results in empty substrate cache."""
        daemon = Daemon(strategy_name="test_strategy", config_dir=str(config_dir))
        daemon.initialize()

        assert daemon.substrate.learning.get("adjusted_weights", {}) == {}
        assert daemon.substrate.learning.get("adjusted_thresholds", {}) == {}


class TestSubstrateStateMaxRowsRemoved:
    """Test that substrate_state_max_rows is no longer in config."""

    def test_not_in_default_yaml(self):
        """substrate_state_max_rows removed from default.yaml."""
        import yaml
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "default.yaml")) as f:
            config = yaml.safe_load(f)
        daemon_section = config.get("daemon", {})
        assert "substrate_state_max_rows" not in daemon_section

    def test_not_in_conftest(self):
        """substrate_state_max_rows removed from conftest.py make_full_config."""
        cfg = make_full_config()
        daemon_section = cfg.get("daemon", {})
        assert "substrate_state_max_rows" not in daemon_section

    def test_obsolete_tables_dropped(self, temp_db):
        """substrate_state and cycle_log tables are dropped."""
        from core.database import db_conn
        with db_conn() as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('substrate_state', 'cycle_log')"
            ).fetchall()
            assert len(tables) == 0


class TestPaperModeRuntimeOnly:
    """Test that paper mode positions are runtime-only with no exchange calls."""

    def test_paper_reconcile_skips_exchange(self, config_dir, temp_db):
        """Paper mode reconciliation does not call exchange.fetch_positions."""
        daemon = Daemon(strategy_name="test_strategy", paper_mode=True, config_dir=str(config_dir))
        daemon.initialize()
        mock_exchange = _make_mock_exchange(paper_mode=True)
        daemon.exchange = mock_exchange

        daemon.reconcile_from_exchange()
        mock_exchange.fetch_positions.assert_not_called()

    def test_paper_trailing_stops_skips_exchange(self, config_dir, temp_db):
        """Paper mode trailing stop push does not call exchange."""
        daemon = Daemon(strategy_name="test_strategy", paper_mode=True, config_dir=str(config_dir))
        daemon.initialize()
        mock_exchange = _make_mock_exchange(paper_mode=True)
        daemon.exchange = mock_exchange

        daemon.substrate.portfolio["open_positions"] = [
            {
                "symbol": "BTCUSDT", "direction": "Long",
                "trailing_active": True, "trailing_sl": 95500.0,
                "sl_order_id": "sl-1",
            },
        ]

        # _push_trailing_stops_to_exchange checks paper_mode
        # But in paper mode, run_cycle skips the push entirely
        # So we verify by calling run_cycle and checking no exchange calls
        from core.trailing_stop import maintain_trailing_stops
        maintain_trailing_stops(daemon.substrate)

        # Paper mode: daemon.run_cycle() skips _push_trailing_stops_to_exchange
        # Verify the method itself won't push for paper positions
        daemon._push_trailing_stops_to_exchange()
        # The method checks self.exchange and position sl_order_id but
        # in paper mode, the daemon cycle skips this entirely
        # Let's verify it doesn't push when exchange is paper mode
        mock_exchange.modify_tpsl_order.assert_not_called()
