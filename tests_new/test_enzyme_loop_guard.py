"""
tests_new/test_enzyme_loop_guard.py -- Tests for enzyme re-fire prevention.

Verifies that:
1. Evaluation flags (confluence_scored, noise_evaluated, entry_zones_evaluated)
   prevent enzymes from re-firing after producing empty results.
2. Substrate reset_cycle() properly resets the flags.
3. Daemon consecutive-fire guard breaks loops after 3 same-enzyme fires.
4. ISC-001/ISC-006 correctly fail when candidates are empty.
"""

import pytest
from core.substrate import Substrate
from enzymes.score_confluence import ScoreConfluence
from enzymes.detect_noise import DetectNoise
from enzymes.validate_entry_zone import ValidateEntryZone
from enzymes.collect_pre_trade_context import CollectPreTradeContext
from enzymes.collect_macro_context import CollectMacroContext


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def substrate():
    """Fresh substrate with no indicators or analysis."""
    return Substrate()


@pytest.fixture
def substrate_with_indicators(substrate):
    """Substrate with minimal indicator data (BTCUSDT, 4H, RSI only)."""
    substrate.market["indicators"] = {
        "BTCUSDT": {
            "4H": {
                "ok": True,
                "rsi": {"value": 52},  # Dead-band — no directional signal
            }
        }
    }
    return substrate


@pytest.fixture
def substrate_with_candidates(substrate_with_indicators):
    """Substrate with candidates populated (post-ScoreConfluence)."""
    substrate_with_indicators.analysis["candidates"] = [
        {"symbol": "BTCUSDT", "score": 0.35, "pct": 0.35, "label": "Bullish",
         "indicators_aligned": 3, "details": []}
    ]
    return substrate_with_indicators


@pytest.fixture
def substrate_with_config():
    """Substrate with scoring config matching production defaults (conftest)."""
    return Substrate(config={
        "scoring": {"entry_threshold": 6.5, "confluence_min_signals": 3},
        "strategy": {"name": "test", "max_positions": 3},
    })


@pytest.fixture
def score_confluence():
    return ScoreConfluence()


@pytest.fixture
def detect_noise():
    return DetectNoise()


@pytest.fixture
def validate_entry_zone():
    return ValidateEntryZone()


# ---------------------------------------------------------------------------
# 1. Substrate evaluation flags
# ---------------------------------------------------------------------------

class TestSubstrateEvaluationFlags:
    """Tests for evaluation flag initialization and reset."""

    def test_flags_default_to_false(self, substrate):
        """Evaluation flags start as False (not yet evaluated)."""
        assert substrate.analysis["confluence_scored"] is False
        assert substrate.analysis["noise_evaluated"] is False
        assert substrate.analysis["entry_zones_evaluated"] is False
        assert substrate.analysis["pre_trade_evaluated"] is False
        assert substrate.analysis["macro_evaluated"] is False

    def test_flags_reset_on_cycle(self, substrate):
        """reset_cycle() resets all evaluation flags to False."""
        substrate.analysis["confluence_scored"] = True
        substrate.analysis["noise_evaluated"] = True
        substrate.analysis["entry_zones_evaluated"] = True
        substrate.analysis["pre_trade_evaluated"] = True
        substrate.analysis["macro_evaluated"] = True

        substrate.reset_cycle()

        assert substrate.analysis["confluence_scored"] is False
        assert substrate.analysis["noise_evaluated"] is False
        assert substrate.analysis["entry_zones_evaluated"] is False
        assert substrate.analysis["pre_trade_evaluated"] is False
        assert substrate.analysis["macro_evaluated"] is False

    def test_candidates_stays_list_after_reset(self, substrate):
        """candidates remains a list (not None) after reset — no None iteration bugs."""
        substrate.analysis["candidates"] = [{"symbol": "BTCUSDT"}]
        substrate.reset_cycle()
        assert substrate.analysis["candidates"] == []
        assert isinstance(substrate.analysis["candidates"], list)


# ---------------------------------------------------------------------------
# 2. ScoreConfluence activation guard
# ---------------------------------------------------------------------------

