"""
tests/test_substrate.py -- Tests for the Substrate state container.

Phase A validation: substrate creation, config loading, ISC verification,
serialization, and cycle reset.

Tests verify:
  - No hardcoded ISC defaults (SubstrateConfigError on missing validity)
  - ISC operators work correctly (any_score_gte, best_field_gte, count_lt, etc.)
  - isc_blocks_trade() / failed_isc_ids() gate methods
  - Score normalization (0-10 scale) for ISC-001 threshold comparison
  - from_persistent_dict() fallback chain (no silent empty validity)
  - ISC-004 reads from live config, not stale state
"""

import json
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.substrate import Substrate, ISCCheck, SubstrateConfigError
from conftest import make_full_config


# --- Helper: find ISC by ID instead of fragile index access ---

def _get_isc(sub: Substrate, isc_id: str) -> ISCCheck:
    """Find an ISCCheck by its ID from the substrate's validity list."""
    for isc in sub.validity:
        if isc.id == isc_id:
            return isc
    raise KeyError(f"ISC {isc_id!r} not found in substrate.validity")


class TestSubstrateCreation:
    """Test substrate initialization from config."""

    def test_creation_from_config(self):
        """Substrate requires a complete config — no hardcoded defaults."""
        config = make_full_config()
        sub = Substrate(config=config)
        assert sub.strategy["name"] == "test_strategy"
        assert sub.portfolio["equity"] == 0.0
        assert sub.market["symbols_watched"] == ["BTCUSDT", "ETHUSDT"]
        assert sub.analysis["candidates"] == []
        assert sub.decisions["action"] == ""
        assert sub.learning["idle_cycles"] == 0
        assert len(sub.validity) == 7  # 7 ISC conditions from config

    def test_creation_without_config_raises(self):
        """Substrate() with no config raises SubstrateConfigError for missing required keys."""
        with pytest.raises(SubstrateConfigError):
            Substrate()

    def test_creation_without_validity_raises(self):
        """Substrate with config missing 'validity' raises SubstrateConfigError."""
        config = make_full_config()
        del config["validity"]
        with pytest.raises(SubstrateConfigError, match="validity"):
            Substrate(config=config)

    def test_creation_with_empty_validity_raises(self):
        """Substrate with empty validity list raises SubstrateConfigError."""
        config = make_full_config(validity=[])
        with pytest.raises(SubstrateConfigError, match="validity"):
            Substrate(config=config)

    def test_creation_from_config_override(self):
        """Substrate uses config values — overrides propagate correctly."""
        config = make_full_config(
            strategy={"name": "my_strat", "max_positions": 5},
            portfolio={"max_positions": 5, "risk_per_trade_pct": 2.0},
            scoring={"entry_threshold": 7.5, "confluence_min_signals": 4},
        )
        sub = Substrate(config=config)
        assert sub.strategy["name"] == "my_strat"
        assert sub.strategy["max_positions"] == 5
        assert sub.market["symbols_watched"] == ["BTCUSDT", "ETHUSDT"]
        assert sub.portfolio["max_positions"] == 5
        assert sub.portfolio["risk_per_trade_pct"] == 2.0
        assert sub.learning["idle_cycles"] == 0  # not from config

    def test_config_values_populated_in_portfolio(self):
        """Portfolio config values (risk_per_trade_pct, leverage) are populated from config."""
        config = make_full_config(portfolio={
            "risk_per_trade_pct": 2.0,
            "leverage": 10,
            "max_positions": 5,
        })
        sub = Substrate(config=config)
        assert sub.portfolio["risk_per_trade_pct"] == 2.0
        assert sub.portfolio["leverage"] == 10
        assert sub.portfolio["max_positions"] == 5

    def test_config_reference_stored(self):
        """Substrate stores config reference for ISC lookups."""
        config = make_full_config(scoring={"entry_threshold": 7.5})
        sub = Substrate(config=config)
        assert sub.cfg("scoring.entry_threshold") == 7.5


