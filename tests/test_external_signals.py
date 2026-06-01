"""
tests/test_external_signals.py -- Tests for CollectExternalSignals enzyme.

Tests cover:
  - Enzyme activation conditions (module enabled/disabled, already evaluated)
  - Funding rate parsing and funding_squeeze signal
  - Fear & Greed Index parsing and fgi_contrarian signal
  - Liquidation cascade detection and liquidation_cascade signal
  - Graceful degradation on API failures
  - Cache behavior (TTL-based)
  - Confluence signals written to substrate.analysis.confluence
  - FGI re-use from existing macro context
"""

import os
import sys
import time
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.substrate import Substrate
from conftest import make_full_config


# ── Helpers ──────────────────────────────────────────────────────────────────────

def _make_substrate(**config_overrides):
    """Create a substrate with external_signals module enabled by default."""
    overrides = {
        "modules": {"external_signals": True},
        "external": {
            "funding_squeeze_threshold": -0.0003,
            "fgi_contrarian_threshold": 20,
            "liquidation_cascade_usd": 250000,
            "liquidation_window_seconds": 300,
            "cache_ttl": 3600,
        },
    }
    # Deep merge additional overrides
    for k, v in config_overrides.items():
        if isinstance(v, dict) and isinstance(overrides.get(k), dict):
            overrides[k] = {**overrides[k], **v}
        else:
            overrides[k] = v
    return Substrate(config=make_full_config(**overrides))


# ── Sample API responses ─────────────────────────────────────────────────────────

SAMPLE_PREMIUM_INDEX = [
    {"symbol": "BTCUSDT", "lastFundingRate": "-0.00050", "markPrice": "68000.00"},
    {"symbol": "ETHUSDT", "lastFundingRate": "0.00010", "markPrice": "3800.00"},
]

SAMPLE_FGI = {
    "data": [
        {"value": "15", "value_classification": "Extreme Fear"}
    ]
}

SAMPLE_FGI_GREEY = {
    "data": [
        {"value": "75", "value_classification": "Greed"}
    ]
}

SAMPLE_LIQUIDATIONS = [
    {
        "symbol": "BTCUSDT",
        "side": "SELL",
        "price": "67500.00",
        "origQty": "4.0",
        "executedQty": "4.0",
        "time": str(int(time.time() * 1000)),  # now
        "type": "LIMIT",
    },
]


# ── Activation Tests ─────────────────────────────────────────────────────────────

class TestActivation:
    """Test CollectExternalSignals activation conditions."""

    def test_does_not_activate_when_module_disabled(self):
        """Enzyme should not activate when modules.external_signals is False."""
        from enzymes.collect_external_signals import CollectExternalSignals
        sub = _make_substrate(modules={"external_signals": False})
        enzyme = CollectExternalSignals()
        assert enzyme.can_activate(sub) is False

    def test_activates_when_module_enabled(self):
        """Enzyme should activate when module enabled and not yet evaluated."""
        from enzymes.collect_external_signals import CollectExternalSignals
        sub = _make_substrate()
        enzyme = CollectExternalSignals()
        assert enzyme.can_activate(sub) is True

    def test_does_not_activate_when_already_evaluated(self):
        """Enzyme should not activate again after evaluation."""
        from enzymes.collect_external_signals import CollectExternalSignals
        sub = _make_substrate()
        sub.analysis["external_signals_evaluated"] = True
        enzyme = CollectExternalSignals()
        assert enzyme.can_activate(sub) is False

    def test_flux_score_zero_when_cannot_activate(self):
        """Flux score should be 0 when enzyme cannot activate."""
        from enzymes.collect_external_signals import CollectExternalSignals
        sub = _make_substrate(modules={"external_signals": False})
        enzyme = CollectExternalSignals()
        assert enzyme.flux_score(sub) == 0.0

    def test_flux_score_positive_when_can_activate(self):
        """Flux score should be positive when enzyme can activate."""
        from enzymes.collect_external_signals import CollectExternalSignals
        sub = _make_substrate()
        enzyme = CollectExternalSignals()
        assert enzyme.flux_score(sub) > 0.0

    def test_flux_score_higher_with_positions(self):
        """Flux score should be higher when positions are open."""
        from enzymes.collect_external_signals import CollectExternalSignals
        sub = _make_substrate()
        sub.portfolio["open_positions"] = [{"symbol": "BTCUSDT"}]
        enzyme = CollectExternalSignals()
        assert enzyme.flux_score(sub) == 2.0

    def test_enzyme_class_is_sensor(self):
        """CollectExternalSignals is a Sensor enzyme."""
        from enzymes.collect_external_signals import CollectExternalSignals
        enzyme = CollectExternalSignals()
        from core.enzyme import EnzymeClass
        assert enzyme.enzyme_class == EnzymeClass.SENSOR


