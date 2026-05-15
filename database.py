"""
database.py — SQLite schema definition and connection helpers.

The database has four tables:
  positions       — one row per closed trade (core data, from position_history CSV)
  orders          — individual order fills linked to a position
  wallet_snapshots — wallet balance history (for equity curve chart)
  import_log      — tracks which CSV files have been imported and when
"""

import logging
import os
import sqlite3
from contextlib import contextmanager

_log = logging.getLogger(__name__)

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "trading_journal.db"))


def get_conn():
    """Return a sqlite3 connection with row_factory set to dict-like Row."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # safe for concurrent reads
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db_conn():
    """Context manager that opens a connection and guarantees close on exit."""
    conn = get_conn()
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Create all tables if they do not exist yet. Safe to call on every startup."""
    conn = get_conn()
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
            conn.execute(sql)
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                _log.error("Migration %d (%s) failed: %s", ver, name, e, exc_info=True)
                raise
            _log.debug("Migration %d: column already exists (%s)", ver, name)
        conn.execute("INSERT INTO schema_version (version, name) VALUES (?,?)", (ver, name))
        conn.commit()
        _log.info("Applied migration %d: %s", ver, name)

    # ── positions ──────────────────────────────────────────────────────────────
    # Primary trade table. One row = one closed futures position.
    # Fields map directly to Bitget's "position history" export columns,
    # plus three user-editable fields (notes, tags) and two calculated ones
    # (duration_minutes, leverage_guess).
    cur.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol           TEXT    NOT NULL,        -- e.g. 'BOMEUSDT'
            base_asset       TEXT    NOT NULL,        -- e.g. 'BOME'
            direction        TEXT    NOT NULL,        -- 'Long' or 'Short'
            margin_mode      TEXT,                    -- 'Cross' or 'Isolated'
            open_time        TEXT    NOT NULL,        -- ISO datetime string
            close_time       TEXT    NOT NULL,        -- ISO datetime string
            duration_minutes INTEGER,                 -- calculated: close - open in minutes
            entry_price      REAL,
            close_price      REAL,
            size_contracts   TEXT,                    -- raw: '400000BOME'
            size_usdt        REAL,                    -- closed value in USDT
            position_pnl     REAL,                    -- gross PnL before fees
            realized_pnl     REAL,                    -- net PnL after fees
            opening_fee      REAL,
            closing_fee      REAL,
            total_fees       REAL,
            notes            TEXT    DEFAULT '',      -- user-editable freetext
            tags             TEXT    DEFAULT '',      -- comma-separated tags
            is_manual        INTEGER DEFAULT 0,       -- 1 if entered by hand (not imported)
            created_at       TEXT    DEFAULT (datetime('now')),
            updated_at       TEXT    DEFAULT (datetime('now'))
        )
    """)

    # ── orders ─────────────────────────────────────────────────────────────────
    # Individual order records from Bitget's "order history" export.
    # Linked to positions by symbol + time proximity (position_id set during import).
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
            executed         REAL,
            trading_volume   REAL,
            realized_pnl     REAL,
            net_profits      REAL,
            status           TEXT,
            position_id      INTEGER REFERENCES positions(id)
        )
    """)

    # ── wallet_snapshots ───────────────────────────────────────────────────────
    # Every row from Bitget's "transactions" export. Wallet balance at each event
    # lets us draw an equity curve and calculate max drawdown.
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

    # ── analyzed_calls ─────────────────────────────────────────────────────────
    # Saved trade call analyses. One row per call the user analyzed and saved.
    # status: 'saved' → 'matched' (confirmed link to live position) → 'closed'
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

    # ── analyzed_calls column migrations ──────────────────────────────────────────
    _apply(1,  "analyzed_calls.exchange",      "ALTER TABLE analyzed_calls ADD COLUMN exchange TEXT DEFAULT 'bitget'")
    _apply(2,  "analyzed_calls.cot_reasoning", "ALTER TABLE analyzed_calls ADD COLUMN cot_reasoning TEXT DEFAULT NULL")
    _apply(17, "analyzed_calls.analyst",       "ALTER TABLE analyzed_calls ADD COLUMN analyst TEXT DEFAULT ''")
    _apply(18, "analyzed_calls.notes",         "ALTER TABLE analyzed_calls ADD COLUMN notes TEXT DEFAULT ''")
    _apply(19, "analyzed_calls.outcome",       "ALTER TABLE analyzed_calls ADD COLUMN outcome TEXT DEFAULT NULL")
    _apply(20, "analyzed_calls.outcome_pnl",   "ALTER TABLE analyzed_calls ADD COLUMN outcome_pnl REAL DEFAULT NULL")
    _apply(21, "analyzed_calls.hit_tp1",       "ALTER TABLE analyzed_calls ADD COLUMN hit_tp1 INTEGER DEFAULT 0")
    _apply(22, "analyzed_calls.hit_tp2",       "ALTER TABLE analyzed_calls ADD COLUMN hit_tp2 INTEGER DEFAULT 0")
    _apply(23, "analyzed_calls.hit_sl",        "ALTER TABLE analyzed_calls ADD COLUMN hit_sl INTEGER DEFAULT 0")
    _apply(24, "analyzed_calls.outcome_at",    "ALTER TABLE analyzed_calls ADD COLUMN outcome_at TEXT DEFAULT NULL")
    _apply(26, "analyzed_calls.gemini_score",   "ALTER TABLE analyzed_calls ADD COLUMN gemini_score INTEGER DEFAULT NULL")
    _apply(27, "analyzed_calls.consensus_score","ALTER TABLE analyzed_calls ADD COLUMN consensus_score REAL DEFAULT NULL")
    _apply(28, "analyzed_calls.consensus_flag", "ALTER TABLE analyzed_calls ADD COLUMN consensus_flag TEXT DEFAULT NULL")
    _apply(29, "analyzed_calls.risk_verdict_json", "ALTER TABLE analyzed_calls ADD COLUMN risk_verdict_json TEXT DEFAULT NULL")
    _apply(30, "analyzed_calls.monitor_alert",     "ALTER TABLE analyzed_calls ADD COLUMN monitor_alert INTEGER DEFAULT 0")
    _apply(31, "analyzed_calls.chart_png_b64",     "ALTER TABLE analyzed_calls ADD COLUMN chart_png_b64 TEXT DEFAULT NULL")

    # ── pending_limits ─────────────────────────────────────────────────────────
    # Limit orders the user has placed on exchange but not yet triggered.
    # "Shadow trades" — tracked for risk and correlation analysis before they fill.
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
    _apply(3, "pending_limits.bitget_order_id", "ALTER TABLE pending_limits ADD COLUMN bitget_order_id TEXT")

    # ── positions column migrations ────────────────────────────────────────────
    _apply(4,  "positions.analyst",                "ALTER TABLE positions ADD COLUMN analyst TEXT DEFAULT ''")
    _apply(5,  "positions.execution_grade",        "ALTER TABLE positions ADD COLUMN execution_grade TEXT DEFAULT NULL")
    _apply(6,  "positions.execution_grade_reason", "ALTER TABLE positions ADD COLUMN execution_grade_reason TEXT DEFAULT NULL")
    _apply(7,  "positions.setup_type",             "ALTER TABLE positions ADD COLUMN setup_type TEXT DEFAULT ''")
    _apply(8,  "positions.call_id",                "ALTER TABLE positions ADD COLUMN call_id INTEGER DEFAULT NULL")
    _apply(9,  "positions.external_id",            "ALTER TABLE positions ADD COLUMN external_id TEXT DEFAULT NULL")
    _apply(10, "positions.exchange",               "ALTER TABLE positions ADD COLUMN exchange TEXT DEFAULT 'bitget'")
    _apply(11, "positions.leverage",               "ALTER TABLE positions ADD COLUMN leverage INTEGER DEFAULT NULL")
    _apply(12, "positions.market_regime",          "ALTER TABLE positions ADD COLUMN market_regime TEXT DEFAULT NULL")
    _apply(13, "positions.mfe_price",              "ALTER TABLE positions ADD COLUMN mfe_price REAL DEFAULT NULL")
    _apply(14, "positions.mae_price",              "ALTER TABLE positions ADD COLUMN mae_price REAL DEFAULT NULL")
    _apply(15, "positions.mfe_pct",                "ALTER TABLE positions ADD COLUMN mfe_pct REAL DEFAULT NULL")
    _apply(16, "positions.mae_pct",                "ALTER TABLE positions ADD COLUMN mae_pct REAL DEFAULT NULL")

    # ── trader_rulebook ────────────────────────────────────────────────────────
    # Personalised rules synthesised by Claude from trade history.
    # Cleared and regenerated on each rulebook update.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trader_rulebook (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_type    TEXT NOT NULL,   -- 'warning', 'strength', 'habit', 'calibration'
            title        TEXT NOT NULL,
            rule         TEXT NOT NULL,
            confidence   TEXT DEFAULT 'medium',
            data_points  INTEGER DEFAULT 0,
            generated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # ── trade_hindsight ────────────────────────────────────────────────────────
    # Retroactive AI analysis: what would Claude have recommended before each trade?
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trade_hindsight (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id      INTEGER UNIQUE REFERENCES positions(id),
            analyzed_at      TEXT DEFAULT (datetime('now')),

            -- Recommendation (blind — Claude didn't know the actual outcome)
            setup_score      INTEGER,
            setup_label      TEXT,
            would_enter      INTEGER,  -- 1=ENTER, 0=SKIP
            rec_direction    TEXT,     -- Long/Short Claude recommended
            direction_match  INTEGER,  -- 1 if rec matches actual direction
            rec_entry_low    REAL,
            rec_entry_high   REAL,
            rec_sl           REAL,
            rec_tp1          REAL,
            rec_tp2          REAL,
            rec_rr           TEXT,
            key_conditions   TEXT,     -- JSON array
            risks            TEXT,     -- JSON array
            skip_reason      TEXT,

            -- Comparison
            actual_pnl       REAL,
            hypothetical_pnl REAL,     -- P&L if recommendation had been followed
            verdict          TEXT,     -- TP|TN|FP|FN|NEUTRAL (signal accuracy category)

            -- Raw
            analysis_json    TEXT,
            input_tokens     INTEGER,
            output_tokens    INTEGER
        )
    """)

    # ── trader_rulebook_history ────────────────────────────────────────────────
    # Keeps the last 3 rulebook versions so we can compare rule evolution.
    _apply(25, "trader_rulebook_history", """
        CREATE TABLE IF NOT EXISTS trader_rulebook_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            version     INTEGER NOT NULL,
            rules_json  TEXT    NOT NULL,
            trade_count INTEGER,
            saved_at    TEXT    DEFAULT (datetime('now'))
        )
    """)

    # ── settings ──────────────────────────────────────────────────────────────
    # Key-value store: last sync time, account equity, rulebook timestamps.
    # Also created by bitget_sync._ensure_settings_table() but must exist here
    # so ai_rulebook works even if a sync has never run.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # ── import_log ─────────────────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS import_log (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            filename       TEXT,
            file_type      TEXT,     -- 'positions', 'orders', 'order_details', 'transactions'
            rows_imported  INTEGER,
            imported_at    TEXT DEFAULT (datetime('now'))
        )
    """)

    # ── token_usage ────────────────────────────────────────────────────────────
    # One row per Claude API call. Provides cost visibility per module.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS token_usage (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            ts             TEXT    DEFAULT (datetime('now')),
            module         TEXT    NOT NULL,   -- 'call_analyzer', 'scanner', 'rulebook', 'hindsight', 'advisor'
            model          TEXT    NOT NULL,
            input_tokens   INTEGER NOT NULL,
            output_tokens  INTEGER NOT NULL,
            cached_tokens  INTEGER DEFAULT 0
        )
    """)

    conn.commit()
    _log.info("DB initialized at %s", DB_PATH)
    conn.close()


if __name__ == "__main__":
    init_db()
