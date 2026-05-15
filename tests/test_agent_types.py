"""Tests for agent_types.py additions — ScannerSetup + factory helpers."""


def test_scanner_setup_is_dict_subclass():
    from agent_types import ScannerSetup
    s: ScannerSetup = {"symbol": "BTCUSDT", "setup_score": 7, "direction": "Long"}
    assert s["symbol"] == "BTCUSDT"


def test_empty_interpreter_has_required_fields():
    from agent_types import empty_interpreter
    r = empty_interpreter("BTCUSDT")
    assert r["symbol"] == "BTCUSDT"
    assert r["trend_direction"] == "neutral"
    assert isinstance(r["sr_levels"], list)
    assert "score" in r["confluence_score"]


def test_empty_sentiment_has_required_fields():
    from agent_types import empty_sentiment
    r = empty_sentiment()
    assert r["macro_bias"] == "neutral"
    assert r["contra_signal"] is False
    assert isinstance(r["key_factors"], list)


def test_empty_interpreter_default_symbol():
    from agent_types import empty_interpreter
    r = empty_interpreter()
    assert r["symbol"] == ""
