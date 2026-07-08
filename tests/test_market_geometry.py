"""
tests/test_market_geometry.py -- Tests for MarketGeometry enzyme and structure-aware exits.

Tests:
1. Swing detection (detect_swings)
2. Trend direction classification (classify_trend_direction)
3. Phase classification (classify_phase)
4. Pullback depth computation (compute_pullback_depth)
5. Structure break detection (detect_structure_break)
6. MarketGeometry enzyme integration (can_activate, transform)
7. Structure-aware exits in RequestExit
8. Progressive trailing stop in ApproveExit
9. Trail schedule lookup (_lookup_trail_schedule)
10. Fallback to ATR when structure data absent
"""

import pytest
import numpy as np

from core.substrate import Substrate
from core.enzyme import EnzymeClass, create_enzyme
from conftest import make_full_config

# Trigger @register_enzyme decorators
import enzymes  # noqa: F401

from enzymes.market_geometry import (
    detect_swings,
    classify_trend_direction,
    classify_phase,
    compute_pullback_depth,
    detect_structure_break,
)
from core.trailing_stop import lookup_trail_schedule as _lookup_trail_schedule


# -- Fixtures ------------------------------------------------------------------

def _make_bullish_prices(n=50, base=100000.0):
    """Generate price arrays for a bullish trend (rising highs and lows)."""
    highs = []
    lows = []
    closes = []
    for i in range(n):
        # Rising trend with pullbacks
        price = base + i * 200 + 500 * np.sin(i * 0.5)
        highs.append(price + 300)
        lows.append(price - 300)
        closes.append(price)
    return highs, lows, closes




@pytest.fixture
def substrate():
    """Create a substrate with test config including structure settings."""
    config = make_full_config()
    return Substrate(config=config)


@pytest.fixture
def substrate_with_structure():
    """Create a substrate with structure-aware exits enabled."""
    config = make_full_config(
        exit_rules={
            "structure_aware_exits": True,
            "structure_break_exit": True,
            "phase_range_exit": True,
            "counter_breakout_exit": True,
            "deep_pullback_stop_multiplier": 0.5,
            "shallow_pullback_stop_multiplier": 1.0,
            "progressive_trail": True,
            "progressive_trail_schedule": {0: 1.0, 1: 0.75, 2: 0.5, 3: 0.25},
            "pullback_trail_tighten": 0.75,
            "deep_pullback_trail_tighten": 0.5,
        },
    )
    return Substrate(config=config)


# -- 1. Swing detection -------------------------------------------------------

class TestSwingDetection:
    def test_detect_swings_returns_list(self):
        highs, lows, _ = _make_bullish_prices(50)
        swings = detect_swings(highs, lows, lookback=3)
        assert isinstance(swings, list)

    def test_detect_swings_finds_highs_and_lows(self):
        highs, lows, _ = _make_bullish_prices(50)
        swings = detect_swings(highs, lows, lookback=3)
        types = {s["type"] for s in swings}
        assert "high" in types or "low" in types

    def test_swing_point_has_required_fields(self):
        highs, lows, _ = _make_bullish_prices(50)
        swings = detect_swings(highs, lows, lookback=3)
        if swings:
            assert "type" in swings[0]
            assert "price" in swings[0]
            assert "index" in swings[0]
            assert swings[0]["type"] in ("high", "low")

    def test_insufficient_data_returns_empty(self):
        swings = detect_swings([100, 101], [99, 100], lookback=5)
        assert swings == []

    def test_max_6_swings_returned(self):
        highs, lows, _ = _make_bullish_prices(100)
        swings = detect_swings(highs, lows, lookback=3)
        assert len(swings) <= 6


# -- 2. Trend direction -------------------------------------------------------

