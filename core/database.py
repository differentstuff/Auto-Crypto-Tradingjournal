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

    # ── schema_version ─────────────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version    INTEGER PRIMARY KEY,
            name       TEXT    NOT NULL,
            applied_at TEXT    DEFAULT (datetime('now'))
        )
    """)

    # ── Baseline: all tables in their FINAL form ───────────────────────────────
    # If the DB is fresh, CREATE IF NOT EXISTS creates everything.
    # If the DB is existing, IF NOT EXISTS skips already-present tables,
    # and _apply_migration handles ALTERs for columns added later.

    cur.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id                     INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol                 TEXT    NOT NULL,
            base_asset             TEXT    NOT NULL,
            direction              TEXT    NOT NULL,
            margin_mode            TEXT,
            open_time              TEXT    NOT NULL,
            close_time             TEXT    NOT NULL,
            duration_minutes       INTEGER,
            entry_price            REAL,
            close_price            REAL,
            size_contracts         TEXT,
            size_usdt              REAL,
            position_pnl           REAL,
            realized_pnl           REAL,
            opening_fee            REAL,
            closing_fee            REAL,
            total_fees             REAL,
            notes                  TEXT    DEFAULT '',
            tags                   TEXT    DEFAULT '',
            is_manual              INTEGER DEFAULT 0,
            analyst                TEXT    DEFAULT '',
            execution_grade        TEXT,
            execution_grade_reason TEXT,
            setup_type             TEXT    DEFAULT '',
            call_id                INTEGER,
            external_id            TEXT,
            exchange               TEXT    DEFAULT 'bitget',
            leverage               INTEGER,
            market_regime          TEXT,
            mfe_price              REAL,
            mae_price              REAL,
            mfe_pct                REAL,
            mae_pct                REAL,
            setup_score            INTEGER,
            funding_pnl            REAL,
            signal_price           REAL,
            execution_lag_minutes  INTEGER,
            created_at             TEXT    DEFAULT (datetime('now')),
            updated_at             TEXT    DEFAULT (datetime('now'))
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
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol              TEXT    NOT NULL,
            direction           TEXT    NOT NULL,
            call_text           TEXT,
            entry_price         REAL,
            dca_price           REAL,
            sl_price            REAL,
            tp1_price           REAL,
            tp2_price           REAL,
            avg_entry           REAL,
            total_notional      REAL,
            margin_needed       REAL,
            risk_pct            REAL,
            risk_amount         REAL,
            leverage            INTEGER,
            has_dca             INTEGER DEFAULT 0,
            has_candle_close_sl INTEGER DEFAULT 0,
            setup_score         INTEGER,
            setup_label         TEXT,
            rr_ratio            TEXT,
            trade_type          TEXT,
            sl_warning          TEXT,
            entry_timing        TEXT,
            analysis_json       TEXT,
            status              TEXT    DEFAULT 'saved',
            matched_at          TEXT,
            exchange            TEXT    DEFAULT 'bitget',
            cot_reasoning       TEXT,
            analyst             TEXT    DEFAULT '',
            notes               TEXT    DEFAULT '',
            outcome             TEXT,
            outcome_pnl         REAL,
            hit_tp1             INTEGER DEFAULT 0,
            hit_tp2             INTEGER DEFAULT 0,
            hit_sl              INTEGER DEFAULT 0,
            outcome_at          TEXT,
            gemini_score        INTEGER,
            consensus_score     REAL,
            consensus_flag      TEXT,
            risk_verdict_json   TEXT,
            monitor_alert       INTEGER DEFAULT 0,
            chart_png_b64       TEXT,
            regime_label        TEXT,
            ml_win_prob         REAL,
            created_at          TEXT    DEFAULT (datetime('now'))
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
        CREATE TABLE IF NOT EXISTS trader_rulebook_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            version     INTEGER NOT NULL,
            rules_json  TEXT    NOT NULL,
            trade_count INTEGER,
            saved_at    TEXT    DEFAULT (datetime('now'))
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
            file_type       TEXT,
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

    cur.execute("""
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

    cur.execute("""
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

    # ── Learning tables (final form) ────────────────────────────────────────────

    cur.execute("""
        CREATE TABLE IF NOT EXISTS trade_learning (
            id                          INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id                    INTEGER REFERENCES positions(id),
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

    # ── Exchange-as-truth tables ────────────────────────────────────────────────

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

    # ── Drop obsolete tables (exchange-as-truth) ──────────────────────────────
    cur.execute("DROP TABLE IF EXISTS substrate_state")
    cur.execute("DROP TABLE IF EXISTS cycle_log")

    conn.commit()

    # ── Mark baseline as applied ───────────────────────────────────────────────
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

    # ── Future migrations ──────────────────────────────────────────────────────
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


# --- Substrate persistence helpers (REMOVED) ──────────────────────────────
# Exchange-as-truth: substrate is ephemeral, never persisted to DB.
# load_latest_substrate() — REMOVED
# save_substrate() — REMOVED
# save_cycle_log() — REMOVED (cycle_log table dropped)
