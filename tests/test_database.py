"""
tests_new/test_database.py -- Tests for the database module.

Phase A validation: table creation, learning tables, substrate persistence.
"""

import json
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import init_db, get_conn, db_conn, save_substrate, load_latest_substrate, save_cycle_log
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

            # New learning tables
            for expected in ["trade_learning", "signal_accuracy", "combination_accuracy",
                             "trajectory_accuracy", "idle_cycles", "idle_condition_accuracy",
                             "weight_history", "rulebook_versions", "substrate_state", "cycle_log"]:
                assert expected in table_names, f"Missing learning table: {expected}"

    def test_init_idempotent(self, temp_db):
        """Calling init_db multiple times is safe."""
        init_db()
        init_db()

    def test_wal_mode(self, temp_db):
        """Database uses WAL journal mode."""
        with db_conn() as conn:
            result = conn.execute("PRAGMA journal_mode").fetchone()
            assert result[0].lower() == "wal"


class TestSubstratePersistence:
    """Test substrate save/load to database."""

    def test_save_and_load_substrate(self, temp_db):
        """
        Substrate durable state survives roundtrip through database.

        save_substrate() stores to_persistent_json() which contains only
        durable fields: strategy, portfolio, learning, validity.
        Per-cycle fields (market, analysis, decisions) are NOT persisted --
        they are stale on restart and repopulated by enzymes on the first cycle.
        """
        from core.substrate import Substrate

        config = make_full_config(strategy={"name": "test_persist", "uid": "test-persist-uid"})
        sub = Substrate(config=config)
        sub.portfolio["equity"] = 5000.0
        sub.decisions["action"] = "wait"   # set but NOT persisted (per-cycle)
        sub.learning["idle_cycles"] = 3

        row_id = save_substrate(sub)
        assert row_id > 0

        loaded = load_latest_substrate("test_persist")
        assert loaded is not None
        # Durable fields are present
        assert loaded["strategy"]["name"] == "test_persist"
        assert loaded["portfolio"]["equity"] == 5000.0
        assert loaded["learning"]["idle_cycles"] == 3
        # Per-cycle fields are NOT in the persistent dict
        assert "decisions" not in loaded
        assert "market" not in loaded
        assert "analysis" not in loaded

    def test_load_latest_substrate(self, temp_db):
        """load_latest_substrate returns the most recent entry."""
        from core.substrate import Substrate

        config = make_full_config(strategy={"name": "test_latest", "uid": "test-latest-uid"})
        sub1 = Substrate(config=config)
        sub1.portfolio["equity"] = 1000.0
        save_substrate(sub1)

        sub2 = Substrate(config=config)
        sub2.portfolio["equity"] = 2000.0
        save_substrate(sub2)

        loaded = load_latest_substrate("test_latest")
        assert loaded["portfolio"]["equity"] == 2000.0

    def test_load_nonexistent_substrate(self, temp_db):
        """load_latest_substrate returns None if no state exists."""
        result = load_latest_substrate("nonexistent_strategy")
        assert result is None


class TestCycleLog:
    """Test cycle logging."""

    def test_save_cycle_log(self, temp_db):
        """Cycle log entries are saved correctly."""
        row_id = save_cycle_log(
            strategy_name="momentum_rising",
            cycle_count=1,
            action="wait",
            enzymes_fired=["Wait"],
            isc_results={"ISC-001": "failed", "ISC-002": "verified"},
            duration_ms=150,
        )
        assert row_id > 0

        with db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM cycle_log WHERE id = ?", (row_id,)
            ).fetchone()
            assert row["strategy_name"] == "momentum_rising"
            assert row["cycle_count"] == 1
            assert row["action"] == "wait"
            assert row["duration_ms"] == 150


class TestLearningTables:
    """Test new learning tables have correct schema."""

    def test_trade_learning_schema(self, temp_db):
        """trade_learning table has all required columns."""
        with db_conn() as conn:
            cols = conn.execute("PRAGMA table_info(trade_learning)").fetchall()
            col_names = [c["name"] for c in cols]
            for expected in ["symbol", "direction", "strategy_name",
                             "confluence_score_at_entry", "signals_at_entry_json",
                             "pre_trade_trajectory_pattern", "pre_trade_coincidence_risk",
                             "max_favorable_excursion_pct", "max_adverse_excursion_pct",
                             "rulebook_version"]:
                assert expected in col_names, f"Missing column: {expected}"

    def test_signal_accuracy_schema(self, temp_db):
        """signal_accuracy table has all required columns."""
        with db_conn() as conn:
            cols = conn.execute("PRAGMA table_info(signal_accuracy)").fetchall()
            col_names = [c["name"] for c in cols]
            for expected in ["indicator_name", "total_fired", "correct",
                             "accuracy_pct", "confidence_95_low", "confidence_95_high",
                             "verdict"]:
                assert expected in col_names, f"Missing column: {expected}"

    def test_combination_accuracy_schema(self, temp_db):
        """combination_accuracy table has composite primary key."""
        with db_conn() as conn:
            cols = conn.execute("PRAGMA table_info(combination_accuracy)").fetchall()
            col_names = [c["name"] for c in cols]
            for expected in ["combination_name", "direction_state",
                             "win_rate_pct", "p_value", "significance"]:
                assert expected in col_names, f"Missing column: {expected}"

    def test_idle_cycles_schema(self, temp_db):
        """idle_cycles table has all required columns."""
        with db_conn() as conn:
            cols = conn.execute("PRAGMA table_info(idle_cycles)").fetchall()
            col_names = [c["name"] for c in cols]
            for expected in ["strategy_name", "idle_reasons_json",
                             "market_conditions_json", "hypothetical_pnl_if_entered",
                             "retrospect_validated"]:
                assert expected in col_names, f"Missing column: {expected}"

    def test_weight_history_schema(self, temp_db):
        """weight_history table tracks weight changes."""
        with db_conn() as conn:
            cols = conn.execute("PRAGMA table_info(weight_history)").fetchall()
            col_names = [c["name"] for c in cols]
            for expected in ["indicator_name", "old_weight", "new_weight", "justification"]:
                assert expected in col_names, f"Missing column: {expected}"

    def test_rulebook_versions_schema(self, temp_db):
        """rulebook_versions table stores generated rulebooks."""
        with db_conn() as conn:
            cols = conn.execute("PRAGMA table_info(rulebook_versions)").fetchall()
            col_names = [c["name"] for c in cols]
            for expected in ["version", "rulebook_text", "trades_recorded_at_generation"]:
                assert expected in col_names, f"Missing column: {expected}"
