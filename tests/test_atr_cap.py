"""
tests/test_atr_cap.py -- Tests for ATR-based position sizing cap.

Validates that:
1. High-ATR assets get smaller positions than Kelly alone
2. Low-ATR assets are unaffected (Kelly size unchanged)
3. ATR cap is a MAXIMUM — can only reduce, never increase
4. Graceful degradation when ATR or config is missing
5. Exact validation examples from design YAML
6. Full enzyme integration with ATR cap
"""

import pytest

from core.substrate import Substrate
from enzymes.approve_trade import _compute_atr_cap, _compute_size, _kelly_fraction
from conftest import make_full_config


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_substrate(**overrides) -> Substrate:
    """Create a Substrate with full config for testing."""
    return Substrate(config=make_full_config(**overrides))


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def substrate():
    """Standard substrate with atr_cap_equity_pct."""
    return _make_substrate()


@pytest.fixture
def substrate_no_atr_cap():
    """Substrate without atr_cap_equity_pct key."""
    cfg = make_full_config()
    cfg["portfolio"].pop("atr_cap_equity_pct", None)
    return Substrate(config=cfg)


# ── 1. _compute_atr_cap pure function ─────────────────────────────────────────

class TestComputeAtrCap:
    def test_high_atr_small_cap(self, substrate):
        """High ATR → small cap (volatile asset gets constrained)."""
        # equity=10000, atr_cap_equity_pct=2.0, ATR=100
        # cap = (10000 * 2.0) / 100 = 200
        cap = _compute_atr_cap(10000, 100, substrate)
        assert cap == pytest.approx(200.0, abs=0.01)

    def test_low_atr_large_cap(self, substrate):
        """Low ATR → large cap (calm asset, cap likely won't bind)."""
        # equity=10000, atr_cap_equity_pct=2.0, ATR=10
        # cap = (10000 * 2.0) / 10 = 2000
        cap = _compute_atr_cap(10000, 10, substrate)
        assert cap == pytest.approx(2000.0, abs=0.01)

    def test_zero_atr_returns_zero(self, substrate):
        """ATR=0 → cap doesn't apply (graceful degradation)."""
        cap = _compute_atr_cap(10000, 0, substrate)
        assert cap == 0.0

    def test_negative_atr_returns_zero(self, substrate):
        """Negative ATR → cap doesn't apply."""
        cap = _compute_atr_cap(10000, -5, substrate)
        assert cap == 0.0

    def test_zero_equity_returns_zero(self, substrate):
        """Zero equity → cap doesn't apply."""
        cap = _compute_atr_cap(0, 100, substrate)
        assert cap == 0.0

    def test_missing_config_returns_zero(self, substrate_no_atr_cap):
        """Missing atr_cap_equity_pct → cap doesn't apply."""
        cap = _compute_atr_cap(10000, 100, substrate_no_atr_cap)
        assert cap == 0.0

    def test_zero_cap_pct_returns_zero(self):
        """atr_cap_equity_pct=0 → cap doesn't apply."""
        sub = _make_substrate(portfolio={"atr_cap_equity_pct": 0})
        cap = _compute_atr_cap(10000, 100, sub)
        assert cap == 0.0

    def test_negative_cap_pct_returns_zero(self):
        """Negative atr_cap_equity_pct → cap doesn't apply."""
        sub = _make_substrate(portfolio={"atr_cap_equity_pct": -1})
        cap = _compute_atr_cap(10000, 100, sub)
        assert cap == 0.0

    def test_custom_cap_pct(self):
        """Custom atr_cap_equity_pct changes the cap proportionally."""
        sub = _make_substrate(portfolio={"atr_cap_equity_pct": 4.0})
        # equity=10000, atr_cap_equity_pct=4.0, ATR=100
        # cap = (10000 * 4.0) / 100 = 400
        cap = _compute_atr_cap(10000, 100, sub)
        assert cap == pytest.approx(400.0, abs=0.01)


# ── 2. _compute_size with ATR cap ─────────────────────────────────────────────

