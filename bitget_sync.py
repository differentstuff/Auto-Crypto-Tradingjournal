"""
bitget_sync.py — Live sync from Bitget API into the local SQLite database.

Entry point: run_sync(conn)
  Fetches new positions, orders, and bills since the last successful sync.
  Uses positionId / orderId / billId as idempotency keys to prevent duplicates.
  Stores last-sync timestamp in the 'settings' table so each run only fetches new data.

Auto-sync is started by app.py via start_background_sync().
Manual sync is triggered by POST /api/sync.
"""

import re
import threading
import time
from datetime import datetime, timezone

import bitget_client as bc
from database import get_conn

SYNC_INTERVAL_SECONDS  = 5 * 60    # auto-sync every 5 minutes
STARTUP_LOOKBACK_DAYS  = 2         # orders/bills catch-up window on first sync after (re)start
RULEBOOK_INTERVAL_DAYS = 7         # regenerate trader rulebook weekly


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _ensure_settings_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    # Migrations — add columns that may be missing from older DBs
    cols = [r[1] for r in conn.execute("PRAGMA table_info(positions)").fetchall()]
    if "external_id" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN external_id TEXT")
    if "analyst" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN analyst TEXT DEFAULT ''")
    conn.commit()


def _get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def _set_setting(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, str(value)))
    conn.commit()


# ── Field mapping helpers ──────────────────────────────────────────────────────

def _ms_to_dt(ms_str) -> str:
    """Convert epoch-milliseconds string to 'YYYY-MM-DD HH:MM:SS'."""
    if not ms_str:
        return ""
    try:
        ts = int(ms_str) / 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def _f(val, default=None):
    """Parse float, return default if blank/None."""
    if val is None or str(val).strip() == "":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _duration_minutes(open_str: str, close_str: str):
    try:
        fmt = "%Y-%m-%d %H:%M:%S"
        return int((datetime.strptime(close_str, fmt) -
                    datetime.strptime(open_str,  fmt)).total_seconds() / 60)
    except Exception:
        return None


# ── Position sync ──────────────────────────────────────────────────────────────