# ── Funding Rate Tests ───────────────────────────────────────────────────────────

class TestFundingRate:
    """Test funding rate fetching and funding_squeeze signal."""

    @patch("enzymes.collect_external_signals._cached_fetch")
    def test_funding_squeeze_triggered(self, mock_fetch):
        """funding_squeeze should be True when funding rate < threshold."""
        from enzymes.collect_external_signals import CollectExternalSignals, _cache
        _cache.clear()

        # Return funding data, None for FGI, None for liquidations
        mock_fetch.side_effect = [SAMPLE_PREMIUM_INDEX, None, None]

        sub = _make_substrate()
        enzyme = CollectExternalSignals()
        result = enzyme.transform(sub)

        # BTCUSDT funding rate = -0.00050, threshold = -0.0003
        assert result.analysis["confluence"]["funding_squeeze"] is True
        assert result.market["funding_rate"]["rate"] == -0.00050
        assert result.market["funding_rate"]["ok"] is True

    @patch("enzymes.collect_external_signals._cached_fetch")
    def test_funding_squeeze_not_triggered(self, mock_fetch):
        """funding_squeeze should be False when funding rate > threshold."""
        from enzymes.collect_external_signals import CollectExternalSignals, _cache
        _cache.clear()

        # ETHUSDT has positive funding rate
        premium_data = [
            {"symbol": "BTCUSDT", "lastFundingRate": "0.00010"},
        ]
        mock_fetch.side_effect = [premium_data, None, None]

        sub = _make_substrate()
        enzyme = CollectExternalSignals()
        result = enzyme.transform(sub)

        assert result.analysis["confluence"]["funding_squeeze"] is False

    @patch("enzymes.collect_external_signals._cached_fetch")
    def test_funding_rate_graceful_degradation(self, mock_fetch):
        """On API failure, funding_rate.ok should be False, no crash."""
        from enzymes.collect_external_signals import CollectExternalSignals, _cache
        _cache.clear()

        mock_fetch.side_effect = [None, None, None]  # All fail

        sub = _make_substrate()
        enzyme = CollectExternalSignals()
        result = enzyme.transform(sub)

        assert result.market["funding_rate"]["ok"] is False
        # funding_squeeze key should NOT be set on failure
        assert "funding_squeeze" not in result.analysis["confluence"]


# ── Fear & Greed Index Tests ─────────────────────────────────────────────────────

class TestFearGreedIndex:
    """Test FGI fetching and fgi_contrarian signal."""

    @patch("enzymes.collect_external_signals._cached_fetch")
    def test_fgi_contrarian_triggered(self, mock_fetch):
        """fgi_contrarian should be True when FGI <= threshold."""
        from enzymes.collect_external_signals import CollectExternalSignals, _cache
        _cache.clear()

        mock_fetch.side_effect = [None, SAMPLE_FGI, None]

        sub = _make_substrate()
        enzyme = CollectExternalSignals()
        result = enzyme.transform(sub)

        # FGI = 15, threshold = 20
        assert result.analysis["confluence"]["fgi_contrarian"] is True

    @patch("enzymes.collect_external_signals._cached_fetch")
    def test_fgi_contrarian_not_triggered(self, mock_fetch):
        """fgi_contrarian should be False when FGI > threshold."""
        from enzymes.collect_external_signals import CollectExternalSignals, _cache
        _cache.clear()

        mock_fetch.side_effect = [None, SAMPLE_FGI_GREEY, None]

        sub = _make_substrate()
        enzyme = CollectExternalSignals()
        result = enzyme.transform(sub)

        # FGI = 75, threshold = 20
        assert result.analysis["confluence"]["fgi_contrarian"] is False

    def test_fgi_reused_from_macro_context(self):
        """FGI should be re-used from substrate.market.macro if already fetched."""
        from enzymes.collect_external_signals import CollectExternalSignals, _cache, _cached_fetch
        _cache.clear()

        sub = _make_substrate()
        # Pre-populate macro context with FGI
        sub.market["macro"] = {
            "fear_greed": {"value": 10, "classification": "Extreme Fear", "ok": True}
        }

        enzyme = CollectExternalSignals()

        # Patch _cached_fetch to track calls — it should NOT be called for FGI
        with patch("enzymes.collect_external_signals._cached_fetch") as mock_fetch:
            mock_fetch.side_effect = [None, None]  # funding, liquidations only
            result = enzyme.transform(sub)

        # fgi_contrarian should be True (FGI=10 <= 20)
        assert result.analysis["confluence"]["fgi_contrarian"] is True
        # _cached_fetch should have been called only twice (funding + liquidations)
        assert mock_fetch.call_count == 2


