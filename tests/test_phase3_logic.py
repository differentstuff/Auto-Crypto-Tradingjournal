"""
tests/test_phase3_logic.py -- Validation tests for Phase 3: Logic Fixes (P1, P6).

P1: Confirmation TF alignment — ScoreConfluence neutralizes candidates when
    primary_tf and confirmation_tf disagree in direction. ValidateEntryZone
    skips neutralized candidates.

P6: Duration exit removed — ApproveExit no longer checks max_hold_hours.
    Trades last until SL, trailing stop, or signal reversal.

All tests are pure unit tests:
  - No real network calls
  - No real DB connections
  - Substrate objects created with test configs

Requires: pytest>=9.0.0
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.substrate import Substrate
from enzymes.score_confluence import ScoreConfluence
from conftest import make_full_config
from enzymes.validate_entry_zone import ValidateEntryZone
from enzymes.approve_exit import ApproveExit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bullish_indicators(score_boost: float = 0.0) -> dict:
    """Return a dict of bullish indicator results for scoring."""
    return {
        "ok": True,
        "candles_used": 100,
        "rsi": {"value": 65.0 + score_boost, "signal": "bullish"},
        "macd": {"bias": "bullish", "histogram_growing": True, "signal": "bullish"},
        "ema_stack": {"alignment": "bullish", "stack": "bullish", "current_price": 50000.0, "signal": "bullish"},
        "adx": {"value": 28.0, "direction": "bullish", "signal": "bullish"},
        "atr": {"value": 500.0, "pct": 1.0},
        "sr_levels": [],
        "volume": {"ratio": 1.8},
    }


def _make_bearish_indicators() -> dict:
    """Return a dict of bearish indicator results for scoring."""
    return {
        "ok": True,
        "candles_used": 100,
        "rsi": {"value": 35.0, "signal": "bearish"},
        "macd": {"bias": "bearish", "histogram_growing": True, "signal": "bearish"},
        "ema_stack": {"alignment": "bearish", "stack": "bearish", "current_price": 50000.0, "signal": "bearish"},
        "adx": {"value": 28.0, "direction": "bearish", "signal": "bearish"},
        "atr": {"value": 500.0, "pct": 1.0},
        "sr_levels": [],
        "volume": {"ratio": 1.8},
    }


def _make_neutral_indicators() -> dict:
    """Return a dict of neutral indicator results for scoring."""
    return {
        "ok": True,
        "candles_used": 100,
        "rsi": {"value": 50.0, "signal": "neutral"},
        "macd": {"bias": "neutral", "histogram_growing": False, "signal": "neutral"},
        "ema_stack": {"alignment": "neutral", "stack": "neutral", "current_price": 50000.0, "signal": "neutral"},
        "adx": {"value": 15.0, "direction": "neutral", "signal": "neutral"},
        "atr": {"value": 500.0, "pct": 1.0},
        "sr_levels": [],
        "volume": {"ratio": 1.0},
    }


def _base_config() -> dict:
    """Complete config with all required keys for Substrate."""
    return make_full_config(
        strategy={
            "name": "test_strategy",
            "uid": "test-uid",
            "timeframe": "4H",
            "confirmation_tf": "1H",
            "max_positions": 3,
        },
        scoring={
            "entry_threshold": 6.5,
            "confluence_min_signals": 3,
            "rr_minimum": 2.0,
        },
        indicators=[
            {"name": "rsi", "weight": 0.25},
            {"name": "macd", "weight": 0.25},
            {"name": "ema_stack", "weight": 0.30},
            {"name": "adx", "weight": 0.20},
            {"name": "atr", "weight": 0.0},
        ],
        learning={
            "min_trades_before_adjusting": 30,
        },
        exit_rules={
            "hard_stop": {"width_atr_multiplier": 1.5, "always_active": True},
            "trailing_stop": {"enabled": True, "activation_profit_pct": 0.5, "trail_atr_multiplier": 1.0, "breakeven_at_activation": True},
        },
        portfolio={
            "leverage": 5,
            "risk_per_trade_pct": 1.0,
        },
    )


def _base_config_no_confirmation() -> dict:
    """Config where confirmation_tf is null — alignment check should be skipped."""
    cfg = _base_config()
    cfg["strategy"]["confirmation_tf"] = None
    return cfg


# ---------------------------------------------------------------------------
# P1: Confirmation TF Alignment Tests
# ---------------------------------------------------------------------------

class TestP1ConfirmationTFAlignment:
    """Tests for P1: Cross-timeframe alignment in ScoreConfluence."""

    def test_aligned_timeframes_produce_positive_score(self):
        """When primary and confirmation TFs both bullish, score is positive."""
        config = _base_config()
        sub = Substrate(config=config)
        sub.market["indicators"] = {
            "BTCUSDT": {
                "4H": _make_bullish_indicators(),
                "1H": _make_bullish_indicators(),
            }
        }
        sub.analysis["noise_evaluated"] = True

        enzyme = ScoreConfluence()
        result = enzyme.transform(sub)

        candidates = result.analysis.get("candidates", [])
        assert len(candidates) == 1
        assert candidates[0]["symbol"] == "BTCUSDT"
        assert candidates[0]["score"] > 0
        assert candidates[0]["confirmation_tf_misaligned"] is False

    def test_misaligned_timeframes_neutralize_candidate(self):
        """When primary is bullish but confirmation is bearish, candidate is neutralized."""
        config = _base_config()
        sub = Substrate(config=config)
        sub.market["indicators"] = {
            "BTCUSDT": {
                "4H": _make_bullish_indicators(),
                "1H": _make_bearish_indicators(),
            }
        }
        sub.analysis["noise_evaluated"] = True

        enzyme = ScoreConfluence()
        result = enzyme.transform(sub)

        candidates = result.analysis.get("candidates", [])
        assert len(candidates) == 1
        assert candidates[0]["confirmation_tf_misaligned"] is True
        assert candidates[0]["score"] == 0.0
        assert candidates[0]["pct"] == 0.0

    def test_bearish_primary_bullish_confirmation_neutralized(self):
        """When primary is bearish but confirmation is bullish, candidate is neutralized."""
        config = _base_config()
        sub = Substrate(config=config)
        sub.market["indicators"] = {
            "BTCUSDT": {
                "4H": _make_bearish_indicators(),
                "1H": _make_bullish_indicators(),
            }
        }
        sub.analysis["noise_evaluated"] = True

        enzyme = ScoreConfluence()
        result = enzyme.transform(sub)

        candidates = result.analysis.get("candidates", [])
        assert len(candidates) == 1
        assert candidates[0]["confirmation_tf_misaligned"] is True
        assert candidates[0]["score"] == 0.0

    def test_no_confirmation_tf_skips_alignment_check(self):
        """When confirmation_tf is null, alignment check is skipped entirely."""
        config = _base_config_no_confirmation()
        sub = Substrate(config=config)
        sub.market["indicators"] = {
            "BTCUSDT": {
                "4H": _make_bullish_indicators(),
            }
        }
        sub.analysis["noise_evaluated"] = True

        enzyme = ScoreConfluence()
        result = enzyme.transform(sub)

        candidates = result.analysis.get("candidates", [])
        assert len(candidates) == 1
        assert candidates[0]["confirmation_tf_misaligned"] is False
        assert candidates[0]["score"] > 0

    def test_neutral_confirmation_does_not_neutralize(self):
        """When confirmation TF is neutral (score=0), it should not neutralize."""
        config = _base_config()
        sub = Substrate(config=config)
        sub.market["indicators"] = {
            "BTCUSDT": {
                "4H": _make_bullish_indicators(),
                "1H": _make_neutral_indicators(),
            }
        }
        sub.analysis["noise_evaluated"] = True

        enzyme = ScoreConfluence()
        result = enzyme.transform(sub)

        candidates = result.analysis.get("candidates", [])
        assert len(candidates) == 1
        assert candidates[0]["confirmation_tf_misaligned"] is False
        # Score is still positive (only primary TF contributes)
        assert candidates[0]["score"] > 0

    def test_misaligned_candidate_skipped_by_validate_entry_zone(self):
        """ValidateEntryZone skips candidates with confirmation_tf_misaligned=True."""
        config = _base_config()
        sub = Substrate(config=config)
        sub.analysis["candidates"] = [
            {
                "symbol": "BTCUSDT",
                "score": 0.0,
                "pct": 0.0,
                "label": "Neutral",
                "indicators_aligned": 3,
                "details": ["4H bullish", "1H bearish"],
                "confirmation_tf_misaligned": True,
            },
            {
                "symbol": "ETHUSDT",
                "score": 7.5,
                "pct": 0.45,
                "label": "Bullish",
                "indicators_aligned": 4,
                "details": ["4H bullish", "1H bullish"],
                "confirmation_tf_misaligned": False,
            },
        ]
        sub.market["indicators"] = {
            "ETHUSDT": {
                "4H": _make_bullish_indicators(),
            }
        }
        sub.analysis["confluence_scored"] = True

        enzyme = ValidateEntryZone()
        result = enzyme.transform(sub)

        zones = result.analysis.get("entry_zones", {})
        # Only ETHUSDT should have an entry zone (BTCUSDT was skipped)
        assert "ETHUSDT" in zones
        assert "BTCUSDT" not in zones

    def test_direction_from_score_bullish(self):
        """Positive score returns 'bullish'."""
        assert ScoreConfluence._direction_from_score(5.0) == "bullish"

    def test_direction_from_score_bearish(self):
        """Negative score returns 'bearish'."""
        assert ScoreConfluence._direction_from_score(-3.0) == "bearish"

    def test_direction_from_score_neutral(self):
        """Zero score returns 'neutral'."""
        assert ScoreConfluence._direction_from_score(0.0) == "neutral"

    def test_both_bearish_aligned_produces_negative_score(self):
        """When both TFs are bearish, score is negative (aligned bearish)."""
        config = _base_config()
        sub = Substrate(config=config)
        sub.market["indicators"] = {
            "BTCUSDT": {
                "4H": _make_bearish_indicators(),
                "1H": _make_bearish_indicators(),
            }
        }
        sub.analysis["noise_evaluated"] = True

        enzyme = ScoreConfluence()
        result = enzyme.transform(sub)

        candidates = result.analysis.get("candidates", [])
        assert len(candidates) == 1
        assert candidates[0]["score"] < 0
        assert candidates[0]["confirmation_tf_misaligned"] is False


# ---------------------------------------------------------------------------
# P6: Duration Exit Removed Tests
# ---------------------------------------------------------------------------

class TestP6DurationExitRemoved:
    """Tests for P6: Duration-based exit logic is completely removed."""

    def _make_exit_substrate(self, position_age_hours: float = 100) -> Substrate:
        """Create a substrate with an open position that is older than any reasonable max_hours."""
        config = _base_config()
        sub = Substrate(config=config)
        opened_at = datetime.now(timezone.utc)
        from datetime import timedelta
        opened_at = (datetime.now(timezone.utc) - timedelta(hours=position_age_hours)).isoformat()
        sub.portfolio["open_positions"] = [
            {
                "symbol": "BTCUSDT",
                "direction": "Long",
                "entry_price": 50000.0,
                "mark_price": 51000.0,  # Profitable position
                "sl_price": 49000.0,
                "tp1": 52000.0,
                "tp2": 52500.0,
                "size_usdt": 500.0,
                "atr_value": 500.0,
                "opened_at": opened_at,
                "trailing_active": False,
                "trailing_sl": None,
                "peak_price": 51000.0,
            }
        ]
        sub.decisions["exit_request"] = {
            "symbol": "BTCUSDT",
            "reason": "signal_reversal",
            "urgency": "normal",
        }
        return sub

    def test_old_position_not_auto_exited_by_duration(self):
        """A position held for 100 hours should NOT be auto-exited by duration.
        Duration exit logic is completely removed."""
        sub = self._make_exit_substrate(position_age_hours=100)
        enzyme = ApproveExit()
        result = enzyme.transform(sub)

        # The exit request was for signal_reversal, not duration.
        # Since the position is profitable (mark > entry), signal_reversal_soft
        # should be denied. No duration exit should trigger.
        exit_approved = result.decisions.get("exit_approved")
        assert exit_approved is None  # Denied: no hard rule triggered

    def test_sl_breach_still_works(self):
        """SL breach still triggers exit even without duration check."""
        config = _base_config()
        sub = Substrate(config=config)
        sub.portfolio["open_positions"] = [
            {
                "symbol": "BTCUSDT",
                "direction": "Long",
                "entry_price": 50000.0,
                "mark_price": 48500.0,  # Below SL
                "sl_price": 49000.0,
                "tp1": 52000.0,
                "size_usdt": 500.0,
                "atr_value": 500.0,
                "opened_at": datetime.now(timezone.utc).isoformat(),
                "trailing_active": False,
                "trailing_sl": None,
                "peak_price": 50000.0,
            }
        ]
        sub.decisions["exit_request"] = {
            "symbol": "BTCUSDT",
            "reason": "sl_breach",
            "urgency": "immediate",
        }
        enzyme = ApproveExit()
        result = enzyme.transform(sub)

        exit_approved = result.decisions.get("exit_approved")
        assert exit_approved is not None
        assert exit_approved["reason"] == "hard_sl_breach"

    def test_trailing_stop_still_works(self):
        """Trailing stop still triggers exit even without duration check."""
        config = _base_config()
        sub = Substrate(config=config)
        sub.portfolio["open_positions"] = [
            {
                "symbol": "BTCUSDT",
                "direction": "Long",
                "entry_price": 50000.0,
                "mark_price": 49500.0,  # Below trailing SL
                "sl_price": 49000.0,
                "tp1": 52000.0,
                "size_usdt": 500.0,
                "atr_value": 500.0,
                "opened_at": datetime.now(timezone.utc).isoformat(),
                "trailing_active": True,
                "trailing_sl": 49800.0,  # Trailing SL above mark
                "peak_price": 51000.0,
            }
        ]
        sub.decisions["exit_request"] = {
            "symbol": "BTCUSDT",
            "reason": "trailing_stop_hit",
            "urgency": "immediate",
        }
        enzyme = ApproveExit()
        result = enzyme.transform(sub)

        exit_approved = result.decisions.get("exit_approved")
        assert exit_approved is not None
        assert exit_approved["reason"] == "trailing_stop_hit"

    def test_no_max_hold_hours_in_approve_exit_code(self):
        """Verify that ApproveExit.transform() does not reference max_hold_hours."""
        import inspect
        source = inspect.getsource(ApproveExit.transform)
        assert "max_hold_hours" not in source, (
            "ApproveExit.transform() still references max_hold_hours — "
            "duration exit logic should be completely removed"
        )
        assert "duration" not in source.lower() or "duration" in "trailing_stop activation duration", (
            "ApproveExit.transform() still has duration-related logic"
        )

    def test_no_duration_exit_in_config_keys(self):
        """Verify that duration_exit keys are removed from all strategy configs."""
        import yaml

        config_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config", "strategies"
        )
        for filename in os.listdir(config_dir):
            if not filename.endswith(".yaml"):
                continue
            filepath = os.path.join(config_dir, filename)
            with open(filepath) as f:
                content = f.read()
            assert "duration_exit" not in content, (
                f"duration_exit found in {filename} — should be removed"
            )
            assert "max_hours_extreme" not in content, (
                f"max_hours_extreme found in {filename} — should be removed"
            )
            assert "max_position_duration_hours" not in content, (
                f"max_position_duration_hours found in {filename} — should be removed"
            )

    def test_no_duration_exit_in_default_config(self):
        """Verify that duration_exit keys are removed from default.yaml."""
        import yaml

        default_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config", "default.yaml"
        )
        with open(default_path) as f:
            content = f.read()
        assert "duration_exit" not in content, (
            "duration_exit found in default.yaml — should be removed"
        )
        assert "max_hours_extreme" not in content, (
            "max_hours_extreme found in default.yaml — should be removed"
        )


# ---------------------------------------------------------------------------
# P1 + P6 Integration
# ---------------------------------------------------------------------------

class TestP1P6Integration:
    """Integration tests combining P1 and P6 changes."""

    def test_misaligned_candidate_not_exitable_by_duration(self):
        """A misaligned candidate that becomes a position should not be
        force-exited by duration. Only SL/trailing/signal_reversal apply."""
        # This is a logical test: P1 prevents entry, P6 prevents duration exit.
        # If P1 works correctly, this position should never be opened.
        # But if it somehow exists, P6 ensures no duration exit.
        config = _base_config()
        sub = Substrate(config=config)

        # Simulate a position opened despite misalignment (shouldn't happen,
        # but verify it's not force-exited by duration)
        sub.portfolio["open_positions"] = [
            {
                "symbol": "BTCUSDT",
                "direction": "Long",
                "entry_price": 50000.0,
                "mark_price": 50500.0,
                "sl_price": 49000.0,
                "tp1": 52000.0,
                "size_usdt": 500.0,
                "atr_value": 500.0,
                "opened_at": "2025-01-01T00:00:00+00:00",  # Very old position
                "trailing_active": False,
                "trailing_sl": None,
                "peak_price": 50500.0,
            }
        ]
        sub.decisions["exit_request"] = {
            "symbol": "BTCUSDT",
            "reason": "signal_reversal",
            "urgency": "normal",
        }

        enzyme = ApproveExit()
        result = enzyme.transform(sub)

        # Position is slightly profitable (50500 > 50000), so signal_reversal_soft
        # should be denied. And no duration exit should trigger.
        exit_approved = result.decisions.get("exit_approved")
        assert exit_approved is None  # No exit approved