class TestComputeSizeWithAtrCap:
    def test_atr_cap_reduces_high_vol_size(self):
        """High ATR → ATR cap reduces position below Kelly size."""
        sub = _make_substrate()
        sizing = _compute_size(
            equity=10000,
            entry_price=50000,
            sl_price=49000,  # 2% stop distance
            direction="Long",
            kelly_fraction=0.25,
            leverage=5,
            substrate=sub,
            atr_value=100,
        )
        assert sizing["atr_cap_applied"] is True
        assert sizing["size_usdt"] == 200.0
        assert sizing["atr_cap_notional"] == pytest.approx(200.0, abs=0.01)

    def test_atr_cap_overrides_min_size_floor(self):
        """ATR cap is a hard maximum that overrides the min_size_pct floor."""
        sub = _make_substrate()
        # min_size_pct_of_equity=5.0 → min_notional = 500
        # ATR cap with ATR=100 → atr_cap_notional = 200
        # ATR cap must win: size = 200, not 500
        sizing = _compute_size(
            equity=10000,
            entry_price=50000,
            sl_price=49000,
            direction="Long",
            kelly_fraction=0.25,
            leverage=5,
            substrate=sub,
            atr_value=100,
        )
        assert sizing["atr_cap_applied"] is True
        assert sizing["size_usdt"] == 200.0  # ATR cap, not min floor (500)

    def test_min_size_floor_applies_when_atr_cap_does_not_bind(self):
        """min_size_pct floor still applies when ATR cap doesn't bind."""
        sub = _make_substrate()
        # Very small kelly → notional below min_size_pct floor
        # Low ATR → ATR cap doesn't bind
        sizing = _compute_size(
            equity=10000,
            entry_price=50000,
            sl_price=49000,
            direction="Long",
            kelly_fraction=0.01,  # tiny kelly → notional well below min floor
            leverage=5,
            substrate=sub,
            atr_value=10,  # low ATR → cap = 2000, won't bind
        )
        assert sizing["atr_cap_applied"] is False
        # min_size_pct_of_equity=5.0 → min_notional = 500
        assert sizing["size_usdt"] == 500.0

    def test_atr_cap_no_effect_low_vol(self, substrate):
        """Low ATR → ATR cap doesn't bind, Kelly size unchanged."""
        sizing_no_atr = _compute_size(
            equity=10000,
            entry_price=50000,
            sl_price=49000,
            direction="Long",
            kelly_fraction=0.25,
            leverage=5,
            substrate=substrate,
            atr_value=0,  # no ATR cap
        )
        sizing_with_atr = _compute_size(
            equity=10000,
            entry_price=50000,
            sl_price=49000,
            direction="Long",
            kelly_fraction=0.25,
            leverage=5,
            substrate=substrate,
            atr_value=10,  # low ATR → large cap
        )
        assert sizing_with_atr["atr_cap_applied"] is False
        assert sizing_with_atr["size_usdt"] == sizing_no_atr["size_usdt"]

    def test_atr_cap_zero_atr_no_effect(self, substrate):
        """ATR=0 → cap doesn't apply, same as no ATR."""
        sizing = _compute_size(
            equity=10000,
            entry_price=50000,
            sl_price=49000,
            direction="Long",
            kelly_fraction=0.25,
            leverage=5,
            substrate=substrate,
            atr_value=0,
        )
        assert sizing["atr_cap_applied"] is False
        assert sizing["atr_cap_notional"] == 0.0

    def test_atr_cap_missing_config_no_effect(self, substrate_no_atr_cap):
        """Missing atr_cap_equity_pct → cap doesn't apply."""
        sizing = _compute_size(
            equity=10000,
            entry_price=50000,
            sl_price=49000,
            direction="Long",
            kelly_fraction=0.25,
            leverage=5,
            substrate=substrate_no_atr_cap,
            atr_value=100,
        )
        assert sizing["atr_cap_applied"] is False

    def test_atr_cap_never_increases_size(self, substrate):
        """ATR cap can only reduce position size, never increase it."""
        sizing_baseline = _compute_size(
            equity=10000,
            entry_price=50000,
            sl_price=49000,
            direction="Long",
            kelly_fraction=0.25,
            leverage=5,
            substrate=substrate,
            atr_value=0,
        )
        for atr in [1, 5, 10, 50, 100, 500, 1000]:
            sizing = _compute_size(
                equity=10000,
                entry_price=50000,
                sl_price=49000,
                direction="Long",
                kelly_fraction=0.25,
                leverage=5,
                substrate=substrate,
                atr_value=atr,
            )
            assert sizing["size_usdt"] <= sizing_baseline["size_usdt"], (
                f"ATR cap increased size with ATR={atr}: "
                f"{sizing['size_usdt']} > {sizing_baseline['size_usdt']}"
            )

    def test_validation_examples_from_design(self, substrate):
        """Exact validation examples from the design YAML."""
        cap_high = _compute_atr_cap(10000, 100, substrate)
        cap_low = _compute_atr_cap(10000, 10, substrate)
        assert cap_high == pytest.approx(200.0, abs=0.01)
        assert cap_low == pytest.approx(2000.0, abs=0.01)

    def test_return_dict_has_atr_cap_fields(self, substrate):
        """Return dict always includes atr_cap_applied and atr_cap_notional."""
        sizing = _compute_size(
            equity=10000, entry_price=50000, sl_price=49000,
            direction="Long", kelly_fraction=0.25, leverage=5,
            substrate=substrate, atr_value=100,
        )
        assert "atr_cap_applied" in sizing
        assert "atr_cap_notional" in sizing

        # Zero equity case
        sizing_zero = _compute_size(
            equity=0, entry_price=50000, sl_price=49000,
            direction="Long", kelly_fraction=0.25, leverage=5,
            substrate=substrate, atr_value=100,
        )
        assert "atr_cap_applied" in sizing_zero
        assert "atr_cap_notional" in sizing_zero
        assert sizing_zero["atr_cap_applied"] is False
        assert sizing_zero["atr_cap_notional"] == 0.0