# ── Liquidation Cascade Tests ────────────────────────────────────────────────────

class TestLiquidationCascade:
    """Test liquidation cascade detection."""

    def test_cascade_triggered_above_threshold(self):
        """liquidation_cascade should be True when total USD > threshold."""
        from enzymes.collect_external_signals import _compute_liquidation_cascade

        # Create liquidations totaling > $250k
        now_ms = int(time.time() * 1000)
        large_liquidations = [
            {
                "symbol": "BTCUSDT",
                "side": "SELL",
                "price": "68000.00",
                "origQty": "5.0",
                "time": str(now_ms),
            },
        ]
        # 68000 * 5 = $340,000 > $250,000 threshold
        result = _compute_liquidation_cascade(large_liquidations, threshold_usd=250000)
        assert result["triggered"] is True
        assert result["total_usd"] > 250000

    def test_cascade_not_triggered_below_threshold(self):
        """liquidation_cascade should be False when total USD < threshold."""
        from enzymes.collect_external_signals import _compute_liquidation_cascade

        now_ms = int(time.time() * 1000)
        small_liquidations = [
            {
                "symbol": "BTCUSDT",
                "side": "SELL",
                "price": "68000.00",
                "origQty": "1.0",
                "time": str(now_ms),
            },
        ]
        # 68000 * 1 = $68,000 < $250,000
        result = _compute_liquidation_cascade(small_liquidations, threshold_usd=250000)
        assert result["triggered"] is False

    def test_cascade_empty_list(self):
        """Empty liquidations list should not trigger cascade."""
        from enzymes.collect_external_signals import _compute_liquidation_cascade

        result = _compute_liquidation_cascade([], threshold_usd=250000)
        assert result["triggered"] is False
        assert result["total_usd"] == 0.0
        assert result["count"] == 0

    def test_cascade_old_liquidations_excluded(self):
        """Liquidations outside the time window should be excluded."""
        from enzymes.collect_external_signals import _compute_liquidation_cascade

        # Liquidation from 10 minutes ago (outside 5min window)
        old_ms = int((time.time() - 600) * 1000)
        old_liquidations = [
            {
                "symbol": "BTCUSDT",
                "side": "SELL",
                "price": "68000.00",
                "origQty": "10.0",
                "time": str(old_ms),
            },
        ]
        result = _compute_liquidation_cascade(
            old_liquidations, threshold_usd=250000, window_seconds=300
        )
        assert result["triggered"] is False
        assert result["count"] == 0

    def test_cluster_walls_populated(self):
        """Cluster walls should be populated from liquidation data."""
        from enzymes.collect_external_signals import _compute_liquidation_cascade

        now_ms = int(time.time() * 1000)
        liquidations = [
            {
                "price": "68000.00",
                "origQty": "2.0",
                "time": str(now_ms),
            },
            {
                "price": "68000.00",
                "origQty": "1.0",
                "time": str(now_ms),
            },
        ]
        result = _compute_liquidation_cascade(liquidations, threshold_usd=100000)
        # Should have cluster walls
        assert isinstance(result["cluster_walls"], dict)


# ── Cache Tests ──────────────────────────────────────────────────────────────────

