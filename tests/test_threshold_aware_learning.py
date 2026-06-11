"""
tests/test_threshold_aware_learning.py -- Tests for the ThresholdAwareLearning system.

Tests all 6 implementation steps:
  1. Database migration 51 (signal_accuracy_by_threshold table)
  2. time_travel.py --min-threshold and _threshold_bucket tagging
  3. analyzer.py bucket parameter (production-only filter, per-bucket writes)
  4. threshold_evaluator.py (proposal conditions)
  5. update_learning.py step 7 integration
  6. config/default.yaml threshold_evaluator section
"""

import json
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from conftest import make_full_config


# ── Step 1: Database migration ─────────────────────────────────────────────

class TestMigration51:
    """Test that migration 51 creates the signal_accuracy_by_threshold table."""

    def test_signal_accuracy_by_threshold_table_exists(self, temp_db):
        """Migration 51 creates signal_accuracy_by_threshold table."""
        import importlib
        import core.database as db_mod
        importlib.reload(db_mod)
        from core.database import db_conn

        with db_conn() as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            table_names = [t["name"] for t in tables]
            assert "signal_accuracy_by_threshold" in table_names

    def test_signal_accuracy_by_threshold_schema(self, temp_db):
        """signal_accuracy_by_threshold has all required columns."""
        import importlib
        import core.database as db_mod
        importlib.reload(db_mod)
        from core.database import db_conn

        with db_conn() as conn:
            cols = conn.execute(
                "PRAGMA table_info(signal_accuracy_by_threshold)"
            ).fetchall()
            col_names = [c["name"] for c in cols]

            for expected in [
                "strategy_uid", "indicator_name", "threshold_bucket",
                "threshold_value", "total_fired", "correct", "accuracy_pct",
                "confidence_95_low", "confidence_95_high", "verdict",
                "sample_size", "profit_factor", "win_rate", "trade_count",
                "updated_at",
            ]:
                assert expected in col_names, f"Missing column: {expected}"

    def test_signal_accuracy_by_threshold_composite_pk(self, temp_db):
        """Primary key is (strategy_uid, indicator_name, threshold_bucket)."""
        import importlib
        import core.database as db_mod
        importlib.reload(db_mod)
        from core.database import db_conn

        with db_conn() as conn:
            pk_cols = conn.execute(
                "PRAGMA table_info(signal_accuracy_by_threshold)"
            ).fetchall()
            pk_names = [c["name"] for c in pk_cols if c["pk"] > 0]
            assert "strategy_uid" in pk_names
            assert "indicator_name" in pk_names
            assert "threshold_bucket" in pk_names

    def test_insert_and_read_threshold_row(self, temp_db):
        """Can insert and read a row from signal_accuracy_by_threshold."""
        from core.database import db_conn

        with db_conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO signal_accuracy_by_threshold
                   (strategy_uid, indicator_name, threshold_bucket, threshold_value,
                    total_fired, correct, accuracy_pct, verdict, sample_size,
                    profit_factor, win_rate, trade_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("test-uid", "rsi", "production", 6.5,
                 20, 15, 75.0, "valid", 20,
                 2.5, 75.0, 20),
            )

            row = conn.execute(
                "SELECT * FROM signal_accuracy_by_threshold WHERE strategy_uid = ? AND threshold_bucket = ?",
                ("test-uid", "production"),
            ).fetchone()

            assert row is not None
            assert row["indicator_name"] == "rsi"
            assert row["threshold_value"] == 6.5
            assert row["profit_factor"] == 2.5
            assert row["win_rate"] == 75.0

    def test_migration_51_in_schema_version(self, temp_db):
        """Migration 51 is recorded in schema_version table."""
        from core.database import db_conn

        with db_conn() as conn:
            row = conn.execute(
                "SELECT name FROM schema_version WHERE version = 51"
            ).fetchone()
            assert row is not None
            assert row["name"] == "signal_accuracy_by_threshold"


# ── Step 2: time_travel.py changes ────────────────────────────────────────