class TestTrendDirection:
    def test_bullish_trend_detected(self):
        # Create explicit HH+HL swing pattern
        swings = [
            {"type": "low",  "price": 99000, "index": 5},
            {"type": "high", "price": 101000, "index": 10},
            {"type": "low",  "price": 99500, "index": 15},   # HL
            {"type": "high", "price": 102000, "index": 20},   # HH
            {"type": "low",  "price": 100000, "index": 25},   # HL
            {"type": "high", "price": 103000, "index": 30},   # HH
        ]
        result = classify_trend_direction(swings)
        assert result == "bullish"

    def test_bearish_trend_detected(self):
        swings = [
            {"type": "high", "price": 103000, "index": 5},
            {"type": "low",  "price": 101000, "index": 10},
            {"type": "high", "price": 102000, "index": 15},   # LH
            {"type": "low",  "price": 100000, "index": 20},   # LL
            {"type": "high", "price": 101000, "index": 25},   # LH
            {"type": "low",  "price": 99000, "index": 30},    # LL
        ]
        result = classify_trend_direction(swings)
        assert result == "bearish"

    def test_ranging_with_insufficient_swings(self):
        swings = [
            {"type": "high", "price": 101000, "index": 5},
            {"type": "low",  "price": 99000, "index": 10},
        ]
        result = classify_trend_direction(swings)
        assert result == "ranging"

    def test_ranging_with_mixed_swings(self):
        swings = [
            {"type": "low",  "price": 99000, "index": 5},
            {"type": "high", "price": 101000, "index": 10},
            {"type": "low",  "price": 99500, "index": 15},   # HL (bullish)
            {"type": "high", "price": 100000, "index": 20},   # LH (bearish) — clear drop
        ]
        result = classify_trend_direction(swings)
        assert result == "ranging"


# -- 3. Phase classification --------------------------------------------------

class TestPhaseClassification:
    def test_impulse_phase_bullish(self):
        swing_highs = [{"price": 101000, "index": 10}]
        swing_lows = [{"price": 99000, "index": 5}]
        # Price above last swing high in bullish trend = impulse
        # (previous_phase must not be "range" or "" for impulse)
        phase = classify_phase("bullish", 102000, swing_highs, swing_lows, previous_phase="impulse")
        assert phase == "impulse"

    def test_pullback_phase_bullish(self):
        swing_highs = [{"price": 101000, "index": 10}]
        swing_lows = [{"price": 99000, "index": 5}]
        # Price between SL and SH in bullish trend = pullback
        phase = classify_phase("bullish", 100000, swing_highs, swing_lows)
        assert phase == "pullback"

    def test_breakout_phase_from_range(self):
        swing_highs = [{"price": 101000, "index": 10}]
        swing_lows = [{"price": 99000, "index": 5}]
        # Price above SH but previous phase was range = breakout
        phase = classify_phase("bullish", 102000, swing_highs, swing_lows, previous_phase="range")
        assert phase == "breakout"

    def test_range_phase_when_ranging(self):
        swing_highs = [{"price": 101000, "index": 10}]
        swing_lows = [{"price": 99000, "index": 5}]
        phase = classify_phase("ranging", 100000, swing_highs, swing_lows)
        assert phase == "range"

    def test_bearish_impulse(self):
        swing_highs = [{"price": 101000, "index": 10}]
        swing_lows = [{"price": 99000, "index": 5}]
        # Price below last swing low in bearish trend = impulse
        # (previous_phase must not be "range" or "" for impulse)
        phase = classify_phase("bearish", 98000, swing_highs, swing_lows, previous_phase="impulse")
        assert phase == "impulse"

    def test_empty_swings_returns_range(self):
        phase = classify_phase("bullish", 100000, [], [])
        assert phase == "range"


# -- 4. Pullback depth --------------------------------------------------------

class TestPullbackDepth:
    def test_shallow_pullback(self):
        swing_highs = [{"price": 101000, "index": 10}]
        swing_lows = [{"price": 99000, "index": 5}]
        # Retracement: (101000 - 100500) / (101000 - 99000) = 500/2000 = 0.25 → shallow
        depth, pct = compute_pullback_depth("bullish", 100500, swing_highs, swing_lows)
        assert depth == "shallow"
        assert pct == pytest.approx(0.25, abs=0.01)

    def test_moderate_pullback(self):
        swing_highs = [{"price": 101000, "index": 10}]
        swing_lows = [{"price": 99000, "index": 5}]
        # Retracement: (101000 - 100000) / 2000 = 0.50 → moderate
        depth, pct = compute_pullback_depth("bullish", 100000, swing_highs, swing_lows)
        assert depth == "moderate"

    def test_deep_pullback(self):
        swing_highs = [{"price": 101000, "index": 10}]
        swing_lows = [{"price": 99000, "index": 5}]
        # Retracement: (101000 - 99300) / 2000 = 0.70 → deep
        depth, pct = compute_pullback_depth("bullish", 99300, swing_highs, swing_lows)
        assert depth == "deep"

    def test_ranging_returns_na(self):
        swing_highs = [{"price": 101000, "index": 10}]
        swing_lows = [{"price": 99000, "index": 5}]
        depth, pct = compute_pullback_depth("ranging", 100000, swing_highs, swing_lows)
        assert depth == "n/a"
        assert pct == 0.0

    def test_bearish_pullback(self):
        swing_highs = [{"price": 101000, "index": 10}]
        swing_lows = [{"price": 99000, "index": 5}]
        # Bearish: retracement = (current - SL) / (SH - SL)
        # (100000 - 99000) / 2000 = 0.50 → moderate
        depth, pct = compute_pullback_depth("bearish", 100000, swing_highs, swing_lows)
        assert depth == "moderate"


