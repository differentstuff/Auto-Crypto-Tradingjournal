# tests/test_ai_client_fallback.py
"""Tests for Gemini fallback in ai_client.send()"""
import pytest
from unittest.mock import patch, MagicMock
import anthropic


def _make_anthropic_response(text="Claude response"):
    msg = MagicMock()
    msg.content = [MagicMock(text=text)]
    msg.usage.input_tokens = 100
    msg.usage.output_tokens = 50
    msg.usage.cache_read_input_tokens = 0
    return msg


def test_send_succeeds_with_anthropic(monkeypatch):
    """Normal path: Anthropic succeeds, returns text."""
    import ai_client
    monkeypatch.setattr(
        ai_client._client, "messages",
        MagicMock(create=MagicMock(return_value=_make_anthropic_response("hello")))
    )
    text, cached = ai_client.send("test", "claude-haiku-4-5-20251001",
                                   [{"role": "user", "content": "hi"}], 100)
    assert text == "hello"
    assert cached == 0


def test_send_falls_back_to_gemini_on_api_error(monkeypatch):
    """Anthropic raises APIError → Gemini fallback called → returns Gemini text."""
    import ai_client
    monkeypatch.setattr(
        ai_client._client, "messages",
        MagicMock(create=MagicMock(side_effect=anthropic.APIError.__new__(anthropic.APIError)))
    )
    import gemini_client
    monkeypatch.setattr(gemini_client, "send_text",
                        MagicMock(return_value="gemini fallback text"))
    text, cached = ai_client.send("test", "claude-haiku-4-5-20251001",
                                   [{"role": "user", "content": "hi"}], 100)
    assert text == "gemini fallback text"
    assert cached == 0


def test_send_raises_if_both_fail(monkeypatch):
    """Both Anthropic and Gemini fail → RuntimeError raised."""
    import ai_client
    monkeypatch.setattr(
        ai_client._client, "messages",
        MagicMock(create=MagicMock(side_effect=anthropic.APIError.__new__(anthropic.APIError)))
    )
    import gemini_client
    monkeypatch.setattr(gemini_client, "send_text",
                        MagicMock(return_value=None))
    with pytest.raises(RuntimeError, match="Both Anthropic and Gemini failed"):
        ai_client.send("test", "claude-haiku-4-5-20251001",
                        [{"role": "user", "content": "hi"}], 100)


def test_messages_to_prompt_with_system():
    """Converts messages list + system to flat prompt strings."""
    import ai_client
    messages = [
        {"role": "user", "content": "analyze this trade"},
        {"role": "assistant", "content": "sure, what setup?"},
        {"role": "user", "content": "BTCUSDT long"},
    ]
    prompt, sys = ai_client._messages_to_prompt(messages, "You are a trader")
    assert "[USER]" in prompt
    assert "[ASSISTANT]" in prompt
    assert "BTCUSDT long" in prompt
    assert sys == "You are a trader"


def test_messages_to_prompt_cached_format():
    """Handles list-of-blocks content format (prompt caching)."""
    import ai_client
    messages = [
        {"role": "user", "content": [
            {"type": "text", "text": "here is the context"},
            {"type": "text", "text": "now score this"},
        ]}
    ]
    prompt, _ = ai_client._messages_to_prompt(messages, None)
    assert "here is the context" in prompt
    assert "now score this" in prompt
