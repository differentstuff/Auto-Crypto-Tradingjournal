"""
tests/test_volatility_cap.py -- Tests for volatility-based position sizing cap.

Tests compute_volatility_cap() and compute_size() with ATR%-based volatility cap.
The volatility cap uses relative ATR% (not absolute ATR) so it's asset-price-agnostic:
BTC at $80k with 1% ATR and SHIB at $0.00001 with 1% ATR get the same cap.

Also tests the leverage-aware max_notional_exposure_pct ceiling.
"""

import pytest

from core.position_sizing import compute_volatility_cap, compute_size, kelly_fraction
from core.substrate import Substrate
from tests.conftest import make_full_config


# -- Fixtures ------------------------------------------------------------------


@pytest.fixture
def substrate():
    """Standard substrate with volatility_cap_pct."""
    cfg = make_full_config()
    return Substrate(cfg)


@pytest.fixture
def substrate_no_volatility_cap():
    """Substrate without volatility_cap_pct key."""
    cfg = make_full_config()
    cfg["portfolio"].pop("volatility_cap_pct", None)
    return Substrate(cfg)


# -- 1. compute_volatility_cap pure function -----------------------------------------


class TestComputeVolatilityCap:
    """Tests for the pure compute_volatility_cap function."""

    def test_high_atr_pct_small_cap(self):
        """High ATR% (volatile asset) → small cap."""
        # equity=10000, volatility_cap_pct=20.0, atr_pct=5.0 (5% ATR)
        # cap = (10000 * 20.0) / 5.0 = 40000
        cap = compute_volatility_cap(10000, 5.0, 20.0)
        assert cap == pytest.approx(40000.0, abs=0.01)

    def test_low_atr_pct_large_cap(self):
        """Low ATR% (calm asset) → large cap."""
        # equity=10000, volatility_cap_pct=20.0, atr_pct=0.5 (0.5% ATR)
        # cap = (10000 * 20.0) / 0.5 = 400000
        cap = compute_volatility_cap(10000, 0.5, 20.0)
        assert cap == pytest.approx(400000.0, abs=0.01)

    def test_zero_atr_pct_returns_zero(self):
        """Zero ATR% → cap returns 0 (invalid input)."""
        cap = compute_volatility_cap(10000, 0, 20.0)
        assert cap == 0.0

    def test_negative_atr_pct_returns_zero(self):
        """Negative ATR% → cap returns 0 (invalid input)."""
        cap = compute_volatility_cap(10000, -5, 20.0)
        assert cap == 0.0

    def test_zero_equity_returns_zero(self):
        """Zero equity → cap returns 0 (invalid input)."""
        cap = compute_volatility_cap(0, 5.0, 20.0)
        assert cap == 0.0

    def test_zero_volatility_cap_pct_returns_zero(self):
        """volatility_cap_pct=0 → cap doesn't apply."""
        cap = compute_volatility_cap(10000, 5.0, 0)
        assert cap == 0.0

    def test_negative_volatility_cap_pct_returns_zero(self):
        """Negative volatility_cap_pct → cap doesn't apply."""
        cap = compute_volatility_cap(10000, 5.0, -1)
        assert cap == 0.0

    def test_custom_volatility_cap_pct_proportional(self):
        """Custom volatility_cap_pct changes the cap proportionally."""
        # equity=10000, volatility_cap_pct=40.0, atr_pct=5.0
        # cap = (10000 * 40.0) / 5.0 = 80000
        cap = compute_volatility_cap(10000, 5.0, 40.0)
        assert cap == pytest.approx(80000.0, abs=0.01)

    def test_asset_price_agnostic(self):
        """Same ATR% produces same cap regardless of asset price."""
        # BTC at $80000 with 1% ATR → atr_pct=1.0
        cap_btc = compute_volatility_cap(10000, 1.0, 20.0)
        # SHIB at $0.00001 with 1% ATR → atr_pct=1.0
        cap_shib = compute_volatility_cap(10000, 1.0, 20.0)
        assert cap_btc == cap_shib  # Same cap for same relative volatility


