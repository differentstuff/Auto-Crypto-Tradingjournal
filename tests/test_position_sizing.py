"""
tests/test_position_sizing.py -- Tests for core.position_sizing pure functions.

Validates:
1. kelly_fraction: maps score to Kelly, respects min/max caps
2. compute_volatility_cap: high ATR% -> small cap, low ATR% -> large cap
3. compute_size: position sizing with Kelly, volatility cap, min/max floor
4. compute_gross_pnl: gross P&L for long and short
"""

import pytest

from core.position_sizing import (
    kelly_fraction,
    compute_volatility_cap,
    compute_size,
    compute_gross_pnl,
)


# -- Kelly fraction ------------------------------------------------------------

class TestKellyFraction:
    def test_score_maps_to_win_rate(self):
        """Score 0 → wr_base, score 10 → wr_base + wr_range."""
        kf_low = kelly_fraction(0, kelly_min=0.05, kelly_max=0.25, wr_base=0.35, wr_range=0.40, avg_win_r=2.0)
        kf_high = kelly_fraction(10, kelly_min=0.05, kelly_max=0.25, wr_base=0.35, wr_range=0.40, avg_win_r=2.0)
        assert kf_low < kf_high  # Higher score → higher Kelly

    def test_capped_at_kelly_max(self):
        """Very high score should cap at kelly_max."""
        kf = kelly_fraction(10, kelly_min=0.05, kelly_max=0.25, wr_base=0.35, wr_range=0.40, avg_win_r=2.0)
        assert kf <= 0.25

    def test_floored_at_kelly_min(self):
        """Very low score should floor at kelly_min."""
        kf = kelly_fraction(0, kelly_min=0.05, kelly_max=0.25, wr_base=0.35, wr_range=0.40, avg_win_r=2.0)
        assert kf >= 0.05

    def test_default_params(self):
        """Default params match production config values."""
        kf = kelly_fraction(7.0)  # Uses defaults
        assert 0.05 <= kf <= 0.25


# -- Volatility cap ----------------------------------------------------------

class TestComputeVolatilityCap:
    def test_high_atr_pct_small_cap(self):
        """High ATR% → small cap (volatile asset gets constrained)."""
        # equity=10000, volatility_cap_pct=2.0, atr_pct=5.0 → cap = (10000*2)/5 = 4000
        cap = compute_volatility_cap(10000, 5.0, 2.0)
        assert cap == pytest.approx(4000.0, abs=0.01)

    def test_low_atr_pct_large_cap(self):
        """Low ATR% → large cap (calm asset, cap likely won't bind)."""
        # equity=10000, volatility_cap_pct=2.0, atr_pct=0.5 → cap = (10000*2)/0.5 = 40000
        cap = compute_volatility_cap(10000, 0.5, 2.0)
        assert cap == pytest.approx(40000.0, abs=0.01)

    def test_zero_atr_pct_returns_zero(self):
        cap = compute_volatility_cap(10000, 0, 2.0)
        assert cap == 0.0

    def test_zero_equity_returns_zero(self):
        cap = compute_volatility_cap(0, 5.0, 2.0)
        assert cap == 0.0

    def test_zero_cap_pct_returns_zero(self):
        cap = compute_volatility_cap(10000, 5.0, 0)
        assert cap == 0.0


# -- Position sizing ----------------------------------------------------------

class TestComputeSize:
    def test_basic_sizing(self):
        """Basic position sizing without volatility cap."""
        sizing = compute_size(
            equity=10000, entry_price=50000, sl_price=49000,
            direction="Long", kelly_frac=0.15, leverage=5,
            risk_per_trade_pct=1.0, max_size_pct=25.0, min_size_pct=5.0,
            atr_pct=0, volatility_cap_pct=0,
        )
        assert sizing["size_usdt"] > 0
        assert sizing["margin_usdt"] > 0
        assert sizing["volatility_cap_applied"] is False

    def test_volatility_cap_reduces_size(self):
        """High ATR% → volatility cap reduces position below Kelly size."""
        # equity=10000, atr_pct=5.0, volatility_cap_pct=0.5 → cap = (10000*0.5)/5.0 = 1000
        # stop_dist=2%, risk_amt=100, base_notional=5000, kelly=0.25 → 1250
        # 1250 > 1000 → cap binds at 1000
        sizing = compute_size(
            equity=10000, entry_price=50000, sl_price=49000,
            direction="Long", kelly_frac=0.25, leverage=5,
            risk_per_trade_pct=1.0, max_size_pct=25.0, min_size_pct=5.0,
            atr_pct=5.0, volatility_cap_pct=0.5,
        )
        assert sizing["volatility_cap_applied"] is True
        assert sizing["size_usdt"] == 1000.0  # volatility cap = (10000 * 0.5) / 5.0 = 1000

    def test_volatility_cap_overrides_min_floor(self):
        """Volatility cap overrides min_size floor when it binds below it."""
        # equity=10000, atr_pct=10.0, volatility_cap_pct=0.3 → cap = (10000*0.3)/10 = 300
        # stop_dist=2%, risk_amt=100, base_notional=5000, kelly=0.25 → 1250
        # min_size = 10000 * 5/100 = 500
        # cap=300 < min=500 → cap overrides floor
        sizing = compute_size(
            equity=10000, entry_price=50000, sl_price=49000,
            direction="Long", kelly_frac=0.25, leverage=5,
            risk_per_trade_pct=1.0, max_size_pct=25.0, min_size_pct=5.0,
            atr_pct=10.0, volatility_cap_pct=0.3,
        )
        assert sizing["volatility_cap_applied"] is True
        assert sizing["size_usdt"] == 300.0  # volatility cap overrides min floor (500)

    def test_zero_equity_returns_empty(self):
        sizing = compute_size(
            equity=0, entry_price=50000, sl_price=49000,
            direction="Long", kelly_frac=0.15, leverage=5,
            risk_per_trade_pct=1.0, max_size_pct=25.0, min_size_pct=5.0,
        )
        assert sizing["size_usdt"] == 0


# -- Gross P&L ----------------------------------------------------------------

class TestComputeGrossPnl:
    def test_long_winner(self):
        result = compute_gross_pnl(entry_price=100, exit_price=110, direction="Long", size_usdt=1000)
        assert result["pnl_pct"] == pytest.approx(10.0)
        assert result["pnl_usdt"] == pytest.approx(100.0)

    def test_long_loser(self):
        result = compute_gross_pnl(entry_price=100, exit_price=90, direction="Long", size_usdt=1000)
        assert result["pnl_pct"] == pytest.approx(-10.0)
        assert result["pnl_usdt"] == pytest.approx(-100.0)

    def test_short_winner(self):
        result = compute_gross_pnl(entry_price=100, exit_price=90, direction="Short", size_usdt=1000)
        assert result["pnl_pct"] == pytest.approx(10.0)
        assert result["pnl_usdt"] == pytest.approx(100.0)

    def test_short_loser(self):
        result = compute_gross_pnl(entry_price=100, exit_price=110, direction="Short", size_usdt=1000)
        assert result["pnl_pct"] == pytest.approx(-10.0)
        assert result["pnl_usdt"] == pytest.approx(-100.0)

    def test_zero_size_returns_zero(self):
        result = compute_gross_pnl(entry_price=100, exit_price=110, direction="Long", size_usdt=0)
        assert result["pnl_pct"] == 0.0
        assert result["pnl_usdt"] == 0.0
