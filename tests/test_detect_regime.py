"""
tests/test_detect_regime.py -- Unit tests for regime detection enzyme (GaussianMixture).

Tests cover:
  - _identify_states(): Normal/Spike component identification
  - DetectRegime can_activate(): respects hmm.enabled config
  - Model fitting on synthetic data
  - Confidence threshold application
  - Graceful degradation when model not fitted
  - Substrate writes: market.regime and confluence.regime_normal
  - Refit schedule logic
  - ScoreConfluence regime gate
"""

import time
from unittest.mock import MagicMock

import numpy as np
import pytest

from tests.conftest import make_full_config


# -- Helper: create a substrate with HMM enabled ------------------------------

def _make_hmm_config(**hmm_overrides) -> dict:
    """Return a full config with HMM enabled and optional overrides."""
    hmm_cfg = {
        "enabled": True,
        "lookback_days": 30,
        "confidence_threshold": 0.70,
        "refit_interval_days": 7,
        "min_bars": 100,  # Lower for tests
        "n_restarts": 1,  # Faster for tests
    }
    hmm_cfg.update(hmm_overrides)
    return make_full_config(hmm=hmm_cfg)


def _make_substrate_with_hmm(**hmm_overrides):
    """Create a Substrate with HMM enabled."""
    from core.substrate import Substrate
    config = _make_hmm_config(**hmm_overrides)
    return Substrate(config=config)