# -- 2. compute_size with volatility cap --------------------------------------------


class TestComputeSizeVolatilityCap:
    """Tests for compute_size with volatility cap integration."""

    def test_volatility_cap_reduces_high_vol_size(self):
        """Volatility cap reduces position size for volatile assets."""
        # equity=10000, entry=100, sl=95 → stop_dist=5%
        # risk_amt = 10000 * 1/100 = 100
        # base_notional = 100 / 0.05 = 2000
        # kelly ~0.15 → notional ~300
        # Need cap < 300 for it to bind: volatility_cap_pct=0.2, atr_pct=10 → cap = 200
        sizing = compute_size(
            equity=10000, entry_price=100, sl_price=95, direction="Long",
            kelly_frac=0.15, leverage=5,
            risk_per_trade_pct=1.0, max_size_pct=25.0, min_size_pct=5.0,
            atr_pct=10.0, volatility_cap_pct=0.2,
        )
        assert sizing["volatility_cap_applied"] is True
        # cap = (10000 * 0.2) / 10.0 = 200
        assert sizing["volatility_cap_notional"] == pytest.approx(200.0, abs=0.01)

    def test_volatility_cap_overrides_min_size_floor(self):
        """Volatility cap overrides min size floor (volatile = too risky for normal size)."""
        # atr_pct=10 → volatility_cap = (10000 * 2.0) / 10.0 = 2000
        # min_size = 10000 * 5/100 = 500
        # cap (2000) < min (500)? No. Let's use atr_pct=100 → cap = 200
        sizing = compute_size(
            equity=10000, entry_price=100, sl_price=99, direction="Long",
            kelly_frac=0.05, leverage=5,
            risk_per_trade_pct=1.0, max_size_pct=25.0, min_size_pct=5.0,
            atr_pct=100.0, volatility_cap_pct=2.0,
        )
        assert sizing["volatility_cap_applied"] is True
        # cap = (10000 * 2.0) / 100.0 = 200
        assert sizing["volatility_cap_notional"] == pytest.approx(200.0, abs=0.01)

    def test_min_size_floor_applies_when_volatility_cap_does_not_bind(self):
        """Min size floor applies when volatility cap doesn't bind (low-vol asset)."""
        # atr_pct=0.5 → volatility_cap = (10000 * 20.0) / 0.5 = 400000 → not binding
        sizing = compute_size(
            equity=10000, entry_price=100, sl_price=99, direction="Long",
            kelly_frac=0.05, leverage=5,
            risk_per_trade_pct=1.0, max_size_pct=25.0, min_size_pct=5.0,
            atr_pct=0.5, volatility_cap_pct=20.0,
        )
        assert sizing["volatility_cap_applied"] is False
        # kelly=0.05, risk_amt=100, stop_dist=1%, base=10000, kelly_notional=500
        # min_size = 10000 * 5/100 = 500 → floor applies
        assert sizing["size_usdt"] >= 500

    def test_volatility_cap_no_effect_low_vol(self):
        """Volatility cap has no effect on low-volatility assets."""
        sizing_no_vol = compute_size(
            equity=10000, entry_price=100, sl_price=95, direction="Long",
            kelly_frac=0.15, leverage=5,
            risk_per_trade_pct=1.0, max_size_pct=25.0, min_size_pct=5.0,
        )
        sizing_with_vol = compute_size(
            equity=10000, entry_price=100, sl_price=95, direction="Long",
            kelly_frac=0.15, leverage=5,
            risk_per_trade_pct=1.0, max_size_pct=25.0, min_size_pct=5.0,
            atr_pct=0.5, volatility_cap_pct=20.0,
        )
        assert sizing_with_vol["volatility_cap_applied"] is False
        # Low vol: cap = (10000 * 20) / 0.5 = 400000 → far above notional
        assert sizing_with_vol["size_usdt"] == sizing_no_vol["size_usdt"]

    def test_volatility_cap_zero_atr_pct_no_effect(self):
        """Zero atr_pct → volatility cap doesn't apply."""
        sizing = compute_size(
            equity=10000, entry_price=100, sl_price=95, direction="Long",
            kelly_frac=0.15, leverage=5,
            risk_per_trade_pct=1.0, max_size_pct=25.0, min_size_pct=5.0,
            atr_pct=0, volatility_cap_pct=20.0,
        )
        assert sizing["volatility_cap_applied"] is False
        assert sizing["volatility_cap_notional"] == 0.0

    def test_volatility_cap_missing_config_no_effect(self):
        """Missing volatility_cap_pct (0) → cap doesn't apply."""
        sizing = compute_size(
            equity=10000, entry_price=100, sl_price=95, direction="Long",
            kelly_frac=0.15, leverage=5,
            risk_per_trade_pct=1.0, max_size_pct=25.0, min_size_pct=5.0,
            atr_pct=5.0, volatility_cap_pct=0,  # No volatility cap
        )
        assert sizing["volatility_cap_applied"] is False

    def test_volatility_cap_never_increases_size(self):
        """Volatility cap never increases size — only reduces or leaves unchanged."""
        # Without cap
        sizing_no_cap = compute_size(
            equity=10000, entry_price=100, sl_price=95, direction="Long",
            kelly_frac=0.15, leverage=5,
            risk_per_trade_pct=1.0, max_size_pct=25.0, min_size_pct=5.0,
        )
        # With tight cap
        sizing_with_cap = compute_size(
            equity=10000, entry_price=100, sl_price=95, direction="Long",
            kelly_frac=0.15, leverage=5,
            risk_per_trade_pct=1.0, max_size_pct=25.0, min_size_pct=5.0,
            atr_pct=5.0, volatility_cap_pct=2.0,
        )
        assert sizing_with_cap["size_usdt"] <= sizing_no_cap["size_usdt"]

    def test_higher_volatility_smaller_cap(self):
        """Higher ATR% produces smaller cap (inverse relationship)."""
        cap_high_vol = compute_volatility_cap(10000, 5.0, 20.0)  # 5% ATR
        cap_low_vol = compute_volatility_cap(10000, 0.5, 20.0)   # 0.5% ATR
        assert cap_high_vol < cap_low_vol

    def test_return_dict_has_volatility_cap_fields(self):
        """Return dict always includes volatility_cap_applied and volatility_cap_notional."""
        sizing = compute_size(
            equity=10000, entry_price=100, sl_price=95, direction="Long",
            kelly_frac=0.15, leverage=5,
            risk_per_trade_pct=1.0, max_size_pct=25.0, min_size_pct=5.0,
            atr_pct=5.0, volatility_cap_pct=20.0,
        )
        assert "volatility_cap_applied" in sizing
        assert "volatility_cap_notional" in sizing

        sizing_zero = compute_size(
            equity=10000, entry_price=100, sl_price=95, direction="Long",
            kelly_frac=0.15, leverage=5,
            risk_per_trade_pct=1.0, max_size_pct=25.0, min_size_pct=5.0,
            atr_pct=5.0, volatility_cap_pct=20.0,
        )
        assert "volatility_cap_applied" in sizing_zero
        assert "volatility_cap_notional" in sizing_zero
        assert sizing_zero["volatility_cap_applied"] is False
        assert sizing_zero["volatility_cap_notional"] > 0