def _sync_positions(conn) -> int:
    """
    Fetch new closed positions using cursor-only pagination (no time filter).

    Bitget's position history startTime/endTime filter by OPEN time, not close
    time. Any position held longer than the sync window would be silently
    missed. Instead we fetch the newest 300 positions (3 pages x 100), check
    each one against the DB, and insert any that are not already stored.
    All checks are by positionId (unique external_id index), so this is fast.

    Returns number of new rows inserted.
    """
    rows = bc.get_recent_positions(max_pages=3)
    if not rows:
        return 0

    cur      = conn.cursor()
    inserted = 0

    for r in rows:
        ext_id = r.get("positionId", "")
        if not ext_id:
            continue

        exists = cur.execute(
            "SELECT id FROM positions WHERE external_id=?", (ext_id,)
        ).fetchone()
        if exists:
            continue  # already stored — keep checking, don't break

        symbol     = r.get("symbol", "")
        base_asset = re.sub(r"USDT$", "", symbol)
        direction  = "Long" if r.get("holdSide", "").lower() == "long" else "Short"
        margin     = "Cross" if "cross" in r.get("marginMode", "").lower() else "Isolated"
        open_time  = _ms_to_dt(r.get("ctime"))
        close_time = _ms_to_dt(r.get("utime"))
        duration   = _duration_minutes(open_time, close_time)

        entry_price  = _f(r.get("openAvgPrice"))
        close_price  = _f(r.get("closeAvgPrice"))
        size_raw     = str(r.get("openTotalPos", ""))
        size_usdt    = _f(r.get("closeTotalPos"))
        realized_pnl = _f(r.get("pnl"))
        position_pnl = _f(r.get("netProfit"))
        opening_fee  = _f(r.get("openFee"))
        closing_fee  = _f(r.get("closeFee"))
        funding      = _f(r.get("totalFunding"), 0)
        total_fees   = (opening_fee or 0) + (closing_fee or 0) + funding

        if size_usdt is None and close_price and size_raw:
            try:
                size_usdt = float(size_raw) * close_price
            except Exception:
                pass

        cur.execute("""
            INSERT INTO positions
              (symbol, base_asset, direction, margin_mode,
               open_time, close_time, duration_minutes,
               entry_price, close_price,
               size_contracts, size_usdt,
               position_pnl, realized_pnl,
               opening_fee, closing_fee, total_fees,
               external_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            symbol, base_asset, direction, margin,
            open_time, close_time, duration,
            entry_price, close_price,
            size_raw + base_asset, size_usdt,
            position_pnl, realized_pnl,
            opening_fee, closing_fee, total_fees,
            ext_id,
        ))
        inserted += 1

    conn.commit()
    return inserted


# ── Auto-close matched calls ───────────────────────────────────────────────────

def _auto_close_calls(conn, exchange: str = "bitget") -> int:
    """
    For every 'matched' call on `exchange`, find the most recent closed position
    on the same exchange with the same symbol/direction that closed after the call
    was created. Determines outcome from close_price vs SL/TP levels.

    exchange: 'bitget' | 'blofin' — only closes calls matched to this exchange.
    Calls with no exchange set (NULL) default to 'bitget' for backward compat.
    Run after each sync — safe to call repeatedly (only touches 'matched' calls).
    """
    cur   = conn.cursor()
    calls = cur.execute("""
        SELECT id, symbol, direction, sl_price, tp1_price, tp2_price, created_at
        FROM analyzed_calls
        WHERE status = 'matched'
          AND COALESCE(exchange, 'bitget') = ?
    """, (exchange,)).fetchall()

    closed = 0
    for call in calls:
        call_id, symbol, direction, sl_price, tp1_price, tp2_price, created_at = call

        is_long = "long" in (direction or "").lower()
        pos_dir = "Long" if is_long else "Short"

        pos = cur.execute("""
            SELECT close_price, realized_pnl
            FROM positions
            WHERE symbol    = ?
              AND direction = ?
              AND close_time > ?
              AND COALESCE(exchange, 'bitget') = ?
            ORDER BY close_time DESC
            LIMIT 1
        """, (symbol, pos_dir, created_at or "", exchange)).fetchone()

        if not pos:
            continue

        close_price, realized_pnl = pos
        if close_price is None:
            continue

        hit_sl = hit_tp1 = hit_tp2 = 0
        outcome = "manual"

        if is_long:
            if sl_price and close_price <= sl_price:
                hit_sl, outcome = 1, "lost"
            elif tp2_price and close_price >= tp2_price:
                hit_tp1, hit_tp2, outcome = 1, 1, "won"
            elif tp1_price and close_price >= tp1_price:
                hit_tp1, outcome = 1, "won"
        else:
            if sl_price and close_price >= sl_price:
                hit_sl, outcome = 1, "lost"
            elif tp2_price and close_price <= tp2_price:
                hit_tp1, hit_tp2, outcome = 1, 1, "won"
            elif tp1_price and close_price <= tp1_price:
                hit_tp1, outcome = 1, "won"

        cur.execute("""
            UPDATE analyzed_calls
            SET status      = 'closed',
                outcome     = ?,
                outcome_pnl = ?,
                hit_tp1     = ?,
                hit_tp2     = ?,
                hit_sl      = ?,
                outcome_at  = datetime('now')
            WHERE id = ?
        """, (outcome, realized_pnl, hit_tp1, hit_tp2, hit_sl, call_id))
        closed += 1
        print(f"[Sync] Auto-closed call #{call_id} {symbol} {pos_dir} → {outcome} (PnL: {realized_pnl})", flush=True)

    conn.commit()
    return closed


# ── Order sync ─────────────────────────────────────────────────────────────────

def _sync_orders(conn, start_ms: int, end_ms: int = None) -> int:
    """Fetch new orders from Bitget since start_ms and insert into DB."""
    rows = bc.get_order_history(start_ms=start_ms, end_ms=end_ms)
    if not rows:
        return 0

    cur      = conn.cursor()
    inserted = 0

    for r in rows:
        order_id = str(r.get("orderId", "")).strip()
        if not order_id:
            continue

        trade_side = r.get("tradeSide", "")
        pos_side   = r.get("posSide", "")
        if trade_side == "open":
            direction = f"Open {pos_side.capitalize()}"
        else:
            direction = f"Close {pos_side.capitalize()}"

        try:
            cur.execute("""
                INSERT OR IGNORE INTO orders
                  (order_id, date, direction, symbol, order_source,
                   transaction_type, price, avg_price,
                   order_amount, executed, trading_volume,
                   realized_pnl, net_profits, status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                order_id,
                _ms_to_dt(r.get("cTime")),
                direction,
                r.get("symbol", ""),
                r.get("enterPointSource", ""),
                r.get("orderSource", ""),
                _f(r.get("price")),
                _f(r.get("priceAvg")),
                _f(r.get("size")),
                _f(r.get("baseVolume")),
                _f(r.get("quoteVolume")),
                _f(r.get("totalProfits")),
                _f(r.get("totalProfits")),
                r.get("status", ""),
            ))
            inserted += 1
        except Exception:
            pass

    conn.commit()
    return inserted