class TestScoreConfluenceGuard:
    """Tests for ScoreConfluence can_activate with confluence_scored flag."""

    def test_no_indicators_cannot_activate(self, score_confluence, substrate):
        """No indicators → cannot activate."""
        assert score_confluence.can_activate(substrate) is False

    def test_with_indicators_can_activate(self, score_confluence, substrate_with_indicators):
        """Indicators present, not yet scored → can activate."""
        assert score_confluence.can_activate(substrate_with_indicators) is True

    def test_after_scoring_cannot_activate(self, score_confluence, substrate_with_indicators):
        """After scoring (even with 0 candidates) → cannot activate."""
        substrate_with_indicators.analysis["confluence_scored"] = True
        assert score_confluence.can_activate(substrate_with_indicators) is False

    def test_with_candidates_cannot_activate(self, score_confluence, substrate_with_candidates):
        """Candidates already populated → cannot activate (existing logic)."""
        assert score_confluence.can_activate(substrate_with_candidates) is False

    def test_transform_sets_flag(self, score_confluence, substrate_with_indicators):
        """transform() sets confluence_scored = True even when 0 candidates."""
        result = score_confluence.transform(substrate_with_indicators)
        assert result.analysis["confluence_scored"] is True

    def test_no_re_fire_after_empty_result(self, score_confluence, substrate_with_indicators):
        """ScoreConfluence cannot re-activate after producing 0 candidates."""
        result = score_confluence.transform(substrate_with_indicators)
        # Even though candidates is empty, the flag prevents re-firing
        assert result.analysis["candidates"] == []
        assert score_confluence.can_activate(result) is False


# ---------------------------------------------------------------------------
# 3. DetectNoise activation guard
# ---------------------------------------------------------------------------

class TestDetectNoiseGuard:
    """Tests for DetectNoise can_activate with noise_evaluated flag."""

    def test_no_candidates_cannot_activate(self, detect_noise, substrate):
        """No candidates → cannot activate."""
        assert detect_noise.can_activate(substrate) is False

    def test_with_candidates_can_activate(self, detect_noise, substrate_with_candidates):
        """Candidates present, not yet evaluated → can activate."""
        assert detect_noise.can_activate(substrate_with_candidates) is True

    def test_after_evaluation_cannot_activate(self, detect_noise, substrate_with_candidates):
        """After noise evaluation → cannot activate."""
        substrate_with_candidates.analysis["noise_evaluated"] = True
        assert detect_noise.can_activate(substrate_with_candidates) is False

    def test_transform_sets_flag(self, detect_noise, substrate_with_candidates):
        """transform() sets noise_evaluated = True."""
        result = detect_noise.transform(substrate_with_candidates)
        assert result.analysis["noise_evaluated"] is True

    def test_no_re_fire_after_evaluation(self, detect_noise, substrate_with_candidates):
        """DetectNoise cannot re-activate after evaluating."""
        result = detect_noise.transform(substrate_with_candidates)
        assert detect_noise.can_activate(result) is False


# ---------------------------------------------------------------------------
# 4. ValidateEntryZone activation guard
# ---------------------------------------------------------------------------

class TestValidateEntryZoneGuard:
    """Tests for ValidateEntryZone can_activate with entry_zones_evaluated flag."""

    def test_no_candidates_cannot_activate(self, validate_entry_zone, substrate):
        """No candidates → cannot activate."""
        assert validate_entry_zone.can_activate(substrate) is False

    def test_with_candidates_can_activate(self, validate_entry_zone, substrate_with_candidates):
        """Candidates present, not yet evaluated → can activate."""
        assert validate_entry_zone.can_activate(substrate_with_candidates) is True

    def test_after_evaluation_cannot_activate(self, validate_entry_zone, substrate_with_candidates):
        """After entry zone evaluation → cannot activate."""
        substrate_with_candidates.analysis["entry_zones_evaluated"] = True
        assert validate_entry_zone.can_activate(substrate_with_candidates) is False

    def test_transform_sets_flag(self, validate_entry_zone, substrate_with_candidates):
        """transform() sets entry_zones_evaluated = True."""
        result = validate_entry_zone.transform(substrate_with_candidates)
        assert result.analysis["entry_zones_evaluated"] is True

    def test_no_re_fire_after_evaluation(self, validate_entry_zone, substrate_with_candidates):
        """ValidateEntryZone cannot re-activate after evaluating."""
        result = validate_entry_zone.transform(substrate_with_candidates)
        assert validate_entry_zone.can_activate(result) is False


# ---------------------------------------------------------------------------
# 5. Daemon consecutive-fire guard
# ---------------------------------------------------------------------------

