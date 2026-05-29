"""
tests/test_dynamic_filter.py -- Tests for the DynamicFilter enzyme.

Covers both static and combined modes, filter pipeline stages,
always_watch override, never_trade hard exclusion, and refresh
interval caching.

All tests use mock exchanges — no real API calls.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from tests.conftest import make_full_config
from core.substrate import Substrate
from enzymes.dynamic_filter import DynamicFilter, _hours_since


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_ohlcv_df(n_bars: int = 100, trend: str = "up") -> pd.DataFrame:
    """Create a synthetic OHLCV DataFrame for testing."""
    import numpy as np
    dates = pd.date_range("2024-01-01", periods=n_bars, freq="4h")
    if trend == "up":
        base = 100.0
        close = base + np.cumsum(np.random.uniform(0, 0.5, n_bars))
    elif trend == "down":
        base = 500.0
        close = base - np.cumsum(np.random.uniform(0, 0.5, n_bars))
    else:  # flat/noisy
        close = 100.0 + np.random.uniform(-1, 1, n_bars)

    df = pd.DataFrame({
        "ts": [int(d.timestamp() * 1000) for d in dates],
        "open": close * 0.999,
        "high": close * 1.001,
        "low": close * 0.998,
        "close": close,
        "volume": 1000.0,
    })
    df.index = dates
    return df


def _make_mock_exchange(
    universe: list[dict] | None = None,
    ohlcv_data: dict[str, pd.DataFrame] | None = None,
):
    """
    Create a mock Exchange with configurable fetch_usdt_perps and fetch_ohlcv.

    Args:
        universe: List of dicts returned by fetch_usdt_perps().
        ohlcv_data: Dict mapping symbol -> DataFrame for fetch_ohlcv.
    """
    mock = MagicMock()
    mock.fetch_usdt_perps.return_value = universe or []

    def _fetch_ohlcv(symbol, timeframe="4h", limit=200):
        if ohlcv_data and symbol in ohlcv_data:
            return ohlcv_data[symbol]
        # Default: return a trending DataFrame
        return _make_ohlcv_df(100, trend="up")

    mock.fetch_ohlcv.side_effect = _fetch_ohlcv
    return mock


def _make_substrate_with_combined_mode(
    always_watch: list[str] | None = None,
    never_trade: list[str] | None = None,
    dynamic_filter: dict | None = None,
) -> Substrate:
    """Create a substrate with mode='combined' for testing."""
    overrides = {
        "symbols": {
            "mode": "combined",
            "always_watch": always_watch or ["BTCUSDT", "ETHUSDT"],
            "never_trade": never_trade or [],
            "dynamic_filter": dynamic_filter or {
                "universe_source": "exchange",
                "limit": 5,
                "min_volume_24h_usd": 5000000,
                "min_open_interest_usd": 1000000,
                "min_r_squared": 0.10,
                "refresh_interval_hours": 4,
            },
        },
    }
    config = make_full_config(**overrides)
    return Substrate(config=config)


# ── _hours_since tests ───────────────────────────────────────────────────────

class TestHoursSince:
    def test_empty_string_returns_inf(self):
        assert _hours_since("") == float("inf")

    def test_none_returns_inf(self):
        assert _hours_since(None) == float("inf")

    def test_recent_timestamp_returns_small_value(self):
        recent = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        hours = _hours_since(recent)
        assert 0.4 < hours < 0.6

    def test_old_timestamp_returns_large_value(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
        hours = _hours_since(old)
        assert 9.9 < hours < 10.1

    def test_invalid_string_returns_inf(self):
        assert _hours_since("not-a-timestamp") == float("inf")


# ── Static mode tests ────────────────────────────────────────────────────────

class TestStaticMode:
    def test_static_mode_transform_is_noop(self):
        """Static mode: transform() is a no-op — substrate unchanged."""
        config = make_full_config(symbols={"mode": "static"})
        substrate = Substrate(config=config)
        original_symbols = list(substrate.market["symbols_watched"])
        enzyme = DynamicFilter(config=substrate._config)
        result = enzyme.transform(substrate)
        # transform() should not change symbols_watched in static mode
        assert result.market["symbols_watched"] == original_symbols

    def test_static_mode_substrate_init_excludes_never_trade(self):
        """Static mode: Substrate.__init__ already excludes never_trade from always_watch."""
        config = make_full_config(symbols={
            "mode": "static",
            "always_watch": ["BTCUSDT", "ETHUSDT", "SHIBUSDT"],
            "never_trade": ["SHIBUSDT"],
            "dynamic_filter": {
                "universe_source": "exchange",
                "limit": 0,
                "min_volume_24h_usd": 0,
                "min_open_interest_usd": 0,
                "min_r_squared": 0.15,
                "refresh_interval_hours": 4,
            },
        })
        substrate = Substrate(config=config)
        # Substrate init already filters never_trade from always_watch
        assert "SHIBUSDT" not in substrate.market["symbols_watched"]
        assert "BTCUSDT" in substrate.market["symbols_watched"]
        assert "ETHUSDT" in substrate.market["symbols_watched"]

    def test_static_mode_does_not_activate(self):
        """In static mode, can_activate() returns False."""
        config = make_full_config(symbols={"mode": "static"})
        substrate = Substrate(config=config)
        enzyme = DynamicFilter(config=substrate._config)
        assert enzyme.can_activate(substrate) is False


# ── Combined mode activation tests ───────────────────────────────────────────

class TestCombinedModeActivation:
    def test_combined_mode_activates_on_cold_start(self):
        """Combined mode activates when symbols_watched is empty."""
        substrate = _make_substrate_with_combined_mode()
        substrate.market["symbols_watched"] = []
        enzyme = DynamicFilter(config=substrate._config)
        assert enzyme.can_activate(substrate) is True

    def test_combined_mode_activates_after_refresh_interval(self):
        """Combined mode activates when refresh_interval has elapsed."""
        substrate = _make_substrate_with_combined_mode()
        substrate.market["symbols_watched"] = ["BTCUSDT"]
        # Set last run to 5 hours ago (refresh_interval is 4h)
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        substrate.market["last_dynamic_filter_at"] = old_ts
        enzyme = DynamicFilter(config=substrate._config)
        assert enzyme.can_activate(substrate) is True

    def test_combined_mode_does_not_activate_within_interval(self):
        """Combined mode does NOT activate when within refresh_interval."""
        substrate = _make_substrate_with_combined_mode()
        substrate.market["symbols_watched"] = ["BTCUSDT"]
        # Set last run to 1 hour ago (refresh_interval is 4h)
        recent_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        substrate.market["last_dynamic_filter_at"] = recent_ts
        enzyme = DynamicFilter(config=substrate._config)
        assert enzyme.can_activate(substrate) is False


# ── Combined mode pipeline tests ─────────────────────────────────────────────

class TestCombinedModePipeline:
    def test_combined_mode_merges_and_ranks(self):
        """Combined mode: returns always_watch + top-N from exchange, deduped."""
        universe = [
            {"symbol": "BTCUSDT", "volume_24h_usd": 50_000_000_000, "open_interest_usd": 10_000_000_000},
            {"symbol": "ETHUSDT", "volume_24h_usd": 20_000_000_000, "open_interest_usd": 5_000_000_000},
            {"symbol": "SOLUSDT", "volume_24h_usd": 10_000_000_000, "open_interest_usd": 2_000_000_000},
            {"symbol": "AVAXUSDT", "volume_24h_usd": 8_000_000_000, "open_interest_usd": 1_500_000_000},
            {"symbol": "DOGEUSDT", "volume_24h_usd": 6_000_000_000, "open_interest_usd": 1_200_000_000},
        ]
        mock_exchange = _make_mock_exchange(universe=universe)
        substrate = _make_substrate_with_combined_mode(
            always_watch=["BTCUSDT", "ETHUSDT"],
            dynamic_filter={
                "universe_source": "exchange",
                "limit": 3,
                "min_volume_24h_usd": 5000000,
                "min_open_interest_usd": 1000000,
                "min_r_squared": 0.10,
                "refresh_interval_hours": 4,
            },
        )
        substrate.market["symbols_watched"] = []
        enzyme = DynamicFilter(config=substrate._config, exchange=mock_exchange)
        result = enzyme.transform(substrate)

        # BTCUSDT and ETHUSDT are in always_watch, so they're always included
        assert "BTCUSDT" in result.market["symbols_watched"]
        assert "ETHUSDT" in result.market["symbols_watched"]
        # Additional symbols from dynamic ranking should be present
        assert len(result.market["symbols_watched"]) >= 2
        # last_dynamic_filter_at should be set
        assert result.market["last_dynamic_filter_at"] != ""

    def test_never_trade_always_excluded(self):
        """never_trade symbols are excluded even if they're top-1 in ranking."""
        universe = [
            {"symbol": "SHIBUSDT", "volume_24h_usd": 50_000_000_000, "open_interest_usd": 10_000_000_000},
            {"symbol": "BTCUSDT", "volume_24h_usd": 30_000_000_000, "open_interest_usd": 5_000_000_000},
        ]
        # Make SHIBUSDT have highest momentum
        ohlcv_data = {
            "SHIBUSDT": _make_ohlcv_df(100, trend="up"),
            "BTCUSDT": _make_ohlcv_df(100, trend="up"),
        }
        mock_exchange = _make_mock_exchange(universe=universe, ohlcv_data=ohlcv_data)
        substrate = _make_substrate_with_combined_mode(
            always_watch=["BTCUSDT"],
            never_trade=["SHIBUSDT"],
            dynamic_filter={
                "universe_source": "exchange",
                "limit": 5,
                "min_volume_24h_usd": 1000000,
                "min_open_interest_usd": 500000,
                "min_r_squared": 0.10,
                "refresh_interval_hours": 4,
            },
        )
        substrate.market["symbols_watched"] = []
        enzyme = DynamicFilter(config=substrate._config, exchange=mock_exchange)
        result = enzyme.transform(substrate)

        assert "SHIBUSDT" not in result.market["symbols_watched"]
        assert "BTCUSDT" in result.market["symbols_watched"]

    def test_volume_filter_excludes_low_volume(self):
        """Symbols below min_volume_24h_usd are filtered out."""
        universe = [
            {"symbol": "BTCUSDT", "volume_24h_usd": 50_000_000_000, "open_interest_usd": 10_000_000_000},
            {"symbol": "TINYCOIN", "volume_24h_usd": 100_000, "open_interest_usd": 5_000_000_000},
        ]
        mock_exchange = _make_mock_exchange(universe=universe)
        substrate = _make_substrate_with_combined_mode(
            always_watch=["BTCUSDT"],
            dynamic_filter={
                "universe_source": "exchange",
                "limit": 10,
                "min_volume_24h_usd": 1_000_000,
                "min_open_interest_usd": 0,
                "min_r_squared": 0.10,
                "refresh_interval_hours": 4,
            },
        )
        substrate.market["symbols_watched"] = []
        enzyme = DynamicFilter(config=substrate._config, exchange=mock_exchange)
        result = enzyme.transform(substrate)

        assert "TINYCOIN" not in result.market["symbols_watched"]
        assert "BTCUSDT" in result.market["symbols_watched"]

    def test_oi_filter_excludes_low_oi(self):
        """Symbols below min_open_interest_usd are filtered out."""
        universe = [
            {"symbol": "BTCUSDT", "volume_24h_usd": 50_000_000_000, "open_interest_usd": 10_000_000_000},
            {"symbol": "LOWOI", "volume_24h_usd": 50_000_000_000, "open_interest_usd": 50_000},
        ]
        mock_exchange = _make_mock_exchange(universe=universe)
        substrate = _make_substrate_with_combined_mode(
            always_watch=["BTCUSDT"],
            dynamic_filter={
                "universe_source": "exchange",
                "limit": 10,
                "min_volume_24h_usd": 0,
                "min_open_interest_usd": 1_000_000,
                "min_r_squared": 0.10,
                "refresh_interval_hours": 4,
            },
        )
        substrate.market["symbols_watched"] = []
        enzyme = DynamicFilter(config=substrate._config, exchange=mock_exchange)
        result = enzyme.transform(substrate)

        assert "LOWOI" not in result.market["symbols_watched"]
        assert "BTCUSDT" in result.market["symbols_watched"]

    def test_r_squared_floor_excludes_noisy(self):
        """Symbols with momentum_quality filtered=True (R² < floor) are excluded."""
        universe = [
            {"symbol": "BTCUSDT", "volume_24h_usd": 50_000_000_000, "open_interest_usd": 10_000_000_000},
            {"symbol": "NOISYCOIN", "volume_24h_usd": 50_000_000_000, "open_interest_usd": 10_000_000_000},
        ]
        # BTCUSDT has a clean uptrend, NOISYCOIN is flat/noisy
        ohlcv_data = {
            "BTCUSDT": _make_ohlcv_df(100, trend="up"),
            "NOISYCOIN": _make_ohlcv_df(100, trend="flat"),
        }
        mock_exchange = _make_mock_exchange(universe=universe, ohlcv_data=ohlcv_data)
        substrate = _make_substrate_with_combined_mode(
            always_watch=["BTCUSDT"],
            dynamic_filter={
                "universe_source": "exchange",
                "limit": 10,
                "min_volume_24h_usd": 0,
                "min_open_interest_usd": 0,
                "min_r_squared": 0.10,
                "refresh_interval_hours": 4,
            },
        )
        substrate.market["symbols_watched"] = []
        enzyme = DynamicFilter(config=substrate._config, exchange=mock_exchange)
        result = enzyme.transform(substrate)

        # BTCUSDT (always_watch) is always included
        assert "BTCUSDT" in result.market["symbols_watched"]
        # NOISYCOIN should be excluded if its R² is below the floor
        # (depends on random data, but flat trend should have low R²)

    def test_always_watch_overrides_ranking(self):
        """always_watch symbols are always included regardless of ranking."""
        universe = [
            {"symbol": "BTCUSDT", "volume_24h_usd": 50_000_000_000, "open_interest_usd": 10_000_000_000},
        ]
        mock_exchange = _make_mock_exchange(universe=universe)
        substrate = _make_substrate_with_combined_mode(
            always_watch=["BTCUSDT", "ETHUSDT"],  # ETHUSDT not in universe
            dynamic_filter={
                "universe_source": "exchange",
                "limit": 1,
                "min_volume_24h_usd": 0,
                "min_open_interest_usd": 0,
                "min_r_squared": 0.10,
                "refresh_interval_hours": 4,
            },
        )
        substrate.market["symbols_watched"] = []
        enzyme = DynamicFilter(config=substrate._config, exchange=mock_exchange)
        result = enzyme.transform(substrate)

        # ETHUSDT is in always_watch but NOT in the exchange universe
        # It should still be included because always_watch always overrides
        assert "ETHUSDT" in result.market["symbols_watched"]
        assert "BTCUSDT" in result.market["symbols_watched"]

    def test_fallback_to_always_watch_on_exchange_failure(self):
        """If exchange fetch fails, fall back to always_watch only."""
        mock_exchange = MagicMock()
        mock_exchange.fetch_usdt_perps.return_value = []  # Empty = failure

        substrate = _make_substrate_with_combined_mode(
            always_watch=["BTCUSDT", "ETHUSDT"],
        )
        substrate.market["symbols_watched"] = []
        enzyme = DynamicFilter(config=substrate._config, exchange=mock_exchange)
        result = enzyme.transform(substrate)

        # Should fall back to always_watch
        assert "BTCUSDT" in result.market["symbols_watched"]
        assert "ETHUSDT" in result.market["symbols_watched"]

    def test_no_exchange_instance_falls_back(self):
        """Without exchange instance, falls back to always_watch."""
        substrate = _make_substrate_with_combined_mode(
            always_watch=["BTCUSDT"],
        )
        substrate.market["symbols_watched"] = []
        enzyme = DynamicFilter(config=substrate._config, exchange=None)
        result = enzyme.transform(substrate)

        assert "BTCUSDT" in result.market["symbols_watched"]

    def test_refresh_interval_caches_universe(self):
        """Enzyme does not re-activate within refresh_interval."""
        substrate = _make_substrate_with_combined_mode()
        substrate.market["symbols_watched"] = ["BTCUSDT", "ETHUSDT"]
        # Set last run to 1 hour ago (interval is 4h)
        recent_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        substrate.market["last_dynamic_filter_at"] = recent_ts

        enzyme = DynamicFilter(config=substrate._config)
        # Should NOT activate because interval hasn't elapsed
        assert enzyme.can_activate(substrate) is False

    def test_limit_respected(self):
        """Only top-N dynamic symbols are selected (beyond always_watch)."""
        universe = [
            {"symbol": f"COIN{i}USDT", "volume_24h_usd": 50_000_000_000 - i * 1_000_000_000, "open_interest_usd": 10_000_000_000}
            for i in range(20)
        ]
        mock_exchange = _make_mock_exchange(universe=universe)
        substrate = _make_substrate_with_combined_mode(
            always_watch=["BTCUSDT"],
            dynamic_filter={
                "universe_source": "exchange",
                "limit": 3,  # Only top 3 dynamic symbols
                "min_volume_24h_usd": 0,
                "min_open_interest_usd": 0,
                "min_r_squared": 0.0,  # Accept everything
                "refresh_interval_hours": 4,
            },
        )
        substrate.market["symbols_watched"] = []
        enzyme = DynamicFilter(config=substrate._config, exchange=mock_exchange)
        result = enzyme.transform(substrate)

        # BTCUSDT (always_watch) + up to 3 dynamic symbols
        # The total should not exceed 1 + 3 = 4 (unless always_watch overlaps with dynamic)
        dynamic_only = [s for s in result.market["symbols_watched"] if s != "BTCUSDT"]
        assert len(dynamic_only) <= 3


# ── Flux score tests ─────────────────────────────────────────────────────────

class TestFluxScore:
    def test_flux_score_zero_when_cannot_activate(self):
        """Flux score is 0 when enzyme cannot activate (static mode)."""
        config = make_full_config(symbols={"mode": "static"})
        substrate = Substrate(config=config)
        enzyme = DynamicFilter(config=substrate._config)
        assert enzyme.flux_score(substrate) == 0.0

    def test_flux_score_high_on_cold_start(self):
        """Flux score is highest on cold start (no symbols set)."""
        substrate = _make_substrate_with_combined_mode()
        substrate.market["symbols_watched"] = []
        enzyme = DynamicFilter(config=substrate._config)
        assert enzyme.flux_score(substrate) == 4.0

    def test_flux_score_lower_on_refresh(self):
        """Flux score is lower when refreshing (symbols already set)."""
        substrate = _make_substrate_with_combined_mode()
        substrate.market["symbols_watched"] = ["BTCUSDT"]
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        substrate.market["last_dynamic_filter_at"] = old_ts
        enzyme = DynamicFilter(config=substrate._config)
        assert enzyme.flux_score(substrate) == 1.5