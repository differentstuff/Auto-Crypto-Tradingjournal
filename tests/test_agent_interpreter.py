import pandas as pd
import numpy as np
import pytest
from agent_types import CollectorResult, InterpreterResult
import agent_data_interpreter as interp


def _mock_candles(n=100) -> pd.DataFrame:
    """Generate synthetic OHLCV data with a mild uptrend."""
    idx = pd.date_range("2026-01-01", periods=n, freq="4h")
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    df = pd.DataFrame({
        "open":   close - 0.2,
        "high":   close + 0.5,
        "low":    close - 0.5,
        "close":  close,
        "volume": np.random.randint(1000, 5000, n).astype(float),
    }, index=idx)
    return df


def _mock_collected(symbol="BTCUSDT") -> CollectorResult:
    df = _mock_candles()
    return CollectorResult(
        symbol="BTCUSDT", candles={"4H": df, "1D": df},
        funding_rate={}, open_interest={}, long_short={},
        fear_greed={}, fred_macro={}, nansen={}, grok={},
        fetched_at=0.0,
    )


def test_interpreter_returns_correct_shape():
    result = interp.run({"collected": _mock_collected()})
    assert isinstance(result, dict)
    assert result["symbol"] == "BTCUSDT"
    assert "by_timeframe" in result
    assert "sr_levels" in result
    assert "confluence_score" in result
    assert result["trend_direction"] in ("bullish", "bearish", "neutral")
    assert result["momentum_bias"] in ("strong", "moderate", "weak", "conflicted")
    assert isinstance(result["prompt_text"], str)
    assert len(result["prompt_text"]) <= 500


def test_interpreter_handles_empty_candles():
    collected = _mock_collected()
    collected["candles"] = {"4H": pd.DataFrame(), "1D": pd.DataFrame()}
    result = interp.run({"collected": collected})
    assert result["trend_direction"] == "neutral"
    assert result["sr_levels"] == []
    assert result["momentum_bias"] == "conflicted"
