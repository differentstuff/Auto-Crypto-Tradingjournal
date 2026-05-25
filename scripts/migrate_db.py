#!/usr/bin/env python3
"""
Database migration script for Auto-Crypto-Tradingjournal.

Adds columns that may be missing from older database schemas.
Safe to rerun — checks if each column exists before adding it.

Usage:
    python3 scripts/migrate_db.py
    python3 scripts/migrate_db.py --db-path /path/to/trading_journal.db
"""

import sqlite3
import sys
import os

# Default DB path (same as core/database.py)
DEFAULT_DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "trading_journal.db"),
)


def column_exists(cursor: sqlite3.Cursor, table: str, column: str) -> bool:
    """Check if a column exists in a table."""
    cursor.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in cursor.fetchall()]
    return column in columns


def migrate_db(db_path: str = DEFAULT_DB_PATH) -> list[str]:
    """
    Run all pending migrations. Returns list of applied migrations.
    
    Safe to rerun — each migration checks if the column already exists.
    """
    if not os.path.exists(db_path):
        print(f"Database not found at {db_path}. It will be created by init_db() on first run.")
        return []
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    applied = []
    
    try:
        cur = conn.cursor()
        
        # Migration 47: trade_learning.pre_trade_trajectory_pattern
        if not column_exists(cur, "trade_learning", "pre_trade_trajectory_pattern"):
            cur.execute("ALTER TABLE trade_learning ADD COLUMN pre_trade_trajectory_pattern TEXT")
            applied.append("47: trade_learning.pre_trade_trajectory_pattern")
        
        # Migration 48: trade_learning.pre_trade_coincidence_risk
        if not column_exists(cur, "trade_learning", "pre_trade_coincidence_risk"):
            cur.execute("ALTER TABLE trade_learning ADD COLUMN pre_trade_coincidence_risk TEXT")
            applied.append("48: trade_learning.pre_trade_coincidence_risk")
        
        # Migration 49: trade_learning.strategy_uid (may already exist from migration 40)
        if not column_exists(cur, "trade_learning", "strategy_uid"):
            cur.execute("ALTER TABLE trade_learning ADD COLUMN strategy_uid TEXT DEFAULT 'legacy'")
            applied.append("49: trade_learning.strategy_uid")
        
        # Ensure schema_version table exists
        cur.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT DEFAULT (datetime('now'))
            )
        """)
        
        # Record applied migrations
        for migration in applied:
            version = int(migration.split(":")[0])
            name = migration.split(": ")[1]
            cur.execute(
                "INSERT OR IGNORE INTO schema_version (version, name) VALUES (?, ?)",
                (version, name),
            )
        
        conn.commit()
        
    finally:
        conn.close()
    
    return applied


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Database migration script")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH, help="Path to SQLite database")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
    args = parser.parse_args()
    
    if args.dry_run:
        print(f"DRY RUN: Would check migrations on {args.db_path}")
        print("Migrations that would be checked:")
        print("  47: trade_learning.pre_trade_trajectory_pattern")
        print("  48: trade_learning.pre_trade_coincidence_risk")
        print("  49: trade_learning.strategy_uid")
        sys.exit(0)
    
    applied = migrate_db(args.db_path)
    
    if applied:
        print(f"Applied {len(applied)} migration(s):")
        for migration in applied:
            print(f"  {migration}")
    else:
        print("All migrations already applied — no changes needed.")
    
    print(f"Database: {args.db_path}")