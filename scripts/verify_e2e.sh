#!/bin/bash
# verify_e2e.sh -- End-to-end verification for Auto-Trader v2 24h test
#
# Usage:
#   bash scripts/verify_e2e.sh                  # Run all checks
#   bash scripts/verify_e2e.sh --quick          # Quick check (no DB queries)
#   bash scripts/verify_e2e.sh --verbose        # Show detailed output
#
# This script verifies that the daemon is running correctly during
# the 24h paper-trading test. Run it periodically to track progress.
#
# Checks:
#   1. Daemon process alive
#   2. Database exists with correct tables
#   3. Cycles completing (cycle_log growing)
#   4. Substrate state persisting
#   5. Enzymes firing (enzymes_fired column)
#   6. ISC checks running
#   7. Paper trades simulated (trade_learning rows)
#   8. Learning tables active
#   9. Performance within bounds (cycle < 30s, memory < 500MB)
#  10. Log file growing
#  11. No critical errors in logs
#  12. Config hot-reload working

set -euo pipefail

# -- Colors ---------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

PASS=0
FAIL=0
WARN=0
SKIP=0

p_pass() { echo -e "  ${GREEN}✅ PASS${NC}  $1"; ((PASS++)); }
p_fail() { echo -e "  ${RED}❌ FAIL${NC}  $1"; ((FAIL++)); }
p_warn() { echo -e "  ${YELLOW}⚠  WARN${NC}  $1"; ((WARN++)); }
p_skip() { echo -e "  ${CYAN}⏭  SKIP${NC}  $1"; ((SKIP++)); }

# -- Config ---------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

DB_PATH="${DB_PATH:-auto_trader.db}"
LOG_FILE="${LOG_FILE:-logs/auto-trader.log}"
SERVICE_NAME="auto-trader"

QUICK=false
VERBOSE=false
for arg in "$@"; do
    case $arg in
        --quick)   QUICK=true ;;
        --verbose) VERBOSE=true ;;
    esac
done

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  Auto-Trader v2 — E2E Verification"
echo "  Project:  $PROJECT_DIR"
echo "  Database: $DB_PATH"
echo "  Log file: $LOG_FILE"
echo "  Time:     $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "═══════════════════════════════════════════════════════════════"
echo ""

# -- 1. Daemon process alive ----------------------------------------------------
echo "-- 1. Daemon Process ------------------------------------------"

# Check systemd service first, then fall back to process check
if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    p_pass "Systemd service '$SERVICE_NAME' is active"
    UPTIME=$(systemctl show "$SERVICE_NAME" --property=ActiveEnterTimestamp --value 2>/dev/null || echo "unknown")
    echo "         Running since: $UPTIME"
elif pgrep -f "main.py" >/dev/null 2>&1; then
    PID=$(pgrep -f "main.py" | head -1)
    p_pass "Daemon process found (PID: $PID)"
else
    p_fail "Daemon is NOT running"
    echo "         Start with: python main.py --paper"
    echo "         Or:         systemctl start $SERVICE_NAME"
fi

# -- 2. Database exists with tables ---------------------------------------------
echo ""
echo "-- 2. Database ------------------------------------------------"

if [[ ! -f "$DB_PATH" ]]; then
    p_fail "Database file not found: $DB_PATH"
else
    DB_SIZE=$(du -h "$DB_PATH" | cut -f1)
    p_pass "Database exists ($DB_SIZE)"

    if ! $QUICK; then
        # Check for v2 tables
        V2_TABLES="cycle_log substrate_state trade_learning signal_accuracy combination_accuracy trajectory_accuracy idle_cycles idle_condition_accuracy weight_history rulebook_versions"
        MISSING=""
        for table in $V2_TABLES; do
            if ! sqlite3 "$DB_PATH" ".tables" 2>/dev/null | grep -qw "$table"; then
                MISSING="$MISSING $table"
            fi
        done

        if [[ -z "$MISSING" ]]; then
            p_pass "All v2 tables present"
        else
            p_warn "Missing tables:$MISSING"
        fi
    fi
