"""
sync_base.py — Shared infrastructure for exchange sync drivers.

Extracted from bitget_sync.py to eliminate duplication with blofin_sync.py.
Adding a 3rd exchange (e.g. Hyperliquid) is a 1-file addition.
"""
import threading
import time
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable


# ── Settings helpers (was duplicated in both sync files) ───────────────────────

def _get_setting(conn, key: str, default=None):
    """Read a value from the settings table. Returns default if not found."""
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def _set_setting(conn, key: str, value: str) -> None:
    """Upsert a value in the settings table."""
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, str(value)))
    conn.commit()


# ── SyncDriver protocol ────────────────────────────────────────────────────────

@runtime_checkable
class SyncDriver(Protocol):
    """Interface every exchange sync driver must satisfy."""
    name: str

    def is_configured(self) -> bool: ...
    def fetch_equity(self) -> dict: ...
    def fetch_positions(self, since_ms: int = None) -> list: ...
    def extra_steps(self, conn) -> None: ...


# ── Thread-safe sync status container ─────────────────────────────────────────

class SyncState:
    """Thread-safe sync status container for exchange sync drivers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict = {
            "running": False,
            "last_run": None,
            "next_run": None,
            "last_error": None,
            "last_result": None,
        }

    def update(self, **kwargs) -> None:
        with self._lock:
            self._data.update(kwargs)

    def snapshot(self) -> dict:
        """Return a shallow copy of status (safe for HTTP response)."""
        with self._lock:
            return dict(self._data)

    def try_start(self) -> bool:
        """Mark as running atomically. Returns True if acquired, False if already running."""
        with self._lock:
            if self._data["running"]:
                return False
            self._data["running"] = True
            self._data["last_error"] = None
            return True

    def finish(self, result: dict = None, error: str = None) -> None:
        with self._lock:
            self._data["running"] = False
            self._data["last_run"] = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
            if error:
                self._data["last_error"] = error
            if result:
                self._data["last_result"] = result


# ── Shared call-resolution functions ──────────────────────────────────────────

def auto_close_calls(conn, exchange: str = "bitget") -> int:
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


def retroactive_close_calls(conn) -> int:
    """
    For every 'saved' call older than 2 hours with entry/sl/tp1 prices set,
    fetch 1H candles and check if price hit TP1, TP2, or SL since creation.
    Records outcome retroactively; outcome_pnl is NULL (no actual trade).
    Returns number of calls resolved.
    """
    import chart_context

    cur   = conn.cursor()
    calls = cur.execute("""
        SELECT id, symbol, direction, sl_price, tp1_price, tp2_price, created_at
        FROM analyzed_calls
        WHERE status      = 'saved'
          AND sl_price    IS NOT NULL
          AND tp1_price   IS NOT NULL
          AND entry_price IS NOT NULL
          AND created_at  < datetime('now', '-2 hours')
    """).fetchall()

    now_ms   = int(time.time() * 1000)
    resolved = 0

    for call in calls:
        call_id, symbol, direction, sl_price, tp1_price, tp2_price, created_at = call

        try:
            df = chart_context.get_candles_at_time(symbol, "1H", now_ms, limit=500)
        except Exception:
            continue

        if df.empty:
            continue

        try:
            created_dt = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            created_ms = int(created_dt.timestamp() * 1000)
        except Exception:
            continue

        df = df[df["timestamp"] > created_ms]
        if df.empty:
            continue

        is_long   = "long" in (direction or "").lower()
        hit_sl    = hit_tp1 = hit_tp2 = 0
        outcome   = None

        for _, row in df.iterrows():
            high = row["high"]
            low  = row["low"]
            if is_long:
                candle_open = float(row.get("open", 0) or 0)
                sl_hit  = bool(sl_price  and float(low)  <= sl_price)
                tp2_hit = bool(tp2_price and float(high) >= tp2_price)
                tp1_hit = bool(tp1_price and float(high) >= tp1_price)
                # If candle opened above SL, the position was safe at open → TP takes priority
                open_above_sl = not sl_price or candle_open > sl_price
                if tp2_hit and (open_above_sl or not sl_hit):
                    hit_tp1, hit_tp2, outcome = 1, 1, "won"
                    break
                elif tp1_hit and (open_above_sl or not sl_hit):
                    hit_tp1, outcome = 1, "won"
                    break
                elif sl_hit:
                    hit_sl, outcome = 1, "lost"
                    break
            else:  # short
                candle_open = float(row.get("open", 0) or 0)
                sl_hit  = bool(sl_price  and float(high) >= sl_price)
                tp2_hit = bool(tp2_price and float(low)  <= tp2_price)
                tp1_hit = bool(tp1_price and float(low)  <= tp1_price)
                # If candle opened below SL, the position was safe at open → TP takes priority
                open_below_sl = not sl_price or candle_open < sl_price
                if tp2_hit and (open_below_sl or not sl_hit):
                    hit_tp1, hit_tp2, outcome = 1, 1, "won"
                    break
                elif tp1_hit and (open_below_sl or not sl_hit):
                    hit_tp1, outcome = 1, "won"
                    break
                elif sl_hit:
                    hit_sl, outcome = 1, "lost"
                    break

        if outcome is None:
            continue

        cur.execute("""
            UPDATE analyzed_calls
            SET status      = 'closed',
                outcome     = ?,
                outcome_pnl = NULL,
                hit_tp1     = ?,
                hit_tp2     = ?,
                hit_sl      = ?,
                outcome_at  = datetime('now')
            WHERE id = ?
        """, (outcome, hit_tp1, hit_tp2, hit_sl, call_id))
        resolved += 1
        print(f"[Sync] Retroactive #{call_id} {symbol} {direction} → {outcome}", flush=True)

    conn.commit()
    return resolved
