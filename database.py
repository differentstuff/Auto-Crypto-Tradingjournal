"""
database.py — SQLite schema definition and connection helpers.

The database has four tables:
  positions       — one row per closed trade (core data, from position_history CSV)
  orders          — individual order fills linked to a position
  wallet_snapshots — wallet balance history (for equity curve chart)
  import_log      — tracks which CSV files have been imported and when
"""

import sqlite3
import os
from contextlib import contextmanager

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
    _new_cols = [
        ("analyst",         "TEXT DEFAULT ''"),
        ("outcome",         "TEXT DEFAULT NULL"),
        ("outcome_pnl",     "REAL DEFAULT NULL"),
        ("hit_tp1",         "INTEGER DEFAULT 0"),
        ("hit_tp2",         "INTEGER DEFAULT 0"),
        ("hit_sl",          "INTEGER DEFAULT 0"),
        ("outcome_at",      "TEXT DEFAULT NULL"),
        ("actual_notional", "REAL DEFAULT NULL"),
    ]
    for _col, _typedef in _new_cols:
        try:
            cur.execute(f"ALTER TABLE analyzed_calls ADD COLUMN {_col} {_typedef}")
        except sqlite3.OperationalError:
            pass

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
    # Safe migration for bitget_order_id on existing tables
    try:
        cur.execute("ALTER TABLE pending_limits ADD COLUMN bitget_order_id TEXT")
    except sqlite3.OperationalError:
        pass

    # ── positions column migrations ────────────────────────────────────────────
    _pos_new_cols = [
        ("analyst",                "TEXT DEFAULT ''"),
        ("execution_grade",        "TEXT DEFAULT NULL"),
        ("execution_grade_reason", "TEXT DEFAULT NULL"),
        ("setup_type",             "TEXT DEFAULT ''"),
        ("call_id",                "INTEGER DEFAULT NULL"),
    ]
    for _col, _typedef in _pos_new_cols:
        try:
            cur.execute(f"ALTER TABLE positions ADD COLUMN {_col} {_typedef}")
        except sqlite3.OperationalError:
            pass

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

    conn.commit()
    conn.close()
    print(f"[DB] Initialized at {DB_PATH}")


if __name__ == "__main__":
    init_db()