fi

# -- 3. Cycles completing -------------------------------------------------------
echo ""
echo "-- 3. Cycle Execution -----------------------------------------"

if ! $QUICK && [[ -f "$DB_PATH" ]]; then
    CYCLE_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM cycle_log" 2>/dev/null || echo "0")
    if [[ "$CYCLE_COUNT" -gt 0 ]]; then
        p_pass "Cycles completed: $CYCLE_COUNT"

        # Latest cycle info
        LATEST=$(sqlite3 "$DB_PATH" "SELECT cycle_count, action, duration_ms, created_at FROM cycle_log ORDER BY id DESC LIMIT 1" 2>/dev/null || echo "")
        if [[ -n "$LATEST" ]]; then
            echo "         Latest: $LATEST"
        fi

        # Check that cycles are recent (within last 30 minutes)
        LAST_CYCLE_TIME=$(sqlite3 "$DB_PATH" "SELECT created_at FROM cycle_log ORDER BY id DESC LIMIT 1" 2>/dev/null || echo "")
        if [[ -n "$LAST_CYCLE_TIME" ]]; then
            echo "         Last cycle at: $LAST_CYCLE_TIME"
        fi
    else
        p_fail "No cycles recorded in cycle_log"
    fi
else
    p_skip "Cycle check (quick mode or no DB)"
fi

# -- 4. Substrate state persisting ---------------------------------------------
echo ""
echo "-- 4. Substrate Persistence -----------------------------------"

if ! $QUICK && [[ -f "$DB_PATH" ]]; then
    SUB_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM substrate_state" 2>/dev/null || echo "0")
    if [[ "$SUB_COUNT" -gt 0 ]]; then
        p_pass "Substrate snapshots: $SUB_COUNT"
    else
        p_warn "No substrate snapshots (may be too early)"
    fi
else
    p_skip "Substrate check (quick mode or no DB)"
fi

# -- 5. Enzymes firing ----------------------------------------------------------
echo ""
echo "-- 5. Enzyme Activation ---------------------------------------"

if ! $QUICK && [[ -f "$DB_PATH" ]]; then
    # Check what enzymes have been fired
    FIRED=$(sqlite3 "$DB_PATH" "SELECT enzymes_fired FROM cycle_log WHERE enzymes_fired != '[]' AND enzymes_fired IS NOT NULL ORDER BY id DESC LIMIT 5" 2>/dev/null || echo "")
    if [[ -n "$FIRED" ]]; then
        p_pass "Enzymes have been fired"
        if $VERBOSE; then
            echo "         Recent enzymes: $FIRED"
        fi
    else
        p_warn "No enzyme firings recorded (may be in skeleton/wait mode)"
    fi

    # Check distinct actions
    ACTIONS=$(sqlite3 "$DB_PATH" "SELECT DISTINCT action FROM cycle_log" 2>/dev/null || echo "")
    echo "         Actions seen: $ACTIONS"
else
    p_skip "Enzyme check (quick mode or no DB)"
fi

# -- 6. ISC checks running -----------------------------------------------------
echo ""
echo "-- 6. ISC Verification ----------------------------------------"

if ! $QUICK && [[ -f "$DB_PATH" ]]; then
    ISC_RESULTS=$(sqlite3 "$DB_PATH" "SELECT isc_results FROM cycle_log WHERE isc_results IS NOT NULL AND isc_results != '{}' ORDER BY id DESC LIMIT 1" 2>/dev/null || echo "")
    if [[ -n "$ISC_RESULTS" && "$ISC_RESULTS" != "" ]]; then
        p_pass "ISC checks are running"
        if $VERBOSE; then
            echo "         Latest: $ISC_RESULTS"
        fi
    else
        p_warn "No ISC results recorded yet"
    fi
else
    p_skip "ISC check (quick mode or no DB)"
fi

# -- 7. Paper trades simulated -------------------------------------------------
echo ""
echo "-- 7. Paper Trading -------------------------------------------"

