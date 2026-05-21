""" llm/anthropic_client.py -- Anthropic SDK client for the LLM router.

Single-provider client: takes an API key and returns text or raises.
No global state, no singleton, no cascade logic. The router owns
the KeyManager and decides which provider to call.

Contract:
  send(key, prompt, system, max_tokens, model) -> str
  Raises LLMClientError with .status_code on any HTTP error.
  The router catches these, calls km.report_error(), and tries fallback.

Port of: ai_client.py (stripped of cascade/fallback/global-state logic)
"""

from __future__ import annotations

import logging
from typing import Optional

_log = logging.getLogger(__name__)


class LLMClientError(Exception):
    """Raised when an LLM provider call fails. Carries the HTTP status code."""

    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


def send(
    key: str,
    prompt: str,
    system: Optional[str] = None,
    max_tokens: int = 1024,
    model: str = "claude-sonnet-4-6",
) -> str:
    """
    Make a single Anthropic chat-completion call.

    Args:
        key:        API key (from KeyManager, not from env).
        prompt:     User prompt text.
        system:     Optional system prompt.
        max_tokens: Maximum output tokens.
        model:      Model identifier (e.g. "claude-sonnet-4-6").

    Returns:
        Response text string.

    Raises:
        LLMClientError with .status_code on any API error.
    """
    import anthropic

    client = anthropic.Anthropic(api_key=key)

    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system

    try:
        message = client.messages.create(**kwargs)
        text = message.content[0].text

        # Log token usage if available
        if hasattr(message, "usage"):
            _log.debug(
                "Anthropic call: model=%s input=%d output=%d",
                model,
                message.usage.input_tokens,
                message.usage.output_tokens,
            )

        return text

    except anthropic.RateLimitError as exc:
        status = getattr(exc, "status_code", 429) or 429
        _log.warning("Anthropic rate limit (status %d) for model %s", status, model)
        raise LLMClientError(f"Anthropic rate limit: {exc}", status_code=status) from exc

    except anthropic.AuthenticationError as exc:
        status = getattr(exc, "status_code", 401) or 401
        _log.error("Anthropic auth error (status %d) — key may be invalid", status)
        raise LLMClientError(f"Anthropic auth error: {exc}", status_code=status) from exc

    except anthropic.InternalServerError as exc:
        status = getattr(exc, "status_code", 500) or 500
        _log.warning("Anthropic server error (status %d)", status)
        raise LLMClientError(f"Anthropic server error: {exc}", status_code=status) from exc

    except anthropic.APIError as exc:
        status = getattr(exc, "status_code", 0) or 0
        _log.warning("Anthropic API error (status %d): %s", status, exc)
        raise LLMClientError(f"Anthropic API error: {exc}", status_code=status) from exc

    except Exception as exc:
        _log.error("Unexpected Anthropic error: %s", exc)
        raise LLMClientError(f"Unexpected error: {exc}", status_code=0) from exc