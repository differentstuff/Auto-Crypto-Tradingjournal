"""
core/database.py -- SQLite WAL database with learning tables.

Ports the existing database schema and adds new learning tables
for the reaction network architecture.

Existing tables (ported from database.py):
  - positions, orders, wallet_snapshots, analyzed_calls, etc.

New learning tables:
  - trade_learning, signal_accuracy, combination_accuracy,
    trajectory_accuracy, idle_cycles, idle_condition_accuracy,
    weight_history, rulebook_versions, substrate_state
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

    Callers do NOT need to call conn.commit() manually. Any unhandled
    exception triggers a rollback so partial writes are never silently lost.
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

    Uses try/finally to guarantee the connection is closed even if a
    migration fails mid-way.
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
            # Use executescript for multi-statement SQL (migrations that
            # rename+create+insert+drop), execute for single statements.
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

    # ── Learning tables (CREATE before UID migrations so fresh DBs have new schema) ──

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
        CREATE TABLE IF NOT EXISTS substrate_state (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_name   TEXT NOT NULL,
            cycle_count     INTEGER DEFAULT 0,
            substrate_json  TEXT NOT NULL,
            created_at      TEXT DEFAULT (datetime('now'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS cycle_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_name   TEXT NOT NULL,
            cycle_count     INTEGER NOT NULL,
            action          TEXT NOT NULL,
            enzymes_fired   TEXT,
            isc_results     TEXT,
            duration_ms     INTEGER,
            created_at      TEXT DEFAULT (datetime('now'))
        )
    """)

    conn.commit()

    # ── Strategy UID migrations (for existing databases with old schema) ──────
    # On fresh databases, CREATE TABLE IF NOT EXISTS already created the new schema.
    # These migrations only matter for existing databases that have the old schema.
    # Add strategy_uid column to tables that only need a new column (no PK change)
    _apply(40, "trade_learning.strategy_uid", "ALTER TABLE trade_learning ADD COLUMN strategy_uid TEXT DEFAULT 'legacy'")
    _apply(41, "weight_history.strategy_uid", "ALTER TABLE weight_history ADD COLUMN strategy_uid TEXT DEFAULT 'legacy'")
    _apply(42, "rulebook_versions.strategy_uid", "ALTER TABLE rulebook_versions ADD COLUMN strategy_uid TEXT DEFAULT 'legacy'")

    # Tables with PK changes need rebuild: rename, create new, migrate, drop old.
    # These only run on existing DBs where the old schema is present.
    # On fresh DBs, the tables already have the new schema so these are skipped
    # (schema_version already has them marked as applied by the CREATE TABLE step).
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

    # ── Challenger system tables ──────────────────────────────────────────────
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

    # ── LLM validation tracking fields ──────────────────────────────────────────
    # These columns record LLM verdicts alongside trade outcomes so we can
    # answer: "Does LLM validation actually improve trade outcomes?"
    # llm_verdict:  proceed|confirm|concern|adjust (or NULL if LLM was disabled)
    # llm_reason:   free-text LLM reasoning
    # llm_model:    model used (e.g. "z-ai/glm-5.1")
    # llm_enabled:  1 if LLM was active at trade time, 0 if not
    # llm_override: 1 if trade was allowed via LLM "proceed" despite sub-threshold score
    _apply(48, "trade_learning_llm_fields", """
        ALTER TABLE trade_learning ADD COLUMN llm_verdict TEXT;
        ALTER TABLE trade_learning ADD COLUMN llm_reason TEXT;
        ALTER TABLE trade_learning ADD COLUMN llm_model TEXT;
        ALTER TABLE trade_learning ADD COLUMN llm_enabled INTEGER DEFAULT 0;
        ALTER TABLE trade_learning ADD COLUMN llm_override INTEGER DEFAULT 0
    """)

    conn.commit()
    _log.info("DB initialized at %s", DB_PATH)
    # Note: connection is closed by init_db()'s finally block, not here.


# --- Substrate persistence helpers -------------------------------------------

def save_substrate(substrate, max_rows: int = 200) -> int:
    """
    Save substrate state to database. Returns row id.

    Prunes old rows so the table never exceeds max_rows per strategy.
    max_rows is read from config (daemon.substrate_state_max_rows) by the
    daemon and passed here. Default 200 keeps ~2 days of 15-min cycles.
    """
    strategy_name = substrate.strategy.get("name", "")
    with db_conn() as conn:
        cur = conn.execute(
            """INSERT INTO substrate_state
               (strategy_name, cycle_count, substrate_json)
               VALUES (?, ?, ?)""",
            (
                strategy_name,
                substrate._cycle_count,
                substrate.to_persistent_json(),
            ),
        )
        row_id = cur.lastrowid

        # Prune: keep only the most recent max_rows rows per strategy
        conn.execute(
            """DELETE FROM substrate_state
               WHERE strategy_name = ?
               AND id NOT IN (
                   SELECT id FROM substrate_state
                   WHERE strategy_name = ?
                   ORDER BY id DESC
                   LIMIT ?
               )""",
            (strategy_name, strategy_name, max_rows),
        )
        return row_id


def load_latest_substrate(strategy_name: str = "") -> Optional[dict]:
    """Load the most recent substrate state from database."""
    with db_conn() as conn:
        query = """
            SELECT substrate_json FROM substrate_state
            WHERE 1=1
        """
        params: list = []
        if strategy_name:
            query += " AND strategy_name = ?"
            params.append(strategy_name)
        query += " ORDER BY id DESC LIMIT 1"

        row = conn.execute(query, params).fetchone()
        if row:
            return json.loads(row["substrate_json"])
    return None


def save_cycle_log(
    strategy_name: str,
    cycle_count: int,
    action: str,
    enzymes_fired: list,
    isc_results: dict,
    duration_ms: int,
) -> int:
    """Log a completed cycle."""
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
