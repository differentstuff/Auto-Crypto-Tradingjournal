""" llm/anthropic_client.py -- Anthropic SDK client for the LLM router.

Single-provider client: takes an API key and returns text or raises.
No global state, no singleton, no cascade logic. The router owns
the KeyManager and decides which provider to call.

Contract:
  send(key, prompt, system, max_tokens, model, **params) -> str
  Raises LLMClientError with .status_code on any HTTP error.
  The router catches these, calls km.report_error(), and tries fallback.

Port of: ai_client.py (stripped of cascade/fallback/global-state logic)
"""

from __future__ import annotations

import logging
from typing import Optional

_log = logging.getLogger(__name__)

# Effort → budget_tokens mapping for Anthropic extended thinking
_EFFORT_TO_BUDGET = {
    "none": 0,       # Disable thinking entirely
    "minimal": 1024,
    "low": 2048,
    "medium": 4096,
    "high": 8192,
    "xhigh": 16384,
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
    max_tokens: int = 1024,
    model: str = "claude-sonnet-4-6",
    reasoning: Optional[dict] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    response_format: Optional[str] = None,
    seed: Optional[int] = None,
    transforms: Optional[list] = None,
    provider_order: Optional[list] = None,
    base_url: Optional[str] = None,
) -> str:
    """
    Make a single Anthropic chat-completion call.

    Args:
        key:            API key (from KeyManager, not from env).
        prompt:         User prompt text.
        system:         Optional system prompt.
        max_tokens:     Maximum output tokens.
        model:          Model identifier (e.g. "claude-sonnet-4-6").
        reasoning:      Reasoning control dict, e.g. {"effort": "none"} or {"effort": "high"}.
                        Maps to Anthropic's "thinking" parameter.
        temperature:    Sampling temperature (0.0 = deterministic).
        top_p:          Nucleus sampling parameter.
        response_format: "json" for prompt enforcement (Anthropic has no native JSON mode).
        seed:           Not supported by Anthropic API — ignored.
        transforms:     Not applicable to Anthropic — ignored.
        provider_order: Not applicable to Anthropic — ignored.
        base_url:       Override base URL (from env, injected by config_loader).

    Returns:
        Response text string.

    Raises:
        LLMClientError with .status_code on any API error.
    """
    import anthropic

    client_kwargs = {"api_key": key}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = anthropic.Anthropic(**client_kwargs)

    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system

    # Apply temperature and top_p
    if temperature is not None:
        kwargs["temperature"] = temperature
    if top_p is not None:
        kwargs["top_p"] = top_p

    # Apply reasoning → thinking parameter
    if reasoning:
        effort = reasoning.get("effort", "medium")
        budget = _EFFORT_TO_BUDGET.get(effort, 4096)
        if budget > 0:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}
            # Anthropic requires max_tokens > budget_tokens
            if max_tokens <= budget:
                kwargs["max_tokens"] = budget + max_tokens

    # Note: Anthropic doesn't natively support response_format.
    # JSON enforcement is handled by prompt instructions + response_parser validation.
    # seed is not supported by Anthropic API.

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