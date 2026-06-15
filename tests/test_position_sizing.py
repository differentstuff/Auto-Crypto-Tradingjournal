"""
tests/test_position_sizing.py -- Tests for core.position_sizing pure functions.

Validates:
1. kelly_fraction: maps score to Kelly, respects min/max caps
2. compute_atr_cap: high ATR → small cap, low ATR → large cap
3. compute_size: position sizing with Kelly, ATR cap, min/max floor
4. compute_pnl: gross P&L for long and short
5. compute_net_pnl: net P&L after simulated fees
"""

import pytest

from core.position_sizing import (
    kelly_fraction,
    compute_atr_cap,
    compute_size,
    compute_pnl,
    compute_net_pnl,
)


# ── Kelly fraction ────────────────────────────────────────────────────────────

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


# ── ATR cap ──────────────────────────────────────────────────────────────────

class TestComputeAtrCap:
    def test_high_atr_small_cap(self):
        """High ATR → small cap (volatile asset gets constrained)."""
        cap = compute_atr_cap(10000, 100, 2.0)
        assert cap == pytest.approx(200.0, abs=0.01)

    def test_low_atr_large_cap(self):
        """Low ATR → large cap (calm asset, cap likely won't bind)."""
        cap = compute_atr_cap(10000, 10, 2.0)
        assert cap == pytest.approx(2000.0, abs=0.01)

    def test_zero_atr_returns_zero(self):
        cap = compute_atr_cap(10000, 0, 2.0)
        assert cap == 0.0

    def test_zero_equity_returns_zero(self):
        cap = compute_atr_cap(0, 100, 2.0)
        assert cap == 0.0

    def test_zero_cap_pct_returns_zero(self):
        cap = compute_atr_cap(10000, 100, 0)
        assert cap == 0.0


# ── Position sizing ──────────────────────────────────────────────────────────

class TestComputeSize:
    def test_basic_sizing(self):
        """Basic position sizing without ATR cap."""
        sizing = compute_size(
            equity=10000, entry_price=50000, sl_price=49000,
            direction="Long", kelly_frac=0.15, leverage=5,
            risk_per_trade_pct=1.0, max_size_pct=25.0, min_size_pct=5.0,
            atr_value=0, atr_cap_pct=0,
        )
        assert sizing["size_usdt"] > 0
        assert sizing["margin_usdt"] > 0
        assert sizing["atr_cap_applied"] is False

    def test_atr_cap_reduces_size(self):
        """High ATR → ATR cap reduces position below Kelly size."""
        sizing = compute_size(
            equity=10000, entry_price=50000, sl_price=49000,
            direction="Long", kelly_frac=0.25, leverage=5,
            risk_per_trade_pct=1.0, max_size_pct=25.0, min_size_pct=5.0,
            atr_value=100, atr_cap_pct=2.0,
        )
        assert sizing["atr_cap_applied"] is True
        assert sizing["size_usdt"] == 200.0  # ATR cap = (10000 * 2.0) / 100 = 200

    def test_atr_cap_overrides_min_floor(self):
        """ATR cap is a hard maximum that overrides min_size floor."""
        sizing = compute_size(
            equity=10000, entry_price=50000, sl_price=49000,
            direction="Long", kelly_frac=0.25, leverage=5,
            risk_per_trade_pct=1.0, max_size_pct=25.0, min_size_pct=5.0,
            atr_value=100, atr_cap_pct=2.0,
        )
        assert sizing["atr_cap_applied"] is True
        assert sizing["size_usdt"] == 200.0  # ATR cap, not min floor (500)

    def test_zero_equity_returns_empty(self):
        sizing = compute_size(
            equity=0, entry_price=50000, sl_price=49000,
            direction="Long", kelly_frac=0.15, leverage=5,
            risk_per_trade_pct=1.0, max_size_pct=25.0, min_size_pct=5.0,
        )
        assert sizing["size_usdt"] == 0


# ── Gross P&L ────────────────────────────────────────────────────────────────

