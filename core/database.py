"""
core/database.py -- SQLite WAL database with learning tables.

Exchange-as-truth architecture:
  - substrate_state table REMOVED (substrate is ephemeral, rebuilt from exchange)
  - cycle_log table REMOVED (no longer needed without substrate persistence)
  - load_latest_substrate() REMOVED (substrate never loaded from DB)
  - save_substrate() REMOVED (substrate never persisted to DB)
  - position_metadata table ADDED (stores atr_pct for TP recalculation)
  - Learning data tables KEPT (signal_accuracy, combination_accuracy, etc.)
  - New learning tables ADDED (adjusted_weights, adjusted_thresholds, etc.)

The substrate is a cache of exchange state. It is rebuilt fresh on every
startup and reconciled from the exchange every cycle. No persistence needed.
"""

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

_log = logging.getLogger(__name__)

DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "data/trading_journal.db"),
)


def get_conn():
    """Return a sqlite3 connection with row_factory set to dict-like Row."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=100")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=500")
    return conn


@contextmanager
def db_conn():
    """
    Context manager that opens a connection, auto-commits on clean exit,
    and rolls back + re-raises on any exception.
    """
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """
    Create all tables if they do not exist yet. Safe to call on every startup.

    Exchange-as-truth: substrate_state and cycle_log tables are NOT created.
    Position metadata and learning data tables ARE created.
    """
    conn = get_conn()
    try:
        _init_db_inner(conn)
    finally:
        conn.close()


def _init_db_inner(conn: sqlite3.Connection) -> None:
    """Internal: run all DDL and migrations on an open connection."""
    cur = conn.cursor()

    # ── schema_version ─────────────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version    INTEGER PRIMARY KEY,
            name       TEXT    NOT NULL,
            applied_at TEXT    DEFAULT (datetime('now'))
        )
    """)
    conn.commit()

    def _applied(ver: int) -> bool:
        return conn.execute(
            "SELECT 1 FROM schema_version WHERE version=?", (ver,)
        ).fetchone() is not None

    def _apply(ver: int, name: str, sql: str):
        if _applied(ver):
            return
        try:
            if sql.strip().count(";") > 1:
                conn.executescript(sql)
            else:
                conn.execute(sql)
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                _log.error("Migration %d (%s) failed: %s", ver, name, e, exc_info=True)
                raise
            _log.debug("Migration %d: column already exists (%s)", ver, name)
        conn.execute(
            "INSERT INTO schema_version (version, name) VALUES (?,?)", (ver, name)
        )
        conn.commit()
        _log.info("Applied migration %d: %s", ver, name)

    # ── Legacy tables (ported from original database.py) ──────────────────────

    cur.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol           TEXT    NOT NULL,
            base_asset       TEXT    NOT NULL,
            direction        TEXT    NOT NULL,
            margin_mode      TEXT,
            open_time        TEXT    NOT NULL,
            close_time       TEXT    NOT NULL,
            duration_minutes INTEGER,
            entry_price      REAL,
            close_price      REAL,
            size_contracts   TEXT,
            size_usdt        REAL,
            position_pnl     REAL,
            realized_pnl     REAL,
            opening_fee      REAL,
            closing_fee      REAL,
            total_fees       REAL,
            notes            TEXT    DEFAULT '',
            tags             TEXT    DEFAULT '',
            is_manual        INTEGER DEFAULT 0,
            created_at       TEXT    DEFAULT (datetime('now')),
            updated_at       TEXT    DEFAULT (datetime('now'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id         TEXT    UNIQUE,
            date             TEXT,
            direction        TEXT,
            symbol           TEXT,
            order_source     TEXT,
            transaction_type TEXT,
            price            REAL,
            avg_price        REAL,
            order_amount     REAL,
            executed          REAL,
            trading_volume   REAL,
            realized_pnl     REAL,
            net_profits      REAL,
            status           TEXT,
            position_id      INTEGER REFERENCES positions(id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS wallet_snapshots (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            order_ref      TEXT,
            date           TEXT,
            symbol         TEXT,
            futures        TEXT,
            margin_mode    TEXT,
            type           TEXT,
            amount         REAL,
            fee            REAL,
            wallet_balance REAL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS analyzed_calls (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol           TEXT NOT NULL,
            direction        TEXT NOT NULL,
            call_text        TEXT,
            entry_price      REAL,
            dca_price        REAL,
            sl_price         REAL,
            tp1_price        REAL,
            tp2_price        REAL,
            avg_entry        REAL,
            total_notional   REAL,
            margin_needed    REAL,
            risk_pct         REAL,
            risk_amount      REAL,
            leverage         INTEGER,
            has_dca          INTEGER DEFAULT 0,
            has_candle_close_sl INTEGER DEFAULT 0,
            setup_score      INTEGER,
            setup_label      TEXT,
            rr_ratio         TEXT,
            trade_type       TEXT,
            sl_warning       TEXT,
            entry_timing     TEXT,
            analysis_json    TEXT,
            status           TEXT DEFAULT 'saved',
            matched_at       TEXT,
            created_at       TEXT DEFAULT (datetime('now'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS pending_limits (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            call_id         INTEGER REFERENCES analyzed_calls(id) ON DELETE SET NULL,
            symbol          TEXT NOT NULL,
            direction       TEXT NOT NULL,
            limit_price     REAL NOT NULL,
            size_usdt       REAL,
            leverage        INTEGER DEFAULT 10,
            sl_price        REAL,
            tp1_price       REAL,
            tp2_price       REAL,
            analyst         TEXT DEFAULT '',
            status          TEXT DEFAULT 'waiting',
            triggered_at    TEXT,
            analysis_json   TEXT,
            notes           TEXT DEFAULT '',
            bitget_order_id TEXT,
            created_at      TEXT DEFAULT (datetime('now'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS trader_rulebook (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_type    TEXT NOT NULL,
            title        TEXT NOT NULL,
            rule         TEXT NOT NULL,
            confidence   TEXT DEFAULT 'medium',
            data_points  INTEGER DEFAULT 0,
            generated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS trade_hindsight (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id      INTEGER UNIQUE REFERENCES positions(id),
            analyzed_at      TEXT DEFAULT (datetime('now')),
            setup_score      INTEGER,
            setup_label      TEXT,
            would_enter      INTEGER,
            rec_direction    TEXT,
            direction_match  INTEGER,
            rec_entry_low    REAL,
            rec_entry_high   REAL,
            rec_sl           REAL,
            rec_tp1          REAL,
            rec_tp2          REAL,
            rec_rr           TEXT,
            key_conditions   TEXT,
            risks            TEXT,
            skip_reason      TEXT,
            actual_pnl       REAL,
            hypothetical_pnl REAL,
            verdict          TEXT,
            analysis_json    TEXT,
            input_tokens     INTEGER,
            output_tokens    INTEGER
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS import_log (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            filename       TEXT,
            file_type      TEXT,
            rows_imported  INTEGER,
            imported_at    TEXT DEFAULT (datetime('now'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS token_usage (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            ts             TEXT    DEFAULT (datetime('now')),
            module         TEXT    NOT NULL,
            model          TEXT    NOT NULL,
            input_tokens   INTEGER NOT NULL,
            output_tokens  INTEGER NOT NULL,
            cached_tokens  INTEGER DEFAULT 0
        )
    """)

    # ── Legacy column migrations ──────────────────────────────────────────────
    _apply(1, "analyzed_calls.exchange", "ALTER TABLE analyzed_calls ADD COLUMN exchange TEXT DEFAULT 'bitget'")
    _apply(2, "analyzed_calls.cot_reasoning", "ALTER TABLE analyzed_calls ADD COLUMN cot_reasoning TEXT DEFAULT NULL")
    _apply(17, "analyzed_calls.analyst", "ALTER TABLE analyzed_calls ADD COLUMN analyst TEXT DEFAULT ''")
    _apply(18, "analyzed_calls.notes", "ALTER TABLE analyzed_calls ADD COLUMN notes TEXT DEFAULT ''")
    _apply(19, "analyzed_calls.outcome", "ALTER TABLE analyzed_calls ADD COLUMN outcome TEXT DEFAULT NULL")
    _apply(20, "analyzed_calls.outcome_pnl", "ALTER TABLE analyzed_calls ADD COLUMN outcome_pnl REAL DEFAULT NULL")
    _apply(21, "analyzed_calls.hit_tp1", "ALTER TABLE analyzed_calls ADD COLUMN hit_tp1 INTEGER DEFAULT 0")
    _apply(22, "analyzed_calls.hit_tp2", "ALTER TABLE analyzed_calls ADD COLUMN hit_tp2 INTEGER DEFAULT 0")
    _apply(23, "analyzed_calls.hit_sl", "ALTER TABLE analyzed_calls ADD COLUMN hit_sl INTEGER DEFAULT 0")
    _apply(24, "analyzed_calls.outcome_at", "ALTER TABLE analyzed_calls ADD COLUMN outcome_at TEXT DEFAULT NULL")
    _apply(25, "trader_rulebook_history", """
        CREATE TABLE IF NOT EXISTS trader_rulebook_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            version     INTEGER NOT NULL,
            rules_json  TEXT    NOT NULL,
            trade_count INTEGER,
            saved_at    TEXT    DEFAULT (datetime('now'))
        )
    """)
    _apply(26, "analyzed_calls.gemini_score", "ALTER TABLE analyzed_calls ADD COLUMN gemini_score INTEGER DEFAULT NULL")
    _apply(27, "analyzed_calls.consensus_score", "ALTER TABLE analyzed_calls ADD COLUMN consensus_score REAL DEFAULT NULL")
    _apply(28, "analyzed_calls.consensus_flag", "ALTER TABLE analyzed_calls ADD COLUMN consensus_flag TEXT DEFAULT NULL")
    _apply(29, "analyzed_calls.risk_verdict_json", "ALTER TABLE analyzed_calls ADD COLUMN risk_verdict_json TEXT DEFAULT NULL")
    _apply(30, "analyzed_calls.monitor_alert", "ALTER TABLE analyzed_calls ADD COLUMN monitor_alert INTEGER DEFAULT 0")
    _apply(31, "analyzed_calls.chart_png_b64", "ALTER TABLE analyzed_calls ADD COLUMN chart_png_b64 TEXT DEFAULT NULL")
    _apply(3, "pending_limits.bitget_order_id", "ALTER TABLE pending_limits ADD COLUMN bitget_order_id TEXT")
    _apply(4, "positions.analyst", "ALTER TABLE positions ADD COLUMN analyst TEXT DEFAULT ''")
    _apply(5, "positions.execution_grade", "ALTER TABLE positions ADD COLUMN execution_grade TEXT DEFAULT NULL")
    _apply(6, "positions.execution_grade_reason", "ALTER TABLE positions ADD COLUMN execution_grade_reason TEXT DEFAULT NULL")
    _apply(7, "positions.setup_type", "ALTER TABLE positions ADD COLUMN setup_type TEXT DEFAULT ''")
    _apply(8, "positions.call_id", "ALTER TABLE positions ADD COLUMN call_id INTEGER DEFAULT NULL")
    _apply(9, "positions.external_id", "ALTER TABLE positions ADD COLUMN external_id TEXT DEFAULT NULL")
    _apply(10, "positions.exchange", "ALTER TABLE positions ADD COLUMN exchange TEXT DEFAULT 'bitget'")
    _apply(11, "positions.leverage", "ALTER TABLE positions ADD COLUMN leverage INTEGER DEFAULT NULL")
    _apply(12, "positions.market_regime", "ALTER TABLE positions ADD COLUMN market_regime TEXT DEFAULT NULL")
    _apply(13, "positions.mfe_price", "ALTER TABLE positions ADD COLUMN mfe_price REAL DEFAULT NULL")
    _apply(14, "positions.mae_price", "ALTER TABLE positions ADD COLUMN mae_price REAL DEFAULT NULL")
    _apply(15, "positions.mfe_pct", "ALTER TABLE positions ADD COLUMN mfe_pct REAL DEFAULT NULL")
    _apply(16, "positions.mae_pct", "ALTER TABLE positions ADD COLUMN mae_pct REAL DEFAULT NULL")
    _apply(32, "optimizer_runs", """
        CREATE TABLE IF NOT EXISTS optimizer_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT    NOT NULL DEFAULT (datetime('now')),
            symbol      TEXT    NOT NULL,
            timeframe   TEXT    NOT NULL,
            days        INTEGER NOT NULL,
            n_trials    INTEGER NOT NULL,
            best_sharpe REAL,
            best_params TEXT,
            duration_sec REAL
        )
    """)
    _apply(33, "entry_watcher_recs", """
        CREATE TABLE IF NOT EXISTS entry_watcher_recs (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol            TEXT NOT NULL,
            direction         TEXT NOT NULL,
            alert_type        TEXT NOT NULL,
            entry_low         REAL,
            entry_high        REAL,
            sl_price          REAL,
            tp1_price         REAL,
            tp2_price         REAL,
            score             REAL,
            archetype         TEXT,
            rationale         TEXT,
            key_conditions    TEXT,
            status            TEXT DEFAULT 'active',
            invalidation_reason TEXT,
            replaced_by       TEXT,
            created_at        TEXT DEFAULT (datetime('now')),
            expires_at        TEXT,
            invalidated_at    TEXT,
            analysis_json     TEXT
        )
    """)
    _apply(34, "positions.setup_score", "ALTER TABLE positions ADD COLUMN setup_score INTEGER DEFAULT NULL")
    _apply(35, "positions.funding_pnl", "ALTER TABLE positions ADD COLUMN funding_pnl REAL DEFAULT NULL")
    _apply(36, "positions.signal_price", "ALTER TABLE positions ADD COLUMN signal_price REAL DEFAULT NULL")
    _apply(37, "positions.execution_lag_minutes", "ALTER TABLE positions ADD COLUMN execution_lag_minutes INTEGER DEFAULT NULL")
    _apply(38, "analyzed_calls.regime_label", "ALTER TABLE analyzed_calls ADD COLUMN regime_label TEXT DEFAULT NULL")
    _apply(39, "analyzed_calls.ml_win_prob", "ALTER TABLE analyzed_calls ADD COLUMN ml_win_prob REAL DEFAULT NULL")

    # ── Learning tables ────────────────────────────────────────────────────────

    cur.execute("""
        CREATE TABLE IF NOT EXISTS trade_learning (
            id                          INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id                    INTEGER REFERENCES positions(id),
            symbol                      TEXT NOT NULL,
            direction                   TEXT NOT NULL,
            strategy_name               TEXT NOT NULL,
            entry_time                  TEXT NOT NULL,
            exit_time                   TEXT,
            outcome                     TEXT,
            pnl_pct                     REAL,
            pnl_usdt                    REAL,
            duration_minutes            INTEGER,
            confluence_score_at_entry   REAL,
            signals_at_entry_json       TEXT,
            pre_trade_trajectory_pattern TEXT,
            pre_trade_coincidence_risk  TEXT,
            max_favorable_excursion_pct REAL,
            max_adverse_excursion_pct   REAL,
            sl_hit                      INTEGER DEFAULT 0,
            trailing_stop_hit           INTEGER DEFAULT 0,
            exit_reason                 TEXT,
            rulebook_version            TEXT,
            analyzed_at                 TEXT DEFAULT (datetime('now'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS signal_accuracy (
            strategy_uid        TEXT NOT NULL DEFAULT 'legacy',
            indicator_name      TEXT NOT NULL,
            total_fired         INTEGER DEFAULT 0,
            correct             INTEGER DEFAULT 0,
            accuracy_pct        REAL DEFAULT 0,
            confidence_95_low   REAL,
            confidence_95_high  REAL,
            verdict             TEXT DEFAULT 'insufficient_data',
            sample_size         INTEGER DEFAULT 0,
            updated_at          TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (strategy_uid, indicator_name)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS combination_accuracy (
            strategy_uid        TEXT NOT NULL DEFAULT 'legacy',
            combination_name    TEXT NOT NULL,
            direction_state     TEXT NOT NULL,
            trades              INTEGER DEFAULT 0,
            won                 INTEGER DEFAULT 0,
            win_rate_pct        REAL DEFAULT 0,
            avg_pnl_pct         REAL DEFAULT 0,
            p_value             REAL,
            significance        TEXT DEFAULT 'insufficient_data',
            updated_at          TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (strategy_uid, combination_name, direction_state)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS trajectory_accuracy (
            strategy_uid        TEXT NOT NULL DEFAULT 'legacy',
            trajectory_pattern  TEXT NOT NULL,
            trades              INTEGER DEFAULT 0,
            won                 INTEGER DEFAULT 0,
            win_rate_pct        REAL DEFAULT 0,
            avg_pnl_pct         REAL DEFAULT 0,
            verdict             TEXT DEFAULT 'insufficient_data',
            updated_at          TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (strategy_uid, trajectory_pattern)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS idle_cycles (
            id                        INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp                 TEXT NOT NULL,
            strategy_name             TEXT NOT NULL,
            idle_reasons_json         TEXT,
            market_conditions_json    TEXT,
            top_candidate_symbol      TEXT,
            top_candidate_score       REAL,
            hypothetical_pnl_if_entered REAL,
            retrospect_validated      INTEGER DEFAULT 0,
            created_at                TEXT DEFAULT (datetime('now'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS idle_condition_accuracy (
            strategy_uid                TEXT NOT NULL DEFAULT 'legacy',
            condition_description       TEXT NOT NULL,
            idle_cycles                 INTEGER DEFAULT 0,
            hypothetical_avg_loss_pct   REAL DEFAULT 0,
            waiting_was_correct_pct     REAL DEFAULT 0,
            verdict                     TEXT DEFAULT 'insufficient_data',
            updated_at                  TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (strategy_uid, condition_description)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS weight_history (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_uid        TEXT NOT NULL DEFAULT 'legacy',
            indicator_name      TEXT NOT NULL,
            old_weight          REAL NOT NULL,
            new_weight          REAL NOT NULL,
            justification       TEXT,
            accuracy_at_time    REAL,
            sample_size_at_time INTEGER,
            changed_at          TEXT DEFAULT (datetime('now'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS rulebook_versions (
            id                          INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_uid                TEXT NOT NULL DEFAULT 'legacy',
            version                     TEXT NOT NULL,
            rulebook_text               TEXT NOT NULL,
            generated_at                TEXT DEFAULT (datetime('now')),
            trades_recorded_at_generation INTEGER,
            source_counts_json           TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS challenger_log (
            id                       INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_uid             TEXT NOT NULL,
            event_type               TEXT NOT NULL,
            source                   TEXT,
            timestamp                TEXT DEFAULT (datetime('now')),
            challenger_weights_json  TEXT,
            current_weights_json     TEXT,
            reason                   TEXT,
            production_profit_factor REAL,
            challenger_profit_factor REAL,
            promoted                 INTEGER DEFAULT 0,
            trade_count              INTEGER,
            symbol                   TEXT,
            entry_score              REAL,
            exit_pnl_pct             REAL,
            exit_reason              TEXT,
            signal_states_json       TEXT
        )
    """)

    # ── Strategy UID migrations ──────────────────────────────────────────────
    _apply(40, "trade_learning.strategy_uid", "ALTER TABLE trade_learning ADD COLUMN strategy_uid TEXT DEFAULT 'legacy'")
    _apply(41, "weight_history.strategy_uid", "ALTER TABLE weight_history ADD COLUMN strategy_uid TEXT DEFAULT 'legacy'")
    _apply(42, "rulebook_versions.strategy_uid", "ALTER TABLE rulebook_versions ADD COLUMN strategy_uid TEXT DEFAULT 'legacy'")

    _apply(43, "signal_accuracy.strategy_uid_pk", """
        ALTER TABLE signal_accuracy RENAME TO _signal_accuracy_old;
        CREATE TABLE signal_accuracy (
            strategy_uid        TEXT NOT NULL DEFAULT 'legacy',
            indicator_name      TEXT NOT NULL,
            total_fired         INTEGER DEFAULT 0,
            correct             INTEGER DEFAULT 0,
            accuracy_pct        REAL DEFAULT 0,
            confidence_95_low   REAL,
            confidence_95_high  REAL,
            verdict             TEXT DEFAULT 'insufficient_data',
            sample_size         INTEGER DEFAULT 0,
            updated_at          TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (strategy_uid, indicator_name)
        );
        INSERT INTO signal_accuracy (strategy_uid, indicator_name, total_fired, correct,
            accuracy_pct, confidence_95_low, confidence_95_high, verdict, sample_size, updated_at)
            SELECT 'legacy', indicator_name, total_fired, correct,
            accuracy_pct, confidence_95_low, confidence_95_high, verdict, sample_size, updated_at
            FROM _signal_accuracy_old;
        DROP TABLE _signal_accuracy_old;
    """)

    _apply(44, "combination_accuracy.strategy_uid_pk", """
        ALTER TABLE combination_accuracy RENAME TO _combination_accuracy_old;
        CREATE TABLE combination_accuracy (
            strategy_uid        TEXT NOT NULL DEFAULT 'legacy',
            combination_name    TEXT NOT NULL,
            direction_state     TEXT NOT NULL,
            trades              INTEGER DEFAULT 0,
            won                 INTEGER DEFAULT 0,
            win_rate_pct        REAL DEFAULT 0,
            avg_pnl_pct         REAL DEFAULT 0,
            p_value             REAL,
            significance        TEXT DEFAULT 'insufficient_data',
            updated_at          TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (strategy_uid, combination_name, direction_state)
        );
        INSERT INTO combination_accuracy (strategy_uid, combination_name, direction_state,
            trades, won, win_rate_pct, avg_pnl_pct, p_value, significance, updated_at)
            SELECT 'legacy', combination_name, direction_state,
            trades, won, win_rate_pct, avg_pnl_pct, p_value, significance, updated_at
            FROM _combination_accuracy_old;
        DROP TABLE _combination_accuracy_old;
    """)

    _apply(45, "trajectory_accuracy.strategy_uid_pk", """
        ALTER TABLE trajectory_accuracy RENAME TO _trajectory_accuracy_old;
        CREATE TABLE trajectory_accuracy (
            strategy_uid        TEXT NOT NULL DEFAULT 'legacy',
            trajectory_pattern  TEXT NOT NULL,
            trades              INTEGER DEFAULT 0,
            won                 INTEGER DEFAULT 0,
            win_rate_pct        REAL DEFAULT 0,
            avg_pnl_pct         REAL DEFAULT 0,
            verdict             TEXT DEFAULT 'insufficient_data',
            updated_at          TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (strategy_uid, trajectory_pattern)
        );
        INSERT INTO trajectory_accuracy (strategy_uid, trajectory_pattern,
            trades, won, win_rate_pct, avg_pnl_pct, verdict, updated_at)
            SELECT 'legacy', trajectory_pattern,
            trades, won, win_rate_pct, avg_pnl_pct, verdict, updated_at
            FROM _trajectory_accuracy_old;
        DROP TABLE _trajectory_accuracy_old;
    """)

    _apply(46, "idle_condition_accuracy.strategy_uid_pk", """
        ALTER TABLE idle_condition_accuracy RENAME TO _idle_condition_accuracy_old;
        CREATE TABLE idle_condition_accuracy (
            strategy_uid                TEXT NOT NULL DEFAULT 'legacy',
            condition_description       TEXT NOT NULL,
            idle_cycles                 INTEGER DEFAULT 0,
            hypothetical_avg_loss_pct   REAL DEFAULT 0,
            waiting_was_correct_pct     REAL DEFAULT 0,
            verdict                     TEXT DEFAULT 'insufficient_data',
            updated_at                  TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (strategy_uid, condition_description)
        );
        INSERT INTO idle_condition_accuracy (strategy_uid, condition_description,
            idle_cycles, hypothetical_avg_loss_pct, waiting_was_correct_pct, verdict, updated_at)
            SELECT 'legacy', condition_description,
            idle_cycles, hypothetical_avg_loss_pct, waiting_was_correct_pct, verdict, updated_at
            FROM _idle_condition_accuracy_old;
        DROP TABLE _idle_condition_accuracy_old;
    """)

    _apply(47, "challenger_log", """
        CREATE TABLE IF NOT EXISTS challenger_log (
            id                       INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_uid             TEXT NOT NULL,
            event_type               TEXT NOT NULL,
            source                   TEXT,
            timestamp                TEXT DEFAULT (datetime('now')),
            challenger_weights_json  TEXT,
            current_weights_json     TEXT,
            reason                   TEXT,
            production_profit_factor REAL,
            challenger_profit_factor REAL,
            promoted                 INTEGER DEFAULT 0,
            trade_count              INTEGER,
            symbol                   TEXT,
            entry_score              REAL,
            exit_pnl_pct             REAL,
            exit_reason              TEXT,
            signal_states_json       TEXT
        )
    """)

    _apply(48, "trade_learning_llm_fields", """
        ALTER TABLE trade_learning ADD COLUMN llm_verdict TEXT;
        ALTER TABLE trade_learning ADD COLUMN llm_reason TEXT;
        ALTER TABLE trade_learning ADD COLUMN llm_model TEXT;
        ALTER TABLE trade_learning ADD COLUMN llm_enabled INTEGER DEFAULT 0;
        ALTER TABLE trade_learning ADD COLUMN llm_override INTEGER DEFAULT 0
    """)

    _apply(49, "karpathy_log", """
        CREATE TABLE IF NOT EXISTS karpathy_log (
            id                       INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_uid             TEXT NOT NULL,
            timestamp                TEXT DEFAULT (datetime('now')),
            param_changed            TEXT NOT NULL,
            old_value                REAL NOT NULL,
            new_value                REAL NOT NULL,
            baseline_profit_factor   REAL,
            proposed_profit_factor   REAL,
            backtest_trades_count    INTEGER DEFAULT 0,
            kept_or_discarded        TEXT NOT NULL,
            reason                   TEXT
        )
    """)

    _apply(50, "hyperopt_log", """
        CREATE TABLE IF NOT EXISTS hyperopt_log (
            id                       INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_uid             TEXT NOT NULL,
            timestamp                TEXT DEFAULT (datetime('now')),
            n_trials                 INTEGER DEFAULT 0,
            baseline_profit_factor   REAL,
            best_profit_factor       REAL,
            candidates_pushed        INTEGER DEFAULT 0,
            search_space_json        TEXT,
            best_weights_json        TEXT,
            duration_seconds         REAL,
            reason                   TEXT
        )
    """)

    _apply(51, "signal_accuracy_by_threshold", """
        CREATE TABLE IF NOT EXISTS signal_accuracy_by_threshold (
            strategy_uid        TEXT NOT NULL,
            indicator_name      TEXT NOT NULL,
            threshold_bucket    TEXT NOT NULL,
            threshold_value     REAL NOT NULL,
            total_fired         INTEGER DEFAULT 0,
            correct             INTEGER DEFAULT 0,
            accuracy_pct        REAL DEFAULT 0,
            confidence_95_low   REAL DEFAULT 0,
            confidence_95_high  REAL DEFAULT 0,
            verdict             TEXT DEFAULT 'insufficient_data',
            sample_size         INTEGER DEFAULT 0,
            profit_factor       REAL,
            win_rate            REAL,
            trade_count         INTEGER DEFAULT 0,
            updated_at          TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (strategy_uid, indicator_name, threshold_bucket)
        )
    """)

    # ── Exchange-as-truth: new tables ─────────────────────────────────────────

    _apply(60, "exchange_as_truth_v3", """
        -- Position metadata for TP recalculation after restart
        -- NOT position state (that comes from exchange)
        CREATE TABLE IF NOT EXISTS position_metadata (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol          TEXT NOT NULL,
            direction       TEXT NOT NULL,
            entry_price     REAL NOT NULL,
            strategy_uid    TEXT NOT NULL DEFAULT 'legacy',
            atr_value       REAL DEFAULT 0,
            atr_pct         REAL DEFAULT 0,
            sl_price        REAL DEFAULT 0,
            tp1             REAL DEFAULT 0,
            tp2             REAL DEFAULT 0,
            size_usdt       REAL DEFAULT 0,
            opened_at       TEXT,
            closed_at       TEXT,
            sl_order_id     TEXT DEFAULT '',
            tp1_order_id    TEXT DEFAULT '',
            tp2_order_id    TEXT DEFAULT '',
            native_trail_order_id TEXT DEFAULT '',
            max_profit_atr  REAL DEFAULT 0,
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now'))
        )
    """)

    _apply(61, "adjusted_weights", """
        CREATE TABLE IF NOT EXISTS adjusted_weights (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_uid    TEXT NOT NULL,
            indicator_name  TEXT NOT NULL,
            weight          REAL NOT NULL,
            updated_at      TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (strategy_uid, indicator_name)
        )
    """)

    _apply(62, "adjusted_thresholds", """
        CREATE TABLE IF NOT EXISTS adjusted_thresholds (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_uid    TEXT NOT NULL,
            threshold_name  TEXT NOT NULL,
            value           REAL NOT NULL,
            updated_at      TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (strategy_uid, threshold_name)
        )
    """)

    _apply(63, "suppressed_signals", """
        CREATE TABLE IF NOT EXISTS suppressed_signals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_uid    TEXT NOT NULL,
            indicator_name  TEXT NOT NULL,
            reason          TEXT DEFAULT '',
            updated_at      TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (strategy_uid, indicator_name)
        )
    """)

    _apply(64, "highlight_signals", """
        CREATE TABLE IF NOT EXISTS highlight_signals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_uid    TEXT NOT NULL,
            indicator_name  TEXT NOT NULL,
            reason          TEXT DEFAULT '',
            updated_at      TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (strategy_uid, indicator_name)
        )
    """)

    _apply(65, "challenger_state", """
        CREATE TABLE IF NOT EXISTS challenger_state (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_uid    TEXT NOT NULL,
            state_json      TEXT NOT NULL,
            updated_at      TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (strategy_uid)
        )
    """)

    # ── Exchange-as-truth: drop substrate persistence tables ──────────────────
    # These tables are no longer needed — substrate is ephemeral.
    # Use DROP IF EXISTS so this is safe on both fresh and existing DBs.
    _apply(66, "drop_substrate_state", "DROP TABLE IF EXISTS substrate_state")
    _apply(67, "drop_cycle_log", "DROP TABLE IF EXISTS cycle_log")

    conn.commit()
    _log.info("DB initialized at %s", DB_PATH)


# --- Substrate persistence helpers (REMOVED) ──────────────────────────────
# Exchange-as-truth: substrate is ephemeral, never persisted to DB.
# load_latest_substrate() — REMOVED
# save_substrate() — REMOVED
# save_cycle_log() — KEPT for audit logging (positions, not substrate state)


def save_cycle_log(
    strategy_name: str,
    cycle_count: int,
    action: str,
    enzymes_fired: list,
    isc_results: dict,
    duration_ms: int,
) -> int:
    """Log a completed cycle to the cycle_log table (if it exists)."""
    try:
        with db_conn() as conn:
            cur = conn.execute(
                """INSERT INTO cycle_log
                   (strategy_name, cycle_count, action, enzymes_fired,
                    isc_results, duration_ms)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    strategy_name,
                    cycle_count,
                    action,
                    json.dumps(enzymes_fired),
                    json.dumps(isc_results),
                    duration_ms,
                ),
            )
            return cur.lastrowid
    except Exception:
        # cycle_log table may not exist (dropped by exchange-as-truth migration)
        # This is fine — cycle logging is optional
        return 0