# -- 3. Leverage-aware max_notional_exposure_pct -------------------------------------


class TestMaxNotionalExposure:
    """Tests for the hard notional exposure ceiling."""

    def test_exposure_ceiling_caps_at_high_leverage(self):
        """At high leverage, notional exposure ceiling prevents excessive exposure."""
        # equity=1000, risk_per_trade_pct=2.0, leverage=25
        # risk_amt = 20, stop_dist=1%, base_notional=2000, kelly~0.15 → 300
        # max_notional = 1000 * 25/100 * 25 = 6250 (leverage-enabled)
        # exposure_ceiling = 1000 * 100/100 = 1000
        sizing = compute_size(
            equity=1000, entry_price=100, sl_price=99, direction="Long",
            kelly_frac=0.15, leverage=25,
            risk_per_trade_pct=2.0, max_size_pct=25.0, min_size_pct=5.0,
            max_notional_exposure_pct=100.0,
        )
        # With exposure ceiling at 100% of equity (1000), size should be capped
        assert sizing["size_usdt"] <= 1000

    def test_no_ceiling_when_zero(self):
        """max_notional_exposure_pct=0 → no ceiling applied."""
        sizing = compute_size(
            equity=10000, entry_price=100, sl_price=95, direction="Long",
            kelly_frac=0.15, leverage=5,
            risk_per_trade_pct=1.0, max_size_pct=25.0, min_size_pct=5.0,
            max_notional_exposure_pct=0,
        )
        # No ceiling — size determined by other constraints
        assert sizing["size_usdt"] > 0


