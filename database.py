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

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "trading_journal.db"))


def get_conn():
    """Return a sqlite3 connection with row_factory set to dict-like Row."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # safe for concurrent reads
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


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
