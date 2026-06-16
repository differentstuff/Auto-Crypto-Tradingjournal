"""
core/virtual_clock.py -- Time virtualization for replay mode.

When active, all time queries return the virtual time.
When inactive, they return real datetime.now().

Usage:
    clock = VirtualClock()
    clock.activate(t_cursor)      # Start virtual time
    clock.advance(t_cursor + 15m) # Advance to next cycle
    clock.deactivate()            # Return to real time

The clock is set on Substrate._clock by the replay driver.
Enzymes access time via substrate.now_iso() or substrate.now_as_datetime().
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


class VirtualClock:
    """
    Time virtualization for replay mode.

    When active, all time queries return the virtual time.
    When inactive, they return real datetime.now().

    The clock is set on Substrate._clock by the replay driver.
    Enzymes access time via substrate.now_iso() or substrate.now_as_datetime().
    """

    def __init__(self) -> None:
        self._virtual_now: Optional[datetime] = None

    @property
    def active(self) -> bool:
        """Return True if virtual clock is active (replay mode)."""
        return self._virtual_now is not None

    def activate(self, t: datetime) -> None:
        """Set the virtual clock to a specific time."""
        self._virtual_now = t

    def advance(self, t: datetime) -> None:
        """Advance the virtual clock to a new time."""
        self._virtual_now = t

    def deactivate(self) -> None:
        """Deactivate virtual clock, return to real time."""
        self._virtual_now = None

    def now(self) -> datetime:
        """Return virtual time if active, otherwise real time."""
        if self._virtual_now is not None:
            return self._virtual_now
        return datetime.now(timezone.utc)

    def now_iso(self) -> str:
        """Return virtual time as ISO string if active, otherwise real time."""
        return self.now().isoformat()

    def now_timestamp(self) -> float:
        """Return virtual time as Unix timestamp if active, otherwise real time."""
        return self.now().timestamp()

    def now_ms(self) -> int:
        """Return virtual time as milliseconds timestamp (for CCXT since= parameter)."""
        return int(self.now_timestamp() * 1000)
