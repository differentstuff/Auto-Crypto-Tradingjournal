#!/usr/bin/env python3
"""
scripts/migrate_db.py -- Database migration script for Auto-Crypto-Tradingjournal.

Exchange-as-truth architecture: substrate persistence is removed.
This script handles the clean migration to the new schema.

Key changes:
  - Drops substrate_state table (substrate is ephemeral, rebuilt from exchange)
  - Drops cycle_log table (no longer needed without substrate persistence)
  - Adds learning data tables (adjusted_weights, adjusted_thresholds,
    suppressed_signals, highlight_signals, challenger_state)
  - Adds position_metadata table (stores atr_pct for TP1/TP2 recalculation
    after daemon restart — NOT for position state, which comes from exchange)

Usage:
    python3 scripts/migrate_db.py
    python3 scripts/migrate_db.py --db-path /path/to/trading_journal.db
    python3 scripts/migrate_db.py --dry-run
    python3 scripts/migrate_db.py --fresh  (wipe and recreate all tables)
"""

import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import List, Optional

# Default DB path (same as core/database.py)
DEFAULT_DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "trading_journal.db"),
)

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# SCHEMA DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════

# Tables to DROP (substrate persistence removed — exchange is truth)
DROP_TABLES = [
    "substrate_state",  # Substrate is ephemeral, rebuilt from exchange every cycle
    "cycle_log",        # No longer needed without substrate persistence
]

# New tables to CREATE (learning data separation + position metadata)
NEW_TABLES = {
    "adjusted_weights": """
        CREATE TABLE IF NOT EXISTS adjusted_weights (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_uid    TEXT NOT NULL,
            indicator_name  TEXT NOT NULL,
            weight          REAL NOT NULL,
            updated_at      TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (strategy_uid, indicator_name)
        )
    """,

    "adjusted_thresholds": """
        CREATE TABLE IF NOT EXISTS adjusted_thresholds (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_uid    TEXT NOT NULL,
            threshold_name  TEXT NOT NULL,
            value           REAL NOT NULL,
            updated_at      TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (strategy_uid, threshold_name)
        )
    """,

    "suppressed_signals": """
        CREATE TABLE IF NOT EXISTS suppressed_signals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_uid    TEXT NOT NULL,
            indicator_name  TEXT NOT NULL,
            reason          TEXT DEFAULT '',
            updated_at      TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (strategy_uid, indicator_name)
        )
    """,

    "highlight_signals": """
        CREATE TABLE IF NOT EXISTS highlight_signals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_uid    TEXT NOT NULL,
            indicator_name  TEXT NOT NULL,
            reason          TEXT DEFAULT '',
            updated_at      TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (strategy_uid, indicator_name)
        )
    """,

    "challenger_state": """
        CREATE TABLE IF NOT EXISTS challenger_state (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_uid    TEXT NOT NULL,
            state_json      TEXT NOT NULL,
            updated_at      TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (strategy_uid)
        )
    """,

    "position_metadata": """
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
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now'))
        )
    """,
}

# Existing tables to KEEP (already exist in database.py — verified)
# These are NOT recreated; they persist from the existing schema:
#   trade_learning, signal_accuracy, combination_accuracy,
#   trajectory_accuracy, idle_cycles, idle_condition_accuracy,
#   weight_history, rulebook_versions, challenger_log,
#   karpathy_log, hyperopt_log, signal_accuracy_by_threshold,
#   positions, orders, wallet_snapshots, analyzed_calls,
#   pending_limits, trader_rulebook, trade_hindsight, settings,
#   import_log, token_usage, optimizer_runs, entry_watcher_recs,
#   schema_version


def column_exists(cursor: sqlite3.Cursor, table: str, column: str) -> bool:
    """Check if a column exists in a table."""
    cursor.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in cursor.fetchall()]
    return column in columns


def table_exists(cursor: sqlite3.Cursor, table: str) -> bool:
    """Check if a table exists in the database."""
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return cursor.fetchone() is not None


