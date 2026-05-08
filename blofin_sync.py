"""
blofin_sync.py — Live sync from Blofin API into the local SQLite database.

Entry point: run_sync()
  Fetches new closed positions since the last successful sync.
  Uses historyId as the idempotency key to prevent duplicates.
  Stores last-sync cursor in the settings table.

Auto-sync is started by app.py via start_background_sync().
Only runs when BLOFIN_API_KEY + BLOFIN_SECRET_KEY are set.
"""

import threading
import time

import blofin_client as bc
from database import get_conn
import market_context as _mkt

SYNC_INTERVAL_SECONDS = 5 * 60  # every 5 minutes (same cadence as Bitget)

_sync_lock   = threading.Lock()
_sync_status = {
    "running":    False,
    "last_run":   None,
    "last_result": None,
    "last_error": None,
    "next_run":   None,
}


def get_status() -> dict:
    return dict(_sync_status)


def _get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def _set_setting(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, str(value)))
    conn.commit()


def _ensure_exchange_col(conn):
    """Add exchange column if missing (older installs)."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(positions)").fetchall()]
    if "exchange" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN exchange TEXT DEFAULT 'bitget'")
        conn.commit()
    if "external_id" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN external_id TEXT")
        conn.commit()


def _sync_positions(conn) -> int:
    """
    Fetch new closed Blofin positions and insert into the positions table.
    Deduplicates by external_id (historyId). Returns count of new rows inserted.
    """
    # Cursor from last sync — use historyId of the oldest row from the last batch
    # so we continue from where we left off.
    # We keep fetching pages until we find a row already in the DB (or run out of data).
    inserted = 0
    after    = None  # start from newest; paginate backwards

    for _ in range(20):  # safety cap at 20 pages
        rows = bc.get_position_history(limit=100, after=after)
        if not rows:
            break

        new_this_page = 0
        for r in rows:
            ext_id = r.get("external_id", "")
            # Skip if already stored
            if ext_id and conn.execute(
                "SELECT 1 FROM positions WHERE external_id=? AND exchange='blofin'",
                (ext_id,)
            ).fetchone():
                continue

            conn.execute("""
                INSERT INTO positions
                    (symbol, base_asset, direction, margin_mode,
                     open_time, close_time, duration_minutes,
                     entry_price, close_price, size_contracts, size_usdt,
                     position_pnl, realized_pnl, opening_fee, closing_fee, total_fees,
                     external_id, exchange, leverage, is_manual)
                VALUES
                    (:symbol, :base_asset, :direction, :margin_mode,
                     :open_time, :close_time, :duration_minutes,
                     :entry_price, :close_price, :size_contracts, :size_usdt,
                     :position_pnl, :realized_pnl, :opening_fee, :closing_fee, :total_fees,
                     :external_id, :exchange, :leverage, 0)
            """, r)
            inserted    += 1
            new_this_page += 1

        conn.commit()

        # Stop when the API has no more pages; keep going even if this page was all-duplicates
        # so initial back-fills and recovery syncs don't miss older trades.
        if len(rows) < 100:
            break

        # Move cursor to the oldest historyId on this page for the next request
        after = rows[-1].get("external_id", "")

    # Tag market regime on newly inserted positions
    if inserted > 0:
        try:
            regime = _mkt.get_btc_regime()
            conn.execute(
                "UPDATE positions SET market_regime = ? WHERE market_regime IS NULL AND exchange = 'blofin'",
                (regime,)
            )
            conn.commit()
        except Exception:
            pass

    return inserted


def run_sync() -> dict:
    """Run one Blofin sync cycle. Returns result dict."""
    if not bc.is_configured():
        return {"skipped": True, "reason": "Blofin credentials not configured"}

    conn = get_conn()
    try:
        _ensure_exchange_col(conn)
        positions = _sync_positions(conn)

        # Auto-close any analyst calls whose matched Blofin position has now closed
        calls_closed = 0
        try:
            from bitget_sync import _auto_close_calls
            calls_closed = _auto_close_calls(conn, exchange="blofin")
        except Exception:
            pass

        # Update equity/balance
        equity_data = bc.get_account_equity()
        if equity_data:
            _set_setting(conn, "blofin_equity",    str(equity_data.get("equity", 0)))
            _set_setting(conn, "blofin_available",  str(equity_data.get("available", 0)))

        _set_setting(conn, "blofin_last_sync_ms", str(int(time.time() * 1000)))

        return {"positions": positions, "calls_closed": calls_closed, "equity": equity_data}
    except Exception as e:
        print(f"[BlofinSync] run_sync error: {e}", flush=True)   # log detail server-side only
        _sync_status["last_error"] = "Sync error — check server logs"  # CWE-209: no exception detail in response
        raise
    finally:
        conn.close()


def start_background_sync():
    """Start a daemon thread that syncs Blofin every SYNC_INTERVAL_SECONDS."""
    if not bc.is_configured():
        print("[BlofinSync] Credentials not set — background sync disabled", flush=True)
        return

    def _loop():
        # Brief delay so Bitget sync can initialise first
        time.sleep(15)
        while True:
            if _sync_lock.acquire(blocking=False):
                try:
                    _sync_status["running"]   = True
                    _sync_status["last_run"]  = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
                    result = run_sync()
                    _sync_status["last_result"] = result
                    _sync_status["last_error"]  = None
                    print(f"[BlofinSync] {result}", flush=True)
                except Exception as e:
                    print(f"[BlofinSync] Error: {e}", flush=True)  # log detail server-side only
                    _sync_status["last_error"] = "Sync error — check server logs"
                finally:
                    _sync_status["running"]  = False
                    _sync_status["next_run"] = time.strftime(
                        "%Y-%m-%d %H:%M:%S",
                        time.localtime(time.time() + SYNC_INTERVAL_SECONDS)
                    )
                    _sync_lock.release()
            time.sleep(SYNC_INTERVAL_SECONDS)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    print("[BlofinSync] Background sync started", flush=True)
