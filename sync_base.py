"""
sync_base.py — Shared infrastructure for exchange sync drivers.

Extracted from bitget_sync.py to eliminate duplication with blofin_sync.py.
Adding a 3rd exchange (e.g. Hyperliquid) is a 1-file addition.
"""
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
