"""
tests_new/test_scheduler.py -- Tests for the cycle scheduler.

Phase A validation: scheduler timing, interval management,
interruptible sleep.
"""

import os
import sys
import time
import threading
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.scheduler import Scheduler


class TestSchedulerCreation:
    """Test scheduler initialization."""

    def test_requires_interval(self):
        """Scheduler requires interval_minutes — no hardcoded default."""
        with pytest.raises(ValueError, match="interval_minutes must be passed from config"):
            Scheduler()

    def test_custom_creation(self):
        """Scheduler can be created with custom interval."""
        s = Scheduler(interval_minutes=5, jitter_seconds=10)
        assert s.interval_minutes == 5
        assert s.jitter_seconds == 10

    def test_interval_seconds(self):
        """interval_seconds returns minutes * 60."""
        s = Scheduler(interval_minutes=10)
        assert s.interval_seconds == 600


class TestSchedulerCycles:
    """Test cycle start/end tracking."""

    def test_start_cycle(self):
        """start_cycle increments cycle count."""
        s = Scheduler(interval_minutes=1, jitter_seconds=0)
        s.start_cycle()
        assert s.cycle_count == 1
        assert s.is_running is True

    def test_end_cycle(self):
        """end_cycle records duration."""
        s = Scheduler(interval_minutes=1, jitter_seconds=0)
        s.start_cycle()
        time.sleep(0.01)  # Small delay for measurable duration
        s.end_cycle()
        assert s.last_cycle_duration_ms > 0

    def test_multiple_cycles(self):
        """Multiple cycles increment count."""
        s = Scheduler(interval_minutes=1, jitter_seconds=0)
        for i in range(5):
            s.start_cycle()
            s.end_cycle()
        assert s.cycle_count == 5

    def test_update_interval(self):
        """update_interval changes the cycle interval."""
        s = Scheduler(interval_minutes=15)
        s.update_interval(5)
        assert s.interval_minutes == 5
        assert s.interval_seconds == 300

    def test_update_same_interval(self):
        """update_interval with same value does not change."""
        s = Scheduler(interval_minutes=15)
        s.update_interval(15)
        assert s.interval_minutes == 15

    def test_stop_interrupts_sleep(self):
        """stop() interrupts an ongoing sleep via shutdown event."""
        s = Scheduler(interval_minutes=1, jitter_seconds=0)
        s.start_cycle()
        s.end_cycle()

        # Start sleep in a thread, then stop
        def sleep_then_stop():
            time.sleep(0.2)
            s.stop()

        thread = threading.Thread(target=sleep_then_stop)
        thread.start()

        start = time.time()
        s.sleep_until_next_cycle()
        elapsed = time.time() - start

        # Should wake up quickly after stop, not wait the full 60s
        assert elapsed < 5.0
        assert s.is_running is False

    def test_repr(self):
        """Scheduler has useful repr."""
        s = Scheduler(interval_minutes=15)
        r = repr(s)
        assert "15m" in r
        assert "cycles=0" in r