class TestSubstrateDotAccess:
    """Test dot-access get/set methods."""

    def test_get_state_value(self):
        """Substrate.get() returns values from substrate state."""
        sub = Substrate(config=make_full_config())
        assert sub.get("strategy.name") == "test_strategy"
        assert sub.get("strategy.max_positions") == 3

    def test_get_config_fallback(self):
        """Substrate.get() falls back to config for values not in state."""
        config = make_full_config(scoring={"entry_threshold": 7.5, "confluence_min_signals": 4})
        sub = Substrate(config=config)
        # scoring is not in substrate state, but should fall back to config
        assert sub.get("scoring.entry_threshold") == 7.5
        assert sub.get("scoring.confluence_min_signals") == 4

    def test_get_default_for_missing(self):
        """Substrate.get() returns default for truly missing keys."""
        sub = Substrate(config=make_full_config())
        assert sub.get("nonexistent.path", "default") == "default"

    def test_set_value(self):
        """Substrate.set() sets values by dotted path."""
        sub = Substrate(config=make_full_config())
        sub.set("decisions.action", "enter")
        assert sub.decisions["action"] == "enter"
        sub.set("strategy.name", "updated")
        assert sub.strategy["name"] == "updated"

    def test_cfg_method(self):
        """cfg() method accesses config values directly."""
        config = make_full_config(scoring={"entry_threshold": 7.5})
        sub = Substrate(config=config)
        assert sub.cfg("scoring.entry_threshold") == 7.5
        # cfg() raises ValueError when key is missing and no default provided
        with pytest.raises(ValueError):
            sub.cfg("nonexistent.path")
        # cfg() returns explicit default when key is missing
        assert sub.cfg("nonexistent.path", 99) == 99


