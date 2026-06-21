"""
tests/test_substrate.py -- Tests for the Substrate state container.

Exchange-as-truth: substrate is ephemeral. No persistence methods.
Tests cover creation, ISC verification, cycle reset, shallow copy.
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.substrate import Substrate, ISCCheck, SubstrateConfigError
from conftest import make_full_config


def _get_isc(sub: Substrate, isc_id: str) -> ISCCheck:
    """Find an ISCCheck by its ID from the substrate's validity list."""
    for isc in sub.validity:
        if isc.id == isc_id:
            return isc
    raise KeyError(f"ISC {isc_id!r} not found in substrate.validity")


class TestSubstrateCreation:
    """Test substrate initialization from config."""

    def test_creation_from_config(self):
        config = make_full_config()
        sub = Substrate(config=config)
        assert sub.strategy["name"] == "test_strategy"
        assert sub.portfolio["equity"] == 0.0
        assert sub.market["symbols_watched"] == ["BTCUSDT", "ETHUSDT"]
        assert len(sub.validity) == 4

    def test_creation_without_config_raises(self):
        with pytest.raises(SubstrateConfigError):
            Substrate()

    def test_creation_without_validity_raises(self):
        config = make_full_config()
        del config["validity"]
        with pytest.raises(SubstrateConfigError, match="validity"):
            Substrate(config=config)

    def test_creation_from_config_override(self):
        config = make_full_config(
            strategy={"name": "my_strat", "max_positions": 5},
            portfolio={"max_positions": 5, "risk_per_trade_pct": 2.0},
        )
        sub = Substrate(config=config)
        assert sub.strategy["name"] == "my_strat"
        assert sub.portfolio["max_positions"] == 5

    def test_cfg_method(self):
        config = make_full_config(scoring={"entry_threshold": 7.5})
        sub = Substrate(config=config)
        assert sub.cfg("scoring.entry_threshold") == 7.5
        with pytest.raises(ValueError):
            sub.cfg("nonexistent.path")
        assert sub.cfg("nonexistent.path", 99) == 99


class TestSubstrateISC:
    """Test ISC verification."""

    def test_isc_initial_state(self):
        sub = Substrate(config=make_full_config())
        for isc in sub.validity:
            assert isc.status == "pending"

    def test_isc_004_vacuously_true(self):
        sub = Substrate(config=make_full_config())
        assert sub._evaluate_isc(_get_isc(sub, "ISC-004")) is True

    def test_isc_001_fails_no_candidates(self):
        sub = Substrate(config=make_full_config())
        assert sub._evaluate_isc(_get_isc(sub, "ISC-001")) is False

    def test_isc_001_passes_with_candidates(self):
        config = make_full_config(scoring={"entry_threshold": 6.5})
        sub = Substrate(config=config)
        sub.analysis["candidates"] = [{"symbol": "BTCUSDT", "score": 7.0}]
        assert sub._evaluate_isc(_get_isc(sub, "ISC-001")) is True

    def test_isc_001_uses_config_threshold(self):
        config = make_full_config(scoring={"entry_threshold": 8.0})
        sub = Substrate(config=config)
        sub.analysis["candidates"] = [{"symbol": "BTCUSDT", "score": 7.0}]
        assert sub._evaluate_isc(_get_isc(sub, "ISC-001")) is False
        sub.analysis["candidates"] = [{"symbol": "BTCUSDT", "score": 8.5}]
        assert sub._evaluate_isc(_get_isc(sub, "ISC-001")) is True

    def test_isc_002_no_trade_pending(self):
        sub = Substrate(config=make_full_config())
        assert sub._evaluate_isc(_get_isc(sub, "ISC-002")) is True

    def test_isc_003_uses_portfolio_risk_pct(self):
        config = make_full_config(portfolio={"risk_per_trade_pct": 2.0})
        sub = Substrate(config=config)
        sub.portfolio["equity"] = 1000.0
        sub.decisions["trade_approved"] = {"size_usdt": 15.0, "sl_price": 100.0}
        assert sub._evaluate_isc(_get_isc(sub, "ISC-003")) is True
        sub.decisions["trade_approved"] = {"size_usdt": 25.0, "sl_price": 100.0}
        assert sub._evaluate_isc(_get_isc(sub, "ISC-003")) is False

    def test_isc_004_reads_from_live_config(self):
        config = make_full_config(strategy={"max_positions": 3})
        sub = Substrate(config=config)
        sub.portfolio["open_positions"] = [{"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"}]
        assert sub._evaluate_isc(_get_isc(sub, "ISC-004")) is True
        sub.portfolio["open_positions"] = [{"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"}, {"symbol": "SOLUSDT"}]
        assert sub._evaluate_isc(_get_isc(sub, "ISC-004")) is False

    def test_no_isc_005_006_007(self):
        sub = Substrate(config=make_full_config())
        isc_ids = [isc.id for isc in sub.validity]
        assert "ISC-005" not in isc_ids
        assert "ISC-006" not in isc_ids
        assert "ISC-007" not in isc_ids

    def test_all_iscs_pass(self):
        sub = Substrate(config=make_full_config())
        assert sub.all_iscs_pass() is False

    def test_isc_blocks_trade_when_failed(self):
        sub = Substrate(config=make_full_config())
        sub.verify_iscs()
        assert sub.isc_blocks_trade() is True

    def test_pending_does_not_block(self):
        sub = Substrate(config=make_full_config())
        assert sub.isc_blocks_trade() is False

    def test_failed_isc_ids(self):
        sub = Substrate(config=make_full_config())
        sub.verify_iscs()
        failed = sub.failed_isc_ids()
        assert "ISC-001" in failed


