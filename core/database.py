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

Flask-journal legacy tables REMOVED (positions, orders, wallet_snapshots,
analyzed_calls, pending_limits, trader_rulebook, trader_rulebook_history,
trade_hindsight, settings, import_log, optimizer_runs, entry_watcher_recs)
— this was originally a manual trading journal fork; the current system is
fully automated, no manual trading was ever deployed against this schema.
Also removed: trade_learning.trade_id column (dangling FK to removed
positions table, never populated by any INSERT).

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
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "data/auto_trader.db"),
)

SCHEMA_VERSION = 1  # Bump when adding new migrations


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
    """Internal: create all tables in their final form, then run any pending migrations."""
    cur = conn.cursor()

    # -- schema_version ---------------------------------------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version    INTEGER PRIMARY KEY,
            name       TEXT    NOT NULL,
            applied_at TEXT    DEFAULT (datetime('now'))
        )
    """)

    # -- Baseline: all tables in their FINAL form -------------------------------
    # If the DB is fresh, CREATE IF NOT EXISTS creates everything.
    # If the DB is existing, IF NOT EXISTS skips already-present tables,
    # and _apply_migration handles ALTERs for columns added later.

    # -- token_usage -----------------------------------------------------



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



    # -- Learning tables (final form) --------------------------------------------

    cur.execute("""
        CREATE TABLE IF NOT EXISTS trade_learning (
            id                          INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol                      TEXT NOT NULL,
            direction                   TEXT NOT NULL,
            strategy_name               TEXT NOT NULL,
            strategy_uid                TEXT    DEFAULT 'legacy',
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
            llm_verdict                 TEXT,
            llm_reason                  TEXT,
            llm_model                   TEXT,
            llm_enabled                 INTEGER DEFAULT 0,
            llm_override                INTEGER DEFAULT 0,
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

    cur.execute("""
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

    cur.execute("""
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

    cur.execute("""
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

    # -- Exchange-as-truth tables ------------------------------------------------

    cur.execute("""
        CREATE TABLE IF NOT EXISTS position_metadata (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol                  TEXT NOT NULL,
            direction               TEXT NOT NULL,
            entry_price             REAL NOT NULL,
            strategy_uid            TEXT NOT NULL DEFAULT 'legacy',
            atr_value               REAL DEFAULT 0,
            atr_pct                 REAL DEFAULT 0,
            sl_price                REAL DEFAULT 0,
            tp1                     REAL DEFAULT 0,
            tp2                     REAL DEFAULT 0,
            size_usdt               REAL DEFAULT 0,
            opened_at               TEXT,
            closed_at               TEXT,
            sl_order_id             TEXT DEFAULT '',
            tp1_order_id            TEXT DEFAULT '',
            tp2_order_id           TEXT DEFAULT '',
            native_trail_order_id   TEXT DEFAULT '',
            max_profit_atr          REAL DEFAULT 0,
            created_at              TEXT DEFAULT (datetime('now')),
            updated_at              TEXT DEFAULT (datetime('now'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS adjusted_weights (
            strategy_uid    TEXT NOT NULL,
            indicator_name  TEXT NOT NULL,
            weight          REAL NOT NULL,
            updated_at      TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (strategy_uid, indicator_name)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS adjusted_thresholds (
            strategy_uid    TEXT NOT NULL,
            threshold_name  TEXT NOT NULL,
            value           REAL NOT NULL,
            updated_at      TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (strategy_uid, threshold_name)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS suppressed_signals (
            strategy_uid    TEXT NOT NULL,
            indicator_name  TEXT NOT NULL,
            reason          TEXT DEFAULT '',
            updated_at      TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (strategy_uid, indicator_name)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS highlight_signals (
            strategy_uid    TEXT NOT NULL,
            indicator_name  TEXT NOT NULL,
            reason          TEXT DEFAULT '',
            updated_at      TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (strategy_uid, indicator_name)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS challenger_state (
            strategy_uid    TEXT NOT NULL PRIMARY KEY,
            state_json      TEXT NOT NULL,
            updated_at      TEXT DEFAULT (datetime('now'))
        )
    """)

    # -- Drop obsolete tables (exchange-as-truth) ------------------------------
    cur.execute("DROP TABLE IF EXISTS substrate_state")
    cur.execute("DROP TABLE IF EXISTS cycle_log")

    conn.commit()

    # -- Mark baseline as applied -----------------------------------------------
    # Record baseline version so future _apply_migration calls know we're past 0
    baseline = conn.execute(
        "SELECT 1 FROM schema_version WHERE version=0"
    ).fetchone()
    if not baseline:
        conn.execute(
            "INSERT INTO schema_version (version, name) VALUES (0, 'baseline_v1')"
        )
        conn.commit()
        _log.info("Applied baseline schema v1")

    # -- Future migrations ------------------------------------------------------
    # Add new migrations here using _apply_migration().
    # The baseline (version 0) covers everything up to the exchange-as-truth rewrite.
    # Version numbers start at 100 for post-rewrite migrations.

    _log.info("DB initialized at %s", DB_PATH)


def _apply_migration(conn, ver: int, name: str, sql: str):
    """Apply a single migration if not already applied."""
    applied = conn.execute(
        "SELECT 1 FROM schema_version WHERE version=?", (ver,)
    ).fetchone() is not None

    if applied:
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


# --- Substrate persistence helpers (REMOVED) ------------------------------
# Exchange-as-truth: substrate is ephemeral, never persisted to DB.
# load_latest_substrate() — REMOVED
# save_substrate() — REMOVED
# save_cycle_log() — REMOVED (cycle_log table dropped)
