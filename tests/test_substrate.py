"""
tests_new/test_substrate.py -- Tests for the Substrate state container.

Phase A validation: substrate creation, config loading, ISC verification,
serialization, and cycle reset.
"""

import json
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.substrate import Substrate, ISCCheck


class TestSubstrateCreation:
    """Test substrate initialization from config."""

    def test_default_creation(self):
        """Substrate can be created with no config (all defaults)."""
        sub = Substrate()
        assert sub.strategy["name"] == ""
        assert sub.portfolio["equity"] == 0.0
        assert sub.market["symbols_watched"] == []
        assert sub.analysis["candidates"] == []
        assert sub.decisions["action"] == "wait"
        assert sub.learning["idle_cycles"] == 0
        assert len(sub.validity) == 7  # 7 default ISC conditions

    def test_creation_from_config(self):
        """Substrate can be created from a config dict."""
        config = {
            "strategy": {"name": "test_strategy", "max_positions": 5},
            "description": "Test strategy description",
            "symbols": {"always_watch": ["BTCUSDT", "ETHUSDT"]},
            "portfolio": {"max_positions": 5, "risk_per_trade_pct": 2.0},
            "scoring": {"entry_threshold": 7.5, "confluence_min_signals": 4},
            "learning": {"min_trades_before_adjusting": 50},
        }
        sub = Substrate(config=config)
        assert sub.strategy["name"] == "test_strategy"
        assert sub.strategy["description"] == "Test strategy description"
        assert sub.strategy["max_positions"] == 5
        assert sub.market["symbols_watched"] == ["BTCUSDT", "ETHUSDT"]
        assert sub.portfolio["max_positions"] == 5
        assert sub.portfolio["risk_per_trade_pct"] == 2.0
        assert sub.learning["idle_cycles"] == 0  # not from config

    def test_config_values_populated_in_portfolio(self):
        """Portfolio config values (risk_per_trade_pct, leverage) are populated from config."""
        config = {
            "portfolio": {
                "risk_per_trade_pct": 2.0,
                "leverage": 10,
                "max_positions": 5,
            },
        }
        sub = Substrate(config=config)
        assert sub.portfolio["risk_per_trade_pct"] == 2.0
        assert sub.portfolio["leverage"] == 10
        assert sub.portfolio["max_positions"] == 5

    def test_config_reference_stored(self):
        """Substrate stores config reference for ISC lookups."""
        config = {"scoring": {"entry_threshold": 7.5}}
        sub = Substrate(config=config)
        assert sub.cfg("scoring.entry_threshold") == 7.5


class TestSubstrateDotAccess:
    """Test dot-access get/set methods."""

    def test_get_state_value(self):
        """Substrate.get() returns values from substrate state."""
        sub = Substrate(config={"strategy": {"name": "my_strat"}})
        assert sub.get("strategy.name") == "my_strat"
        assert sub.get("strategy.max_positions", 3) == 3

    def test_get_config_fallback(self):
        """Substrate.get() falls back to config for values not in state."""
        config = {"scoring": {"entry_threshold": 7.5, "confluence_min_signals": 4}}
        sub = Substrate(config=config)
        # scoring is not in substrate state, but should fall back to config
        assert sub.get("scoring.entry_threshold") == 7.5
        assert sub.get("scoring.confluence_min_signals") == 4

    def test_get_default_for_missing(self):
        """Substrate.get() returns default for truly missing keys."""
        sub = Substrate()
        assert sub.get("nonexistent.path", "default") == "default"

    def test_set_value(self):
        """Substrate.set() sets values by dotted path."""
        sub = Substrate()
        sub.set("decisions.action", "enter")
        assert sub.decisions["action"] == "enter"
        sub.set("strategy.name", "updated")
        assert sub.strategy["name"] == "updated"

    def test_cfg_method(self):
        """cfg() method accesses config values directly."""
        config = {"scoring": {"entry_threshold": 7.5}}
        sub = Substrate(config=config)
        assert sub.cfg("scoring.entry_threshold") == 7.5
        assert sub.cfg("nonexistent.path", 99) == 99