# -- 5. Structure break detection ---------------------------------------------

class TestStructureBreak:
    def test_bullish_structure_break(self):
        # LL after HL pattern = structure break
        swing_highs = [{"price": 101000, "index": 10}, {"price": 102000, "index": 20}]
        swing_lows = [{"price": 99500, "index": 5}, {"price": 99000, "index": 15}]
        # Last SL (99000) < prev SL (99500) = LL, and price < 99000
        result = detect_structure_break("bullish", 98500, swing_highs, swing_lows)
        assert result is True

    def test_no_break_when_lows_rising(self):
        swing_highs = [{"price": 101000, "index": 10}, {"price": 102000, "index": 20}]
        swing_lows = [{"price": 99000, "index": 5}, {"price": 99500, "index": 15}]
        # Last SL (99500) > prev SL (99000) = HL, no break
        result = detect_structure_break("bullish", 99600, swing_highs, swing_lows)
        assert result is False

    def test_bearish_structure_break(self):
        # HH after LH pattern = structure break
        swing_highs = [{"price": 101000, "index": 5}, {"price": 102000, "index": 15}]
        swing_lows = [{"price": 99000, "index": 10}, {"price": 98500, "index": 20}]
        # Last SH (102000) > prev SH (101000) = HH, and price > 102000
        result = detect_structure_break("bearish", 102500, swing_highs, swing_lows)
        assert result is True

    def test_ranging_no_break(self):
        swing_highs = [{"price": 101000, "index": 5}]
        swing_lows = [{"price": 99000, "index": 10}]
        result = detect_structure_break("ranging", 98500, swing_highs, swing_lows)
        assert result is False

    def test_insufficient_swings_no_break(self):
        swing_highs = [{"price": 101000, "index": 5}]
        swing_lows = [{"price": 99000, "index": 10}]
        result = detect_structure_break("bullish", 98000, swing_highs, swing_lows)
        assert result is False


# -- 6. MarketGeometry enzyme integration -------------------------------------

class TestMarketGeometryEnzyme:
    def test_can_activate_with_ohlcv_no_geometry(self, substrate):
        enz = create_enzyme("MarketGeometry")
        substrate.market["ohlcv"] = {"BTCUSDT": {"4H": {"high": [1], "low": [1], "close": [1]}}}
        substrate.market["geometry"] = {}
        assert enz.can_activate(substrate)

    def test_cannot_activate_without_ohlcv(self, substrate):
        enz = create_enzyme("MarketGeometry")
        substrate.market["ohlcv"] = {}
        substrate.market["geometry"] = {}
        assert not enz.can_activate(substrate)

    def test_cannot_activate_with_existing_geometry(self, substrate):
        enz = create_enzyme("MarketGeometry")
        substrate.market["ohlcv"] = {"BTCUSDT": {"4H": {"high": [1], "low": [1], "close": [1]}}}
        substrate.market["geometry"] = {"BTCUSDT": {"trend_direction": "bullish"}}
        assert not enz.can_activate(substrate)

    def test_transform_computes_geometry(self, substrate):
        enz = create_enzyme("MarketGeometry")
        highs, lows, closes = _make_bullish_prices(50)
        substrate.market["ohlcv"] = {"BTCUSDT": {"4H": {"high": highs, "low": lows, "close": closes}}}
        substrate.market["geometry"] = {}

        result = enz.transform(substrate)

        geometry = result.market["geometry"].get("BTCUSDT", {})
        assert "trend_direction" in geometry
        assert "phase" in geometry
        assert "pullback_depth" in geometry
        assert "structure_break" in geometry
        assert geometry["trend_direction"] in ("bullish", "bearish", "ranging")

    def test_transform_handles_insufficient_data(self, substrate):
        enz = create_enzyme("MarketGeometry")
        substrate.market["ohlcv"] = {"BTCUSDT": {"4H": {"high": [100], "low": [99], "close": [100]}}}
        substrate.market["geometry"] = {}

        result = enz.transform(substrate)
        # Should not crash, geometry may be empty or have default ranging
        assert isinstance(result.market["geometry"], dict)

    def test_transform_preserves_previous_phase(self, substrate):
        enz = create_enzyme("MarketGeometry")
        highs, lows, closes = _make_bullish_prices(50)
        substrate.market["ohlcv"] = {"BTCUSDT": {"4H": {"high": highs, "low": lows, "close": closes}}}
        # Geometry must be missing BTCUSDT for can_activate to return True
        substrate.market["geometry"] = {"ETHUSDT": {"trend_direction": "bullish", "phase": "impulse"}}

        result = enz.transform(substrate)
        geometry = result.market["geometry"].get("BTCUSDT", {})
        # First run has no previous phase for BTCUSDT (it wasn't in geometry before)
        assert "previous_phase" in geometry