class TestSubstrateISC:
    """Test ISC (hard-to-vary conditions) verification."""

    def test_isc_initial_state(self):
        """All ISC conditions start as pending."""
        sub = Substrate(config=make_full_config())
        for isc in sub.validity:
            assert isc.status == "pending"

    def test_isc_004_vacuously_true(self):
        """ISC-004 (max positions) is true when no positions open."""
        sub = Substrate(config=make_full_config())
        assert sub._evaluate_isc(_get_isc(sub, "ISC-004")) is True

    def test_isc_005_vacuously_true(self):
        """ISC-005 (no trade when noise) is true when action is wait."""
        sub = Substrate(config=make_full_config())
        assert sub._evaluate_isc(_get_isc(sub, "ISC-005")) is True

    def test_isc_001_fails_no_candidates(self):
        """ISC-001 fails when no candidates exist."""
        sub = Substrate(config=make_full_config())
        assert sub._evaluate_isc(_get_isc(sub, "ISC-001")) is False

    def test_isc_001_passes_with_candidates_on_0_10_scale(self):
        """ISC-001 passes when candidates above threshold exist (0-10 scale)."""
        config = make_full_config(scoring={"entry_threshold": 6.5})
        sub = Substrate(config=config)
        # Score 7.0 on 0-10 scale >= threshold 6.5
        sub.analysis["candidates"] = [
            {"symbol": "BTCUSDT", "score": 7.0, "direction": "long"}
        ]
        assert sub._evaluate_isc(_get_isc(sub, "ISC-001")) is True

    def test_isc_001_uses_config_threshold(self):
        """ISC-001 uses the config entry_threshold, not hardcoded value."""
        config = make_full_config(scoring={"entry_threshold": 8.0})
        sub = Substrate(config=config)
        # Score 7.0 < threshold 8.0, should fail
        sub.analysis["candidates"] = [
            {"symbol": "BTCUSDT", "score": 7.0, "direction": "long"}
        ]
        assert sub._evaluate_isc(_get_isc(sub, "ISC-001")) is False
        # Score 8.5 >= threshold 8.0, should pass
        sub.analysis["candidates"] = [
            {"symbol": "BTCUSDT", "score": 8.5, "direction": "long"}
        ]
        assert sub._evaluate_isc(_get_isc(sub, "ISC-001")) is True

    def test_isc_003_uses_portfolio_risk_pct(self):
        """ISC-003 uses risk_per_trade_pct from portfolio state (set from config)."""
        config = make_full_config(portfolio={"risk_per_trade_pct": 2.0})
        sub = Substrate(config=config)
        sub.portfolio["equity"] = 1000.0
        sub.decisions["trade_approved"] = {"size_usdt": 15.0, "sl_price": 100.0}
        # 15.0 <= 1000.0 * 2.0 / 100 = 20.0 -> True
        assert sub._evaluate_isc(_get_isc(sub, "ISC-003")) is True
        # 25.0 > 20.0 -> False
        sub.decisions["trade_approved"] = {"size_usdt": 25.0, "sl_price": 100.0}
        assert sub._evaluate_isc(_get_isc(sub, "ISC-003")) is False

    def test_isc_006_best_field_gte_checks_best_candidate(self):
        """ISC-006 (best_field_gte) checks only the best (first) candidate."""
        config = make_full_config(scoring={"confluence_min_signals": 4})
        sub = Substrate(config=config)
        # Best candidate has 4 aligned (>= 4), second has only 2
        # With best_field_gte, this should PASS (only best matters)
        sub.analysis["candidates"] = [
            {"symbol": "BTCUSDT", "indicators_aligned": 4},
            {"symbol": "ETHUSDT", "indicators_aligned": 2},
        ]
        assert sub._evaluate_isc(_get_isc(sub, "ISC-006")) is True

    def test_isc_006_best_field_gte_fails_when_best_below_threshold(self):
        """ISC-006 (best_field_gte) fails when best candidate is below threshold."""
        config = make_full_config(scoring={"confluence_min_signals": 4})
        sub = Substrate(config=config)
        # Best candidate has only 3 aligned (< 4)
        sub.analysis["candidates"] = [
            {"symbol": "BTCUSDT", "indicators_aligned": 3},
        ]
        assert sub._evaluate_isc(_get_isc(sub, "ISC-006")) is False

    def test_isc_006_best_field_gte_empty_candidates(self):
        """ISC-006 (best_field_gte) fails when no candidates exist."""
        sub = Substrate(config=make_full_config())
        sub.analysis["candidates"] = []
        assert sub._evaluate_isc(_get_isc(sub, "ISC-006")) is False

    def test_isc_004_reads_from_live_config(self):
        """ISC-004 (count_lt) reads max_positions from live config, not stale state."""
        config = make_full_config(strategy={"max_positions": 3})
        sub = Substrate(config=config)
        # 2 open positions < config max 3 -> True
        sub.portfolio["open_positions"] = [
            {"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"}
        ]
        assert sub._evaluate_isc(_get_isc(sub, "ISC-004")) is True
        # 3 open positions >= config max 3 -> False
        sub.portfolio["open_positions"] = [
            {"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"}, {"symbol": "SOLUSDT"}
        ]
        assert sub._evaluate_isc(_get_isc(sub, "ISC-004")) is False

    def test_all_iscs_pass(self):
        """all_iscs_pass() returns True only when all conditions verified."""
        sub = Substrate(config=make_full_config())
        # With default empty state, some ISC will fail
        assert sub.all_iscs_pass() is False

    def test_isc_002_no_trade_pending(self):
        """ISC-002 is vacuously true when no trade is pending."""
        sub = Substrate(config=make_full_config())
        assert sub._evaluate_isc(_get_isc(sub, "ISC-002")) is True


class TestISCBlocksTrade:
    """Test the ISC gate methods: isc_blocks_trade() and failed_isc_ids()."""

    def test_no_blocks_initially(self):
        """isc_blocks_trade() returns False when all ISCs are pending."""
        sub = Substrate(config=make_full_config())
        # All ISCs start as "pending" — pending does NOT block
        assert sub.isc_blocks_trade() is False

    def test_blocks_when_isc_fails(self):
        """isc_blocks_trade() returns True when any ISC has failed."""
        sub = Substrate(config=make_full_config())
        # Run verification — ISC-001 will fail (no candidates)
        sub.verify_iscs()
        assert sub.isc_blocks_trade() is True

    def test_failed_isc_ids(self):
        """failed_isc_ids() returns IDs of failed ISCs."""
        sub = Substrate(config=make_full_config())
        sub.verify_iscs()
        failed = sub.failed_isc_ids()
        # ISC-001 (no candidates) and ISC-006 (no candidates) should fail
        assert "ISC-001" in failed
        assert "ISC-006" in failed

    def test_no_blocks_when_all_pass(self):
        """isc_blocks_trade() returns False when all ISCs pass."""
        config = make_full_config(scoring={"entry_threshold": 6.5, "confluence_min_signals": 3})
        sub = Substrate(config=config)
        # Set up substrate state so all ISCs pass
        sub.portfolio["equity"] = 1000.0
        sub.analysis["candidates"] = [
            {"symbol": "BTCUSDT", "score": 7.0, "indicators_aligned": 4}
        ]
        sub.analysis["noise_flag"] = False
        sub.decisions["action"] = "wait"
        sub.verify_iscs()
        assert sub.isc_blocks_trade() is False

    def test_pending_does_not_block(self):
        """Pending ISCs (not yet evaluated) do NOT block trades."""
        sub = Substrate(config=make_full_config())
        # Before verify_iscs(), all are pending
        for isc in sub.validity:
            assert isc.status == "pending"
        assert sub.isc_blocks_trade() is False