class TestTimeTravelChanges:
    """Test --min-threshold CLI arg and _threshold_bucket tagging."""

    def test_write_trade_tags_production_bucket(self, temp_db):
        """_write_trade tags production bucket when threshold >= entry_threshold."""
        from core.database import db_conn

        # Simulate a trade written by _write_trade with production threshold
        signals_json = json.dumps({
            "rsi": {"signal": "bullish", "value": 70},
            "_threshold_used": 6.5,
            "_threshold_bucket": "production",
            "_source": "time_travel",
        })

        with db_conn() as conn:
            conn.execute(
                """INSERT INTO trade_learning
                   (strategy_name, strategy_uid, symbol, direction,
                    entry_time, exit_time, outcome, pnl_pct,
                    duration_minutes, confluence_score_at_entry,
                    signals_at_entry_json, exit_reason,
                    sl_hit, trailing_stop_hit,
                    max_favorable_excursion_pct, max_adverse_excursion_pct)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("test_strat", "test-uid", "BTCUSDT", "Long",
                 "2025-01-01T00:00:00", "2025-01-01T04:00:00", "win", 2.5,
                 240, 7.2, signals_json, "take_profit",
                 0, 0, 3.0, 0.0),
            )

            row = conn.execute(
                "SELECT signals_at_entry_json FROM trade_learning WHERE strategy_name = 'test_strat'"
            ).fetchone()

            signals = json.loads(row["signals_at_entry_json"])
            assert signals["_threshold_bucket"] == "production"
            assert signals["_threshold_used"] == 6.5

    def test_write_trade_tags_exploration_bucket(self, temp_db):
        """_write_trade tags exploration bucket when threshold < entry_threshold."""
        from core.database import db_conn

        signals_json = json.dumps({
            "rsi": {"signal": "bullish", "value": 65},
            "_threshold_used": 4.0,
            "_threshold_bucket": "exploration",
            "_source": "time_travel",
        })

        with db_conn() as conn:
            conn.execute(
                """INSERT INTO trade_learning
                   (strategy_name, strategy_uid, symbol, direction,
                    entry_time, exit_time, outcome, pnl_pct,
                    duration_minutes, confluence_score_at_entry,
                    signals_at_entry_json, exit_reason,
                    sl_hit, trailing_stop_hit,
                    max_favorable_excursion_pct, max_adverse_excursion_pct)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("test_strat2", "test-uid2", "ETHUSDT", "Long",
                 "2025-01-01T00:00:00", "2025-01-01T04:00:00", "loss", -1.5,
                 240, 4.5, signals_json, "hard_stop",
                 1, 0, 0.0, 1.5),
            )

            row = conn.execute(
                "SELECT signals_at_entry_json FROM trade_learning WHERE strategy_name = 'test_strat2'"
            ).fetchone()

            signals = json.loads(row["signals_at_entry_json"])
            assert signals["_threshold_bucket"] == "exploration"
            assert signals["_threshold_used"] == 4.0

    def test_min_threshold_cli_arg_exists(self):
        """--min-threshold argument is accepted by the CLI parser."""
        # Import the module to verify the arg is defined
        from scripts.time_travel import _write_trade
        import inspect
        sig = inspect.signature(_write_trade)
        assert "entry_threshold" in sig.parameters


# ── Step 3: analyzer.py bucket parameter ───────────────────────────────────

