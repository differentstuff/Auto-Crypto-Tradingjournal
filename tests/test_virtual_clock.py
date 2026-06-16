"""
tests/test_virtual_clock.py -- Verify VirtualClock returns historical time when active.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.virtual_clock import VirtualClock


class TestVirtualClock:
    """Test VirtualClock time virtualization."""

    def test_inactive_returns_real_time(self):
        """When inactive, now() returns real UTC time."""
        clock = VirtualClock()
        assert not clock.active

        before = datetime.now(timezone.utc)
        result = clock.now()
        after = datetime.now(timezone.utc)

        assert before <= result <= after

    def test_inactive_now_iso(self):
        """When inactive, now_iso() returns a valid ISO string."""
        clock = VirtualClock()
        iso = clock.now_iso()
        # Should be parseable as ISO
        parsed = datetime.fromisoformat(iso)
        assert parsed.tzinfo is not None

    def test_activate_sets_virtual_time(self):
        """activate() sets the virtual time."""
        clock = VirtualClock()
        t = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        clock.activate(t)

        assert clock.active
        assert clock.now() == t

    def test_advance_updates_virtual_time(self):
        """advance() updates the virtual time."""
        clock = VirtualClock()
        t1 = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        clock.activate(t1)

        t2 = t1 + timedelta(minutes=15)
        clock.advance(t2)

        assert clock.now() == t2

    def test_deactivate_returns_real_time(self):
        """deactivate() returns to real time."""
        clock = VirtualClock()
        t = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        clock.activate(t)
        assert clock.active

        clock.deactivate()
        assert not clock.active

        # Should return real time now
        before = datetime.now(timezone.utc)
        result = clock.now()
        after = datetime.now(timezone.utc)
        assert before <= result <= after

    def test_now_iso_virtual(self):
        """now_iso() returns virtual time as ISO string when active."""
        clock = VirtualClock()
        t = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        clock.activate(t)

        iso = clock.now_iso()
        assert "2025-06-15" in iso

    def test_now_timestamp_virtual(self):
        """now_timestamp() returns virtual time as Unix timestamp when active."""
        clock = VirtualClock()
        t = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        clock.activate(t)

        ts = clock.now_timestamp()
        assert ts == t.timestamp()

    def test_now_ms_virtual(self):
        """now_ms() returns virtual time as milliseconds timestamp when active."""
        clock = VirtualClock()
        t = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        clock.activate(t)

        ms = clock.now_ms()
        assert ms == int(t.timestamp() * 1000)

    def test_multiple_advances(self):
        """Multiple advance() calls update time correctly."""
        clock = VirtualClock()
        base = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        clock.activate(base)

        for i in range(10):
            expected = base + timedelta(minutes=15 * (i + 1))
            clock.advance(expected)
            assert clock.now() == expected

    def test_deactivate_without_activate(self):
        """deactivate() without activate() is a no-op."""
        clock = VirtualClock()
        clock.deactivate()
        assert not clock.active


class TestVirtualClockSubstrateIntegration:
    """Test that Substrate delegates time queries to VirtualClock."""

    def test_substrate_now_iso_uses_clock(self):
        """substrate.now_iso() returns virtual time when clock is active."""
        from conftest import make_full_config
        from core.substrate import Substrate

        sub = Substrate(config=make_full_config())
        t = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        sub._clock.activate(t)

        iso = sub.now_iso()
        assert "2025-06-15" in iso

    def test_substrate_now_as_datetime_uses_clock(self):
        """substrate.now_as_datetime() returns virtual time when clock is active."""
        from conftest import make_full_config
        from core.substrate import Substrate

        sub = Substrate(config=make_full_config())
        t = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        sub._clock.activate(t)

        result = sub.now_as_datetime()
        assert result == t

    def test_substrate_now_timestamp_uses_clock(self):
        """substrate.now_timestamp() returns virtual time when clock is active."""
        from conftest import make_full_config
        from core.substrate import Substrate

        sub = Substrate(config=make_full_config())
        t = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        sub._clock.activate(t)

        result = sub.now_timestamp()
        assert result == t.timestamp()

    def test_substrate_static_now_iso_unchanged(self):
        """substrate._now_iso() static method still returns real time."""
        from conftest import make_full_config
        from core.substrate import Substrate

        sub = Substrate(config=make_full_config())
        t = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        sub._clock.activate(t)

        # Static method should return real time, not virtual
        static_iso = Substrate._now_iso()
        assert "2025-06-15" not in static_iso  # Not the virtual time

    def test_substrate_clock_inactive_returns_real_time(self):
        """substrate.now_iso() returns real time when clock is inactive."""
        from conftest import make_full_config
        from core.substrate import Substrate

        sub = Substrate(config=make_full_config())
        # Clock is inactive by default
        assert not sub._clock.active

        before = datetime.now(timezone.utc)
        result = sub.now_as_datetime()
        after = datetime.now(timezone.utc)
        assert before <= result <= after

    def test_shallow_copy_shares_clock(self):
        """shallow_copy() shares the same clock reference."""
        from conftest import make_full_config
        from core.substrate import Substrate

        sub = Substrate(config=make_full_config())
        t = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        sub._clock.activate(t)

        copy = sub.shallow_copy()
        assert copy.now_as_datetime() == t

        # Advancing the clock affects both (shared reference)
        t2 = t + timedelta(minutes=15)
        sub._clock.advance(t2)
        assert copy.now_as_datetime() == t2