# ── Bills sync ─────────────────────────────────────────────────────────────────

def _sync_bills(conn, start_ms: int, end_ms: int = None) -> int:
    """Fetch new account bills since start_ms and insert wallet snapshots."""
    rows = bc.get_account_bills(start_ms=start_ms, end_ms=end_ms)
    if not rows:
        return 0

    cur      = conn.cursor()
    inserted = 0

    cols = [c[1] for c in conn.execute("PRAGMA table_info(wallet_snapshots)").fetchall()]
    if "bill_id" not in cols:
        conn.execute("ALTER TABLE wallet_snapshots ADD COLUMN bill_id TEXT")
        conn.commit()

    for r in rows:
        bill_id = str(r.get("billId", "")).strip()
        if bill_id:
            exists = cur.execute(
                "SELECT id FROM wallet_snapshots WHERE bill_id=?", (bill_id,)
            ).fetchone()
            if exists:
                continue

        cur.execute("""
            INSERT INTO wallet_snapshots
              (order_ref, date, symbol, futures, margin_mode,
               type, amount, fee, wallet_balance, bill_id)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            bill_id,
            _ms_to_dt(r.get("cTime")),
            r.get("coin", "USDT"),
            r.get("symbol", ""),
            "Single-asset",
            r.get("businessType", ""),
            _f(r.get("amount")),
            _f(r.get("fee")),
            _f(r.get("balance")),
            bill_id,
        ))
        inserted += 1

    conn.commit()
    return inserted


# ── Main sync function ─────────────────────────────────────────────────────────

_sync_lock    = threading.Lock()
_startup_done = False
_sync_status  = {
    "running":      False,
    "last_run":     None,
    "last_result":  None,
    "last_error":   None,
    "next_run":     None,
}


MAX_WINDOW_MS = 89 * 24 * 60 * 60 * 1000   # Bitget max: 90 days per request


def _chunked_sync(sync_fn, conn, start_ms: int, end_ms: int) -> int:
    """Call sync_fn in ≤90-day chunks. Returns total rows inserted."""
    total = 0
    cursor = start_ms
    while cursor < end_ms:
        chunk_end = min(cursor + MAX_WINDOW_MS, end_ms)
        total += sync_fn(conn, start_ms=cursor, end_ms=chunk_end)
        cursor = chunk_end + 1
    return total


def run_sync(conn=None) -> dict:
    """
    Run a full incremental sync.
    - Positions: cursor-based (no time filter) — catches trades regardless of hold duration.
    - Orders + bills: time-filtered, split into 90-day chunks.
    - Auto-closes any 'matched' analyst calls whose position has now closed.
    Thread-safe via _sync_lock.
    Returns: {"positions": N, "orders": N, "bills": N, "calls_closed": N, "equity": {...}}
    """
    global _startup_done

    if not _sync_lock.acquire(blocking=False):
        return {"error": "Sync already running"}

    _sync_status["running"] = True
    _sync_status["last_error"] = None

    own_conn = conn is None
    try:
        if own_conn:
            conn = get_conn()

        _ensure_settings_table(conn)

        startup_lookback_ms = int((time.time() - STARTUP_LOOKBACK_DAYS * 86400) * 1000)
        latest_in_db = conn.execute(
            "SELECT MAX(strftime('%s', close_time)) FROM positions"
        ).fetchone()[0]
        if latest_in_db:
            default_start = int(latest_in_db) * 1000 - 60_000
        else:
            default_start = startup_lookback_ms

        last_ms = int(_get_setting(conn, "last_sync_ms", default_start))
        now_ms  = int(time.time() * 1000)

        if not _startup_done:
            last_ms = min(last_ms, startup_lookback_ms)
            print(f"[Sync] Startup catch-up: extending window to {STARTUP_LOOKBACK_DAYS} days back", flush=True)

        print(f"[Sync] Fetching data since {_ms_to_dt(str(last_ms))} ...", flush=True)

        # Positions: cursor-based — sees all recently closed trades regardless of open time
        n_pos    = _sync_positions(conn)
        # Auto-close any matched calls whose position has now synced
        n_closed = _auto_close_calls(conn)
        # Orders + bills: time-filtered
        n_orders = _chunked_sync(_sync_orders, conn, last_ms, now_ms)
        n_bills  = _chunked_sync(_sync_bills,  conn, last_ms, now_ms)
        equity   = bc.get_account_equity()

        _set_setting(conn, "last_sync_ms", now_ms)
        _set_setting(conn, "account_equity", equity.get("accountEquity", ""))
        _set_setting(conn, "available_balance", equity.get("available", ""))

        result = {
            "positions":    n_pos,
            "orders":       n_orders,
            "bills":        n_bills,
            "calls_closed": n_closed,
            "equity":       equity,
            "synced_at":    datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        }

        _startup_done = True
        _sync_status["last_run"]    = result["synced_at"]
        _sync_status["last_result"] = result
        print(f"[Sync] Done — {n_pos} positions, {n_orders} orders, {n_bills} bills, {n_closed} calls auto-closed", flush=True)
        return result

    except Exception as e:
        import traceback
        traceback.print_exc()
        _sync_status["last_error"] = "Sync failed — see server logs"
        return {"error": "Sync failed — see server logs"}
    finally:
        _sync_status["running"] = False
        _sync_lock.release()
        if own_conn and conn:
            conn.close()


# ── Background auto-sync thread ────────────────────────────────────────────────

_bg_thread = None


def start_background_sync():
    """
    Start a daemon thread that syncs every SYNC_INTERVAL_SECONDS.
    Safe to call multiple times — only one thread runs at a time.
    """
    global _bg_thread
    if _bg_thread and _bg_thread.is_alive():
        return

    def _maybe_update_rulebook():
        """Regenerate the trader rulebook if it's been more than RULEBOOK_INTERVAL_DAYS."""
        try:
            import ai_rulebook
            conn = get_conn()
            row  = conn.execute(
                "SELECT value FROM settings WHERE key='rulebook_updated_at'"
            ).fetchone()
            conn.close()
            if row:
                from datetime import timezone as tz
                last = datetime.strptime(row[0], "%Y-%m-%d %H:%M UTC").replace(tzinfo=tz.utc)
                age_days = (datetime.now(tz.utc) - last).days
                if age_days < RULEBOOK_INTERVAL_DAYS:
                    return
            print("[Sync] Updating trader rulebook...", flush=True)
            result = ai_rulebook.update_rulebook()
            if "error" not in result:
                print(f"[Sync] Rulebook updated — {result.get('count', len(result.get('rules', [])))} rules", flush=True)
        except Exception as e:
            print(f"[Sync] Rulebook update skipped: {e}", flush=True)

    def loop():
        time.sleep(10)
        while True:
            next_time = time.time() + SYNC_INTERVAL_SECONDS
            _sync_status["next_run"] = datetime.fromtimestamp(next_time).strftime("%Y-%m-%d %H:%M:%S")
            try:
                run_sync()
                _maybe_update_rulebook()
            except Exception as e:
                print(f"[Sync] Background error: {e}", flush=True)
            wait = max(0, next_time - time.time())
            time.sleep(wait)

    _bg_thread = threading.Thread(target=loop, daemon=True, name="bitget-sync")
    _bg_thread.start()
    print(f"[Sync] Background auto-sync started (every {SYNC_INTERVAL_SECONDS//60}m)", flush=True)


def get_status() -> dict:
    return dict(_sync_status)
