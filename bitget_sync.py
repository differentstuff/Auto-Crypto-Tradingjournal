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

SYNC_INTERVAL_SECONDS = 15 * 60   # auto-sync every 15 minutes


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _ensure_settings_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    # Add external_id column to positions if not present (migration)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(positions)").fetchall()]
    if "external_id" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN external_id TEXT")
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

def _sync_positions(conn, start_ms: int, end_ms: int = None) -> int:
    """
    Fetch new closed positions from Bitget since start_ms and upsert into DB.
    Returns number of new rows inserted.
    """
    rows = bc.get_position_history(start_ms=start_ms, end_ms=end_ms)
    if not rows:
        return 0

    cur      = conn.cursor()
    inserted = 0

    for r in rows:
        ext_id = r.get("positionId", "")
        if not ext_id:
            continue

        # Skip if already in DB by external_id
        exists = cur.execute(
            "SELECT id FROM positions WHERE external_id=?", (ext_id,)
        ).fetchone()
        if exists:
            continue

        symbol    = r.get("symbol", "")
        base_asset= re.sub(r"USDT$", "", symbol)
        direction = "Long" if r.get("holdSide", "").lower() == "long" else "Short"
        margin    = "Cross" if "cross" in r.get("marginMode", "").lower() else "Isolated"
        open_time = _ms_to_dt(r.get("ctime"))    # lowercase ctime for positions
        close_time= _ms_to_dt(r.get("utime"))    # lowercase utime for positions
        duration  = _duration_minutes(open_time, close_time)

        entry_price  = _f(r.get("openAvgPrice"))
        close_price  = _f(r.get("closeAvgPrice"))
        size_raw     = str(r.get("openTotalPos", ""))
        size_usdt    = _f(r.get("closeTotalPos"))  # in USDT for perpetuals
        realized_pnl = _f(r.get("pnl"))           # net after fees
        position_pnl = _f(r.get("netProfit"))      # gross
        opening_fee  = _f(r.get("openFee"))
        closing_fee  = _f(r.get("closeFee"))
        funding      = _f(r.get("totalFunding"), 0)
        total_fees   = (opening_fee or 0) + (closing_fee or 0) + funding

        # quoteVolume (USDT value) is more reliable than closeTotalPos for USDT-M
        # Fall back to contracts * close_price if not available
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

        # tradeSide: 'open' or 'close'; side: 'buy' or 'sell'; posSide: 'long'/'short'
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
                _ms_to_dt(r.get("cTime")),   # uppercase cTime for orders
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

    # Use billId as idempotency key — add column if missing
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

_sync_lock  = threading.Lock()
_sync_status = {
    "running":      False,
    "last_run":     None,   # ISO string
    "last_result":  None,   # dict with counts
    "last_error":   None,
    "next_run":     None,
}


MAX_WINDOW_MS = 89 * 24 * 60 * 60 * 1000   # Bitget max: 90 days per request


def _chunked_sync(sync_fn, conn, start_ms: int, end_ms: int) -> int:
    """
    Call sync_fn(conn, start_ms, end_ms) in ≤90-day chunks.
    Returns total rows inserted across all chunks.
    """
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
    Fetches everything newer than the last successful sync timestamp.
    Splits into 90-day chunks to comply with Bitget API limits.
    Thread-safe via _sync_lock.
    Returns: {"positions": N, "orders": N, "bills": N, "equity": {...}}
    """
    if not _sync_lock.acquire(blocking=False):
        return {"error": "Sync already running"}

    _sync_status["running"] = True
    _sync_status["last_error"] = None

    own_conn = conn is None
    try:
        if own_conn:
            conn = get_conn()

        _ensure_settings_table(conn)

        # Default start: timestamp of the most recent position already in DB.
        # This ensures the first API sync only fetches trades AFTER the CSV import ended,
        # not the full history (which is already imported from CSV).
        # Falls back to 2 days ago if the DB is empty.
        two_days_ago = int((time.time() - 2 * 86400) * 1000)
        latest_in_db = conn.execute(
            "SELECT MAX(strftime('%s', close_time)) FROM positions"
        ).fetchone()[0]
        if latest_in_db:
            # Start 1 minute before the latest known position to catch any overlap
            default_start = int(latest_in_db) * 1000 - 60_000
        else:
            default_start = two_days_ago
        last_ms  = int(_get_setting(conn, "last_sync_ms", default_start))
        now_ms   = int(time.time() * 1000)

        print(f"[Sync] Fetching data since {_ms_to_dt(str(last_ms))} ...")

        n_pos    = _chunked_sync(_sync_positions, conn, last_ms, now_ms)
        n_orders = _chunked_sync(_sync_orders,    conn, last_ms, now_ms)
        n_bills  = _chunked_sync(_sync_bills,     conn, last_ms, now_ms)
        equity   = bc.get_account_equity()

        _set_setting(conn, "last_sync_ms", now_ms)
        _set_setting(conn, "account_equity", equity.get("accountEquity", ""))
        _set_setting(conn, "available_balance", equity.get("available", ""))

        result = {
            "positions": n_pos,
            "orders":    n_orders,
            "bills":     n_bills,
            "equity":    equity,
            "synced_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        }

        _sync_status["last_run"]    = result["synced_at"]
        _sync_status["last_result"] = result
        print(f"[Sync] Done — {n_pos} positions, {n_orders} orders, {n_bills} bills")
        return result

    except Exception as e:
        import traceback
        err = str(e)
        traceback.print_exc()
        _sync_status["last_error"] = err
        return {"error": err}
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
        return  # already running

    def loop():
        # Initial delay so the app finishes starting up
        time.sleep(10)
        while True:
            next_time = time.time() + SYNC_INTERVAL_SECONDS
            _sync_status["next_run"] = datetime.fromtimestamp(next_time).strftime("%Y-%m-%d %H:%M:%S")
            try:
                run_sync()
            except Exception as e:
                print(f"[Sync] Background error: {e}")
            # Sleep until next interval
            wait = max(0, next_time - time.time())
            time.sleep(wait)

    _bg_thread = threading.Thread(target=loop, daemon=True, name="bitget-sync")
    _bg_thread.start()
    print(f"[Sync] Background auto-sync started (every {SYNC_INTERVAL_SECONDS//60}m)")


def get_status() -> dict:
    return dict(_sync_status)
