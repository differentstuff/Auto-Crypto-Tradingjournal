"""
tests/test_database.py -- Tests for the database module.

Exchange-as-truth: substrate_state and cycle_log tables are DROPPED.
No save_substrate or load_latest_substrate functions.
New tables: position_metadata, adjusted_weights, adjusted_thresholds,
suppressed_signals, highlight_signals, challenger_state.
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import init_db, get_conn, db_conn, save_cycle_log
from conftest import make_full_config


class TestDatabaseInit:
    """Test database initialization and table creation."""

    def test_init_creates_tables(self, temp_db):
        """init_db creates all required tables."""
        with db_conn() as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            table_names = [t["name"] for t in tables]

            # Legacy tables
            for expected in ["positions", "orders", "wallet_snapshots",
                             "analyzed_calls", "pending_limits", "trader_rulebook",
                             "trade_hindsight", "settings", "import_log", "token_usage"]:
                assert expected in table_names, f"Missing legacy table: {expected}"

            # Learning tables
            for expected in ["trade_learning", "signal_accuracy", "combination_accuracy",
                             "trajectory_accuracy", "idle_cycles", "idle_condition_accuracy",
                             "weight_history", "rulebook_versions"]:
                assert expected in table_names, f"Missing learning table: {expected}"

            # Exchange-as-truth new tables
            for expected in ["position_metadata", "adjusted_weights", "adjusted_thresholds",
                             "suppressed_signals", "highlight_signals", "challenger_state"]:
                assert expected in table_names, f"Missing exchange-as-truth table: {expected}"

    def test_substrate_state_dropped(self, temp_db):
        """Exchange-as-truth: substrate_state table is dropped."""
        with db_conn() as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='substrate_state'"
            ).fetchall()
            assert len(tables) == 0, "substrate_state should be dropped"

    def test_init_idempotent(self, temp_db):
        """Calling init_db multiple times is safe."""
        init_db()
        init_db()

    def test_wal_mode(self, temp_db):
        with db_conn() as conn:
            result = conn.execute("PRAGMA journal_mode").fetchone()
            assert result[0].lower() == "wal"


class TestPositionMetadata:
    """Test position_metadata table for exchange-as-truth reconciliation."""

    def test_position_metadata_schema(self, temp_db):
        """position_metadata table has all required columns."""
        with db_conn() as conn:
            cols = conn.execute("PRAGMA table_info(position_metadata)").fetchall()
            col_names = [c["name"] for c in cols]
            for expected in ["symbol", "direction", "entry_price", "strategy_uid",
                             "atr_value", "atr_pct", "sl_price", "tp1", "tp2",
                             "size_usdt", "opened_at", "closed_at",
                             "sl_order_id", "tp1_order_id", "tp2_order_id",
                             "native_trail_order_id", "max_profit_atr"]:
                assert expected in col_names, f"Missing column: {expected}"


class TestLearningTables:
    """Test learning tables have correct schema."""

    def test_trade_learning_schema(self, temp_db):
        with db_conn() as conn:
            cols = conn.execute("PRAGMA table_info(trade_learning)").fetchall()
            col_names = [c["name"] for c in cols]
            for expected in ["symbol", "direction", "strategy_name",
                             "confluence_score_at_entry", "signals_at_entry_json"]:
                assert expected in col_names, f"Missing column: {expected}"

    def test_signal_accuracy_schema(self, temp_db):
        with db_conn() as conn:
            cols = conn.execute("PRAGMA table_info(signal_accuracy)").fetchall()
            col_names = [c["name"] for c in cols]
            for expected in ["indicator_name", "total_fired", "correct",
                             "accuracy_pct", "verdict"]:
                assert expected in col_names, f"Missing column: {expected}"

    def test_weight_history_schema(self, temp_db):
        with db_conn() as conn:
            cols = conn.execute("PRAGMA table_info(weight_history)").fetchall()
            col_names = [c["name"] for c in cols]
            for expected in ["indicator_name", "old_weight", "new_weight", "justification"]:
                assert expected in col_names, f"Missing column: {expected}"

    def test_adjusted_weights_schema(self, temp_db):
        """adjusted_weights table for learning data separation."""
        with db_conn() as conn:
            cols = conn.execute("PRAGMA table_info(adjusted_weights)").fetchall()
            col_names = [c["name"] for c in cols]
            for expected in ["strategy_uid", "indicator_name", "weight"]:
                assert expected in col_names, f"Missing column: {expected}"

    def test_adjusted_thresholds_schema(self, temp_db):
        """adjusted_thresholds table for learning data separation."""
        with db_conn() as conn:
            cols = conn.execute("PRAGMA table_info(adjusted_thresholds)").fetchall()
            col_names = [c["name"] for c in cols]
            for expected in ["strategy_uid", "threshold_name", "value"]:
                assert expected in col_names, f"Missing column: {expected}"
