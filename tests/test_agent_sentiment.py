# tests/test_agent_sentiment.py
import pytest
from agent_types import CollectorResult, SentimentResult
import agent_market_sentiment as sent


def _collected(fg=55, long_pct=70, funding_rate=0.0005) -> CollectorResult:
    return CollectorResult(
        symbol="XRPUSDT", candles={}, fetched_at=0.0,
        open_interest={}, fred_macro={}, nansen={},
        grok={"text": "Bullish momentum building", "weight": 0.4},
        fear_greed={"value": fg, "classification": "Greed", "ok": True},
        long_short={"long_pct": long_pct, "short_pct": 100 - long_pct,
                    "bias": "crowded long" if long_pct > 65 else "balanced", "ok": True},
        funding_rate={"rate": funding_rate, "rate_pct": funding_rate * 100,
                      "direction": "longs paying" if funding_rate > 0 else "shorts paying",
                      "high": abs(funding_rate) >= 0.0005, "ok": True},
    )


def test_contra_signal_when_crowd_opposes_long():
    # 70% longs + high funding → crowd is against new Long entry
    result = sent.run({"symbol": "XRPUSDT", "direction": "Long", "collected": _collected()})
    assert result["contra_signal"] is True
    assert result["crowd_position"] == "majority_long"
    assert result["funding_bias"] == "longs_paying"
    assert len(result["key_factors"]) >= 1


def test_no_contra_signal_for_short_when_crowd_long():
    # Crowd is long → Short is contrarian (good for short) → no contra_signal
    result = sent.run({"symbol": "XRPUSDT", "direction": "Short", "collected": _collected()})
    assert result["contra_signal"] is False


def test_neutral_defaults_on_empty_data():
    empty = CollectorResult(
        symbol="XYZUSDT", candles={}, fetched_at=0.0,
        funding_rate={}, open_interest={}, long_short={},
        fear_greed={}, fred_macro={}, nansen={}, grok={},
    )
    result = sent.run({"symbol": "XYZUSDT", "direction": "Long", "collected": empty})
    assert result["macro_bias"] == "neutral"
    assert result["sentiment_score"] == 5.0
    assert result["contra_signal"] is False
