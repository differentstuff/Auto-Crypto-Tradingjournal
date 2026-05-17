#!/usr/bin/env bash
# scripts/backup_pi_to_mac.sh
# Rsync trading journal DB from Pi to Mac. Scheduled via launchd daily at 03:00.
#
# Required env vars (set via launchd plist EnvironmentVariables or in your shell):
#   TRADING_JOURNAL_PI_USER       — Pi SSH username (e.g. "pi" or your user)
#   TRADING_JOURNAL_PI_HOST       — Pi hostname or IP (e.g. "192.168.1.10")
#   TRADING_JOURNAL_PI_DB_PATH    — Absolute path to DB on Pi (e.g. "/home/USER/trading-journal/trading_journal.db")
#   TRADING_JOURNAL_PI_PASSWORD   — SSH password (or pass as $1)
#
# Optional:
#   TRADING_JOURNAL_BACKUP_DIR    — Local backup directory (default: $HOME/Documents/TradingJournalBackups)
#   TRADING_JOURNAL_BACKUP_KEEP   — Number of backups to retain (default: 30)

set -euo pipefail

PI_USER="${TRADING_JOURNAL_PI_USER:?set TRADING_JOURNAL_PI_USER env var}"
PI_HOST="${TRADING_JOURNAL_PI_HOST:?set TRADING_JOURNAL_PI_HOST env var}"
PI_DB_PATH="${TRADING_JOURNAL_PI_DB_PATH:?set TRADING_JOURNAL_PI_DB_PATH env var}"
LOCAL_DIR="${TRADING_JOURNAL_BACKUP_DIR:-$HOME/Documents/TradingJournalBackups}"
KEEP="${TRADING_JOURNAL_BACKUP_KEEP:-30}"

mkdir -p "$LOCAL_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
DEST="$LOCAL_DIR/trading_journal_${TIMESTAMP}.db"

PASSWORD="${1:-${TRADING_JOURNAL_PI_PASSWORD:-}}"
if [[ -z "$PASSWORD" ]]; then
    echo "[Backup] ERROR: set TRADING_JOURNAL_PI_PASSWORD env var or pass as \$1" >&2
    exit 1
fi

expect -c "
set timeout 60
spawn rsync -az --progress \
    -e {ssh -o StrictHostKeyChecking=no} \
    ${PI_USER}@${PI_HOST}:${PI_DB_PATH} ${DEST}
expect {
    \"password:\" { send \"${PASSWORD}\r\"; exp_continue }
    eof
}
"

if [[ -f "$DEST" ]]; then
    SIZE=$(du -sh "$DEST" | cut -f1)
    echo "[Backup] OK: $DEST ($SIZE)"
else
    echo "[Backup] FAILED: $DEST not created" >&2
    exit 1
fi

# Rolling delete: keep newest $KEEP
ls -t "$LOCAL_DIR"/trading_journal_*.db 2>/dev/null \
    | tail -n +$((KEEP+1)) \
    | while read -r OLD; do rm -f "$OLD"; echo "[Backup] Removed old: $OLD"; done