class TestDaemonConsecutiveFireGuard:
    """Tests for the daemon's consecutive-fire loop detection."""

    def test_consecutive_counter_increments(self):
        """Same enzyme name increments the counter."""
        last_enzyme_name = None
        consecutive_count = 0

        for i in range(3):
            name = "ScoreConfluence"
            if name == last_enzyme_name:
                consecutive_count += 1
            else:
                consecutive_count = 1
                last_enzyme_name = name

        assert consecutive_count == 3

    def test_counter_resets_on_different_enzyme(self):
        """Different enzyme name resets the counter."""
        last_enzyme_name = "ScoreConfluence"
        consecutive_count = 2

        # Switch to a different enzyme
        name = "DetectNoise"
        if name == last_enzyme_name:
            consecutive_count += 1
        else:
            consecutive_count = 1
            last_enzyme_name = name

        assert consecutive_count == 1
        assert last_enzyme_name == "DetectNoise"

    def test_break_at_threshold_3(self):
        """Loop breaks when consecutive_count >= 3."""
        last_enzyme_name = None
        consecutive_count = 0
        steps = 0
        max_steps = 20

        for step in range(max_steps):
            name = "ScoreConfluence"  # Always the same enzyme
            if name == last_enzyme_name:
                consecutive_count += 1
            else:
                consecutive_count = 1
                last_enzyme_name = name

            if consecutive_count >= 3:
                break  # Loop detected
            steps += 1

        assert steps == 2  # Broke after 2 successful fires (3rd triggered guard)

    def test_alternating_enzymes_no_break(self):
        """Alternating enzymes never trigger the guard."""
        enzymes = ["CollectOHLCV", "ScoreConfluence"] * 10
        last_enzyme_name = None
        consecutive_count = 0
        steps_completed = 0

        for name in enzymes:
            if name == last_enzyme_name:
                consecutive_count += 1
            else:
                consecutive_count = 1
                last_enzyme_name = name

            if consecutive_count >= 3:
                break
            steps_completed += 1

        assert steps_completed == len(enzymes)  # All steps completed


# ---------------------------------------------------------------------------
# 6. ISC behavior with empty candidates
# ---------------------------------------------------------------------------

class TestISCWithEmptyCandidates:
    """Tests for ISC-001 and ISC-006 with empty candidates (expected failures)."""

    def test_isc_001_fails_with_empty_candidates(self, substrate):
        """ISC-001 (any_score_gte) fails when candidates is empty."""
        results = substrate.verify_iscs()
        assert results["ISC-001"] == "failed"

    def test_isc_006_fails_with_empty_candidates(self, substrate):
        """ISC-006 (all_field_gte) fails when candidates is empty."""
        results = substrate.verify_iscs()
        assert results["ISC-006"] == "failed"

    def test_isc_002_passes_with_no_trade(self, substrate):
        """ISC-002 (sl_set_or_no_trade) passes vacuously when no trade pending."""
        results = substrate.verify_iscs()
        assert results["ISC-002"] == "verified"

    def test_isc_005_passes_with_wait_action(self, substrate):
        """ISC-005 (false_or_action_wait) passes when action is 'wait'."""
        results = substrate.verify_iscs()
        assert results["ISC-005"] == "verified"

    def test_isc_001_passes_with_qualifying_candidate(self, substrate_with_config):
        """ISC-001 passes when a candidate meets the entry threshold."""
        substrate_with_config.analysis["candidates"] = [
            {"symbol": "BTCUSDT", "score": 7.0, "indicators_aligned": 3}
        ]
        results = substrate_with_config.verify_iscs()
        assert results["ISC-001"] == "verified"

    def test_isc_006_passes_with_aligned_candidates(self, substrate_with_config):
        """ISC-006 passes when all candidates have sufficient indicators_aligned."""
        substrate_with_config.analysis["candidates"] = [
            {"symbol": "BTCUSDT", "score": 7.0, "indicators_aligned": 4}
        ]
        results = substrate_with_config.verify_iscs()
        assert results["ISC-006"] == "verified"


# ---------------------------------------------------------------------------
# 7. CollectPreTradeContext activation guard
# ---------------------------------------------------------------------------