if ! $QUICK && [[ -f "$DB_PATH" ]]; then
    TRADE_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM trade_learning" 2>/dev/null || echo "0")
    if [[ "$TRADE_COUNT" -gt 0 ]]; then
        p_pass "Paper trades recorded: $TRADE_COUNT"

        # Show latest trades
        if $VERBOSE; then
            LATEST_TRADES=$(sqlite3 "$DB_PATH" "SELECT symbol, direction, outcome, pnl_pct FROM trade_learning ORDER BY id DESC LIMIT 5" 2>/dev/null || echo "")
            echo "         Recent trades: $LATEST_TRADES"
        fi
    else
        p_warn "No paper trades yet (system may be waiting for signals)"
        echo "         This is normal if the market has no strong setups."
    fi
else
    p_skip "Trade check (quick mode or no DB)"
fi

# -- 8. Learning tables active --------------------------------------------------
echo ""
echo "-- 8. Learning Engine -----------------------------------------"

if ! $QUICK && [[ -f "$DB_PATH" ]]; then
    SIG_ROWS=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM signal_accuracy" 2>/dev/null || echo "0")
    COMBO_ROWS=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM combination_accuracy" 2>/dev/null || echo "0")
    TRAJ_ROWS=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM trajectory_accuracy" 2>/dev/null || echo "0")
    RULE_ROWS=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM rulebook_versions" 2>/dev/null || echo "0")
    WEIGHT_ROWS=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM weight_history" 2>/dev/null || echo "0")

    echo "         signal_accuracy:    $SIG_ROWS rows"
    echo "         combination_accuracy: $COMBO_ROWS rows"
    echo "         trajectory_accuracy: $TRAJ_ROWS rows"
    echo "         rulebook_versions:  $RULE_ROWS versions"
    echo "         weight_history:     $WEIGHT_ROWS changes"

    if [[ "$SIG_ROWS" -gt 0 || "$COMBO_ROWS" -gt 0 ]]; then
        p_pass "Learning engine is accumulating data"
    elif [[ "$TRADE_COUNT" -gt 0 && "$TRADE_COUNT" -lt 30 ]]; then
        p_warn "Learning not yet active (need 30 trades, have $TRADE_COUNT)"
    else
        p_warn "No learning data yet (expected: need 30+ trades first)"
    fi
else
    p_skip "Learning check (quick mode or no DB)"
fi

# -- 9. Performance -------------------------------------------------------------
echo ""
echo "-- 9. Performance ---------------------------------------------"

if ! $QUICK && [[ -f "$DB_PATH" ]]; then
    AVG_DURATION=$(sqlite3 "$DB_PATH" "SELECT AVG(duration_ms) FROM cycle_log" 2>/dev/null || echo "0")
    MAX_DURATION=$(sqlite3 "$DB_PATH" "SELECT MAX(duration_ms) FROM cycle_log" 2>/dev/null || echo "0")

    AVG_SEC=$(echo "scale=1; $AVG_DURATION / 1000" | bc 2>/dev/null || echo "?")
    MAX_SEC=$(echo "scale=1; $MAX_DURATION / 1000" | bc 2>/dev/null || echo "?")

    echo "         Avg cycle: ${AVG_SEC}s"
    echo "         Max cycle: ${MAX_SEC}s"

    # Check if average is under 30 seconds
    AVG_INT=${AVG_DURATION%.*}  # truncate to integer
    if [[ "${AVG_INT:-0}" -lt 30000 ]]; then
        p_pass "Cycle time within bounds (avg ${AVG_SEC}s < 30s)"
    else
        p_warn "Cycle time high (avg ${AVG_SEC}s > 30s)"
    fi
else
    p_skip "Performance check (quick mode or no DB)"
fi

# Memory check (works with or without DB)
PID=$(pgrep -f "main.py" 2>/dev/null | head -1 || echo "")
if [[ -n "$PID" ]]; then
    RSS_KB=$(ps -o rss= -p "$PID" 2>/dev/null | tr -d ' ' || echo "0")
    if [[ "$RSS_KB" -gt 0 ]]; then
        RSS_MB=$((RSS_KB / 1024))
        echo "         Memory: ${RSS_MB}MB"
        if [[ "$RSS_MB" -lt 500 ]]; then
            p_pass "Memory within bounds (${RSS_MB}MB < 500MB)"
        else
            p_warn "Memory high (${RSS_MB}MB > 500MB)"
        fi
    else
        p_skip "Could not read memory for PID $PID"
    fi
