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

from enzymes.approve_trade import _compute_atr_cap, _compute_size, _kelly_fraction
from conftest import make_full_config


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def config():
    """Standard config with atr_cap_equity_pct."""
    return make_full_config()


@pytest.fixture
def config_no_atr_cap():
    """Config without atr_cap_equity_pct key."""
    cfg = make_full_config()
    cfg["portfolio"].pop("atr_cap_equity_pct", None)
    return cfg


# ── 1. _compute_atr_cap pure function ─────────────────────────────────────────

class TestComputeAtrCap:
    def test_high_atr_small_cap(self, config):
        """High ATR → small cap (volatile asset gets constrained)."""
        # equity=10000, atr_cap_equity_pct=2.0, ATR=100
        # cap = (10000 * 2.0) / 100 = 200
        cap = _compute_atr_cap(10000, 100, config)
        assert cap == pytest.approx(200.0, abs=0.01)

    def test_low_atr_large_cap(self, config):
        """Low ATR → large cap (calm asset, cap likely won't bind)."""
        # equity=10000, atr_cap_equity_pct=2.0, ATR=10
        # cap = (10000 * 2.0) / 10 = 2000
        cap = _compute_atr_cap(10000, 10, config)
        assert cap == pytest.approx(2000.0, abs=0.01)

    def test_zero_atr_returns_zero(self, config):
        """ATR=0 → cap doesn't apply (graceful degradation)."""
        cap = _compute_atr_cap(10000, 0, config)
        assert cap == 0.0

    def test_negative_atr_returns_zero(self, config):
        """Negative ATR → cap doesn't apply."""
        cap = _compute_atr_cap(10000, -5, config)
        assert cap == 0.0

    def test_zero_equity_returns_zero(self, config):
        """Zero equity → cap doesn't apply."""
        cap = _compute_atr_cap(0, 100, config)
        assert cap == 0.0

    def test_missing_config_returns_zero(self, config_no_atr_cap):
        """Missing atr_cap_equity_pct → cap doesn't apply."""
        cap = _compute_atr_cap(10000, 100, config_no_atr_cap)
        assert cap == 0.0

    def test_zero_cap_pct_returns_zero(self):
        """atr_cap_equity_pct=0 → cap doesn't apply."""
        cfg = make_full_config()
        cfg["portfolio"]["atr_cap_equity_pct"] = 0
        cap = _compute_atr_cap(10000, 100, cfg)
        assert cap == 0.0

    def test_negative_cap_pct_returns_zero(self):
        """Negative atr_cap_equity_pct → cap doesn't apply."""
        cfg = make_full_config()
        cfg["portfolio"]["atr_cap_equity_pct"] = -1
        cap = _compute_atr_cap(10000, 100, cfg)
        assert cap == 0.0

    def test_custom_cap_pct(self):
        """Custom atr_cap_equity_pct changes the cap proportionally."""
        cfg = make_full_config()
        cfg["portfolio"]["atr_cap_equity_pct"] = 4.0
        # equity=10000, atr_cap_equity_pct=4.0, ATR=100
        # cap = (10000 * 4.0) / 100 = 400
        cap = _compute_atr_cap(10000, 100, cfg)
        assert cap == pytest.approx(400.0, abs=0.01)


# ── 2. _compute_size with ATR cap ─────────────────────────────────────────────

