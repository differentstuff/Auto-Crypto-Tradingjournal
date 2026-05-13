# tests/test_agent_trade_monitor.py
import pytest
from unittest.mock import patch
from agent_types import MonitorInput, MonitorResult, InterpreterResult, SentimentResult
import agent_trade_monitor as mon


def _interp() -> InterpreterResult:
    return InterpreterResult(
        symbol="BTCUSDT", by_timeframe={}, sr_levels=[],
        confluence_score={}, trend_direction="bearish",
        momentum_bias="strong", prompt_text="bearish momentum",
    )


def _sent() -> SentimentResult:
    return SentimentResult(
        macro_bias="bearish", sentiment_score=3.0, funding_bias="longs_paying",
        crowd_position="majority_long", contra_signal=True,
        key_factors=["F&G 80 — Extreme Greed"],
        grok_summary="", prompt_text="Bearish macro",
    )


def _position(symbol="BTCUSDT", unrealized_pct=-35.0, duration_min=300):
    return {
        "symbol": symbol, "side": "long",
        "unrealized_pct": unrealized_pct,
        "duration_minutes": duration_min,
        "markPrice": "95000",
        "openPrice": "102000",
        "unrealizedPL": "-700",
        "leverage": "10",
    }


@patch("agent_trade_monitor._call_haiku")
def test_critical_loss_returns_close_recommendation(mock_haiku):
    mock_haiku.return_value = MonitorResult(
        action="Close Now", action_reason="Position down -35%, bearish confluence",
        risk_rating=9, alert_level="critical",
        tp_recommendation={"price": "0", "rationale": ""},
        sl_recommendation={"price": "101000", "rationale": "Above entry"},
        key_risks=["High funding cost", "Bearish divergence"],
        summary="Close to prevent further loss",
        _symbol="BTCUSDT", _checked_at=0.0,
    )
    result = mon.run({"position": _position(), "original_prep": {},
                      "interpreted": _interp(), "sentiment": _sent()})
    assert result["risk_rating"] >= 7
    assert result["action"] in ("Close Now", "Partial Close")
    assert result["alert_level"] == "critical"