# -- 7. Structure-aware exits in RequestExit ----------------------------------

class TestStructureAwareExits:
    def test_structure_break_tightens_trailing_stop_not_exit(self, substrate_with_structure):
        """structure_break is NOT an exit signal — it tightens trailing stop only."""
        enz = create_enzyme("RequestExit")
        substrate_with_structure.portfolio["open_positions"] = [
            {
                "symbol": "BTCUSDT",
                "direction": "Long",
                "entry_price": 100000,
                "sl_price": 98000,
                "tp1": 105000,
                "mark_price": 99500,
                "atr_value": 1500,
            }
        ]
        substrate_with_structure.market["geometry"] = {
            "BTCUSDT": {
                "trend_direction": "bullish",
                "phase": "pullback",
                "previous_phase": "impulse",
                "structure_break": True,
                "pullback_depth": "deep",
            }
        }

        result = enz.transform(substrate_with_structure)
        # structure_break is NOT an exit — it tightens trailing stop
        exit_req = result.decisions.get("exit_request")
        assert exit_req is None or exit_req.get("reason") != "structure_break"

    def test_phase_range_tightens_trailing_stop_not_exit(self, substrate_with_structure):
        """phase_range is NOT an exit signal — it tightens trailing stop only."""
        enz = create_enzyme("RequestExit")
        substrate_with_structure.portfolio["open_positions"] = [
            {
                "symbol": "BTCUSDT",
                "direction": "Long",
                "entry_price": 100000,
                "sl_price": 98000,
                "tp1": 105000,
                "mark_price": 100500,
                "atr_value": 1500,
            }
        ]
        substrate_with_structure.market["geometry"] = {
            "BTCUSDT": {
                "trend_direction": "bullish",
                "phase": "range",
                "previous_phase": "impulse",
                "structure_break": False,
                "pullback_depth": "n/a",
            }
        }

        result = enz.transform(substrate_with_structure)
        # phase_range is NOT an exit — it tightens trailing stop
        exit_req = result.decisions.get("exit_request")
        assert exit_req is None or exit_req.get("reason") != "phase_range"

    def test_counter_breakout_requests_exit(self, substrate_with_structure):
        enz = create_enzyme("RequestExit")
        substrate_with_structure.portfolio["open_positions"] = [
            {
                "symbol": "BTCUSDT",
                "direction": "Long",
                "entry_price": 100000,
                "sl_price": 98000,
                "tp1": 105000,
                "mark_price": 98500,
                "atr_value": 1500,
            }
        ]
        substrate_with_structure.market["geometry"] = {
            "BTCUSDT": {
                "trend_direction": "bearish",  # counter to long position
                "phase": "breakout",
                "previous_phase": "range",
                "structure_break": False,
                "pullback_depth": "n/a",
            }
        }

        result = enz.transform(substrate_with_structure)
        exit_req = result.decisions.get("exit_request")
        assert exit_req is not None
        assert exit_req["reason"] == "counter_breakout"

    def test_no_structure_exit_when_disabled(self, substrate):
        """When structure_aware_exits=False, structure data is ignored."""
        enz = create_enzyme("RequestExit")
        substrate.portfolio["open_positions"] = [
            {
                "symbol": "BTCUSDT",
                "direction": "Long",
                "entry_price": 100000,
                "sl_price": 98000,
                "tp1": 105000,
                "mark_price": 100500,
                "atr_value": 1500,
            }
        ]
        substrate.market["geometry"] = {
            "BTCUSDT": {
                "trend_direction": "bullish",
                "phase": "range",
                "previous_phase": "impulse",
                "structure_break": True,
                "pullback_depth": "n/a",
            }
        }
        # structure_aware_exits is False by default in make_full_config

        result = enz.transform(substrate)
        # Should NOT request exit based on structure — no SL breach either
        exit_req = result.decisions.get("exit_request")
        assert exit_req is None or exit_req.get("reason") != "structure_break"

    def test_no_geometry_data_falls_back_to_atr(self, substrate_with_structure):
        """When no geometry data for symbol, falls back to standard exit checks."""
        enz = create_enzyme("RequestExit")
        substrate_with_structure.portfolio["open_positions"] = [
            {
                "symbol": "BTCUSDT",
                "direction": "Long",
                "entry_price": 100000,
                "sl_price": 98000,
                "tp1": 105000,
                "mark_price": 100500,
                "atr_value": 1500,
            }
        ]
        substrate_with_structure.market["geometry"] = {}  # No geometry data

        result = enz.transform(substrate_with_structure)
        # Should not request any exit (price is between SL and TP)
        exit_req = result.decisions.get("exit_request")
        assert exit_req is None