class TestComputeSizeWithAtrCap:
    def test_atr_cap_reduces_high_vol_size(self):
        """High ATR → ATR cap reduces position below Kelly size."""
        # Use config with min_size_pct=0 so the floor doesn't override the ATR cap
        cfg = make_full_config()
        cfg["risk"]["min_size_pct_of_equity"] = 0.0
        # equity=10000, risk_per_trade_pct=1.0,
        # kelly_fraction=0.25, leverage=5, max_size_pct=25
        # Without ATR cap: notional = (10000 * 1.0/100) / 0.02 * 0.25 = 1250
        # ATR cap with ATR=100: (10000 * 2.0) / 100 = 200
        # So ATR cap should bind: size = 200
        sizing = _compute_size(
            equity=10000,
            entry_price=50000,
            sl_price=49000,  # 2% stop distance
            direction="Long",
            kelly_fraction=0.25,
            leverage=5,
            config=cfg,
            atr_value=100,
        )
        assert sizing["atr_cap_applied"] is True
        assert sizing["size_usdt"] == 200.0
        assert sizing["atr_cap_notional"] == pytest.approx(200.0, abs=0.01)


    def test_atr_cap_no_effect_low_vol(self, config):
        """Low ATR → ATR cap doesn't bind, Kelly size unchanged."""
        # ATR cap with ATR=10: (10000 * 2.0) / 10 = 2000
        # Kelly size is ~1250, which is < 2000, so cap doesn't bind
        sizing_no_atr = _compute_size(
            equity=10000,
            entry_price=50000,
            sl_price=49000,
            direction="Long",
            kelly_fraction=0.25,
            leverage=5,
            config=config,
            atr_value=0,  # no ATR cap
        )
        sizing_with_atr = _compute_size(
            equity=10000,
            entry_price=50000,
            sl_price=49000,
            direction="Long",
            kelly_fraction=0.25,
            leverage=5,
            config=config,
            atr_value=10,  # low ATR → large cap
        )
        assert sizing_with_atr["atr_cap_applied"] is False
        assert sizing_with_atr["size_usdt"] == sizing_no_atr["size_usdt"]

    def test_atr_cap_zero_atr_no_effect(self, config):
        """ATR=0 → cap doesn't apply, same as no ATR."""
        sizing = _compute_size(
            equity=10000,
            entry_price=50000,
            sl_price=49000,
            direction="Long",
            kelly_fraction=0.25,
            leverage=5,
            config=config,
            atr_value=0,
        )
        assert sizing["atr_cap_applied"] is False
        assert sizing["atr_cap_notional"] == 0.0

    def test_atr_cap_missing_config_no_effect(self, config_no_atr_cap):
        """Missing atr_cap_equity_pct → cap doesn't apply."""
        sizing = _compute_size(
            equity=10000,
            entry_price=50000,
            sl_price=49000,
            direction="Long",
            kelly_fraction=0.25,
            leverage=5,
            config=config_no_atr_cap,
            atr_value=100,
        )
        assert sizing["atr_cap_applied"] is False

    def test_atr_cap_never_increases_size(self, config):
        """ATR cap can only reduce position size, never increase it."""
        # Compute size without ATR cap
        sizing_baseline = _compute_size(
            equity=10000,
            entry_price=50000,
            sl_price=49000,
            direction="Long",
            kelly_fraction=0.25,
            leverage=5,
            config=config,
            atr_value=0,
        )
        # Compute size with various ATR values
        for atr in [1, 5, 10, 50, 100, 500, 1000]:
            sizing = _compute_size(
                equity=10000,
                entry_price=50000,
                sl_price=49000,
                direction="Long",
                kelly_fraction=0.25,
                leverage=5,
                config=config,
                atr_value=atr,
            )
            assert sizing["size_usdt"] <= sizing_baseline["size_usdt"], (
                f"ATR cap increased size with ATR={atr}: "
                f"{sizing['size_usdt']} > {sizing_baseline['size_usdt']}"
            )

    def test_validation_examples_from_design(self, config):
        """Exact validation examples from the design YAML.

        equity=10000, atr_cap_equity_pct=2.0:
          - ATR=100 → max_position = 200
          - ATR=10  → max_position = 2000
        """
        cap_high = _compute_atr_cap(10000, 100, config)
        cap_low = _compute_atr_cap(10000, 10, config)
        assert cap_high == pytest.approx(200.0, abs=0.01)
        assert cap_low == pytest.approx(2000.0, abs=0.01)

    def test_return_dict_has_atr_cap_fields(self, config):
        """Return dict always includes atr_cap_applied and atr_cap_notional."""
        # Normal case
        sizing = _compute_size(
            equity=10000, entry_price=50000, sl_price=49000,
            direction="Long", kelly_fraction=0.25, leverage=5,
            config=config, atr_value=100,
        )
        assert "atr_cap_applied" in sizing
        assert "atr_cap_notional" in sizing

        # Zero equity case
        sizing_zero = _compute_size(
            equity=0, entry_price=50000, sl_price=49000,
            direction="Long", kelly_fraction=0.25, leverage=5,
            config=config, atr_value=100,
        )
        assert "atr_cap_applied" in sizing_zero
        assert "atr_cap_notional" in sizing_zero
        assert sizing_zero["atr_cap_applied"] is False
        assert sizing_zero["atr_cap_notional"] == 0.0


# ── 3. Full enzyme integration ────────────────────────────────────────────────

class TestApproveTradeWithAtrCap:
    def test_atr_cap_in_approved_dict(self, config):
        """Approved trade dict includes atr_cap_applied and atr_cap_notional."""
        from core.substrate import Substrate
        import enzymes  # noqa: F401 — trigger registration

        substrate = Substrate(config=config)
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

    def test_high_atr_gets_smaller_approved_size(self, config):
        """High-ATR asset gets smaller approved size than low-ATR asset."""
        from core.substrate import Substrate
        import enzymes  # noqa: F401

        # High ATR scenario
        substrate_high = Substrate(config=config)
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
        substrate_low = Substrate(config=config)
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