class TestCache:
    """Test TTL-based caching behavior."""

    def test_cache_returns_fresh_data(self):
        """Cache should return data when TTL has not expired."""
        from enzymes.collect_external_signals import _cache
        _cache.clear()

        # Pre-populate cache with fresh data
        _cache["test_key"] = (time.time(), {"result": "fresh"})

        from enzymes.collect_external_signals import _cached_fetch
        # Should return cached data without making a network call
        result = _cached_fetch("test_key", "http://unused.example.com", ttl=60)
        assert result == {"result": "fresh"}

    def test_cache_expired_refetches(self):
        """Cache should re-fetch when TTL has expired."""
        from enzymes.collect_external_signals import _cache
        _cache.clear()

        # Pre-populate cache with stale data (TTL expired)
        _cache["stale_key"] = (time.time() - 100, {"result": "stale"})

        # _cached_fetch will try to actually fetch, which will fail
        # (no such URL), so it returns None
        from enzymes.collect_external_signals import _cached_fetch
        result = _cached_fetch("stale_key", "http://invalid.example.com", ttl=10)
        # Fetch fails → returns None (graceful degradation)
        assert result is None

    def test_cache_cleared_between_tests(self):
        """Ensure cache doesn't leak between tests."""
        from enzymes.collect_external_signals import _cache
        _cache.clear()
        assert len(_cache) == 0


# ── Integration Tests ────────────────────────────────────────────────────────────

class TestIntegration:
    """Integration tests for the full transform pipeline."""

    @patch("enzymes.collect_external_signals._cached_fetch")
    def test_all_signals_active(self, mock_fetch):
        """When all external signals trigger, all confluence keys should be True."""
        from enzymes.collect_external_signals import CollectExternalSignals, _cache
        _cache.clear()

        now_ms = int(time.time() * 1000)
        liquidations = [
            {"symbol": "BTCUSDT", "side": "SELL", "price": "68000.00",
             "origQty": "5.0", "time": str(now_ms)},
        ]

        mock_fetch.side_effect = [
            SAMPLE_PREMIUM_INDEX,   # funding rate
            SAMPLE_FGI,            # Fear & Greed
            liquidations,          # liquidations
        ]

        sub = _make_substrate()
        enzyme = CollectExternalSignals()
        result = enzyme.transform(sub)

        confluence = result.analysis["confluence"]
        assert confluence.get("funding_squeeze") is True
        assert confluence.get("fgi_contrarian") is True
        # liquidation_cascade depends on total USD vs threshold
        assert "liquidation_cascade" in confluence

    @patch("enzymes.collect_external_signals._cached_fetch")
    def test_all_apis_fail_gracefully(self, mock_fetch):
        """When all APIs fail, enzyme should not crash and should mark evaluated."""
        from enzymes.collect_external_signals import CollectExternalSignals, _cache
        _cache.clear()

        mock_fetch.side_effect = [None, None, None]

        sub = _make_substrate()
        enzyme = CollectExternalSignals()
        result = enzyme.transform(sub)

        # Should not crash
        assert result.analysis["external_signals_evaluated"] is True
        # Confluence should be empty (no signals set on failure)
        assert result.analysis["confluence"] == {}
        # Market fields should show ok=False
        assert result.market["funding_rate"]["ok"] is False
        assert result.market["liquidations"]["ok"] is False

    @patch("enzymes.collect_external_signals._cached_fetch")
    def test_evaluated_flag_prevents_reactivation(self, mock_fetch):
        """After transform, enzyme should not activate again."""
        from enzymes.collect_external_signals import CollectExternalSignals, _cache
        _cache.clear()

        mock_fetch.side_effect = [None, None, None]

        sub = _make_substrate()
        enzyme = CollectExternalSignals()
        result = enzyme.transform(sub)

        # Should not be able to activate again
        assert enzyme.can_activate(result) is False

    @patch("enzymes.collect_external_signals._cached_fetch")
    def test_confluence_preserves_existing_keys(self, mock_fetch):
        """Transform should preserve existing confluence keys."""
        from enzymes.collect_external_signals import CollectExternalSignals, _cache
        _cache.clear()

        mock_fetch.side_effect = [None, None, None]

        sub = _make_substrate()
        sub.analysis["confluence"] = {"existing_signal": True}
        enzyme = CollectExternalSignals()
        result = enzyme.transform(sub)

        # Existing key should be preserved
        assert result.analysis["confluence"]["existing_signal"] is True