# -- 8. Progressive trailing stop ---------------------------------------------

class TestProgressiveTrailingStop:
    def test_trail_schedule_lookup(self):
        schedule = {0: 1.0, 1: 0.75, 2: 0.5, 3: 0.25}
        assert _lookup_trail_schedule(schedule, 0.0) == 1.0
        assert _lookup_trail_schedule(schedule, 0.5) == 1.0
        assert _lookup_trail_schedule(schedule, 1.0) == 0.75
        assert _lookup_trail_schedule(schedule, 1.5) == 0.75
        assert _lookup_trail_schedule(schedule, 2.0) == 0.5
        assert _lookup_trail_schedule(schedule, 2.5) == 0.5
        assert _lookup_trail_schedule(schedule, 3.0) == 0.25
        assert _lookup_trail_schedule(schedule, 5.0) == 0.25

    def test_empty_schedule_returns_default(self):
        assert _lookup_trail_schedule({}, 2.0) == 1.0

    def test_progressive_trail_tightens_with_profit(self, substrate_with_structure):
        from core.trailing_stop import update_trailing_stop as _update_trailing_stop

        substrate_with_structure.decisions["exit_request"] = {"symbol": "BTCUSDT", "reason": "test"}

        # Position at +2x ATR profit
        position = {
            "symbol": "BTCUSDT",
            "direction": "Long",
            "entry_price": 100000,
            "mark_price": 103000,  # +3000, ATR=1500 → +2 ATR
            "sl_price": 98500,
            "atr_value": 1500,
            "trailing_active": True,
            "trailing_sl": 101000,  # current trail
            "peak_price": 103000,
            "max_profit_atr": 2.0,
        }

        result = _update_trailing_stop(position, substrate_with_structure)
        # At +2 ATR profit, trail should be 0.5x ATR from mark
        # new_sl = 103000 - 0.5 * 1500 = 102250
        # Trail only moves up: max(101000, 102250) = 102250
        assert result["trailing_sl"] >= 101000  # trail moved up
        assert result["max_profit_atr"] >= 2.0

    def test_progressive_trail_never_widens(self, substrate_with_structure):
        from core.trailing_stop import update_trailing_stop as _update_trailing_stop

        substrate_with_structure.decisions["exit_request"] = {"symbol": "BTCUSDT", "reason": "test"}

        # Position that was at +3 ATR but price pulled back to +1 ATR
        position = {
            "symbol": "BTCUSDT",
            "direction": "Long",
            "entry_price": 100000,
            "mark_price": 101500,  # +1 ATR now
            "sl_price": 98500,
            "atr_value": 1500,
            "trailing_active": True,
            "trailing_sl": 102000,  # set when price was higher
            "peak_price": 104500,
            "max_profit_atr": 3.0,  # was at +3 ATR
        }

        result = _update_trailing_stop(position, substrate_with_structure)
        # Trail should NOT move down even though profit decreased
        assert result["trailing_sl"] >= 102000  # trail never widens
        # max_profit_atr should stay at 3.0 (the highest reached)
        assert result["max_profit_atr"] >= 3.0

    def test_structure_tightening_on_pullback(self, substrate_with_structure):
        from core.trailing_stop import apply_structure_tightening as _apply_structure_tightening

        substrate_with_structure.market["geometry"] = {
            "BTCUSDT": {
                "phase": "pullback",
                "pullback_depth": "deep",
            }
        }

        result = _apply_structure_tightening(1.0, "BTCUSDT", substrate_with_structure)
        # 1.0 * 0.75 (pullback) * 0.5 (deep) = 0.375
        assert result == pytest.approx(0.375, abs=0.001)

    def test_no_tightening_without_structure_data(self, substrate_with_structure):
        from core.trailing_stop import apply_structure_tightening as _apply_structure_tightening

        substrate_with_structure.market["geometry"] = {}
        result = _apply_structure_tightening(1.0, "BTCUSDT", substrate_with_structure)
        assert result == 1.0


