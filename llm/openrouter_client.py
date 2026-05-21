""" llm/openrouter_client.py -- OpenRouter client for the LLM router.

OpenRouter proxies many providers behind a single OpenAI-compatible API.
We use the :free routed models to extend capacity at zero cost.

Quirk: OpenRouter requires HTTP-Referer + X-Title headers on requests
to be eligible for the free pool. We send identifying headers so
OpenRouter's usage dashboard groups our calls correctly.

Single-provider client: takes an API key and returns text or raises.
The router owns the KeyManager and decides which provider to call.

Contract:
  send(key, prompt, system, max_tokens, model) -> str
  Raises LLMClientError with .status_code on any HTTP error.
  The router catches these, calls km.report_error(), and tries fallback.

Port of: openrouter_client.py (moved to llm/ package, same logic)
"""

from __future__ import annotations

import logging
from typing import Optional

_log = logging.getLogger(__name__)

_OPENROUTER_BASE = "https://openrouter.ai/api/v1"

# Required headers for free-tier eligibility on OpenRouter
_DEFAULT_HEADERS = {
    "HTTP-Referer": "https://github.com/anvilfilbert/Auto-Crypto-Tradingjournal",
    "X-Title": "Trading Journal",
}


class LLMClientError(Exception):
    """Raised when an LLM provider call fails. Carries the HTTP status code."""

    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


def send(
    key: str,
    prompt: str,
    system: Optional[str] = None,
    max_tokens: int = 2048,
    model: str = "deepseek/deepseek-v4-0324:free",
) -> str:
    """
    Make a single OpenRouter chat-completion call via OpenAI-compatible API.

    Args:
        key:        API key (from KeyManager, not from env).
        prompt:     User prompt text.
        system:     Optional system prompt.
        max_tokens: Maximum output tokens.
        model:      OpenRouter model identifier (e.g. "deepseek/deepseek-v4-0324:free").

    Returns:
        Response text string.

    Raises:
        LLMClientError with .status_code on any HTTP error.
    """
    import openai

    client = openai.OpenAI(
        base_url=_OPENROUTER_BASE,
        api_key=key,
        default_headers=_DEFAULT_HEADERS,
    )

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
        )
        text = response.choices[0].message.content

        if text is None:
            raise LLMClientError(
                f"OpenRouter returned None content for model {model}",
                status_code=0,
            )

        _log.debug("OpenRouter call: model=%s", model)
        return text

    except openai.RateLimitError as exc:
        status = getattr(exc, "status_code", 429) or 429
        _log.warning("OpenRouter rate limit (status %d) for model %s", status, model)
        raise LLMClientError(f"OpenRouter rate limit: {exc}", status_code=status) from exc

    except openai.AuthenticationError as exc:
        status = getattr(exc, "status_code", 401) or 401
        _log.error("OpenRouter auth error (status %d) — key may be invalid", status)
        raise LLMClientError(f"OpenRouter auth error: {exc}", status_code=status) from exc

    except openai.APIError as exc:
        status = getattr(exc, "status_code", 0) or 0
        _log.warning("OpenRouter API error (status %d): %s", status, exc)
        raise LLMClientError(f"OpenRouter API error: {exc}", status_code=status) from exc

    except LLMClientError:
        raise  # re-raise our own errors

    except Exception as exc:
        _log.error("Unexpected OpenRouter error: %s", exc)
        raise LLMClientError(f"Unexpected error: {exc}", status_code=0) from exc