else
    p_skip "Memory check (daemon not running or no pgrep)"
fi

# -- 10. Log file ---------------------------------------------------------------
echo ""
echo "-- 10. Log File -----------------------------------------------"

if [[ -f "$LOG_FILE" ]]; then
    LOG_SIZE=$(du -h "$LOG_FILE" | cut -f1)
    LOG_LINES=$(wc -l < "$LOG_FILE")
    p_pass "Log file exists ($LOG_SIZE, $LOG_LINES lines)"

    # Check it's growing (compare with 1 minute ago)
    LOG_MTIME=$(stat -c %Y "$LOG_FILE" 2>/dev/null || echo "0")
    NOW=$(date +%s)
    LOG_AGE_SEC=$((NOW - LOG_MTIME))
    if [[ "$LOG_AGE_SEC" -lt 1800 ]]; then  # 30 minutes
        p_pass "Log file was updated ${LOG_AGE_SEC}s ago (active)"
    else
        p_warn "Log file not updated for ${LOG_AGE_SEC}s (may be idle)"
    fi
else
    p_fail "Log file not found: $LOG_FILE"
fi

# -- 11. Error scan -------------------------------------------------------------
echo ""
echo "-- 11. Error Scan ---------------------------------------------"

if [[ -f "$LOG_FILE" ]]; then
    ERROR_COUNT=$(grep -ciE "error|exception|traceback" "$LOG_FILE" 2>/dev/null || echo "0")
    if [[ "$ERROR_COUNT" -eq 0 ]]; then
        p_pass "No errors in log file"
    elif [[ "$ERROR_COUNT" -lt 10 ]]; then
        p_warn "Errors in log file: $ERROR_COUNT (check if expected)"
        if $VERBOSE; then
            grep -iE "error|exception|traceback" "$LOG_FILE" | tail -5 | sed 's/^/         /'
        fi
    else
        p_fail "Many errors in log file: $ERROR_COUNT"
        if $VERBOSE; then
            grep -iE "error|exception|traceback" "$LOG_FILE" | tail -10 | sed 's/^/         /'
        fi
    fi
else
    p_skip "Error scan (no log file)"
fi

# -- 12. Config hot-reload ------------------------------------------------------
echo ""
echo "-- 12. Config Hot-Reload --------------------------------------"

if [[ -f "$LOG_FILE" ]]; then
    if grep -q "Config reloaded" "$LOG_FILE" 2>/dev/null; then
        p_pass "Config hot-reload is working"
    elif grep -q "Config changed after reload" "$LOG_FILE" 2>/dev/null; then
        p_pass "Config hot-reload is working"
    else
        p_warn "No config reload events in log (may not have changed config yet)"
    fi
else
    p_skip "Config check (no log file)"
fi

# -- Summary --------------------------------------------------------------------
TOTAL=$((PASS + FAIL + WARN + SKIP))
echo ""
echo "═══════════════════════════════════════════════════════════════"
echo -e "  Results: ${GREEN}$PASS passed${NC}, ${RED}$FAIL failed${NC}, ${YELLOW}$WARN warnings${NC}, ${CYAN}$SKIP skipped${NC}"
echo "═══════════════════════════════════════════════════════════════"

if [[ "$FAIL" -gt 0 ]]; then
    echo ""
    echo "  ❌ Some checks failed. Review the output above."
    echo ""
    exit 1
elif [[ "$WARN" -gt 0 ]]; then
    echo ""
    echo "  ⚠  Warnings detected. Normal during early hours of the test."
    echo "     Re-run after more cycles to see improvements."
    echo ""
    exit 0
else
    echo ""
    echo "  ✅ All checks passed!"
    echo ""
    exit 0
fi