class TestSubstrateSoftPenalties:
    """Test soft penalties and effective score."""

    def test_soft_penalties_default_zero(self):
        sub = Substrate(config=make_full_config())
        penalties = sub.soft_penalties()
        assert all(r == 0.0 for r in penalties.values())

    def test_compute_effective_score_no_penalties(self):
        sub = Substrate(config=make_full_config())
        assert sub.compute_effective_score(7.0) == 7.0

    def test_compute_effective_score_with_penalties(self):
        sub = Substrate(config=make_full_config())
        sub.analysis["noise_penalty_ratio"] = 0.3
        sub.analysis["confluence_penalty_ratio"] = 0.3
        sub.analysis["trajectory_penalty_ratio"] = 0.0
        assert abs(sub.compute_effective_score(7.0) - 3.43) < 0.01


class TestSubstrateCycleReset:
    """Test cycle reset."""

    def test_reset_cycle(self):
        sub = Substrate(config=make_full_config())
        sub.analysis["candidates"] = [{"symbol": "BTCUSDT", "score": 7.0}]
        sub.decisions["action"] = "enter"
        sub.decisions["trade_approved"] = {"symbol": "BTCUSDT"}
        sub.reset_cycle()
        assert sub.analysis["candidates"] == []
        assert sub.decisions["action"] == ""
        assert sub.decisions["trade_approved"] is None
        assert sub._cycle_count == 1

    def test_reset_cycle_resets_isc_to_pending(self):
        sub = Substrate(config=make_full_config())
        sub.verify_iscs()
        sub.reset_cycle()
        for isc in sub.validity:
            assert isc.status == "pending"

    def test_mark_idle(self):
        sub = Substrate(config=make_full_config())
        sub.mark_idle("no candidates")
        assert sub.decisions["action"] == "wait"
        assert sub.learning["idle_cycles"] == 1
        assert "no candidates" in sub.learning["idle_reasons"]


class TestSubstrateShallowCopy:
    """Test shallow copy for enzyme execution safety."""

    def test_shallow_copy_preserves_state(self):
        sub = Substrate(config=make_full_config())
        sub.portfolio["equity"] = 5000.0
        copy = sub.shallow_copy()
        assert copy.portfolio["equity"] == 5000.0
        # Modifying copy's top-level dict doesn't affect original
        copy.portfolio["equity"] = 9999.0
        assert sub.portfolio["equity"] == 5000.0

    def test_shallow_copy_shares_config(self):
        config = make_full_config(scoring={"entry_threshold": 7.5})
        sub = Substrate(config=config)
        copy = sub.shallow_copy()
        assert copy.cfg("scoring.entry_threshold") == 7.5