class TestISCCheck:
    """Test ISCCheck data class."""

    def test_creation(self):
        """ISCCheck can be created from dict."""
        isc = ISCCheck.from_dict({
            "id": "ISC-001",
            "criterion": "test criterion",
            "verification": "test verification",
            "status": "pending",
        })
        assert isc.id == "ISC-001"
        assert isc.status == "pending"

    def test_to_dict(self):
        """ISCCheck serializes to dict."""
        isc = ISCCheck("ISC-001", "test", "verify")
        d = isc.to_dict()
        assert d["id"] == "ISC-001"
        assert d["criterion"] == "test"
        assert d["status"] == "pending"

    def test_best_field_gte_operator_in_dict(self):
        """ISCCheck with best_field_gte operator serializes correctly."""
        isc = ISCCheck("ISC-006", "test", "verify", operator="best_field_gte")
        d = isc.to_dict()
        assert d["operator"] == "best_field_gte"
        isc2 = ISCCheck.from_dict(d)
        assert isc2.operator == "best_field_gte"


class TestSubstrateSerialization:
    """Test substrate serialization and deserialization."""

    def test_to_dict_roundtrip(self):
        """Substrate survives dict roundtrip."""
        config = make_full_config(strategy={"name": "roundtrip_test"})
        sub = Substrate(config=config)
        sub.decisions["action"] = "wait"
        sub.learning["idle_cycles"] = 5

        d = sub.to_dict()
        sub2 = Substrate.from_dict(d, config=config)

        assert sub2.strategy["name"] == "roundtrip_test"
        assert sub2.decisions["action"] == "wait"
        assert sub2.learning["idle_cycles"] == 5

    def test_to_json_roundtrip(self):
        """Substrate survives JSON roundtrip."""
        config = make_full_config(strategy={"name": "json_test"})
        sub = Substrate(config=config)
        sub.portfolio["equity"] = 10000.0

        json_str = sub.to_json()
        sub2 = Substrate.from_json(json_str, config=config)

        assert sub2.strategy["name"] == "json_test"
        assert sub2.portfolio["equity"] == 10000.0

    def test_json_is_valid(self):
        """Substrate JSON output is valid JSON."""
        sub = Substrate(config=make_full_config())
        json_str = sub.to_json()
        parsed = json.loads(json_str)
        assert "strategy" in parsed
        assert "portfolio" in parsed
        assert "validity" in parsed

    def test_config_preserved_after_restore(self):
        """Config reference is preserved after DB restore."""
        config = make_full_config(scoring={"entry_threshold": 7.5})
        sub = Substrate(config=config)
        d = sub.to_dict()

        # Restore with same config
        sub2 = Substrate.from_dict(d, config=config)
        assert sub2.cfg("scoring.entry_threshold") == 7.5

        # Restore with different config (config updated)
        new_config = make_full_config(scoring={"entry_threshold": 9.0})
        sub3 = Substrate.from_dict(d, config=new_config)
        assert sub3.cfg("scoring.entry_threshold") == 9.0

    def test_from_persistent_dict_no_validity_uses_config(self):
        """from_persistent_dict with empty validity falls back to config."""
        config = make_full_config()
        sub = Substrate(config=config)
        d = sub.to_persistent_dict()
        # Remove validity from persistent dict
        d["validity"] = []
        # Should fall back to config's validity
        sub2 = Substrate.from_persistent_dict(d, config=config)
        assert len(sub2.validity) == 7

    def test_from_persistent_dict_no_validity_no_config_raises(self):
        """from_persistent_dict with no validity and no config raises error.
        When config=None, cls(config=None) raises for missing strategy keys first."""
        config = make_full_config()
        sub = Substrate(config=config)
        d = sub.to_persistent_dict()
        d["validity"] = []
        # No config provided — Substrate() raises for missing strategy keys
        with pytest.raises(SubstrateConfigError):
            Substrate.from_persistent_dict(d, config=None)

    def test_from_persistent_dict_no_validity_empty_config_raises(self):
        """from_persistent_dict with no validity and config without validity raises error.
        The error comes from __init__ which validates config has validity."""
        config = make_full_config()
        sub = Substrate(config=config)
        d = sub.to_persistent_dict()
        d["validity"] = []
        # Config without validity — __init__ catches it immediately
        config_no_val = make_full_config()
        del config_no_val["validity"]
        with pytest.raises(SubstrateConfigError, match="validity"):
            Substrate.from_persistent_dict(d, config=config_no_val)


