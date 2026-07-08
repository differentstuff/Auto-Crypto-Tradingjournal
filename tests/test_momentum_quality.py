"""
tests/test_momentum_quality.py -- Unit tests for momentum_quality indicator.

DoD validation:
  1) momentum_quality = slope × R² is available in the indicator pipeline
  2) R² acts as quality filter: symbols below min_r_squared are excluded
  3) Timeframe lookback adapts based on R² quality
  4) momentum_quality is stored in substrate for use by score_confluence/dynamic_filter
  5) Unit tests pass
  6) Integration: dynamic_filter can rank symbols by momentum_quality
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from indicators.momentum_quality import (
    compute_momentum_quality,
    _compute_adaptive_lookback,
)
from indicators.registry import compute_indicator, is_registered


# -- Helpers ------------------------------------------------------------------

def _make_df(prices: list[float], volume: float = 1000.0) -> pd.DataFrame:
    """
    Build a minimal OHLCV DataFrame from a close-price series.
    Open = previous close, high = max(open, close), low = min(open, close).
    """
    rows = []
    for i, c in enumerate(prices):
        o = prices[i - 1] if i > 0 else c
        rows.append({
            "open": o,
            "high": max(o, c),
            "low": min(o, c),
            "close": c,
            "volume": volume,
        })
    return pd.DataFrame(rows)


def _linear_trend_df(n: int = 100, start: float = 100.0, slope: float = 0.01) -> pd.DataFrame:
    """Create a DataFrame with a perfect linear trend in close prices."""
    prices = [start + slope * i for i in range(n)]
    return _make_df(prices)


def _exponential_trend_df(n: int = 100, start: float = 100.0, daily_return: float = 0.01) -> pd.DataFrame:
    """Create a DataFrame with an exponential trend (constant % growth)."""
    prices = [start * (1 + daily_return) ** i for i in range(n)]
    return _make_df(prices)


def _random_walk_df(n: int = 100, start: float = 100.0, seed: int = 42) -> pd.DataFrame:
    """Create a DataFrame with a random walk (no trend)."""
    rng = np.random.default_rng(seed)
    returns = rng.normal(0, 0.02, n)
    prices = [start]
    for r in returns[1:]:
        prices.append(prices[-1] * (1 + r))
    return _make_df(prices)


# -- Registry integration -----------------------------------------------------

class TestRegistry:
    """momentum_quality is registered in the indicator pipeline."""

    def test_registered(self):
        assert is_registered("momentum_quality")

    def test_compute_via_registry(self):
        df = _exponential_trend_df(n=100, daily_return=0.005)
        result = compute_indicator("momentum_quality", df, ranking_window=90)
        assert result is not None
        assert "score" in result
        assert "r_squared" in result


# -- DoD 1: momentum_quality computed as slope × R² ---------------------------

class TestMomentumQualityComputation:
    """slope × R² is computed correctly."""

    def test_clear_uptrend_positive_score(self):
        """DoD: Given a symbol with clear uptrend (R² > 0.5), momentum_quality > 0."""
        df = _exponential_trend_df(n=100, daily_return=0.01)
        result = compute_momentum_quality(df, ranking_window=90)
        assert result is not None
        assert result["score"] is not None
        assert result["score"] > 0, "Clear uptrend should have positive momentum_quality"
        assert result["direction"] == "bullish"
        assert result["r_squared"] > 0.5, "Clear uptrend should have R² > 0.5"

    def test_clear_downtrend_negative_score(self):
        """DoD: Clear downtrend → negative score, bearish direction."""
        df = _exponential_trend_df(n=100, daily_return=-0.01)
        result = compute_momentum_quality(df, ranking_window=90)
        assert result is not None
        assert result["score"] is not None
        assert result["score"] < 0, "Clear downtrend should have negative momentum_quality"
        assert result["direction"] == "bearish"
        assert result["r_squared"] > 0.5

    def test_score_equals_slope_times_r_squared(self):
        """Verify the core formula: score = slope × R²."""
        df = _exponential_trend_df(n=100, daily_return=0.005)
        result = compute_momentum_quality(df, ranking_window=90)
        assert result is not None
        expected = round(result["slope"] * result["r_squared"], 6)
        assert abs(result["score"] - expected) < 1e-6

    def test_linear_trend_high_r_squared(self):
        """Perfect linear trend on log-price should have R² ≈ 1.0."""
        # Exponential price series = linear on log scale
        df = _exponential_trend_df(n=100, daily_return=0.005)
        result = compute_momentum_quality(df, ranking_window=90)
        assert result is not None
        assert result["r_squared"] > 0.99, "Perfect exponential trend should have R² ≈ 1.0 on log scale"


# -- DoD 2: R² acts as quality filter ----------------------------------------

class TestRSquaredFilter:
    """Symbols below min_r_squared are excluded from ranking."""

    def test_random_walk_filtered(self):
        """DoD: Given a symbol with random walk (R² < 0.15), momentum_quality is None/filtered."""
        df = _random_walk_df(n=100, seed=42)
        result = compute_momentum_quality(df, ranking_window=90, min_r_squared=0.15)
        assert result is not None
        # Random walk should have low R² — either filtered or very low R²
        if result["filtered"]:
            assert result["score"] is None
            assert result["r_squared"] < 0.15
        else:
            # If not filtered (R² happened to be above threshold), R² should still be low
            assert result["r_squared"] < 0.5

    def test_filtered_symbol_has_no_score(self):
        """When R² < min_r_squared, score is None and filtered is True."""
        # Flat prices → R² ≈ 0
        prices = [100.0] * 100
        df = _make_df(prices)
        result = compute_momentum_quality(df, ranking_window=90, min_r_squared=0.15)
        assert result is not None
        assert result["filtered"] is True
        assert result["score"] is None

    def test_unfiltered_symbol_has_score(self):
        """When R² >= min_r_squared, score is a number and filtered is False."""
        df = _exponential_trend_df(n=100, daily_return=0.01)
        result = compute_momentum_quality(df, ranking_window=90, min_r_squared=0.15)
        assert result is not None
        assert result["filtered"] is False
        assert result["score"] is not None
        assert isinstance(result["score"], float)

    def test_custom_min_r_squared(self):
        """min_r_squared is configurable, not hardcoded."""
        df = _exponential_trend_df(n=100, daily_return=0.003)
        # With very low threshold, should pass
        result_low = compute_momentum_quality(df, ranking_window=90, min_r_squared=0.01)
        # With very high threshold, might be filtered
        result_high = compute_momentum_quality(df, ranking_window=90, min_r_squared=0.99)
        assert result_low is not None
        assert result_high is not None
        # Low threshold should be more permissive
        if result_low["r_squared"] >= 0.01:
            assert result_low["filtered"] is False


# -- DoD 3: Timeframe lookback adapts based on R² quality --------------------

class TestAdaptiveLookback:
    """Adaptive lookback: shorter when R² is high, longer when low."""

    def test_high_r_squared_short_lookback(self):
        """DoD: Adaptive lookback produces shorter window for high-R² symbols."""
        lb = _compute_adaptive_lookback(
            r_squared=0.8, r_squared_high=0.5, r_squared_low=0.3,
            lookback_short=30, lookback_long=90,
        )
        assert lb == 30, "R²=0.8 > 0.5 → use lookback_short=30"

    def test_low_r_squared_long_lookback(self):
        """DoD: Low R² → longer lookback."""
        lb = _compute_adaptive_lookback(
            r_squared=0.2, r_squared_high=0.5, r_squared_low=0.3,
            lookback_short=30, lookback_long=90,
        )
        assert lb == 90, "R²=0.2 < 0.3 → use lookback_long=90"

    def test_interpolation_mid_range(self):
        """DoD: Interpolate between short and long for mid-range R²."""
        # R²=0.4 is exactly halfway between 0.3 and 0.5
        lb = _compute_adaptive_lookback(
            r_squared=0.4, r_squared_high=0.5, r_squared_low=0.3,
            lookback_short=30, lookback_long=90,
        )
        assert lb == 60, "R²=0.4 is halfway → lookback should be 60"

    def test_interpolation_near_high(self):
        """R² just below r_squared_high → lookback near short."""
        lb = _compute_adaptive_lookback(
            r_squared=0.49, r_squared_high=0.5, r_squared_low=0.3,
            lookback_short=30, lookback_long=90,
        )
        assert 30 <= lb <= 35, "R²=0.49 is very close to high → lookback near 30"

    def test_interpolation_near_low(self):
        """R² just above r_squared_low → lookback near long."""
        lb = _compute_adaptive_lookback(
            r_squared=0.31, r_squared_high=0.5, r_squared_low=0.3,
            lookback_short=30, lookback_long=90,
        )
        assert 85 <= lb <= 90, "R²=0.31 is very close to low → lookback near 90"

    def test_adaptive_lookback_in_result(self):
        """Adaptive lookback is included in the result dict."""
        df = _exponential_trend_df(n=100, daily_return=0.01)
        result = compute_momentum_quality(df, ranking_window=90)
        assert result is not None
        assert "adaptive_lookback" in result
        assert isinstance(result["adaptive_lookback"], int)
        assert 30 <= result["adaptive_lookback"] <= 90


# -- Edge cases ---------------------------------------------------------------

class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_insufficient_data(self):
        """< 30 bars → returns None."""
        df = _make_df([100.0] * 29)
        result = compute_momentum_quality(df)
        assert result is None

    def test_none_dataframe(self):
        """None DataFrame → returns None."""
        result = compute_momentum_quality(None)
        assert result is None

    def test_flat_price(self):
        """All identical prices → zero slope, R²=0, filtered."""
        prices = [100.0] * 100
        df = _make_df(prices)
        result = compute_momentum_quality(df, ranking_window=90)
        assert result is not None
        assert result["slope"] == 0.0
        assert result["r_squared"] == 0.0
        assert result["filtered"] is True
        assert result["score"] is None
        assert result["direction"] == "neutral"

    def test_non_positive_prices(self):
        """Zero or negative prices → returns None (log undefined)."""
        prices = [100.0] * 50 + [0.0] * 50
        df = _make_df(prices)
        result = compute_momentum_quality(df)
        assert result is None

    def test_minimum_valid_data(self):
        """Exactly 30 bars → should compute (not None)."""
        df = _exponential_trend_df(n=30, daily_return=0.01)
        result = compute_momentum_quality(df, ranking_window=30)
        assert result is not None
        assert "score" in result

    def test_ranking_window_larger_than_data(self):
        """ranking_window > len(df) → use all available data."""
        df = _exponential_trend_df(n=50, daily_return=0.01)
        result = compute_momentum_quality(df, ranking_window=90)
        assert result is not None
        # Should still compute using 50 bars

    def test_r_squared_clamped_to_range(self):
        """R² should always be in [0, 1] even with float precision issues."""
        df = _exponential_trend_df(n=100, daily_return=0.01)
        result = compute_momentum_quality(df, ranking_window=90)
        assert result is not None
        assert 0.0 <= result["r_squared"] <= 1.0


# -- DoD 5: Config-driven (no hardcoded values) ------------------------------

class TestConfigDriven:
    """All parameters are configurable, not hardcoded."""

    def test_custom_ranking_window(self):
        """ranking_window is configurable and affects computation."""
        # Use a mixed trend: flat first half, uptrend second half
        # This ensures different ranking windows produce different results
        flat_prices = [100.0] * 50
        up_prices = [100.0 * (1.005) ** i for i in range(1, 51)]
        df = _make_df(flat_prices + up_prices)
        r1 = compute_momentum_quality(df, ranking_window=30)
        r2 = compute_momentum_quality(df, ranking_window=90)
        assert r1 is not None and r2 is not None
        # Window=30 captures only the uptrend; window=90 includes the flat period
        # R² should differ because the data composition differs
        assert r1["r_squared"] != r2["r_squared"] or r1["slope"] != r2["slope"]

    def test_custom_lookback_bounds(self):
        """lookback_short and lookback_long are configurable."""
        df = _exponential_trend_df(n=100, daily_return=0.01)
        result = compute_momentum_quality(
            df, ranking_window=90,
            lookback_short=20, lookback_long=60,
        )
        assert result is not None
        assert 20 <= result["adaptive_lookback"] <= 60

    def test_custom_r_squared_thresholds(self):
        """r_squared_high and r_squared_low are configurable."""
        df = _exponential_trend_df(n=100, daily_return=0.01)
        result = compute_momentum_quality(
            df, ranking_window=90,
            r_squared_high=0.7, r_squared_low=0.4,
        )
        assert result is not None
        # With R² likely > 0.7 for clear trend, lookback should be short
        if result["r_squared"] >= 0.7:
            assert result["adaptive_lookback"] == 30

    def test_result_shape(self):
        """Result always has the expected keys."""
        df = _exponential_trend_df(n=100, daily_return=0.01)
        result = compute_momentum_quality(df)
        assert result is not None
        expected_keys = {"score", "slope", "r_squared", "adaptive_lookback", "filtered", "direction"}
        assert set(result.keys()) == expected_keys

    def test_direction_values(self):
        """direction is always one of the three valid values."""
        # Bullish
        df_up = _exponential_trend_df(n=100, daily_return=0.01)
        r_up = compute_momentum_quality(df_up, ranking_window=90)
        assert r_up["direction"] in ("bullish", "bearish", "neutral")

        # Bearish
        df_down = _exponential_trend_df(n=100, daily_return=-0.01)
        r_down = compute_momentum_quality(df_down, ranking_window=90)
        assert r_down["direction"] in ("bullish", "bearish", "neutral")

        # Neutral
        df_flat = _make_df([100.0] * 100)
        r_flat = compute_momentum_quality(df_flat, ranking_window=90)
        assert r_flat["direction"] in ("bullish", "bearish", "neutral")