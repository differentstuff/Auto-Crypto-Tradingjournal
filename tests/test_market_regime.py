# tests/test_market_regime.py
import pytest
import numpy as np
import pandas as pd
from unittest.mock import patch


def _fake_ohlcv(n=200):
    np.random.seed(42)
    closes = 100 * np.cumprod(1 + np.random.normal(0.001, 0.02, n))
    return pd.DataFrame({
        "ts": range(n), "open": closes * 0.999, "high": closes * 1.002,
        "low": closes * 0.998, "close": closes,
        "volume": np.random.uniform(1000, 5000, n),
    })


def test_detect_regime_valid_label():
    with patch("market_regime._fetch_ohlcv", return_value=_fake_ohlcv(200)):
        import market_regime
        result = market_regime._fit_and_predict()
    assert result["ok"] is True
    assert result["label"] in ("trending_up", "ranging", "trending_down")
    assert 0.0 <= result["confidence"] <= 1.0


def test_detect_regime_too_short():
    with patch("market_regime._fetch_ohlcv", return_value=_fake_ohlcv(10)):
        import market_regime
        result = market_regime._fit_and_predict()
    assert result["ok"] is False


def test_detect_regime_exception():
    with patch("market_regime._fetch_ohlcv", side_effect=Exception("API down")):
        import market_regime
        result = market_regime._fit_and_predict()
    assert result["ok"] is False