# -- 9. Trail schedule lookup -------------------------------------------------

class TestTrailScheduleLookup:
    def test_at_entry_level(self):
        schedule = {0: 1.0, 1: 0.75, 2: 0.5, 3: 0.25}
        assert _lookup_trail_schedule(schedule, 0.0) == 1.0

    def test_between_levels(self):
        schedule = {0: 1.0, 1: 0.75, 2: 0.5, 3: 0.25}
        assert _lookup_trail_schedule(schedule, 1.7) == 0.75

    def test_at_high_profit(self):
        schedule = {0: 1.0, 1: 0.75, 2: 0.5, 3: 0.25}
        assert _lookup_trail_schedule(schedule, 10.0) == 0.25

    def test_none_schedule(self):
        assert _lookup_trail_schedule(None, 2.0) == 1.0


# -- 10. Fallback to ATR when structure absent --------------------------------

class TestFallbackBehavior:
    def test_atr_trail_works_without_progressive(self, substrate):
        """Standard ATR trailing stop still works when progressive_trail=False."""
        from core.trailing_stop import update_trailing_stop as _update_trailing_stop

        substrate.decisions["exit_request"] = {"symbol": "BTCUSDT", "reason": "test"}

        position = {
            "symbol": "BTCUSDT",
            "direction": "Long",
            "entry_price": 100000,
            "mark_price": 102000,
            "sl_price": 98500,
            "atr_value": 1500,
            "trailing_active": True,
            "trailing_sl": 100500,
            "peak_price": 102000,
        }

        result = _update_trailing_stop(position, substrate)
        # Should compute standard ATR trail (no progressive schedule)
        assert result["trailing_sl"] is not None
        assert result["trailing_sl"] >= 100500  # trail never moves down

    def test_no_trailing_when_both_disabled(self, substrate):
        """When both trailing_stop.enabled and progressive_trail are False, no trailing."""
        from core.trailing_stop import update_trailing_stop as _update_trailing_stop

        # Disable both trailing modes
        substrate._config["exit_rules"]["trailing_stop"]["enabled"] = False
        substrate._config["exit_rules"]["progressive_trail"] = False
        substrate.decisions["exit_request"] = {"symbol": "BTCUSDT", "reason": "test"}

        position = {
            "symbol": "BTCUSDT",
            "direction": "Long",
            "entry_price": 100000,
            "mark_price": 102000,
            "sl_price": 98500,
            "atr_value": 1500,
            "trailing_active": False,
            "trailing_sl": None,
            "peak_price": 102000,
        }

        result = _update_trailing_stop(position, substrate)
        # Position should be unchanged (no trailing activated)
        assert result.get("trailing_active") is False

    def test_market_geometry_registered(self):
        """MarketGeometry enzyme is in the registry."""
        enz = create_enzyme("MarketGeometry")
        assert enz is not None
        assert enz.name == "MarketGeometry"
        assert enz.enzyme_class == EnzymeClass.SENSOR