class TestSubstrateCycleReset:
    """Test cycle reset functionality."""

    def test_reset_cycle(self):
        """reset_cycle() clears per-cycle fields."""
        sub = Substrate(config=make_full_config())
        sub.analysis["candidates"] = [{"symbol": "BTCUSDT", "score": 7.0}]
        sub.analysis["noise_flag"] = True
        sub.decisions["action"] = "enter"
        sub.decisions["trade_approved"] = {"symbol": "BTCUSDT"}

        sub.reset_cycle()

        assert sub.analysis["candidates"] == []
        assert sub.analysis["noise_flag"] is False
        assert sub.decisions["action"] == ""
        assert sub.decisions["trade_approved"] is None
        assert sub._cycle_count == 1

    def test_reset_cycle_resets_isc_to_pending(self):
        """reset_cycle() resets all ISC statuses to pending."""
        sub = Substrate(config=make_full_config())
        sub.verify_iscs()  # Some will fail
        assert some(isc.status == "failed" for isc in sub.validity)

        sub.reset_cycle()
        for isc in sub.validity:
            assert isc.status == "pending"

    def test_mark_idle(self):
        """mark_idle() records idle cycle with reason."""
        sub = Substrate(config=make_full_config())
        sub.mark_idle("no candidates above threshold")

        assert sub.decisions["action"] == "wait"
        assert sub.learning["idle_cycles"] == 1
        assert "no candidates above threshold" in sub.learning["idle_reasons"]
        assert sub.learning["total_idle_cycles_recorded"] == 1


class TestScoreNormalization:
    """Test that confluence scores are on 0-10 scale for ISC-001 compatibility."""

    def test_isc_001_threshold_matches_0_10_score(self):
        """ISC-001 entry_threshold (6.5) works with 0-10 normalized scores."""
        config = make_full_config(scoring={"entry_threshold": 6.5})
        sub = Substrate(config=config)

        # A score of 6.5 on 0-10 scale should pass threshold 6.5
        sub.analysis["candidates"] = [
            {"symbol": "BTCUSDT", "score": 6.5}
        ]
        assert sub._evaluate_isc(_get_isc(sub, "ISC-001")) is True

        # A score of 5.0 on 0-10 scale should fail threshold 6.5
        sub.analysis["candidates"] = [
            {"symbol": "BTCUSDT", "score": 5.0}
        ]
        assert sub._evaluate_isc(_get_isc(sub, "ISC-001")) is False

    def test_isc_001_with_high_threshold(self):
        """ISC-001 with a high threshold (9.0) only passes for very strong signals."""
        config = make_full_config(scoring={"entry_threshold": 9.0})
        sub = Substrate(config=config)

        sub.analysis["candidates"] = [
            {"symbol": "BTCUSDT", "score": 8.5}
        ]
        assert sub._evaluate_isc(_get_isc(sub, "ISC-001")) is False

        sub.analysis["candidates"] = [
            {"symbol": "BTCUSDT", "score": 9.5}
        ]
        assert sub._evaluate_isc(_get_isc(sub, "ISC-001")) is True


# Helper for test_reset_cycle_resets_isc_to_pending
def some(iterable):
    """Return True if any element in the iterable is True."""
    for element in iterable:
        if element:
            return True
    return False