class TestSubstrateISC:
    """Test ISC (hard-to-vary conditions) verification."""

    def test_isc_initial_state(self):
        """All ISC conditions start as pending."""
        sub = Substrate()
        for isc in sub.validity:
            assert isc.status == "pending"

    def test_isc_004_vacuously_true(self):
        """ISC-004 (max positions) is true when no positions open."""
        sub = Substrate()
        assert sub._evaluate_isc(sub.validity[3]) is True

    def test_isc_005_vacuously_true(self):
        """ISC-005 (no trade when noise) is true when action is wait."""
        sub = Substrate()
        assert sub._evaluate_isc(sub.validity[4]) is True

    def test_isc_001_fails_no_candidates(self):
        """ISC-001 fails when no candidates exist."""
        sub = Substrate()
        assert sub._evaluate_isc(sub.validity[0]) is False

    def test_isc_001_passes_with_candidates(self):
        """ISC-001 passes when candidates above threshold exist."""
        config = {"scoring": {"entry_threshold": 6.5}}
        sub = Substrate(config=config)
        sub.analysis["candidates"] = [
            {"symbol": "BTCUSDT", "score": 7.5, "direction": "long"}
        ]
        assert sub._evaluate_isc(sub.validity[0]) is True

    def test_isc_001_uses_config_threshold(self):
        """ISC-001 uses the config entry_threshold, not hardcoded value."""
        config = {"scoring": {"entry_threshold": 8.0}}
        sub = Substrate(config=config)
        # Score 7.0 < threshold 8.0, should fail
        sub.analysis["candidates"] = [
            {"symbol": "BTCUSDT", "score": 7.0, "direction": "long"}
        ]
        assert sub._evaluate_isc(sub.validity[0]) is False
        # Score 8.5 >= threshold 8.0, should pass
        sub.analysis["candidates"] = [
            {"symbol": "BTCUSDT", "score": 8.5, "direction": "long"}
        ]
        assert sub._evaluate_isc(sub.validity[0]) is True

    def test_isc_003_uses_portfolio_risk_pct(self):
        """ISC-003 uses risk_per_trade_pct from portfolio state (set from config)."""
        config = {"portfolio": {"risk_per_trade_pct": 2.0}}
        sub = Substrate(config=config)
        sub.portfolio["equity"] = 1000.0
        sub.decisions["trade_approved"] = {"size_usdt": 15.0, "sl_price": 100.0}
        # 15.0 <= 1000.0 * 2.0 / 100 = 20.0 -> True
        assert sub._evaluate_isc(sub.validity[2]) is True
        # 25.0 > 20.0 -> False
        sub.decisions["trade_approved"] = {"size_usdt": 25.0, "sl_price": 100.0}
        assert sub._evaluate_isc(sub.validity[2]) is False

    def test_isc_006_uses_config_min_signals(self):
        """ISC-006 uses confluence_min_signals from config."""
        config = {"scoring": {"confluence_min_signals": 4}}
        sub = Substrate(config=config)
        # 3 aligned < min 4 -> False
        sub.analysis["candidates"] = [
            {"symbol": "BTCUSDT", "indicators_aligned": 3}
        ]
        assert sub._evaluate_isc(sub.validity[5]) is False
        # 4 aligned >= min 4 -> True
        sub.analysis["candidates"] = [
            {"symbol": "BTCUSDT", "indicators_aligned": 4}
        ]
        assert sub._evaluate_isc(sub.validity[5]) is True

    def test_all_iscs_pass(self):
        """all_iscs_pass() returns True only when all conditions verified."""
        sub = Substrate()
        # With default empty state, some ISC will fail
        assert sub.all_iscs_pass() is False

    def test_isc_002_no_trade_pending(self):
        """ISC-002 is vacuously true when no trade is pending."""
        sub = Substrate()
        assert sub._evaluate_isc(sub.validity[1]) is True


class TestSubstrateSerialization:
    """Test substrate serialization and deserialization."""

    def test_to_dict_roundtrip(self):
        """Substrate survives dict roundtrip."""
        config = {"strategy": {"name": "roundtrip_test"}}
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
        config = {"strategy": {"name": "json_test"}}
        sub = Substrate(config=config)
        sub.portfolio["equity"] = 10000.0

        json_str = sub.to_json()
        sub2 = Substrate.from_json(json_str, config=config)

        assert sub2.strategy["name"] == "json_test"
        assert sub2.portfolio["equity"] == 10000.0

    def test_json_is_valid(self):
        """Substrate JSON output is valid JSON."""
        sub = Substrate()
        json_str = sub.to_json()
        parsed = json.loads(json_str)
        assert "strategy" in parsed
        assert "portfolio" in parsed
        assert "validity" in parsed

    def test_config_preserved_after_restore(self):
        """Config reference is preserved after DB restore."""
        config = {"scoring": {"entry_threshold": 7.5}}
        sub = Substrate(config=config)
        d = sub.to_dict()

        # Restore with same config
        sub2 = Substrate.from_dict(d, config=config)
        assert sub2.cfg("scoring.entry_threshold") == 7.5

        # Restore with different config (config updated)
        new_config = {"scoring": {"entry_threshold": 9.0}}
        sub3 = Substrate.from_dict(d, config=new_config)
        assert sub3.cfg("scoring.entry_threshold") == 9.0


class TestSubstrateCycleReset:
    """Test cycle reset functionality."""

    def test_reset_cycle(self):
        """reset_cycle() clears per-cycle fields."""
        sub = Substrate()
        sub.analysis["candidates"] = [{"symbol": "BTCUSDT", "score": 7.0}]
        sub.analysis["noise_flag"] = True
        sub.decisions["action"] = "enter"
        sub.decisions["trade_approved"] = {"symbol": "BTCUSDT"}

        sub.reset_cycle()

        assert sub.analysis["candidates"] == []
        assert sub.analysis["noise_flag"] is False
        assert sub.decisions["action"] == "wait"
        assert sub.decisions["trade_approved"] is None
        assert sub._cycle_count == 1
        # P7: indicators persist across reset_cycle (managed by CollectOHLCV)
        # They are NOT cleared here — CollectOHLCV refreshes them only on new candles.

    def test_mark_idle(self):
        """mark_idle() records idle cycle with reason."""
        sub = Substrate()
        sub.mark_idle("no candidates above threshold")

        assert sub.decisions["action"] == "wait"
        assert sub.learning["idle_cycles"] == 1
        assert "no candidates above threshold" in sub.learning["idle_reasons"]
        assert sub.learning["total_idle_cycles_recorded"] == 1


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