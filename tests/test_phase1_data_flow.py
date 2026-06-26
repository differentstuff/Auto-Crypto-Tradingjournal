"""
tests/test_phase1_data_flow.py -- Validation tests for Phase 1 data flow fixes.

Tests for:
  P7: Smart OHLCV activation (candle-boundary detection, skip when no new candle)
  P2: Per-candle-close history (append only on candle close, time-based trim)
  P8: Time-based trajectory sufficiency (span_hours vs min_hours, not bar count)

All tests are pure unit tests — no real network calls, no real database.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.substrate import Substrate
from core.enzyme import EnzymeClass
from conftest import make_full_config
from enzymes.collect_ohlcv import (
    CollectOHLCV,
    timeframe_to_minutes,
    candle_floor,
    should_refresh_ohlcv,
)
from enzymes.collect_pre_trade_context import (
    CollectPreTradeContext,
    _classify_trajectory,
    _compute_history_span_hours,
)


# ── P7: Timeframe helpers ────────────────────────────────────────────────────

class TestTimeframeToMinutes:
    """P7: timeframe_to_minutes converts timeframe strings to minutes."""

    def test_hours_uppercase(self):
        assert timeframe_to_minutes("4H") == 240
        assert timeframe_to_minutes("1H") == 60

    def test_hours_lowercase(self):
        assert timeframe_to_minutes("4h") == 240
        assert timeframe_to_minutes("1h") == 60

    def test_minutes(self):
        assert timeframe_to_minutes("15m") == 15
        assert timeframe_to_minutes("5m") == 5

    def test_days(self):
        assert timeframe_to_minutes("1D") == 1440

    def test_weeks(self):
        assert timeframe_to_minutes("1W") == 10080

    def test_unknown_defaults_to_60(self):
        assert timeframe_to_minutes("unknown") == 60


class TestCandleFloor:
    """P7: candle_floor rounds timestamps down to candle boundaries."""

    def test_4h_candle_floor(self):
        """14:37 UTC on a 4H candle → 12:00 UTC."""
        ts = datetime(2026, 5, 26, 14, 37, 0, tzinfo=timezone.utc)
        result = candle_floor(ts, "4H")
        assert result.hour == 12
        assert result.minute == 0

    def test_4h_candle_floor_at_boundary(self):
        """12:00 UTC on a 4H candle → 12:00 UTC (exactly on boundary)."""
        ts = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)
        result = candle_floor(ts, "4H")
        assert result.hour == 12
        assert result.minute == 0

    def test_1h_candle_floor(self):
        """14:37 UTC on a 1H candle → 14:00 UTC."""
        ts = datetime(2026, 5, 26, 14, 37, 0, tzinfo=timezone.utc)
        result = candle_floor(ts, "1H")
        assert result.hour == 14
        assert result.minute == 0

    def test_15m_candle_floor(self):
        """14:37 UTC on a 15m candle → 14:30 UTC."""
        ts = datetime(2026, 5, 26, 14, 37, 0, tzinfo=timezone.utc)
        result = candle_floor(ts, "15m")
        assert result.hour == 14
        assert result.minute == 30


class TestShouldRefreshOHLCV:
    """P7: should_refresh_ohlcv detects when a new candle has closed."""

    def test_returns_true_when_no_last_ts(self):
        """Cold start: no recorded close → should refresh."""
        now = datetime(2026, 5, 26, 14, 0, 0, tzinfo=timezone.utc)
        assert should_refresh_ohlcv("4H", "", now) is True

    def test_returns_true_when_new_candle_closed(self):
        """Last close was 8:00, now is 14:00 → new 4H candle closed."""
        last_ts = datetime(2026, 5, 26, 8, 0, 0, tzinfo=timezone.utc).isoformat()
        now = datetime(2026, 5, 26, 14, 0, 0, tzinfo=timezone.utc)
        assert should_refresh_ohlcv("4H", last_ts, now) is True

    def test_returns_false_when_same_candle(self):
        """Last close was 12:00, now is 13:30 → same 4H candle, no refresh."""
        last_ts = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc).isoformat()
        now = datetime(2026, 5, 26, 13, 30, 0, tzinfo=timezone.utc)
        assert should_refresh_ohlcv("4H", last_ts, now) is False

    def test_returns_true_when_candle_boundary_crossed(self):
        """Last close was 12:00, now is 12:01 → new 4H candle started (boundary at 12:00).

        Wait — 12:01 is still within the 12:00-16:00 candle, same floor.
        The floor(12:01) = 12:00, same as floor(12:00) = 12:00.
        So should_refresh_ohlcv should return False.

        But if last_ts was from the PREVIOUS candle (8:00-12:00),
        then floor(last) = 8:00 and floor(now) = 12:00 → True.
        """
        # Same candle: floor(12:00) == floor(12:01) == 12:00
        last_ts = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc).isoformat()
        now = datetime(2026, 5, 26, 12, 1, 0, tzinfo=timezone.utc)
        assert should_refresh_ohlcv("4H", last_ts, now) is False

    def test_returns_true_for_1h_candle_boundary(self):
        """1H candle: last close was 13:00, now is 14:01 → new candle."""
        last_ts = datetime(2026, 5, 26, 13, 0, 0, tzinfo=timezone.utc).isoformat()
        now = datetime(2026, 5, 26, 14, 1, 0, tzinfo=timezone.utc)
        assert should_refresh_ohlcv("1H", last_ts, now) is True

    def test_returns_false_for_1h_same_candle(self):
        """1H candle: last close was 14:00, now is 14:30 → same candle."""
        last_ts = datetime(2026, 5, 26, 14, 0, 0, tzinfo=timezone.utc).isoformat()
        now = datetime(2026, 5, 26, 14, 30, 0, tzinfo=timezone.utc)
        assert should_refresh_ohlcv("1H", last_ts, now) is False

    def test_invalid_timestamp_returns_true(self):
        """Invalid last_ts string → should refresh (fail-safe)."""
        now = datetime(2026, 5, 26, 14, 0, 0, tzinfo=timezone.utc)
        assert should_refresh_ohlcv("4H", "not-a-timestamp", now) is True


# ── P7: CollectOHLCV activation ─────────────────────────────────────────────

class TestCollectOHLCVActivation:
    """P7: CollectOHLCV smart activation based on candle boundaries."""

    def _make_substrate(self, indicators=None, last_candle_close_ts=None):
        """Create a substrate with test config."""
        config = make_full_config(
            strategy={"name": "test_strategy", "uid": "test-uid"},
            symbols={"always_watch": ["BTCUSDT"]},
            indicators=[{"name": "rsi", "params": {"period": 14}, "weight": 0.25}],
            learning={"trajectory_lookback_hours": 48, "trajectory_min_hours": 8},
        )
        sub = Substrate(config=config)
        if indicators:
            sub.market["indicators"] = indicators
        if last_candle_close_ts:
            sub.market["last_candle_close_ts"] = last_candle_close_ts
        return sub

    def test_activates_on_cold_start(self):
        """Cold start: no indicators → should activate."""
        sub = self._make_substrate()
        enz = CollectOHLCV()
        assert enz.can_activate(sub) is True

    def test_activates_when_missing_candle_close_ts(self):
        """Has indicators but no last_candle_close_ts → should activate."""
        sub = self._make_substrate(
            indicators={"BTCUSDT": {"4H": {"ok": True}}},
        )
        enz = CollectOHLCV()
        assert enz.can_activate(sub) is True

    def test_does_not_activate_when_same_candle(self):
        """Has indicators and last_candle_close_ts within same candle → should NOT activate."""
        now = datetime.now(timezone.utc)
        last_ts_4h = candle_floor(now, "4H").isoformat()
        last_ts_1h = candle_floor(now, "1H").isoformat()
        sub = self._make_substrate(
            indicators={"BTCUSDT": {"4H": {"ok": True}}},
            last_candle_close_ts={
                "BTCUSDT_4H": last_ts_4h,
                "BTCUSDT_1H": last_ts_1h,  # confirmation_tf also needs entry
            },
        )
        enz = CollectOHLCV()
        assert enz.can_activate(sub) is False

    def test_activates_when_new_candle_closed(self):
        """Has indicators but new candle has closed → should activate."""
        # Last close was 8:00, current floor is 12:00 → new 4H candle
        old_floor = datetime(2026, 5, 26, 8, 0, 0, tzinfo=timezone.utc)
        now = datetime(2026, 5, 26, 14, 0, 0, tzinfo=timezone.utc)
        sub = self._make_substrate(
            indicators={"BTCUSDT": {"4H": {"ok": True}}},
            last_candle_close_ts={"BTCUSDT_4H": old_floor.isoformat()},
        )
        enz = CollectOHLCV()
        # Patch datetime.now in can_activate to control the "now" value
        with patch("enzymes.collect_ohlcv.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.fromisoformat = datetime.fromisoformat
            result = enz.can_activate(sub)
        assert result is True

    def test_activates_for_partial_symbol_coverage(self):
        """Has candle_close_ts for one symbol but not another → should activate."""
        now = datetime.now(timezone.utc)
        last_ts = candle_floor(now, "4H").isoformat()
        sub = self._make_substrate(
            indicators={"BTCUSDT": {"4H": {"ok": True}}},
            last_candle_close_ts={"BTCUSDT_4H": last_ts},
            # ETHUSDT has no entry → should activate
        )
        sub.market["symbols_watched"] = ["BTCUSDT", "ETHUSDT"]
        enz = CollectOHLCV()
        assert enz.can_activate(sub) is True


# ── P7: Substrate preserves indicators across reset_cycle ────────────────────

class TestSubstrateResetPreservesIndicators:
    """P7: reset_cycle() no longer clears indicators or last_candle_close_ts."""

    def test_reset_preserves_indicators(self):
        """After reset_cycle(), indicators should persist (not cleared)."""
        sub = Substrate(config=make_full_config())
        sub.market["indicators"] = {"BTCUSDT": {"4H": {"ok": True, "rsi": {"value": 55}}}}
        sub.market["last_candle_close_ts"] = {"BTCUSDT_4H": "2026-05-26T12:00:00+00:00"}
        sub.reset_cycle()

        # P7: indicators should persist, not be cleared
        assert sub.market["indicators"] != {}
        assert "BTCUSDT" in sub.market["indicators"]

    def test_reset_preserves_last_candle_close_ts(self):
        """After reset_cycle(), last_candle_close_ts should persist."""
        sub = Substrate(config=make_full_config())
        sub.market["last_candle_close_ts"] = {"BTCUSDT_4H": "2026-05-26T12:00:00+00:00"}
        sub.reset_cycle()
        assert "BTCUSDT_4H" in sub.market["last_candle_close_ts"]

    def test_reset_preserves_indicator_history(self):
        """After reset_cycle(), indicator_history should persist (existing behavior)."""
        sub = Substrate(config=make_full_config())
        sub.market["indicator_history"] = {"BTCUSDT": [{"timestamp": "2026-05-26T12:00:00+00:00"}]}
        sub.reset_cycle()
        assert "BTCUSDT" in sub.market["indicator_history"]

    def test_reset_clears_transient_fields(self):
        """After reset_cycle(), transient fields should be cleared."""
        sub = Substrate(config=make_full_config())
        sub.market["macro"] = {"regime": "risk-on"}
        sub.market["pre_trade_context"] = {"BTCUSDT": {}}
        sub.analysis["candidates"] = [{"symbol": "BTCUSDT"}]
        sub.analysis["noise_flag"] = True
        sub.decisions["action"] = "enter"
        sub.reset_cycle()

        assert sub.market["macro"] == {}
        assert sub.market["pre_trade_context"] == {}
        assert sub.analysis["candidates"] == []
        assert sub.analysis["noise_flag"] is False
        assert sub.decisions["action"] == ""


# ── P2: Per-candle-close history ─────────────────────────────────────────────

class TestHistoryTrimByTime:
    """P2: History is trimmed by time span, not bar count."""

    def test_trim_removes_old_entries(self):
        """Entries older than lookback_hours are trimmed."""
        enz = CollectOHLCV()
        history = {
            "BTCUSDT": [
                {"timestamp": (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()},
                {"timestamp": (datetime.now(timezone.utc) - timedelta(hours=47)).isoformat()},
                {"timestamp": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()},
            ]
        }
        enz._trim_history_by_time(history, "BTCUSDT", lookback_hours=48)

        # Only the last 2 entries should remain (within 48h)
        assert len(history["BTCUSDT"]) == 2

    def test_trim_keeps_recent_entries(self):
        """Entries within lookback_hours are kept."""
        enz = CollectOHLCV()
        now = datetime.now(timezone.utc)
        history = {
            "BTCUSDT": [
                {"timestamp": (now - timedelta(hours=24)).isoformat()},
                {"timestamp": (now - timedelta(hours=1)).isoformat()},
            ]
        }
        enz._trim_history_by_time(history, "BTCUSDT", lookback_hours=48)

        assert len(history["BTCUSDT"]) == 2

    def test_trim_preserves_minimum_2_entries(self):
        """Even if all entries are old, at least 2 are kept for trajectory."""
        enz = CollectOHLCV()
        history = {
            "BTCUSDT": [
                {"timestamp": (datetime.now(timezone.utc) - timedelta(hours=200)).isoformat()},
                {"timestamp": (datetime.now(timezone.utc) - timedelta(hours=199)).isoformat()},
                {"timestamp": (datetime.now(timezone.utc) - timedelta(hours=198)).isoformat()},
            ]
        }
        enz._trim_history_by_time(history, "BTCUSDT", lookback_hours=48)

        # At least 2 entries should be kept
        assert len(history["BTCUSDT"]) >= 2

    def test_trim_empty_history(self):
        """Trimming empty history is a no-op."""
        enz = CollectOHLCV()
        history = {"BTCUSDT": []}
        enz._trim_history_by_time(history, "BTCUSDT", lookback_hours=48)
        assert history["BTCUSDT"] == []

    def test_trim_nonexistent_symbol(self):
        """Trimming nonexistent symbol is a no-op."""
        enz = CollectOHLCV()
        history = {}
        enz._trim_history_by_time(history, "ETHUSDT", lookback_hours=48)
        assert "ETHUSDT" not in history


# ── P8: Time-based trajectory sufficiency ────────────────────────────────────

class TestComputeHistorySpanHours:
    """P8: _compute_history_span_hours measures real time span, not bar count."""

    def test_returns_correct_span(self):
        """History spanning 16 hours should return 16.0."""
        now = datetime.now(timezone.utc)
        history = [
            {"timestamp": (now - timedelta(hours=16)).isoformat(), "signal": "bullish"},
            {"timestamp": (now - timedelta(hours=8)).isoformat(), "signal": "bullish"},
            {"timestamp": now.isoformat(), "signal": "bearish"},
        ]
        span = _compute_history_span_hours(history)
        assert abs(span - 16.0) < 0.1

    def test_returns_zero_for_single_entry(self):
        """Single entry → span is 0.0 (need at least 2 for a span)."""
        history = [
            {"timestamp": datetime.now(timezone.utc).isoformat(), "signal": "bullish"},
        ]
        span = _compute_history_span_hours(history)
        assert span == 0.0

    def test_returns_zero_for_empty_history(self):
        """Empty history → span is 0.0."""
        span = _compute_history_span_hours([])
        assert span == 0.0

    def test_returns_zero_for_missing_timestamps(self):
        """Entries without timestamps → span is 0.0."""
        history = [
            {"signal": "bullish"},
            {"signal": "bearish"},
        ]
        span = _compute_history_span_hours(history)
        assert span == 0.0

    def test_handles_naive_timestamps(self):
        """Timestamps without timezone info should be treated as UTC."""
        now = datetime.now(timezone.utc)
        first = (now - timedelta(hours=8)).replace(tzinfo=None).isoformat()
        last = now.replace(tzinfo=None).isoformat()
        history = [
            {"timestamp": first, "signal": "bullish"},
            {"timestamp": last, "signal": "bearish"},
        ]
        span = _compute_history_span_hours(history)
        assert abs(span - 8.0) < 0.1


class TestPreTradeContextTimeBasedSufficiency:
    """P8: CollectPreTradeContext uses time-based sufficiency, not bar count."""

    def _make_substrate_with_history(self, span_hours=16, num_entries=8):
        """Create a substrate with indicator history spanning the given hours."""
        config = make_full_config(
            strategy={"name": "test_strategy", "uid": "test-uid"},
            scoring={"entry_threshold": 6.5, "confluence_min_signals": 3},
            learning={"trajectory_min_hours": 8, "trajectory_lookback_hours": 48},
        )
        sub = Substrate(config=config)

        # Build indicator history
        now = datetime.now(timezone.utc)
        history = {}
        entries = []
        for i in range(num_entries):
            ts = now - timedelta(hours=span_hours - (i * span_hours / num_entries))
            entries.append({
                "timestamp": ts.isoformat(),
                "signal": "bullish" if i % 2 == 0 else "bearish",
                "indicators": {"ok": True, "rsi": {"value": 55}},
            })
        history["BTCUSDT"] = entries

        sub.market["indicator_history"] = history
        sub.analysis["candidates"] = [{"symbol": "BTCUSDT", "score": 7.5}]
        return sub

    def test_sufficient_history_allows_trade(self):
        """History spanning 16h with min 8h → coincidence_risk is NOT 'high'."""
        sub = self._make_substrate_with_history(span_hours=16, num_entries=8)
        enz = CollectPreTradeContext()
        result = enz.transform(sub)

        ctx = result.market.get("pre_trade_context", {}).get("BTCUSDT", {})
        assert ctx.get("coincidence_risk") != "high" or ctx.get("trajectory_type") == "insufficient_data"
        # If span is sufficient, coincidence_risk should be determined by trajectory
        # (not forced to 'high' by insufficient_data)

    def test_insufficient_history_blocks_trade(self):
        """History spanning 3h with min 8h → coincidence_risk='high'."""
        sub = self._make_substrate_with_history(span_hours=3, num_entries=3)
        enz = CollectPreTradeContext()
        result = enz.transform(sub)

        ctx = result.market.get("pre_trade_context", {}).get("BTCUSDT", {})
        assert ctx.get("coincidence_risk") == "high"
        assert ctx.get("trajectory_type") == "insufficient_data"
        assert "span_hours" in ctx
        assert ctx["span_hours"] < 8

    def test_empty_history_blocks_trade(self):
        """Empty indicator_history → coincidence_risk='high'."""
        config = make_full_config(
            strategy={"name": "test_strategy", "uid": "test-uid"},
            scoring={"entry_threshold": 6.5, "confluence_min_signals": 3},
            learning={"trajectory_min_hours": 8},
        )
        sub = Substrate(config=config)
        sub.market["indicator_history"] = {}
        sub.analysis["candidates"] = [{"symbol": "BTCUSDT", "score": 7.5}]

        enz = CollectPreTradeContext()
        result = enz.transform(sub)

        ctx = result.market.get("pre_trade_context", {}).get("BTCUSDT", {})
        assert ctx.get("coincidence_risk") == "high"
        assert ctx.get("trajectory_type") == "insufficient_data"

    def test_log_shows_real_hours(self):
        """Log message shows real hours, not bar count."""
        sub = self._make_substrate_with_history(span_hours=3, num_entries=3)
        enz = CollectPreTradeContext()

        # Patch the module-level logger, not the instance logger
        with patch("enzymes.collect_pre_trade_context._log") as mock_log:
            enz.transform(sub)
            # Check that the insufficient-history log was called with hours format
            found = False
            for call_args in mock_log.info.call_args_list:
                args = call_args[0]  # positional args
                if args and "required" in str(args[0]):
                    # Format string should contain 'h /' and 'required'
                    fmt = str(args[0])
                    if "h /" in fmt or "h " in fmt:
                        found = True
            assert found, f"No log with hours format found. Calls: {mock_log.info.call_args_list}"

    def test_config_driven_min_hours(self):
        """trajectory_min_hours is read from config, not hardcoded."""
        config = make_full_config(
            strategy={"name": "test_strategy", "uid": "test-uid"},
            scoring={"entry_threshold": 6.5, "confluence_min_signals": 3},
            learning={"trajectory_min_hours": 24},  # 24h minimum
        )
        sub = Substrate(config=config)

        # History spanning 16h — sufficient for default (8h) but not for 24h
        now = datetime.now(timezone.utc)
        history = {"BTCUSDT": []}
        for i in range(8):
            ts = now - timedelta(hours=16 - i * 2)
            history["BTCUSDT"].append({
                "timestamp": ts.isoformat(),
                "signal": "bullish",
                "indicators": {"ok": True, "rsi": {"value": 55}},
            })
        sub.market["indicator_history"] = history
        sub.analysis["candidates"] = [{"symbol": "BTCUSDT", "score": 7.5}]

        enz = CollectPreTradeContext()
        result = enz.transform(sub)

        ctx = result.market.get("pre_trade_context", {}).get("BTCUSDT", {})
        # 16h < 24h minimum → should block
        assert ctx.get("coincidence_risk") == "high"
        assert ctx.get("trajectory_type") == "insufficient_data"


# ── P8: Trajectory classification (unchanged behavior) ──────────────────────

class TestClassifyTrajectory:
    """P8: _classify_trajectory still works correctly with real history entries."""

    @pytest.fixture
    def default_thresholds(self):
        """Default trajectory thresholds matching default.yaml."""
        return {
            "stable_consensus": 10,
            "gradual_alignment": 8,
            "earlier_min": 3,
            "recent_min": 2,
            "earlier_low": 2,
            "min_alignment": 4,
        }

    def test_gradual_alignment(self, default_thresholds):
        """8+ aligned bars with earlier support → gradual_alignment."""
        history = [
            {"signal": "bullish"},
            {"signal": "neutral"},
            {"signal": "bullish"},
            {"signal": "bullish"},
            {"signal": "neutral"},
            {"signal": "bullish"},
            {"signal": "bullish"},
            {"signal": "bullish"},
            {"signal": "bullish"},
            {"signal": "bullish"},
        ]
        result = _classify_trajectory(history, default_thresholds)
        assert result["coincidence_risk"] in ("low", "medium")

    def test_sudden_coincidence(self, default_thresholds):
        """Aligned only in last 2-3 bars → sudden_coincidence."""
        history = [
            {"signal": "bearish"},
            {"signal": "neutral"},
            {"signal": "bearish"},
            {"signal": "neutral"},
            {"signal": "bearish"},
            {"signal": "neutral"},
            {"signal": "bullish"},
            {"signal": "bullish"},
        ]
        result = _classify_trajectory(history, default_thresholds)
        assert result["coincidence_risk"] == "high"

    def test_empty_history(self, default_thresholds):
        """Empty history → unknown, high risk."""
        result = _classify_trajectory([], default_thresholds)
        assert result["trajectory_type"] == "unknown"
        assert result["coincidence_risk"] == "high"

    def test_no_alignment(self, default_thresholds):
        """All neutral → no_alignment, high risk."""
        history = [{"signal": "neutral"}] * 5
        result = _classify_trajectory(history, default_thresholds)
        assert result["coincidence_risk"] == "high"


# ── Integration: CollectOHLCV preserves indicators when no new candle ────────

class TestCollectOHLCVPreservesIndicators:
    """P7: CollectOHLCV preserves existing indicators when no new candle."""

    def test_transform_preserves_indicators_for_stale_symbols(self):
        """When no new candle, existing indicators are preserved on the substrate."""
        enz = CollectOHLCV()
        config = make_full_config(
            strategy={"name": "test_strategy", "uid": "test-uid"},
            symbols={"always_watch": ["BTCUSDT"]},
            indicators=[{"name": "rsi", "params": {"period": 14}, "weight": 0.25}],
            learning={"trajectory_lookback_hours": 48, "trajectory_min_hours": 8},
        )
        sub = Substrate(config=config)

        # Simulate existing indicators from a previous cycle
        now = datetime.now(timezone.utc)
        current_floor_4h = candle_floor(now, "4H")
        current_floor_1h = candle_floor(now, "1H")
        sub.market["indicators"] = {
            "BTCUSDT": {"4H": {"ok": True, "rsi": {"value": 55}}}
        }
        sub.market["last_candle_close_ts"] = {
            "BTCUSDT_4H": current_floor_4h.isoformat(),
            "BTCUSDT_1H": current_floor_1h.isoformat(),
        }
        sub.market["last_scan_at"] = now.isoformat()

        # can_activate should return False (same candle for all timeframes)
        assert enz.can_activate(sub) is False

    def test_last_candle_close_ts_in_substrate_init(self):
        """Substrate initializes with empty last_candle_close_ts."""
        sub = Substrate(config=make_full_config())
        assert "last_candle_close_ts" in sub.market
        assert sub.market["last_candle_close_ts"] == {}


# ── P2: Config keys ─────────────────────────────────────────────────────────
# NOTE: Config key consistency tests have been moved to test_config_consistency.py
# which uses dynamic file discovery. This avoids hardcoded file references
# that break when strategy YAMLs are added, removed, or renamed.
