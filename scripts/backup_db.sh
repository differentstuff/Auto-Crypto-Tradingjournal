#!/bin/bash
# backup_db.sh — Safe online SQLite backup with 7-day rolling window.
# Called by systemd ExecStopPost and daily cron.
DB="/home/fbauer/trading-journal/trading_journal.db"
BACKUP_DIR="/home/fbauer/trading-journal/backups"
mkdir -p "$BACKUP_DIR"
TS=$(date +%Y%m%d_%H%M%S)
DEST="$BACKUP_DIR/trading_journal_$TS.db"
# .backup is safe during live reads (uses SQLite online backup API)
sqlite3 "$DB" ".backup '$DEST'" && echo "DB backup: $DEST" || echo "DB backup FAILED"
# Keep only the 7 most recent backups
ls -t "$BACKUP_DIR"/trading_journal_*.db 2>/dev/null | tail -n +8 | xargs rm -f