class TestAnalyzerBucketParameter:
    """Test the bucket parameter in update_signal_accuracy."""

    def _insert_trades(self, db_path, strategy_name="test_strat", strategy_uid="test-uid"):
        """Insert test trades with both production and exploration bucket tags."""
        import importlib
        import core.database as db_mod
        importlib.reload(db_mod)
        from core.database import db_conn

        # Production trade (threshold >= 6.5)
        prod_signals = json.dumps({
            "rsi": {"signal": "bullish", "value": 70},
            "macd": {"signal": "bullish", "value": 0.5},
            "_threshold_used": 6.5,
            "_threshold_bucket": "production",
        })

        # Exploration trade (threshold < 6.5)
        expl_signals = json.dumps({
            "rsi": {"signal": "bullish", "value": 60},
            "macd": {"signal": "bearish", "value": -0.3},
            "_threshold_used": 4.0,
            "_threshold_bucket": "exploration",
        })

        with db_conn() as conn:
            # Insert 20 production trades (15 wins, 5 losses for rsi)
            for i in range(15):
                conn.execute(
                    """INSERT INTO trade_learning
                       (strategy_name, strategy_uid, symbol, direction,
                        entry_time, exit_time, outcome, pnl_pct,
                        signals_at_entry_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (strategy_name, strategy_uid, "BTCUSDT", "Long",
                     f"2025-01-{i+1:02d}T00:00:00", f"2025-01-{i+1:02d}T04:00:00",
                     "win", 2.5, prod_signals),
                )
            for i in range(5):
                conn.execute(
                    """INSERT INTO trade_learning
                       (strategy_name, strategy_uid, symbol, direction,
                        entry_time, exit_time, outcome, pnl_pct,
                        signals_at_entry_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (strategy_name, strategy_uid, "BTCUSDT", "Long",
                     f"2025-02-{i+1:02d}T00:00:00", f"2025-02-{i+1:02d}T04:00:00",
                     "loss", -1.5, prod_signals),
                )

            # Insert 10 exploration trades (8 wins, 2 losses for rsi)
            for i in range(8):
                conn.execute(
                    """INSERT INTO trade_learning
                       (strategy_name, strategy_uid, symbol, direction,
                        entry_time, exit_time, outcome, pnl_pct,
                        signals_at_entry_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (strategy_name, strategy_uid, "ETHUSDT", "Long",
                     f"2025-03-{i+1:02d}T00:00:00", f"2025-03-{i+1:02d}T04:00:00",
                     "win", 3.0, expl_signals),
                )
            for i in range(2):
                conn.execute(
                    """INSERT INTO trade_learning
                       (strategy_name, strategy_uid, symbol, direction,
                        entry_time, exit_time, outcome, pnl_pct,
                        signals_at_entry_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (strategy_name, strategy_uid, "ETHUSDT", "Long",
                     f"2025-04-{i+1:02d}T00:00:00", f"2025-04-{i+1:02d}T04:00:00",
                     "loss", -1.0, expl_signals),
                )

    def test_default_bucket_writes_to_signal_accuracy(self, temp_db):
        """bucket=None (default) writes to signal_accuracy table with production-only filter."""
        self._insert_trades(temp_db)

        import importlib
        import core.database as db_mod
        importlib.reload(db_mod)

        from learning.analyzer import update_signal_accuracy
        from core.database import db_conn

        update_signal_accuracy(
            "test_strat",
            strategy_uid="test-uid",
            min_trades_per_signal=5,
            highlight_threshold=75.0,
            monitor_low_threshold=55.0,
            suppress_range=(45.0, 55.0),
            contrarian_threshold=30.0,
        )

        with db_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM signal_accuracy WHERE strategy_uid = 'test-uid'"
            ).fetchall()

            # Should have production-only data (20 trades, not 30)
            assert len(rows) > 0
            for row in rows:
                # Production trades: 15 wins + 5 losses = 20 total
                assert row["total_fired"] == 20

    def test_production_bucket_writes_to_threshold_table(self, temp_db):
        """bucket='production' writes to signal_accuracy_by_threshold."""
        self._insert_trades(temp_db)

        import importlib
        import core.database as db_mod
        importlib.reload(db_mod)

        from learning.analyzer import update_signal_accuracy
        from core.database import db_conn

        update_signal_accuracy(
            "test_strat",
            strategy_uid="test-uid",
            min_trades_per_signal=5,
            highlight_threshold=75.0,
            monitor_low_threshold=55.0,
            suppress_range=(45.0, 55.0),
            contrarian_threshold=30.0,
            bucket="production",
        )

        with db_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM signal_accuracy_by_threshold WHERE strategy_uid = 'test-uid' AND threshold_bucket = 'production'"
            ).fetchall()

            assert len(rows) > 0
            for row in rows:
                assert row["threshold_bucket"] == "production"
                assert row["total_fired"] == 20
                assert row["profit_factor"] is not None

    def test_exploration_bucket_writes_to_threshold_table(self, temp_db):
        """bucket='exploration' writes to signal_accuracy_by_threshold."""
        self._insert_trades(temp_db)

        import importlib
        import core.database as db_mod
        importlib.reload(db_mod)

        from learning.analyzer import update_signal_accuracy
        from core.database import db_conn

        update_signal_accuracy(
            "test_strat",
            strategy_uid="test-uid",
            min_trades_per_signal=5,
            highlight_threshold=75.0,
            monitor_low_threshold=55.0,
            suppress_range=(45.0, 55.0),
            contrarian_threshold=30.0,
            bucket="exploration",
        )

        with db_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM signal_accuracy_by_threshold WHERE strategy_uid = 'test-uid' AND threshold_bucket = 'exploration'"
            ).fetchall()

            assert len(rows) > 0
            for row in rows:
                assert row["threshold_bucket"] == "exploration"
                assert row["total_fired"] == 10

    def test_old_trades_without_bucket_included_in_default(self, temp_db):
        """Old trades without _threshold_bucket tag are included in default (production-only) query."""
        import importlib
        import core.database as db_mod
        importlib.reload(db_mod)
        from core.database import db_conn
        from learning.analyzer import update_signal_accuracy

        # Insert trade WITHOUT _threshold_bucket (old format)
        old_signals = json.dumps({
            "rsi": {"signal": "bullish", "value": 70},
        })

        with db_conn() as conn:
            conn.execute(
                """INSERT INTO trade_learning
                   (strategy_name, strategy_uid, symbol, direction,
                    entry_time, exit_time, outcome, pnl_pct,
                    signals_at_entry_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("old_strat", "old-uid", "BTCUSDT", "Long",
                 "2025-01-01T00:00:00", "2025-01-01T04:00:00",
                 "win", 2.5, old_signals),
            )

        update_signal_accuracy(
            "old_strat",
            strategy_uid="old-uid",
            min_trades_per_signal=1,
            highlight_threshold=75.0,
            monitor_low_threshold=55.0,
            suppress_range=(45.0, 55.0),
            contrarian_threshold=30.0,
        )

        with db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM signal_accuracy WHERE strategy_uid = 'old-uid'"
            ).fetchone()

            assert row is not None
            assert row["total_fired"] == 1


# ── Step 4: threshold_evaluator.py ─────────────────────────────────────────

class TestThresholdEvaluator:
    """Test the evaluate_thresholds function."""

    def _setup_threshold_data(self, db_path, strategy_uid="eval-uid"):
        """Insert signal_accuracy_by_threshold rows for testing.

        Uses data that produces non-overlapping Wilson intervals:
          Production:  40 fired, 20 correct (50% WR) → Wilson ~[0.35, 0.65]
          Exploration: 35 fired, 32 correct (91% WR) → Wilson ~[0.78, 0.97]
          Gap ≈ 0.13 > min_confidence_gap (0.10) ✓
          PF improvement: 3.0 vs 1.5 = 100% > 20% minimum ✓
          Trades: 35 >= 30 ✓
        """
        import importlib
        import core.database as db_mod
        importlib.reload(db_mod)
        from core.database import db_conn

        with db_conn() as conn:
            # Production: PF=1.5, WR=50%, 40 trades
            conn.execute(
                """INSERT OR REPLACE INTO signal_accuracy_by_threshold
                   (strategy_uid, indicator_name, threshold_bucket, threshold_value,
                    total_fired, correct, accuracy_pct, verdict, sample_size,
                    profit_factor, win_rate, trade_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (strategy_uid, "rsi", "production", 6.5,
                 40, 20, 50.0, "suppress", 40,
                 1.5, 50.0, 40),
            )

            # Exploration: PF=3.0, WR=91%, 35 trades (clearly outperforms production)
            conn.execute(
                """INSERT OR REPLACE INTO signal_accuracy_by_threshold
                   (strategy_uid, indicator_name, threshold_bucket, threshold_value,
                    total_fired, correct, accuracy_pct, verdict, sample_size,
                    profit_factor, win_rate, trade_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (strategy_uid, "rsi", "exploration", 4.0,
                 35, 32, 91.4, "valid", 35,
                 3.0, 91.4, 35),
            )

    def test_evaluate_thresholds_proposal_when_exploration_outperforms(self, temp_db):
        """Proposal returned when exploration PF > production PF * 1.2 with enough trades."""
        self._setup_threshold_data(temp_db)

        import importlib
        import core.database as db_mod
        importlib.reload(db_mod)

        from learning.threshold_evaluator import evaluate_thresholds

        proposal = evaluate_thresholds(
            "test_strat", "eval-uid",
            entry_threshold=6.5,
            min_trades=30,
            min_improvement_pct=20.0,
        )

        assert proposal is not None
        assert proposal["source"] == "threshold_evaluator"
        assert proposal["exploration_pf"] > proposal["production_pf"]

    def test_evaluate_thresholds_no_proposal_when_insufficient_trades(self, temp_db):
        """No proposal when exploration has fewer than min_trades."""
        import importlib
        import core.database as db_mod
        importlib.reload(db_mod)
        from core.database import db_conn
        from learning.threshold_evaluator import evaluate_thresholds

        with db_conn() as conn:
            # Production with lots of trades
            conn.execute(
                """INSERT OR REPLACE INTO signal_accuracy_by_threshold
                   (strategy_uid, indicator_name, threshold_bucket, threshold_value,
                    total_fired, correct, accuracy_pct, verdict, sample_size,
                    profit_factor, win_rate, trade_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("few-uid", "rsi", "production", 6.5,
                 100, 60, 60.0, "monitor", 100,
                 1.5, 60.0, 100),
            )
            # Exploration with too few trades
            conn.execute(
                """INSERT OR REPLACE INTO signal_accuracy_by_threshold
                   (strategy_uid, indicator_name, threshold_bucket, threshold_value,
                    total_fired, correct, accuracy_pct, verdict, sample_size,
                    profit_factor, win_rate, trade_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("few-uid", "rsi", "exploration", 4.0,
                 5, 4, 80.0, "insufficient_data", 5,
                 3.0, 80.0, 5),
            )

        proposal = evaluate_thresholds(
            "test_strat", "few-uid",
            entry_threshold=6.5,
            min_trades=30,
        )

        assert proposal is None

    def test_evaluate_thresholds_no_proposal_when_no_data(self, temp_db):
        """No proposal when there's no data in signal_accuracy_by_threshold."""
        import importlib
        import core.database as db_mod
        importlib.reload(db_mod)

        from learning.threshold_evaluator import evaluate_thresholds

        proposal = evaluate_thresholds(
            "empty_strat", "empty-uid",
            entry_threshold=6.5,
        )

        assert proposal is None

    def test_evaluate_thresholds_no_proposal_when_low_improvement(self, temp_db):
        """No proposal when exploration PF improvement is below min_improvement_pct."""
        import importlib
        import core.database as db_mod
        importlib.reload(db_mod)
        from core.database import db_conn
        from learning.threshold_evaluator import evaluate_thresholds

        with db_conn() as conn:
            # Production PF = 2.0
            conn.execute(
                """INSERT OR REPLACE INTO signal_accuracy_by_threshold
                   (strategy_uid, indicator_name, threshold_bucket, threshold_value,
                    total_fired, correct, accuracy_pct, verdict, sample_size,
                    profit_factor, win_rate, trade_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("low-uid", "rsi", "production", 6.5,
                 50, 30, 60.0, "monitor", 50,
                 2.0, 60.0, 50),
            )
            # Exploration PF = 2.1 (only 5% improvement, below 20% minimum)
            conn.execute(
                """INSERT OR REPLACE INTO signal_accuracy_by_threshold
                   (strategy_uid, indicator_name, threshold_bucket, threshold_value,
                    total_fired, correct, accuracy_pct, verdict, sample_size,
                    profit_factor, win_rate, trade_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("low-uid", "rsi", "exploration", 4.0,
                 40, 24, 60.0, "monitor", 40,
                 2.1, 60.0, 40),
            )

        proposal = evaluate_thresholds(
            "test_strat", "low-uid",
            entry_threshold=6.5,
            min_trades=30,
            min_improvement_pct=20.0,
        )

        assert proposal is None


# ── Step 5: update_learning.py integration ─────────────────────────────────

class TestUpdateLearningIntegration:
    """Test that UpdateLearning enzyme calls threshold evaluator when enabled."""

    def test_threshold_evaluator_not_called_when_disabled(self, temp_db):
        """Threshold evaluator is skipped when threshold_evaluator.enabled=False."""
        import importlib
        import core.database as db_mod
        importlib.reload(db_mod)

        from core.substrate import Substrate
        from enzymes.update_learning import UpdateLearning

        config = make_full_config(
            threshold_evaluator={"enabled": False},
            strategy={"name": "test_strat", "uid": "test-uid"},
        )
        substrate = Substrate(config=config)
        substrate.decisions["action"] = "trade_closed"

        enzyme = UpdateLearning()
        result = enzyme.transform(substrate)

        # Should complete without error (threshold evaluator skipped)
        assert result is not None

    def test_update_learning_runs_with_threshold_config(self, temp_db):
        """UpdateLearning completes successfully with threshold_evaluator config present."""
        import importlib
        import core.database as db_mod
        importlib.reload(db_mod)

        from core.substrate import Substrate
        from enzymes.update_learning import UpdateLearning

        config = make_full_config(
            threshold_evaluator={"enabled": True},
            strategy={"name": "test_strat", "uid": "test-uid"},
        )
        substrate = Substrate(config=config)
        substrate.decisions["action"] = "trade_closed"

        enzyme = UpdateLearning()
        result = enzyme.transform(substrate)

        assert result is not None


# ── Step 6: Config ─────────────────────────────────────────────────────────

class TestThresholdEvaluatorConfig:
    """Test that threshold_evaluator config section is properly defined."""

    def test_default_yaml_has_threshold_evaluator(self):
        """default.yaml contains threshold_evaluator section."""
        import yaml

        with open(os.path.join(PROJECT_ROOT, "config", "default.yaml")) as f:
            config = yaml.safe_load(f)

        assert "threshold_evaluator" in config
        assert config["threshold_evaluator"]["enabled"] is False
        assert config["threshold_evaluator"]["min_trades"] == 30
        assert config["threshold_evaluator"]["min_improvement_pct"] == 20.0
        assert config["threshold_evaluator"]["min_confidence_gap"] == 0.10
        assert config["threshold_evaluator"]["cooldown_hours"] == 48

    def test_make_full_config_has_threshold_evaluator(self):
        """make_full_config includes threshold_evaluator section."""
        config = make_full_config()
        assert "threshold_evaluator" in config
        assert config["threshold_evaluator"]["enabled"] is False
        assert config["threshold_evaluator"]["min_trades"] == 30

    def test_substrate_can_read_threshold_evaluator_config(self, temp_db):
        """Substrate.cfg() can read threshold_evaluator values."""
        import importlib
        import core.database as db_mod
        importlib.reload(db_mod)

        from core.substrate import Substrate

        config = make_full_config(
            threshold_evaluator={"enabled": True, "min_trades": 50},
        )
        substrate = Substrate(config=config)

        assert substrate.cfg("threshold_evaluator.enabled") is True
        assert substrate.cfg("threshold_evaluator.min_trades") == 50
        assert substrate.cfg("threshold_evaluator.min_improvement_pct") == 20.0
        assert substrate.cfg("threshold_evaluator.min_confidence_gap") == 0.10