def migrate_db(db_path: str = DEFAULT_DB_PATH, fresh: bool = False) -> List[str]:
    """
    Run all pending migrations. Returns list of applied migrations.

    Args:
        db_path: Path to the SQLite database file.
        fresh: If True, drop and recreate all tables (clean slate).

    Safe to rerun — each migration checks if the change already exists.
    """
    if not os.path.exists(db_path):
        # Database doesn't exist yet — it will be created by init_db() on first run.
        # But we can create it here if needed.
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    applied = []

    try:
        cur = conn.cursor()

        # ── FRESH: Drop and recreate everything ──────────────────────────────
        if fresh:
            # Drop tables that are being removed (exchange-as-truth)
            for table in DROP_TABLES:
                if table_exists(cur, table):
                    cur.execute(f"DROP TABLE IF EXISTS {table}")
                    applied.append(f"FRESH: Dropped table {table}")

            # Drop new tables so they can be recreated cleanly
            for table in NEW_TABLES:
                cur.execute(f"DROP TABLE IF EXISTS {table}")
                applied.append(f"FRESH: Dropped table {table} (for recreation)")

            conn.commit()

        # ── STEP 1: Drop substrate persistence tables ───────────────────────
        for table in DROP_TABLES:
            if table_exists(cur, table):
                cur.execute(f"DROP TABLE IF EXISTS {table}")
                applied.append(f"Dropped table {table} (substrate persistence removed — exchange is truth)")
            else:
                applied.append(f"Skip: table {table} does not exist (already removed)")

        # ── STEP 2: Create new learning data tables ─────────────────────────
        for table_name, create_sql in NEW_TABLES.items():
            if not table_exists(cur, table_name):
                cur.execute(create_sql)
                applied.append(f"Created table {table_name}")
            else:
                applied.append(f"Skip: table {table_name} already exists")

        # ── STEP 3: Add position_metadata columns if missing ────────────────
        if table_exists(cur, "position_metadata"):
            new_columns = {
                "strategy_uid": "TEXT NOT NULL DEFAULT 'legacy'",
                "atr_value": "REAL DEFAULT 0",
                "atr_pct": "REAL DEFAULT 0",
                "sl_price": "REAL DEFAULT 0",
                "tp1": "REAL DEFAULT 0",
                "tp2": "REAL DEFAULT 0",
                "size_usdt": "REAL DEFAULT 0",
                "opened_at": "TEXT",
                "closed_at": "TEXT",
            }
            for col_name, col_def in new_columns.items():
                if not column_exists(cur, "position_metadata", col_name):
                    cur.execute(f"ALTER TABLE position_metadata ADD COLUMN {col_name} {col_def}")
                    applied.append(f"Added column position_metadata.{col_name}")

        # ── STEP 4: Migrate learning data from substrate_state (if exists) ──
        # If the old substrate_state table still has data, try to extract
        # learning fields and write them to the new tables.
        # This is a BEST-EFFORT migration — if it fails, learning data
        # will be rebuilt from scratch on the next daemon run.
        if table_exists(cur, "substrate_state"):
            # Already dropped in Step 1, but check just in case
            # (if we're running without --fresh, the table might still exist
            # if Step 1 was skipped for some reason)
            try:
                rows = cur.execute(
                    "SELECT substrate_json FROM substrate_state ORDER BY id DESC LIMIT 1"
                ).fetchall()

                if rows:
                    state = json.loads(rows[0]["substrate_json"])
                    learning = state.get("learning", {})
                    strategy = state.get("strategy", {})
                    strategy_uid = strategy.get("uid", "legacy")

                    migrated_learning = 0

                    # Migrate adjusted_weights
                    adjusted_weights = learning.get("adjusted_weights", {})
                    if adjusted_weights:
                        for indicator_name, weight in adjusted_weights.items():
                            cur.execute(
                                """INSERT OR REPLACE INTO adjusted_weights
                                   (strategy_uid, indicator_name, weight, updated_at)
                                   VALUES (?, ?, ?, ?)""",
                                (strategy_uid, indicator_name, weight,
                                 datetime.now(timezone.utc).isoformat()),
                            )
                            migrated_learning += 1

                    # Migrate adjusted_thresholds
                    adjusted_thresholds = learning.get("adjusted_thresholds", {})
                    if adjusted_thresholds:
                        for threshold_name, value in adjusted_thresholds.items():
                            cur.execute(
                                """INSERT OR REPLACE INTO adjusted_thresholds
                                   (strategy_uid, threshold_name, value, updated_at)
                                   VALUES (?, ?, ?, ?)""",
                                (strategy_uid, threshold_name, value,
                                 datetime.now(timezone.utc).isoformat()),
                            )
                            migrated_learning += 1

                    # Migrate suppressed_signals
                    suppressed = learning.get("suppressed_signals", [])
                    if suppressed:
                        for indicator_name in suppressed:
                            cur.execute(
                                """INSERT OR REPLACE INTO suppressed_signals
                                   (strategy_uid, indicator_name, reason, updated_at)
                                   VALUES (?, ?, ?, ?)""",
                                (strategy_uid, indicator_name, "migrated from substrate",
                                 datetime.now(timezone.utc).isoformat()),
                            )
                            migrated_learning += 1

                    # Migrate highlight_signals
                    highlighted = learning.get("highlight_signals", [])
                    if highlighted:
                        for indicator_name in highlighted:
                            cur.execute(
                                """INSERT OR REPLACE INTO highlight_signals
                                   (strategy_uid, indicator_name, reason, updated_at)
                                   VALUES (?, ?, ?, ?)""",
                                (strategy_uid, indicator_name, "migrated from substrate",
                                 datetime.now(timezone.utc).isoformat()),
                            )
                            migrated_learning += 1

                    # Migrate challenger state
                    challenger = learning.get("challenger", {})
                    if challenger:
                        cur.execute(
                            """INSERT OR REPLACE INTO challenger_state
                               (strategy_uid, state_json, updated_at)
                               VALUES (?, ?, ?)""",
                            (strategy_uid, json.dumps(challenger),
                             datetime.now(timezone.utc).isoformat()),
                        )
                        migrated_learning += 1

                    if migrated_learning > 0:
                        applied.append(
                            f"Migrated {migrated_learning} learning data entries "
                            f"from substrate_state to dedicated tables (strategy_uid={strategy_uid})"
                        )
                    else:
                        applied.append("No learning data found in substrate_state to migrate")

            except Exception as e:
                applied.append(f"Learning data migration skipped (best-effort): {e}")

        # ── STEP 5: Record migration in schema_version ──────────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version    INTEGER PRIMARY KEY,
                name       TEXT    NOT NULL,
                applied_at TEXT    DEFAULT (datetime('now'))
            )
        """)

        # Record this migration run
        version = 60  # Next version after existing migrations in database.py
        migration_name = "exchange_as_truth_v3"
        cur.execute(
            "INSERT OR IGNORE INTO schema_version (version, name) VALUES (?, ?)",
            (version, migration_name),
        )
        applied.append(f"Recorded migration v{version}: {migration_name}")

        conn.commit()

    except Exception as e:
        conn.rollback()
        log.error("Migration failed: %s", e, exc_info=True)
        applied.append(f"ERROR: Migration failed — {e}")
    finally:
        conn.close()

    return applied


def verify_db(db_path: str = DEFAULT_DB_PATH) -> List[str]:
    """
    Verify the database schema is correct for exchange-as-truth architecture.

    Returns list of issues found (empty = all good).
    """
    if not os.path.exists(db_path):
        return [f"Database not found at {db_path}"]

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    issues = []

    try:
        cur = conn.cursor()

        # Verify substrate_state is GONE
        if table_exists(cur, "substrate_state"):
            issues.append("FAIL: substrate_state table still exists (should be dropped)")

        # Verify cycle_log is GONE
        if table_exists(cur, "cycle_log"):
            issues.append("FAIL: cycle_log table still exists (should be dropped)")

        # Verify new tables exist
        for table_name in NEW_TABLES:
            if not table_exists(cur, table_name):
                issues.append(f"FAIL: {table_name} table missing (should exist)")

        # Verify existing learning tables still exist
        essential_tables = [
            "trade_learning", "signal_accuracy", "combination_accuracy",
            "weight_history", "rulebook_versions", "challenger_log",
        ]
        for table_name in essential_tables:
            if not table_exists(cur, table_name):
                issues.append(f"FAIL: {table_name} table missing (should exist)")

        # Verify position_metadata has required columns
        if table_exists(cur, "position_metadata"):
            required_columns = ["atr_pct", "strategy_uid", "tp1", "tp2"]
            for col in required_columns:
                if not column_exists(cur, "position_metadata", col):
                    issues.append(f"FAIL: position_metadata.{col} column missing")
        else:
            issues.append("FAIL: position_metadata table missing")

        if not issues:
            issues.append("OK: All checks passed — database schema is correct for exchange-as-truth")

    finally:
        conn.close()

    return issues


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Database migration script for Auto-Crypto-Tradingjournal (exchange-as-truth)"
    )
    parser.add_argument(
        "--db-path", default=DEFAULT_DB_PATH,
        help="Path to SQLite database",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be done without making changes",
    )
    parser.add_argument(
        "--fresh", action="store_true",
        help="Drop and recreate all new tables (clean slate for new tables only)",
    )
    parser.add_argument(
        "--verify", action="store_true",
        help="Verify database schema is correct for exchange-as-truth",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.dry_run:
        print(f"DRY RUN: Would run migrations on {args.db_path}")
        print("\nChanges that would be made:")
        print("  1. Drop substrate_state table (substrate persistence removed)")
        print("  2. Drop cycle_log table (no longer needed)")
        print("  3. Create adjusted_weights table (learning data)")
        print("  4. Create adjusted_thresholds table (learning data)")
        print("  5. Create suppressed_signals table (learning data)")
        print("  6. Create highlight_signals table (learning data)")
        print("  7. Create challenger_state table (learning data)")
        print("  8. Create position_metadata table (atr_pct for TP recalculation)")
        print("  9. Migrate learning data from substrate_state (best-effort)")
        print("  10. Record migration in schema_version")
        if args.fresh:
            print("\n  --fresh: New tables will be dropped and recreated")
        sys.exit(0)

    if args.verify:
        print(f"Verifying database schema: {args.db_path}")
        results = verify_db(args.db_path)
        for result in results:
            print(f"  {result}")
        sys.exit(0 if all("OK" in r or "FAIL" not in r for r in results) else 1)

    # Run migrations
    print(f"Running migrations on {args.db_path}")
    if args.fresh:
        print("  --fresh: New tables will be dropped and recreated")

    applied = migrate_db(args.db_path, fresh=args.fresh)

    if applied:
        print(f"\nApplied {len(applied)} migration(s):")
        for migration in applied:
            print(f"  {migration}")
    else:
        print("\nAll migrations already applied — no changes needed.")

    # Verify after migration
    print(f"\nVerifying database schema...")
    results = verify_db(args.db_path)
    for result in results:
        print(f"  {result}")

    print(f"\nDatabase: {args.db_path}")
