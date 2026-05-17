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
        linked = cur.execute(
            "SELECT id FROM positions WHERE call_id=? AND (setup_type IS NULL OR setup_type='')",
            (call_id,)
        ).fetchone()
        if linked:
            _populate_setup_type_from_call(conn, linked[0], call_id)

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


def auto_match_calls(conn, exchange: str = "bitget") -> int:
    """
    For every recently-closed position with no call_id, search for a 'saved'
    analyzed_call with the same symbol + direction created within 30 days
    before the position opened. If found: set positions.call_id and promote
    the call to 'matched' so auto_close_calls() resolves it next cycle.

    Only touches positions closed in the last 7 days (recent sync window).
    Safe to call repeatedly — idempotent (skips already-linked positions).
    Returns number of positions newly linked.
    """
    cur = conn.cursor()

    positions = cur.execute("""
        SELECT id, symbol, direction, open_time
        FROM positions
        WHERE call_id IS NULL
          AND COALESCE(exchange, 'bitget') = ?
          AND close_time >= datetime('now', '-7 days')
        ORDER BY close_time DESC
    """, (exchange,)).fetchall()

    matched = 0
    for pos_id, symbol, direction, open_time in positions:
        dir_filter = "Long" if "long" in (direction or "").lower() else "Short"

        call = cur.execute("""
            SELECT id
            FROM analyzed_calls
            WHERE symbol    = ?
              AND direction LIKE ?
              AND status    = 'saved'
              AND entry_price IS NOT NULL
              AND sl_price    IS NOT NULL
              AND created_at >= datetime(?, '-30 days')
              AND created_at <= ?
            ORDER BY created_at DESC
            LIMIT 1
        """, (symbol, dir_filter + "%", open_time or "9999", open_time or "9999")).fetchone()

        if not call:
            continue

        call_id = call[0]
        cur.execute("UPDATE positions SET call_id=? WHERE id=?", (call_id, pos_id))
        cur.execute("UPDATE analyzed_calls SET status='matched' WHERE id=?", (call_id,))
        matched += 1
        print(f"[Sync] Auto-matched call #{call_id} -> position #{pos_id} ({symbol} {dir_filter})",
              flush=True)
        _populate_setup_type_from_call(conn, pos_id, call_id)

        # Compute execution lag and signal_price
        try:
            from datetime import datetime as _dt
            call_row = conn.execute(
                "SELECT created_at, entry_price FROM analyzed_calls WHERE id=?", (call_id,)
            ).fetchone()
            pos_row = conn.execute(
                "SELECT open_time FROM positions WHERE id=?", (pos_id,)
            ).fetchone()
            if call_row and pos_row and call_row[0] and pos_row[0]:
                fmt = "%Y-%m-%d %H:%M:%S"
                call_dt = _dt.strptime(call_row[0][:19], fmt)
                pos_dt  = _dt.strptime(pos_row[0][:19], fmt)
                lag_min = max(0, int((pos_dt - call_dt).total_seconds() / 60))
                signal_price = float(call_row[1]) if call_row[1] else None
                cur.execute("""
                    UPDATE positions
                    SET execution_lag_minutes=?, signal_price=?
                    WHERE id=? AND execution_lag_minutes IS NULL
                """, (lag_min, signal_price, pos_id))
        except Exception:
            pass

    conn.commit()
    return matched


def _populate_setup_type_from_call(conn, position_id: int, call_id: int) -> None:
    """
    Read trade_type from analyzed_calls.analysis_json and write to positions.setup_type.
    No-op if analysis_json is absent or has no trade_type field.
    """
    import json as _json
    try:
        row = conn.execute(
            "SELECT analysis_json FROM analyzed_calls WHERE id=?", (call_id,)
        ).fetchone()
        if not row or not row[0]:
            return
        data = _json.loads(row[0])
        trade_type = (data.get("trade_type") or data.get("setup_type")
                      or data.get("setup_label") or "")
        if trade_type:
            conn.execute(
                "UPDATE positions SET setup_type=? WHERE id=? AND (setup_type IS NULL OR setup_type='')",
                (trade_type, position_id),
            )
            conn.commit()
    except Exception:
        pass
