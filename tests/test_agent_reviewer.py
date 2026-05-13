# tests/test_agent_reviewer.py
import pytest
from agent_types import InterpreterResult, ReviewerResult
import agent_data_reviewer as rev


def _mock_interpreted(conf_score=6.0, adx=25.0, rsi=65.0, sr_touches=3) -> InterpreterResult:
    return InterpreterResult(
        symbol="BTCUSDT",
        by_timeframe={"4H": {
            "adx":  {"value": adx},
            "rsi":  {"value": rsi},
            "ema":  {"bias": "Bullish"},
            "macd": {"signal": "bullish"},
        }},
        sr_levels=[{"price": 95000, "type": "support", "strength": 0.8,
                    "touches": sr_touches, "recency_score": 0.9}],
        confluence_score={"score": conf_score, "max": 11.8, "label": "Bullish",
                          "bullish": 3.2, "bearish": 0.8, "details": []},
        trend_direction="bullish",
        momentum_bias="moderate",
        prompt_text="[BTCUSDT] 4H: RSI 65",
    )


def test_reviewer_returns_correct_shape(db):
    inp = {"interpreted": _mock_interpreted(), "symbol": "BTCUSDT",
           "direction": "Long", "setup_type": "continuation"}
    result = rev.run(inp, db)
    assert isinstance(result["signal_quality"], float)
    assert 0.0 <= result["signal_quality"] <= 10.0
    assert isinstance(result["warnings"], list)
    assert isinstance(result["backtest_context"], str)
    assert "kpis" in result
    assert "symbol_history" in result
    assert "rubric" in result


def test_low_confluence_reduces_quality(db):
    inp = {"interpreted": _mock_interpreted(conf_score=1.5), "symbol": "BTCUSDT",
           "direction": "Long", "setup_type": "continuation"}
    result = rev.run(inp, db)
    assert result["signal_quality"] <= 8.0
    assert any("confluence" in w.lower() for w in result["warnings"])


def test_low_adx_warns_on_trend_setup(db):
    inp = {"interpreted": _mock_interpreted(adx=15.0), "symbol": "BTCUSDT",
           "direction": "Long", "setup_type": "breakout"}
    result = rev.run(inp, db)
    assert any("adx" in w.lower() for w in result["warnings"])