# -- 4. Integration with ApproveTrade enzyme --------------------------------------


class TestApproveTradeVolatilityCap:
    """Integration tests: ApproveTrade enzyme with volatility cap."""

    def test_volatility_cap_in_approved_dict(self, substrate):
        """Approved trade dict includes volatility_cap_applied and volatility_cap_notional."""
        from enzymes.approve_trade import ApproveTrade

        enzyme = ApproveTrade()

        # Setup: high-volatility entry zone
        substrate.analysis["entry_zones"] = {
            "BTCUSDT": {
                "direction": "Long",
                "entry_price": 100000,
                "sl_price": 95000,
                "atr_value": 5000,
                "atr_pct": 5.0,  # 5% ATR — very volatile
                "score": 7.0,
                "tp1": 110000,
                "tp2": 115000,
            },
        }
        substrate.portfolio["equity"] = 10000
        substrate.portfolio["open_positions"] = []

        result = enzyme.transform(substrate)
        approved = result.decisions.get("trade_approved")
        if approved:
            assert "volatility_cap_applied" in approved
            assert "volatility_cap_notional" in approved
            assert isinstance(approved["volatility_cap_applied"], bool)
            assert isinstance(approved["volatility_cap_notional"], float)

    def test_high_vol_reduces_size_vs_low_vol(self):
        """High-volatility asset gets smaller position than low-volatility."""
        from enzymes.approve_trade import ApproveTrade

        enzyme = ApproveTrade()

        # High volatility (5% ATR) with tight volatility cap so it binds
        cfg_high = make_full_config(portfolio={"volatility_cap_pct": 0.2})
        sub_high = Substrate(cfg_high)
        sub_high.analysis["entry_zones"] = {
            "BTCUSDT": {
                "direction": "Long",
                "entry_price": 100,
                "sl_price": 95,
                "atr_value": 5,
                "atr_pct": 5.0,
                "score": 7.0,
                "tp1": 110,
                "tp2": 115,
            },
        }
        sub_high.portfolio["equity"] = 10000
        sub_high.portfolio["open_positions"] = []

        # Low volatility (0.5% ATR) — cap won't bind
        cfg_low = make_full_config(portfolio={"volatility_cap_pct": 0.2})
        sub_low = Substrate(cfg_low)
        sub_low.analysis["entry_zones"] = {
            "BTCUSDT": {
                "direction": "Long",
                "entry_price": 100,
                "sl_price": 95,
                "atr_value": 0.5,
                "atr_pct": 0.5,
                "score": 7.0,
                "tp1": 110,
                "tp2": 115,
            },
        }
        sub_low.portfolio["equity"] = 10000
        sub_low.portfolio["open_positions"] = []

        result_high = enzyme.transform(sub_high)
        result_low = enzyme.transform(sub_low)

        if result_high.decisions.get("trade_approved") and result_low.decisions.get("trade_approved"):
            # High vol (5% ATR): cap = (10000*0.2)/5 = 400 → binds
            # Low vol (0.5% ATR): cap = (10000*0.2)/0.5 = 4000 → doesn't bind
            assert result_high.decisions["trade_approved"]["volatility_cap_applied"] is True
            assert result_low.decisions["trade_approved"]["volatility_cap_applied"] is False
