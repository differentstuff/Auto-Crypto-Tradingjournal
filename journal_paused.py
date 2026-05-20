"""
journal_paused.py — Global pause switch for AI-firing automations + alerts.

When the `paused` setting is truthy, the following are suppressed:
  - scanner_scheduler._loop() skips its scheduled scan (sleeps and re-checks)
  - monitor_scheduler._loop()  skips its monitor pass
  - telegram_notify.send_*()    no-op (logged but not sent)

The web UI, sync, and manual API endpoints remain fully functional — only
the background AI bursts and outbound Telegram messages pause.

Toggle:
  sqlite3 trading_journal.db "INSERT OR REPLACE INTO settings (key,value) VALUES ('paused','1');"
  sqlite3 trading_journal.db "INSERT OR REPLACE INTO settings (key,value) VALUES ('paused','0');"

Or via API:
  POST /api/settings  body={"key":"paused","value":"1"}

Reads are cached for 10 seconds so we don't query DB on every alert/loop tick.
"""
import time
import threading

from database import db_conn

_CACHE: dict = {"ts": 0.0, "paused": False}
_CACHE_TTL = 10  # seconds
_LOCK = threading.Lock()


def is_paused() -> bool:
    """True if the global pause flag is set in the settings table."""
    now = time.time()
    with _LOCK:
        if now - _CACHE["ts"] < _CACHE_TTL:
            return _CACHE["paused"]
    paused = False
    try:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key='paused'"
            ).fetchone()
            if row and str(row["value"]).strip().lower() in ("1", "true", "yes", "on"):
                paused = True
    except Exception:
        pass
    with _LOCK:
        _CACHE["ts"] = now
        _CACHE["paused"] = paused
    return paused


def set_paused(paused: bool) -> None:
    """Persist the pause flag. Bumps the cache so reads pick it up immediately."""
    val = "1" if paused else "0"
    with db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('paused', ?)",
            (val,),
        )
    with _LOCK:
        _CACHE["ts"] = time.time()
        _CACHE["paused"] = paused