class TestComputePnl:
    def test_long_winner(self):
        result = compute_pnl(entry_price=100, exit_price=110, direction="Long", size_usdt=1000)
        assert result["pnl_pct"] == pytest.approx(10.0)
        assert result["pnl_usdt"] == pytest.approx(100.0)

    def test_long_loser(self):
        result = compute_pnl(entry_price=100, exit_price=90, direction="Long", size_usdt=1000)
        assert result["pnl_pct"] == pytest.approx(-10.0)
        assert result["pnl_usdt"] == pytest.approx(-100.0)

    def test_short_winner(self):
        result = compute_pnl(entry_price=100, exit_price=90, direction="Short", size_usdt=1000)
        assert result["pnl_pct"] == pytest.approx(10.0)
        assert result["pnl_usdt"] == pytest.approx(100.0)

    def test_short_loser(self):
        result = compute_pnl(entry_price=100, exit_price=110, direction="Short", size_usdt=1000)
        assert result["pnl_pct"] == pytest.approx(-10.0)
        assert result["pnl_usdt"] == pytest.approx(-100.0)

    def test_zero_size_returns_zero(self):
        result = compute_pnl(entry_price=100, exit_price=110, direction="Long", size_usdt=0)
        assert result["pnl_pct"] == 0.0
        assert result["pnl_usdt"] == 0.0


# ── Net P&L (after fees) ────────────────────────────────────────────────────

class TestComputeNetPnl:
    def test_fees_deducted_from_winner(self):
        """Winning trade: fees reduce profit."""
        # Gross P&L = +$100, position_size = $1000, fee_rate = 0.06%
        # entry_fee = 1000 * 0.0006 = $0.60
        # exit_notional = 1000 + 100 = $1100
        # exit_fee = 1100 * 0.0006 = $0.66
        # net = 100 - 0.60 - 0.66 = $98.74
        net = compute_net_pnl(gross_pnl_usdt=100.0, position_size_usdt=1000.0, fee_rate=0.0006)
        assert net["net_pnl_usdt"] == pytest.approx(98.74, abs=0.01)
        assert net["entry_fee_usdt"] == pytest.approx(0.60, abs=0.01)
        assert net["exit_fee_usdt"] == pytest.approx(0.66, abs=0.01)
        assert net["total_fees_usdt"] == pytest.approx(1.26, abs=0.01)

    def test_fees_increase_loss(self):
        """Losing trade: fees increase loss."""
        # Gross P&L = -$50, position_size = $1000, fee_rate = 0.06%
        # entry_fee = 1000 * 0.0006 = $0.60
        # exit_notional = 1000 - 50 = $950
        # exit_fee = 950 * 0.0006 = $0.57
        # net = -50 - 0.60 - 0.57 = -$51.17
        net = compute_net_pnl(gross_pnl_usdt=-50.0, position_size_usdt=1000.0, fee_rate=0.0006)
        assert net["net_pnl_usdt"] == pytest.approx(-51.17, abs=0.01)

    def test_zero_fee_returns_gross(self):
        """Zero fee rate: net P&L equals gross P&L."""
        net = compute_net_pnl(gross_pnl_usdt=100.0, position_size_usdt=1000.0, fee_rate=0.0)
        assert net["net_pnl_usdt"] == 100.0
        assert net["total_fees_usdt"] == 0.0

    def test_bitget_taker_rate(self):
        """Verify with Bitget VIP0 taker rate (0.06%)."""
        # $500 position, 5% gross profit = $25
        # entry_fee = 500 * 0.0006 = $0.30
        # exit_notional = 525
        # exit_fee = 525 * 0.0006 = $0.315
        # net = 25 - 0.30 - 0.315 = $24.385
        net = compute_net_pnl(gross_pnl_usdt=25.0, position_size_usdt=500.0, fee_rate=0.0006)
        assert net["net_pnl_usdt"] == pytest.approx(24.39, abs=0.01)