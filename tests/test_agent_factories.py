"""Tests for agent_types factory helpers."""


def test_empty_interpreter_uses_weak_bias():
    """empty_interpreter must use momentum_bias='weak' not 'conflicted'."""
    from agent_types import empty_interpreter
    r = empty_interpreter("BTCUSDT")
    assert r["momentum_bias"] == "weak"


def test_empty_sentiment_importable():
    """empty_sentiment is callable and returns expected shape."""
    from agent_types import empty_sentiment
    r = empty_sentiment()
    assert isinstance(r, dict)


def test_empty_reviewer_importable():
    """empty_reviewer is importable from agent_types."""
    from agent_types import empty_reviewer
    r = empty_reviewer()
    assert "signal_quality" in r
    assert isinstance(r["signal_quality"], float)


def test_orchestrator_no_private_fallbacks():
    """agent_orchestrator must not define _empty_interp or _empty_sent."""
    import agent_orchestrator
    assert not hasattr(agent_orchestrator, '_empty_interp'), \
        "_empty_interp should have been deleted from orchestrator"
    assert not hasattr(agent_orchestrator, '_empty_sent'), \
        "_empty_sent should have been deleted from orchestrator"
