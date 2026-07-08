#!/bin/bash
# backup_db.sh — Safe online SQLite backup with 7-day rolling window.
# Called by systemd ExecStopPost and daily cron.
#
# Usage:
#   bash scripts/backup_db.sh
#
# Works from any directory — auto-detects project root.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

DB="${DB_PATH:-auto_trader.db}"
BACKUP_DIR="${BACKUP_DIR:-backups}"
KEEP="${KEEP_BACKUPS:-7}"

mkdir -p "$BACKUP_DIR"

if [[ ! -f "$DB" ]]; then
    echo "ERROR: Database not found: $DB"
    exit 1
fi

TS=$(date +%Y%m%d_%H%M%S)
DEST="$BACKUP_DIR/auto_trader_$TS.db"

# .backup is safe during live reads (uses SQLite online backup API)
if sqlite3 "$DB" ".backup '$DEST'" 2>/dev/null; then
    SIZE=$(du -h "$DEST" | cut -f1)
    echo "OK  DB backup: $DEST ($SIZE)"
else
    echo "ERROR  DB backup FAILED"
    exit 1
fi

# Keep only the N most recent backups
ls -t "$BACKUP_DIR"/auto_trader_*.db 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f

REMAINING=$(ls -1 "$BACKUP_DIR"/auto_trader_*.db 2>/dev/null | wc -l)
echo "OK  Backups kept: $REMAINING (max $KEEP)"