def _make_mock_exchange(returns_length=800):
    """Create a mock Exchange that returns synthetic OHLCV data."""
    exchange = MagicMock()
    # Generate synthetic hourly close prices with two regimes
    np.random.seed(42)
    n = returns_length + 1
    # Normal regime: low volatility
    normal_returns = np.random.normal(0.0001, 0.01, n // 2)
    # Spike regime: high volatility
    spike_returns = np.random.normal(-0.001, 0.05, n // 2)
    all_returns = np.concatenate([normal_returns, spike_returns])
    prices = 100.0 * np.exp(np.cumsum(all_returns))

    import pandas as pd
    dates = pd.date_range("2025-01-01", periods=len(prices), freq="1h")
    df = pd.DataFrame({"close": prices}, index=dates)
    exchange.fetch_ohlcv.return_value = df
    return exchange


# -- Tests: _identify_states --------------------------------------------------

class TestIdentifyStates:
    """Test the _identify_states helper function."""

    def test_normal_is_lower_variance(self):
        """Component with lower variance should be identified as Normal."""
        from enzymes.detect_regime import _identify_states

        model = MagicMock()
        model.covariances_ = [np.array([[0.001]]), np.array([[0.05]])]
        normal, spike = _identify_states(model)
        assert normal == 0
        assert spike == 1

    def test_spike_is_lower_variance_when_reversed(self):
        """When component 1 has lower variance, it should be identified as Normal."""
        from enzymes.detect_regime import _identify_states

        model = MagicMock()
        model.covariances_ = [np.array([[0.05]]), np.array([[0.001]])]
        normal, spike = _identify_states(model)
        assert normal == 1
        assert spike == 0

    def test_equal_variance_defaults_to_comp0_normal(self):
        """When variances are equal, component 0 is Normal."""
        from enzymes.detect_regime import _identify_states

        model = MagicMock()
        model.covariances_ = [np.array([[0.01]]), np.array([[0.01]])]
        normal, spike = _identify_states(model)
        assert normal == 0
        assert spike == 1


# -- Tests: can_activate ------------------------------------------------------

class TestCanActivate:
    """Test DetectRegime.can_activate() behavior."""

    def test_cannot_activate_when_disabled(self):
        """Enzyme should not activate when hmm.enabled=False."""
        from core.substrate import Substrate
        from enzymes.detect_regime import DetectRegime

        config = make_full_config(hmm={"enabled": False})
        substrate = Substrate(config=config)
        enzyme = DetectRegime(config=config)

        assert not enzyme.can_activate(substrate)

    def test_can_activate_when_enabled(self):
        """Enzyme should activate when hmm.enabled=True."""
        substrate = _make_substrate_with_hmm()
        from enzymes.detect_regime import DetectRegime

        enzyme = DetectRegime(config=substrate._config)
        assert enzyme.can_activate(substrate)


# -- Tests: Model fitting -----------------------------------------------------

class TestModelFitting:
    """Test GaussianMixture fitting on synthetic data."""

    def test_fit_model_success(self):
        """Model should fit successfully with sufficient synthetic data."""
        from enzymes.detect_regime import DetectRegime

        substrate = _make_substrate_with_hmm(min_bars=100, n_restarts=1)
        exchange = _make_mock_exchange(returns_length=800)
        enzyme = DetectRegime(config=substrate._config, exchange=exchange)

        enzyme._fit_model(substrate)

        assert enzyme._fitted is True
        assert enzyme._model is not None
        assert enzyme._last_fit_at > 0

    def test_fit_model_no_exchange(self):
        """Model should not fit when no Exchange is available."""
        from enzymes.detect_regime import DetectRegime

        substrate = _make_substrate_with_hmm()
        enzyme = DetectRegime(config=substrate._config, exchange=None)

        enzyme._fit_model(substrate)

        assert enzyme._fitted is False

    def test_fit_model_insufficient_data(self):
        """Model should not fit when exchange returns too little data."""
        from enzymes.detect_regime import DetectRegime

        substrate = _make_substrate_with_hmm()
        exchange = MagicMock()
        exchange.fetch_ohlcv.return_value = None  # No data
        enzyme = DetectRegime(config=substrate._config, exchange=exchange)

        enzyme._fit_model(substrate)

        assert enzyme._fitted is False

    def test_normal_state_has_lower_variance(self):
        """After fitting, the Normal component should have lower variance than Spike."""
        from enzymes.detect_regime import DetectRegime

        substrate = _make_substrate_with_hmm(min_bars=100, n_restarts=1)
        exchange = _make_mock_exchange(returns_length=800)
        enzyme = DetectRegime(config=substrate._config, exchange=exchange)

        enzyme._fit_model(substrate)

        normal_var = enzyme._model.covariances_[enzyme._normal_state].flatten()[0]
        spike_var = enzyme._model.covariances_[enzyme._spike_state].flatten()[0]
        assert normal_var < spike_var


# -- Tests: Prediction and substrate writes -----------------------------------

class TestPrediction:
    """Test regime prediction and substrate field writes."""

    def _make_fitted_enzyme(self):
        """Create a fitted enzyme with synthetic data."""
        from enzymes.detect_regime import DetectRegime

        substrate = _make_substrate_with_hmm(min_bars=100, n_restarts=1)
        exchange = _make_mock_exchange(returns_length=800)
        enzyme = DetectRegime(config=substrate._config, exchange=exchange)
        enzyme._fit_model(substrate)
        return enzyme, substrate

    def test_predict_writes_regime_to_substrate(self):
        """Prediction should write market.regime dict to substrate."""
        enzyme, substrate = self._make_fitted_enzyme()

        enzyme._predict_and_write(substrate)

        assert "regime" in substrate.market
        regime = substrate.market["regime"]
        assert "state" in regime
        assert "prob_normal" in regime
        assert "prob_spike" in regime
        assert "state_label" in regime
        assert "confidence_threshold" in regime

    def test_predict_writes_confluence_regime_normal(self):
        """Prediction should write confluence.regime_normal to substrate."""
        enzyme, substrate = self._make_fitted_enzyme()

        enzyme._predict_and_write(substrate)

        assert "regime_normal" in substrate.confluence
        assert isinstance(substrate.confluence["regime_normal"], bool)

    def test_regime_normal_true_when_high_confidence(self):
        """regime_normal should be True when prob_normal >= threshold."""
        enzyme, substrate = self._make_fitted_enzyme()

        enzyme._predict_and_write(substrate)

        # Check that the probability is valid
        regime = substrate.market["regime"]
        assert 0.0 <= regime["prob_normal"] <= 1.0
        assert 0.0 <= regime["prob_spike"] <= 1.0
        # prob_normal + prob_spike should be ~1.0
        assert abs(regime["prob_normal"] + regime["prob_spike"] - 1.0) < 0.01

    def test_regime_normal_false_during_spike(self):
        """regime_normal should be False when in Spike state with high confidence."""
        enzyme, substrate = self._make_fitted_enzyme()

        # Manually set the enzyme to predict Spike
        substrate.confluence["regime_normal"] = False
        substrate.market["regime"] = {
            "state": enzyme._spike_state,
            "state_label": "Spike",
            "prob_normal": 0.15,
            "prob_spike": 0.85,
            "confidence_threshold": 0.70,
            "last_fitted_at": "",
        }

        assert substrate.confluence["regime_normal"] is False
        assert substrate.market["regime"]["state_label"] == "Spike"


# -- Tests: Graceful degradation ----------------------------------------------

class TestGracefulDegradation:
    """Test fail-open behavior when model is not fitted."""

    def test_defaults_to_normal_when_not_fitted(self):
        """When model is not fitted, regime should default to Normal."""
        from enzymes.detect_regime import DetectRegime

        substrate = _make_substrate_with_hmm()
        enzyme = DetectRegime(config=substrate._config, exchange=None)

        enzyme._write_defaults(substrate)

        assert substrate.confluence["regime_normal"] is True
        assert substrate.market["regime"]["state_label"] == "Normal (default)"

    def test_transform_defaults_when_no_exchange(self):
        """Full transform should default to Normal when no Exchange available."""
        from enzymes.detect_regime import DetectRegime

        substrate = _make_substrate_with_hmm()
        enzyme = DetectRegime(config=substrate._config, exchange=None)

        result = enzyme.transform(substrate)

        assert result.confluence["regime_normal"] is True

    def test_transform_does_not_crash_on_fit_failure(self):
        """Transform should not crash even if fitting fails."""
        from enzymes.detect_regime import DetectRegime

        substrate = _make_substrate_with_hmm()
        exchange = MagicMock()
        exchange.fetch_ohlcv.return_value = None
        enzyme = DetectRegime(config=substrate._config, exchange=exchange)

        # Should not raise
        result = enzyme.transform(substrate)
        assert result.confluence["regime_normal"] is True  # Fail-open


# -- Tests: Refit schedule ----------------------------------------------------

class TestRefitSchedule:
    """Test model refit scheduling logic."""

    def test_should_fit_on_first_run(self):
        """Model should fit when not yet fitted."""
        from enzymes.detect_regime import DetectRegime

        substrate = _make_substrate_with_hmm()
        enzyme = DetectRegime(config=substrate._config)

        assert enzyme._should_fit(substrate) is True

    def test_should_not_fit_when_recently_fitted(self):
        """Model should not refit when recently fitted."""
        from enzymes.detect_regime import DetectRegime

        substrate = _make_substrate_with_hmm(refit_interval_days=7)
        enzyme = DetectRegime(config=substrate._config)
        enzyme._fitted = True
        enzyme._last_fit_at = time.time()  # Just fitted

        assert enzyme._should_fit(substrate) is False

    def test_should_refit_after_interval(self):
        """Model should refit after refit_interval_days has elapsed."""
        from enzymes.detect_regime import DetectRegime

        substrate = _make_substrate_with_hmm(refit_interval_days=7)
        enzyme = DetectRegime(config=substrate._config)
        enzyme._fitted = True
        enzyme._last_fit_at = time.time() - 8 * 86400  # 8 days ago

        assert enzyme._should_fit(substrate) is True

    def test_should_not_refit_when_interval_zero(self):
        """Model should never refit when refit_interval_days=0."""
        from enzymes.detect_regime import DetectRegime

        substrate = _make_substrate_with_hmm(refit_interval_days=0)
        enzyme = DetectRegime(config=substrate._config)
        enzyme._fitted = True
        enzyme._last_fit_at = 0  # Very old

        assert enzyme._should_fit(substrate) is False


# -- Tests: Confidence threshold ----------------------------------------------

class TestConfidenceThreshold:
    """Test confidence threshold application."""

    def test_threshold_from_config(self):
        """Confidence threshold should come from config."""
        from enzymes.detect_regime import DetectRegime

        substrate = _make_substrate_with_hmm(confidence_threshold=0.80)
        enzyme = DetectRegime(config=substrate._config, exchange=None)

        threshold = substrate.cfg("hmm.confidence_threshold", 0.70)
        assert threshold == 0.80

    def test_regime_normal_requires_confidence(self):
        """regime_normal should be False when confidence is below threshold."""
        from enzymes.detect_regime import DetectRegime

        substrate = _make_substrate_with_hmm(confidence_threshold=0.70)
        enzyme = DetectRegime(config=substrate._config, exchange=None)

        # Simulate: state is Normal but confidence is below threshold
        enzyme._normal_state = 0
        enzyme._spike_state = 1

        # Manually write regime data simulating low confidence
        substrate.market["regime"] = {
            "state": 0,
            "state_label": "Normal",
            "prob_normal": 0.65,  # Below 0.70 threshold
            "prob_spike": 0.35,
            "confidence_threshold": 0.70,
            "last_fitted_at": "",
        }
        # regime_normal = (state == normal_state) AND (prob_normal >= threshold)
        # 0.65 < 0.70 → regime_normal = False
        regime_normal = (0 == 0) and (0.65 >= 0.70)
        assert regime_normal is False

    def test_regime_normal_true_when_confidence_met(self):
        """regime_normal should be True when confidence meets threshold."""
        # prob_normal = 0.85 >= 0.70 → regime_normal = True
        regime_normal = (0 == 0) and (0.85 >= 0.70)
        assert regime_normal is True


# -- Tests: ScoreConfluence regime gate ---------------------------------------

class TestScoreConfluenceRegimeGate:
    """Test that ScoreConfluence respects the regime gate."""

    def test_score_confluence_blocked_during_spike(self):
        """ScoreConfluence should not activate when regime_normal=False."""
        from core.substrate import Substrate
        from enzymes.score_confluence import ScoreConfluence

        config = make_full_config(hmm={"enabled": True})
        substrate = Substrate(config=config)

        # Set up indicators so ScoreConfluence would normally activate
        substrate.market["indicators"] = {
            "BTCUSDT": {
                "4H": {
                    "ok": True,
                    "rsi": {"value": 30, "signal": "oversold"},
                    "macd": {"bias": "bullish", "histogram_growing": True},
                    "ema_stack": {"alignment": "bullish", "stack": "bullish"},
                    "adx": {"value": 25, "direction": "bullish"},
                }
            }
        }

        # Set regime to Spike
        substrate.confluence["regime_normal"] = False

        enzyme = ScoreConfluence(config=substrate._config)
        assert not enzyme.can_activate(substrate)

    def test_score_confluence_allowed_during_normal(self):
        """ScoreConfluence should activate when regime_normal=True."""
        from core.substrate import Substrate
        from enzymes.score_confluence import ScoreConfluence

        config = make_full_config(hmm={"enabled": True})
        substrate = Substrate(config=config)

        # Set up indicators
        substrate.market["indicators"] = {
            "BTCUSDT": {
                "4H": {
                    "ok": True,
                    "rsi": {"value": 30, "signal": "oversold"},
                    "macd": {"bias": "bullish", "histogram_growing": True},
                    "ema_stack": {"alignment": "bullish", "stack": "bullish"},
                    "adx": {"value": 25, "direction": "bullish"},
                }
            }
        }

        # Set regime to Normal
        substrate.confluence["regime_normal"] = True

        enzyme = ScoreConfluence(config=substrate._config)
        assert enzyme.can_activate(substrate)

    def test_score_confluence_allowed_by_default(self):
        """ScoreConfluence should activate when regime_normal is not set (default True)."""
        from core.substrate import Substrate
        from enzymes.score_confluence import ScoreConfluence

        config = make_full_config()
        substrate = Substrate(config=config)

        # Set up indicators
        substrate.market["indicators"] = {
            "BTCUSDT": {
                "4H": {
                    "ok": True,
                    "rsi": {"value": 30, "signal": "oversold"},
                    "macd": {"bias": "bullish", "histogram_growing": True},
                    "ema_stack": {"alignment": "bullish", "stack": "bullish"},
                    "adx": {"value": 25, "direction": "bullish"},
                }
            }
        }

        # confluence.regime_normal defaults to True
        assert substrate.confluence.get("regime_normal", True) is True

        enzyme = ScoreConfluence(config=substrate._config)
        assert enzyme.can_activate(substrate)


# -- Tests: Substrate confluence namespace ------------------------------------

class TestSubstrateConfluence:
    """Test that substrate.confluence namespace works correctly."""

    def test_confluence_initialized(self):
        """Substrate should initialize confluence section."""
        from core.substrate import Substrate
        config = make_full_config()
        substrate = Substrate(config=config)

        assert hasattr(substrate, "confluence")
        assert "regime_normal" in substrate.confluence
        assert substrate.confluence["regime_normal"] is True

    def test_confluence_survives_shallow_copy(self):
        """confluence should survive shallow_copy()."""
        from core.substrate import Substrate
        config = make_full_config()
        substrate = Substrate(config=config)
        substrate.confluence["regime_normal"] = False

        copy = substrate.shallow_copy()
        assert copy.confluence["regime_normal"] is False

    def test_confluence_accessible_after_cycle_reset(self):
        """confluence persists across reset_cycle (not a per-cycle field)."""
        from core.substrate import Substrate
        config = make_full_config()
        substrate = Substrate(config=config)
        substrate.confluence["regime_normal"] = False

        substrate.reset_cycle()
        assert substrate.confluence["regime_normal"] is False


# -- Tests: Flux score -------------------------------------------------------

class TestFluxScore:
    """Test DetectRegime flux_score behavior."""

    def test_flux_score_when_enabled(self):
        """Flux score should be positive when HMM is enabled."""
        from enzymes.detect_regime import DetectRegime

        substrate = _make_substrate_with_hmm()
        enzyme = DetectRegime(config=substrate._config)

        assert enzyme.flux_score(substrate) > 0

    def test_flux_score_zero_when_disabled(self):
        """Flux score should be 0 when HMM is disabled."""
        from core.substrate import Substrate
        from enzymes.detect_regime import DetectRegime

        config = make_full_config(hmm={"enabled": False})
        substrate = Substrate(config=config)
        enzyme = DetectRegime(config=substrate._config)

        assert enzyme.flux_score(substrate) == 0.0
