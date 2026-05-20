"""
core/scheduler.py -- Cycle timing and interval management.

Handles the daemon's sleep/wake cycle, jitter prevention,
and timing metrics for performance monitoring.

Uses interruptible sleep (1s intervals checking shutdown flag)
so SIGTERM/SIGINT are handled within ~1 second even during
long sleep periods.
"""

from __future__ import annotations

import logging
import threading
import time
import random
from typing import Optional

_log = logging.getLogger(__name__)


class Scheduler:
    """
    Manages the daemon's cycle timing.

    Features:
      - Configurable cycle interval (from strategy YAML)
      - Small random jitter to prevent thundering herd
      - Tracks cycle duration for performance monitoring
      - Interruptible sleep: checks shutdown flag every second
    """

    def __init__(self, interval_minutes: int = 15, jitter_seconds: int = 30):
        """
        Args:
            interval_minutes: How often the daemon runs a cycle.
            jitter_seconds: Random jitter added to interval (0 to jitter_seconds).
                            Prevents all instances from hitting APIs at the same time.
        """
        self.interval_minutes = interval_minutes
        self.jitter_seconds = jitter_seconds
        self._last_cycle_start: float = 0.0
        self._last_cycle_end: float = 0.0
        self._cycle_count: int = 0
        self._total_sleep_time: float = 0.0
        self._shutdown_event = threading.Event()

    @property
    def interval_seconds(self) -> int:
        """Cycle interval in seconds."""
        return self.interval_minutes * 60

    @property
    def cycle_count(self) -> int:
        """Number of completed cycles."""
        return self._cycle_count

    @property
    def last_cycle_duration_ms(self) -> int:
        """Duration of the last cycle in milliseconds."""
        if self._last_cycle_start == 0 or self._last_cycle_end == 0:
            return 0
        return int((self._last_cycle_end - self._last_cycle_start) * 1000)

    def start_cycle(self) -> None:
        """Mark the start of a new cycle."""
        self._last_cycle_start = time.time()
        self._cycle_count += 1
        self._shutdown_event.clear()
        _log.info(
            "Cycle %d started (interval=%dm)",
            self._cycle_count,
            self.interval_minutes,
        )

    def end_cycle(self) -> None:
        """Mark the end of a cycle."""
        self._last_cycle_end = time.time()
        duration_ms = self.last_cycle_duration_ms
        _log.info(
            "Cycle %d completed in %dms",
            self._cycle_count,
            duration_ms,
        )

    def sleep_until_next_cycle(self) -> None:
        """
        Sleep until the next cycle should start.

        Uses interruptible sleep: checks shutdown event every 1 second.
        If shutdown is requested, returns immediately.
        Calculates remaining time from cycle end, adds jitter,
        and sleeps in 1-second increments.
        """
        elapsed = time.time() - self._last_cycle_start
        remaining = self.interval_seconds - elapsed

        if remaining > 0:
            jitter = random.uniform(0, self.jitter_seconds)
            total_sleep = remaining + jitter
            _log.info(
                "Sleeping %.1fs until next cycle (jitter=%.1fs)",
                total_sleep,
                jitter,
            )
            # Interruptible sleep: check shutdown every second
            slept = 0.0
            while slept < total_sleep and not self._shutdown_event.is_set():
                chunk = min(1.0, total_sleep - slept)
                time.sleep(chunk)
                slept += chunk
            self._total_sleep_time += slept
        else:
            _log.warning(
                "Cycle took %.1fs, exceeding interval of %ds. "
                "Starting next cycle immediately.",
                elapsed,
                self.interval_seconds,
            )

    def update_interval(self, interval_minutes: int) -> None:
        """Update the cycle interval (e.g. from config hot-reload)."""
        if interval_minutes != self.interval_minutes:
            _log.info(
                "Cycle interval changed: %dm -> %dm",
                self.interval_minutes,
                interval_minutes,
            )
            self.interval_minutes = interval_minutes

    def stop(self) -> None:
        """Signal the scheduler to stop (interrupts sleep immediately)."""
        self._shutdown_event.set()
        _log.info("Scheduler stopped after %d cycles", self._cycle_count)

    @property
    def is_running(self) -> bool:
        return not self._shutdown_event.is_set()

    def __repr__(self) -> str:
        return (
            f"Scheduler(interval={self.interval_minutes}m, "
            f"cycles={self._cycle_count}, "
            f"last_duration={self.last_cycle_duration_ms}ms)"
        )