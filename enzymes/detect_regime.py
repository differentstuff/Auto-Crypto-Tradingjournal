"""
enzymes/detect_regime.py -- Sensor enzyme: regime detection via Gaussian Mixture.

Fits a 2-component GaussianMixture on hourly log-returns to detect market regimes.
Component 0: Normal (low volatility, mean-reverting)
Component 1: Spike  (high volatility event)

Uses sklearn.mixture.GaussianMixture (pure Python, no C extensions required)
instead of hmmlearn — same two-Gaussian decomposition, no Markov transitions
needed for a binary regime gate.

Writes to substrate:
  - market.regime.state          (int: 0=Normal, 1=Spike)
  - market.regime.prob_normal    (float: probability of Normal state)
  - market.regime.prob_spike     (float: probability of Spike state)
  - market.regime.last_fitted_at (str: ISO timestamp of last model fit)
  - confluence.regime_normal     (bool: True only if Normal AND confidence > threshold)

Based on El Oraculo (Niiks7777/el-oraculo) validation:
  - SOL win rate improved from 77.9% to 87.0% (+9.1%) with regime filter
  - 70% confidence threshold prevents whipsaw
  - 2-component Gaussian mixture on hourly log-returns is sufficient

Enzyme class: Sensor
Activates when: hmm.enabled=True in config
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

import numpy as np
from sklearn.mixture import GaussianMixture

from core.enzyme import Enzyme, EnzymeClass, register_enzyme
from core.substrate import Substrate

_log = logging.getLogger(__name__)


def _identify_states(model: GaussianMixture) -> tuple[int, int]:
    """Identify which mixture component is Normal (low var) and which is Spike (high var).

    GaussianMixture doesn't guarantee component 0 = Normal. We identify by
    comparing the diagonal of the covariance matrices — lower variance = Normal.

    Returns:
        (normal_state, spike_state) — component indices (0 or 1)
    """
    var_0 = model.covariances_[0].flatten()[0]
    var_1 = model.covariances_[1].flatten()[0]
    if var_0 <= var_1:
        return 0, 1
    return 1, 0


@register_enzyme
class DetectRegime(Enzyme):
    """
    Sensor enzyme: detect market regime using 2-component GaussianMixture.

    Fits on startup with historical hourly log-returns (30 days default).
    Predicts current regime on each cycle. Writes confluence.regime_normal
    which ScoreConfluence checks before scoring candidates.

    Graceful degradation: if model not fitted, defaults to regime_normal=True
    (fail-open: better to trade than silently block everything).
    """

    name = "DetectRegime"
    enzyme_class = EnzymeClass.SENSOR
    priority = 6  # Higher than ScoreConfluence (3) — fires first

    def __init__(self, config: Optional[dict] = None, exchange=None):
        """
        Initialize DetectRegime.

        Args:
            config: Strategy config dict (same as all enzymes).
            exchange: core.exchange.Exchange instance for hourly data fetching.
        """
        super().__init__(config=config)
        self._exchange = exchange
        self._model: Optional[GaussianMixture] = None
        self._fitted = False
        self._normal_state = 0
        self._spike_state = 1
        self._last_fit_at: float = 0.0  # Unix timestamp
        self._last_returns: Optional[np.ndarray] = None  # cached for prediction

    def requires(self) -> list[str]:
        return ["strategy.name is set"]

    def prohibits(self) -> list[str]:
        return []

    def can_activate(self, substrate: Substrate) -> bool:
        """Activate only when hmm.enabled=True in config."""
        enabled = substrate.cfg("hmm.enabled", False)
        return enabled

    def transform(self, substrate: Substrate) -> Substrate:
        """Detect current market regime and write to substrate."""
        # Step 1: Fit or refit if needed
        if self._should_fit(substrate):
            self._fit_model(substrate)

        # Step 2: Predict current regime
        if self._fitted and self._model is not None:
            self._predict_and_write(substrate)
        else:
            # Graceful degradation: default to Normal regime
            self._write_defaults(substrate)
            self._log.debug(
                "Regime model not fitted — defaulting to regime_normal=True"
            )

        return substrate

    # --- Model fitting ---------------------------------------------------------

    def _should_fit(self, substrate: Substrate) -> bool:
        """Determine if the model needs to be (re)fitted."""
        if not self._fitted:
            return True

        # Check refit interval
        refit_interval_days = substrate.cfg("hmm.refit_interval_days", 7)
        if refit_interval_days <= 0:
            return False  # Never refit

        elapsed_days = (time.time() - self._last_fit_at) / 86400.0
        return elapsed_days >= refit_interval_days

    def _fit_model(self, substrate: Substrate) -> None:
        """Fit a 2-component GaussianMixture on historical hourly log-returns."""
        # Fetch hourly returns data
        returns = self._fetch_hourly_returns(substrate)
        if returns is None or len(returns) < 100:
            self._log.warning(
                "Insufficient data for regime fitting (%d bars, need 100+)",
                len(returns) if returns is not None else 0,
            )
            self._fitted = False
            return

        min_bars = substrate.cfg("hmm.min_bars", 720)
        if len(returns) < min_bars:
            self._log.warning(
                "Insufficient data for reliable regime fitting "
                "(%d bars, need %d). Fitting anyway with available data.",
                len(returns), min_bars,
            )

        try:
            # Reshape for sklearn: (n_samples, n_features)
            X = returns.reshape(-1, 1)

            # Multi-start optimization via n_init (sklearn built-in)
            # GaussianMixture supports n_init parameter for multiple random
            # initializations, selecting the best by lower BIC.
            n_restarts = substrate.cfg("hmm.n_restarts", 3)

            model = GaussianMixture(
                n_components=2,
                covariance_type="full",
                n_init=n_restarts,
                max_iter=1000,
                random_state=42,
                reg_covar=1e-6,  # regularization for numerical stability
            )
            model.fit(X)
            score = model.score(X)  # per-sample average log-likelihood

            self._model = model
            self._normal_state, self._spike_state = _identify_states(
                self._model
            )
            self._fitted = True
            self._last_fit_at = time.time()
            self._last_returns = returns

            self._log.info(
                "Regime model fitted: %d bars, log-likelihood=%.4f, "
                "Normal=comp%d (var=%.6f), Spike=comp%d (var=%.6f)",
                len(returns), score,
                self._normal_state,
                self._model.covariances_[self._normal_state].flatten()[0],
                self._spike_state,
                self._model.covariances_[self._spike_state].flatten()[0],
            )

        except Exception as e:
            self._log.error("Regime model fitting failed: %s", e, exc_info=True)
            self._fitted = False

    def _fetch_hourly_returns(self, substrate: Substrate) -> Optional[np.ndarray]:
        """Fetch hourly close prices and compute log-returns.

        Tries indicator_history first (already on substrate), then
        falls back to direct exchange API call.
        """
        # Try to get hourly data from exchange
        if self._exchange is None:
            self._log.warning("No Exchange instance — cannot fetch hourly data for regime detection")
            return None

        lookback_days = substrate.cfg("hmm.lookback_days", 30)
        # We need ~30 days of hourly candles = 720 bars
        limit = min(lookback_days * 24 + 24, 1000)  # cap at 1000 bars

        # Use the first watched symbol as representative market data
        symbols = substrate.market.get("symbols_watched", [])
        if not symbols:
            self._log.warning("No symbols watched — cannot fetch data for regime detection")
            return None

        # Use BTCUSDT as the regime proxy (most representative of overall market)
        regime_symbol = "BTCUSDT" if "BTCUSDT" in symbols else symbols[0]

        try:
            df = self._exchange.fetch_ohlcv(
                regime_symbol, timeframe="1h", limit=limit
            )
            if df is None or df.empty or len(df) < 100:
                self._log.warning(
                    "Insufficient hourly data for %s (%d bars)",
                    regime_symbol, len(df) if df is not None else 0,
                )
                return None

            # Compute log-returns from close prices
            closes = df["close"].values.astype(float)
            log_returns = np.log(closes[1:] / closes[:-1])

            # Remove NaN/inf values
            log_returns = log_returns[np.isfinite(log_returns)]

            return log_returns

        except Exception as e:
            self._log.error(
                "Failed to fetch hourly data for regime detection: %s", e, exc_info=True
            )
            return None

    # --- Prediction ------------------------------------------------------------

    def _predict_and_write(self, substrate: Substrate) -> None:
        """Predict current regime and write results to substrate."""
        try:
            # Get recent returns for prediction
            returns = self._get_recent_returns(substrate)
            if returns is None or len(returns) < 10:
                self._write_defaults(substrate)
                return

            # Use the last N bars for prediction context
            # Feed enough history for the mixture model to classify reliably
            context_bars = min(len(returns), 240)  # last 10 days of hourly data
            X = returns[-context_bars:].reshape(-1, 1)

            # Predict component assignments and posterior probabilities
            state_sequence = self._model.predict(X)
            probabilities = self._model.predict_proba(X)

            # Current state and probabilities (last bar)
            current_state = int(state_sequence[-1])
            prob_normal = float(probabilities[-1, self._normal_state])
            prob_spike = float(probabilities[-1, self._spike_state])

            # Apply confidence threshold
            confidence_threshold = substrate.cfg(
                "hmm.confidence_threshold", 0.70
            )
            regime_normal = (
                current_state == self._normal_state
                and prob_normal >= confidence_threshold
            )

            # Write to substrate
            substrate.market["regime"] = {
                "state": current_state,
                "state_label": "Normal" if current_state == self._normal_state else "Spike",
                "prob_normal": round(prob_normal, 4),
                "prob_spike": round(prob_spike, 4),
                "confidence_threshold": confidence_threshold,
                "last_fitted_at": datetime.fromtimestamp(
                    self._last_fit_at, tz=timezone.utc
                ).isoformat() if self._last_fit_at else "",
            }
            substrate.confluence["regime_normal"] = regime_normal

            state_label = "Normal" if regime_normal else "Spike"
            self._log.info(
                "Regime: %s (prob_normal=%.2f, prob_spike=%.2f, threshold=%.2f)",
                state_label, prob_normal, prob_spike, confidence_threshold,
            )

        except Exception as e:
            self._log.error("Regime prediction failed: %s", e, exc_info=True)
            self._write_defaults(substrate)

    def _get_recent_returns(self, substrate: Substrate) -> Optional[np.ndarray]:
        """Get recent hourly log-returns for prediction.

        Fetches fresh data each cycle for the most current regime estimate.
        """
        # Fetch fresh data for prediction
        returns = self._fetch_hourly_returns(substrate)
        if returns is not None:
            self._last_returns = returns
        return returns

    def _write_defaults(self, substrate: Substrate) -> None:
        """Write default regime values (Normal) to substrate.

        Used when model is not fitted or prediction fails.
        Fail-open: default to Normal regime so trading is not blocked.
        """
        substrate.market["regime"] = {
            "state": self._normal_state,
            "state_label": "Normal (default)",
            "prob_normal": 1.0,
            "prob_spike": 0.0,
            "confidence_threshold": substrate.cfg(
                "hmm.confidence_threshold", 0.70
            ),
            "last_fitted_at": "",
        }
        substrate.confluence["regime_normal"] = True  # Fail-open

    def flux_score(self, substrate: Substrate) -> float:
        """Dynamic flux: always runs when enabled (foundational Sensor)."""
        if not self.can_activate(substrate):
            return 0.0
        # Regime detection is foundational — it must run before ScoreConfluence.
        # Higher flux than most sensors to ensure it fires early.
        return 2.5