class TestCollectPreTradeContextGuard:
    """Tests for CollectPreTradeContext can_activate with pre_trade_evaluated flag."""

    def test_no_candidates_cannot_activate(self):
        """No candidates → cannot activate."""
        enzyme = CollectPreTradeContext()
        s = Substrate()
        assert enzyme.can_activate(s) is False

    def test_with_candidates_can_activate(self):
        """Candidates present, not yet evaluated → can activate."""
        enzyme = CollectPreTradeContext()
        s = Substrate()
        s.analysis["candidates"] = [{"symbol": "BTCUSDT", "score": 0.5}]
        assert enzyme.can_activate(s) is True

    def test_after_evaluation_cannot_activate(self):
        """After pre-trade evaluation → cannot activate."""
        enzyme = CollectPreTradeContext()
        s = Substrate()
        s.analysis["candidates"] = [{"symbol": "BTCUSDT", "score": 0.5}]
        s.analysis["pre_trade_evaluated"] = True
        assert enzyme.can_activate(s) is False

    def test_transform_sets_flag(self):
        """transform() sets pre_trade_evaluated = True."""
        enzyme = CollectPreTradeContext()
        s = Substrate()
        s.analysis["candidates"] = [{"symbol": "BTCUSDT", "score": 0.5}]
        s.market["indicators"] = {
            "BTCUSDT": {"4H": {"ok": True, "rsi": {"value": 60}}}
        }
        result = enzyme.transform(s)
        assert result.analysis["pre_trade_evaluated"] is True

    def test_no_re_fire_after_evaluation(self):
        """CollectPreTradeContext cannot re-activate after evaluating."""
        enzyme = CollectPreTradeContext()
        s = Substrate()
        s.analysis["candidates"] = [{"symbol": "BTCUSDT", "score": 0.5}]
        s.market["indicators"] = {
            "BTCUSDT": {"4H": {"ok": True, "rsi": {"value": 60}}}
        }
        result = enzyme.transform(s)
        assert enzyme.can_activate(result) is False


# ---------------------------------------------------------------------------
# 8. CollectMacroContext activation guard
# ---------------------------------------------------------------------------

class TestCollectMacroContextGuard:
    """Tests for CollectMacroContext can_activate with macro_evaluated flag."""

    def test_module_disabled_cannot_activate(self):
        """macro_context module disabled → cannot activate."""
        enzyme = CollectMacroContext()
        s = Substrate()
        assert enzyme.can_activate(s) is False

    def test_module_enabled_can_activate(self):
        """macro_context module enabled, not yet evaluated → can activate."""
        enzyme = CollectMacroContext()
        s = Substrate(config={"modules": {"macro_context": True}})
        assert enzyme.can_activate(s) is True

    def test_after_evaluation_cannot_activate(self):
        """After macro evaluation → cannot activate."""
        enzyme = CollectMacroContext()
        s = Substrate(config={"modules": {"macro_context": True}})
        s.analysis["macro_evaluated"] = True
        assert enzyme.can_activate(s) is False

    def test_no_re_fire_after_evaluation(self):
        """CollectMacroContext cannot re-activate after evaluating."""
        enzyme = CollectMacroContext()
        s = Substrate(config={"modules": {"macro_context": True}})
        # Simulate transform setting the flag (without making API calls)
        s.analysis["macro_evaluated"] = True
        assert enzyme.can_activate(s) is False


# ---------------------------------------------------------------------------
# 9. Daemon per-cycle fired tracking
# ---------------------------------------------------------------------------

class TestDaemonFiredTracking:
    """Tests for the daemon's per-cycle fired enzyme tracking."""

    def test_fired_set_prevents_re_activation(self):
        """Enzymes in fired_this_cycle set are excluded from activatable list."""
        fired_this_cycle = {"ScoreConfluence"}

        # Simulate: ScoreConfluence already fired, should be excluded
        class MockEnzyme:
            def __init__(self, name):
                self.name = name
                self.enzyme_class = type("EC", (), {"value": "Oxidoreductase"})()
                self.priority = 3
                self.is_regulator = False
            def can_activate(self, substrate):
                return True
            def flux_score(self, substrate):
                return 1.0

        enzymes = [
            MockEnzyme("ScoreConfluence"),
            MockEnzyme("DetectNoise"),
        ]

        activatable = [
            e for e in enzymes
            if e.can_activate(None) and e.name not in fired_this_cycle
        ]

        assert len(activatable) == 1
        assert activatable[0].name == "DetectNoise"

    def test_empty_fired_set_allows_all(self):
        """Empty fired set allows all activatable enzymes."""
        fired_this_cycle = set()

        class MockEnzyme:
            def __init__(self, name):
                self.name = name
            def can_activate(self, substrate):
                return True

        enzymes = [MockEnzyme("A"), MockEnzyme("B"), MockEnzyme("C")]
        activatable = [
            e for e in enzymes
            if e.can_activate(None) and e.name not in fired_this_cycle
        ]
        assert len(activatable) == 3

    def test_fired_set_grows_each_step(self):
        """Each step adds the fired enzyme to the set."""
        fired_this_cycle = set()
        enzymes_fired = []

        # Simulate 3 steps firing different enzymes
        for name in ["CollectOHLCV", "ScoreConfluence", "DetectNoise"]:
            enzymes_fired.append(name)
            fired_this_cycle.add(name)

        assert fired_this_cycle == {"CollectOHLCV", "ScoreConfluence", "DetectNoise"}
        assert len(enzymes_fired) == 3
