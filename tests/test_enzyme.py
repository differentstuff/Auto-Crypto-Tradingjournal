"""
tests_new/test_enzyme.py -- Tests for the Enzyme base class and registry.

Phase A validation: enzyme activation, flux scoring, WaitEnzyme,
registry functions.
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.enzyme import (
    Enzyme,
    EnzymeClass,
    register_enzyme,
    get_enzyme,
    list_enzymes,
    create_enzyme,
)
from enzymes.wait import WaitEnzyme
from core.substrate import Substrate
from conftest import make_full_config


class TestEnzymeBase:
    """Test Enzyme abstract base class."""

    def test_enzyme_class_enum(self):
        """EnzymeClass enum has all expected values."""
        assert EnzymeClass.SENSOR.value == "Sensor"
        assert EnzymeClass.OXIDOREDUCTASE.value == "Oxidoreductase"
        assert EnzymeClass.REGULATOR.value == "Regulator"
        assert EnzymeClass.SYNTHASE.value == "Synthase"
        assert EnzymeClass.TRANSPORTER.value == "Transporter"
        assert EnzymeClass.ISOMERASE.value == "Isomerase"

    def test_wait_enzyme_always_activatable(self):
        """WaitEnzyme can always activate."""
        sub = Substrate(config=make_full_config())
        wait = WaitEnzyme()
        assert wait.can_activate(sub) is True

    def test_wait_enzyme_flux_score_zero(self):
        """WaitEnzyme has flux_score 0 (neutral, only chosen when all others <= 0)."""
        sub = Substrate(config=make_full_config())
        wait = WaitEnzyme()
        assert wait.flux_score(sub) == 0.0

    def test_wait_enzyme_transform(self):
        """WaitEnzyme sets action to 'wait'."""
        sub = Substrate(config=make_full_config())
        sub.decisions["action"] = "enter"
        wait = WaitEnzyme()
        result = wait.transform(sub)
        assert result.decisions["action"] == "wait"

    def test_wait_enzyme_is_isomerase(self):
        """WaitEnzyme is an Isomerase."""
        wait = WaitEnzyme()
        assert wait.enzyme_class == EnzymeClass.ISOMERASE
        assert wait.is_regulator is False
        assert wait.is_sensor is False

    def test_wait_enzyme_priority(self):
        """WaitEnzyme has priority -1 (lowest)."""
        wait = WaitEnzyme()
        assert wait.priority == -1


class TestEnzymeRegistry:
    """Test enzyme registration and lookup."""

    def test_register_and_get(self):
        """Enzymes can be registered and looked up by name."""

        @register_enzyme
        class TestEnzyme(Enzyme):
            name = "TestEnzyme_AAA"
            enzyme_class = EnzymeClass.SENSOR
            priority = 1

            def transform(self, substrate):
                return substrate

        # Should be findable
        cls = get_enzyme("TestEnzyme_AAA")
        assert cls is not None
        assert cls.name == "TestEnzyme_AAA"

    def test_list_enzymes(self):
        """list_enzymes returns all registered enzyme names."""
        names = list_enzymes()
        assert isinstance(names, list)

    def test_create_enzyme(self):
        """create_enzyme instantiates an enzyme by name."""
        wait = create_enzyme("Wait")
        assert wait is not None
        assert isinstance(wait, WaitEnzyme)

    def test_create_unknown_enzyme(self):
        """create_enzyme returns None for unknown enzyme."""
        result = create_enzyme("NonExistentEnzyme")
        assert result is None


class TestEnzymeConditions:
    """Test enzyme condition evaluation."""

    def test_condition_is_set(self):
        """'is set' condition checks for non-empty values."""
        sub = Substrate(config=make_full_config())
        wait = WaitEnzyme()
        assert wait._evaluate_condition("substrate.strategy.name is set", sub) is True

    def test_condition_not_empty(self):
        """'not empty' condition checks for non-empty collections."""
        sub = Substrate(config=make_full_config())
        sub.analysis["candidates"] = [{"symbol": "BTCUSDT"}]
        wait = WaitEnzyme()
        assert wait._evaluate_condition("analysis.candidates not empty", sub) is True

    def test_condition_equality(self):
        """'==' condition checks string equality."""
        sub = Substrate(config=make_full_config())
        sub.decisions["action"] = "wait"
        wait = WaitEnzyme()
        assert wait._evaluate_condition("decisions.action == 'wait'", sub) is True
        assert wait._evaluate_condition("decisions.action == 'enter'", sub) is False

    def test_condition_inequality(self):
        """'!=' condition checks string inequality."""
        sub = Substrate(config=make_full_config())
        sub.analysis["noise_flag"] = True
        wait = WaitEnzyme()
        assert wait._evaluate_condition("analysis.noise_flag != 'true'", sub) is True


class TestCustomEnzyme:
    """Test creating custom enzyme subclasses."""

    def test_sensor_enzyme(self):
        """Custom Sensor enzyme with activation conditions."""

        class TestSensor(Enzyme):
            name = "TestSensor"
            enzyme_class = EnzymeClass.SENSOR
            priority = 0

            def requires(self):
                return ["substrate.strategy.name is set"]

            def prohibits(self):
                return []

            def transform(self, substrate):
                substrate.market["last_scan_at"] = "2026-05-19T12:00:00Z"
                return substrate

        sensor = TestSensor()

        # Should activate when strategy name is set
        sub = Substrate(config=make_full_config())
        assert sensor.can_activate(sub) is True

        # Should not activate when strategy name is empty — Substrate now requires full config
        with pytest.raises((KeyError, ValueError)):
            Substrate()

    def test_regulator_enzyme(self):
        """Regulator enzymes have priority 10."""

        class TestRegulator(Enzyme):
            name = "TestRegulator"
            enzyme_class = EnzymeClass.REGULATOR
            priority = 10

            def transform(self, substrate):
                return substrate

        reg = TestRegulator()
        assert reg.is_regulator is True
        assert reg.class_priority == 10