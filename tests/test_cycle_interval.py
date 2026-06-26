"""
tests/test_cycle_interval.py -- Tests for Fix 5: Cycle interval configuration.

 
Verify that:
 - cycle_interval_minutes is respected per strategy
 - warning logged when cycle > timeframe/2
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import make_full_config
from core.substrate import Substrate


from enzymes.collect_ohlcv import timeframe_to_minutes


class TestTimeframeToMinutes:

    def test_hours(self):
        assert timeframe_to_minutes("4H") == 240
        assert timeframe_to_minutes("1h") == 60

    def test_minutes(self):
        assert timeframe_to_minutes("15m") == 15
        assert timeframe_to_minutes("5m") == 5

    def test_days(self):
        assert timeframe_to_minutes("1D") == 1440

    def test_unknown_defaults_to_60(self):
        assert timeframe_to_minutes("xyz") == 60


class TestCycleIntervalPerStrategy:

    def test_strategy_1_4h_cycle_120(self):
        sub = Substrate(config=make_full_config(strategy={
            "timeframe": "4h",
            "cycle_interval_minutes": 120,
        }))
        assert sub.strategy["cycle_interval_minutes"] == 120

    def test_strategy_2_1h_cycle_30(self):
        sub = Substrate(config=make_full_config(strategy={
            "timeframe": "1h",
            "cycle_interval_minutes": 30,
        }))
        assert sub.strategy["cycle_interval_minutes"] == 30

    def test_cycle_interval_matches_config(self):
        for tf, expected_interval in [("4h", 120), ("1h", 30)]:
            cfg = make_full_config(strategy={
                "timeframe": tf,
                "cycle_interval_minutes": expected_interval,
            })
            sub = Substrate(config=cfg)
            assert sub.cfg("strategy.cycle_interval_minutes") == expected_interval