# ── 3. Full enzyme integration ────────────────────────────────────────────────

class TestApproveTradeWithAtrCap:
    def test_atr_cap_in_approved_dict(self, substrate):
        """Approved trade dict includes atr_cap_applied and atr_cap_notional."""
        import enzymes  # noqa: F401 — trigger registration

        substrate.portfolio["equity"] = 10000
        substrate.analysis["entry_zones"] = {
            "BTCUSDT": {
                "direction": "Long",
                "entry_price": 50000,
                "sl_price": 49000,
                "tp1": 52000,
                "tp2": 55000,
                "score": 7.0,
                "atr_value": 100,  # High ATR → cap should apply
            },
        }

        from core.enzyme import create_enzyme
        enz = create_enzyme("ApproveTrade")
        result = enz.transform(substrate)

        approved = result.decisions.get("trade_approved")
        assert approved is not None
        assert "atr_cap_applied" in approved
        assert "atr_cap_notional" in approved
        assert isinstance(approved["atr_cap_applied"], bool)
        assert isinstance(approved["atr_cap_notional"], float)

    def test_high_atr_gets_smaller_approved_size(self, substrate):
        """High-ATR asset gets smaller approved size than low-ATR asset."""
        import enzymes  # noqa: F401

        # High ATR scenario
        substrate_high = _make_substrate()
        substrate_high.portfolio["equity"] = 10000
        substrate_high.analysis["entry_zones"] = {
            "BTCUSDT": {
                "direction": "Long",
                "entry_price": 50000,
                "sl_price": 49000,
                "tp1": 52000,
                "tp2": 55000,
                "score": 7.0,
                "atr_value": 100,
            },
        }

        # Low ATR scenario
        substrate_low = _make_substrate()
        substrate_low.portfolio["equity"] = 10000
        substrate_low.analysis["entry_zones"] = {
            "BTCUSDT": {
                "direction": "Long",
                "entry_price": 50000,
                "sl_price": 49000,
                "tp1": 52000,
                "tp2": 55000,
                "score": 7.0,
                "atr_value": 10,
            },
        }

        from core.enzyme import create_enzyme
        enz = create_enzyme("ApproveTrade")

        result_high = enz.transform(substrate_high)
        result_low = enz.transform(substrate_low)

        size_high = result_high.decisions["trade_approved"]["size_usdt"]
        size_low = result_low.decisions["trade_approved"]["size_usdt"]

        # High ATR should result in smaller position
        assert size_high < size_low
        # High ATR should have cap applied
        assert result_high.decisions["trade_approved"]["atr_cap_applied"] is True
        # Low ATR should NOT have cap applied
        assert result_low.decisions["trade_approved"]["atr_cap_applied"] is False