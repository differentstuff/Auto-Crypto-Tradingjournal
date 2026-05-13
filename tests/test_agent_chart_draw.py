# tests/test_agent_chart_draw.py
import pandas as pd
import numpy as np
import base64
import pytest
import agent_chart_draw as cd


def _candles(n=60) -> pd.DataFrame:
    idx   = pd.date_range("2026-01-01", periods=n, freq="4h")
    close = 1.50 + np.cumsum(np.random.randn(n) * 0.005)
    return pd.DataFrame({
        "open":   close - 0.002,
        "high":   close + 0.010,
        "low":    close - 0.010,
        "close":  close,
        "volume": np.random.randint(100, 1000, n).astype(float),
    }, index=idx)


def test_chart_returns_base64_png():
    df = _candles()
    result = cd.draw(
        candles=df,
        symbol="XRPUSDT",
        direction="Long",
        entry=1.52,
        sl=1.46,
        tp1=1.61,
        tp2=1.72,
        criteria=["RSI 72 — overbought | EMA bullish", "Confluence 7.2/10 — Strong Bullish"],
    )
    assert isinstance(result, str)
    assert len(result) > 100
    # Must be valid base64 PNG
    decoded = base64.b64decode(result)
    assert decoded[:8] == b'\x89PNG\r\n\x1a\n'


def test_chart_returns_empty_string_on_error():
    # Empty DataFrame — should not raise
    result = cd.draw(
        candles=pd.DataFrame(),
        symbol="XRPUSDT", direction="Long",
        entry=1.52, sl=1.46, tp1=1.61, tp2=1.72,
        criteria=[],
    )
    assert result == ""
