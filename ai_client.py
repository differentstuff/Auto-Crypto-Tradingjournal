# ai_client.py
"""
Singleton Anthropic client with automatic token logging.
All AI modules import send() instead of constructing their own client.

Gemini fallback: if Anthropic raises ANY APIError (billing, auth, rate-limit,
overload), send() transparently retries through gemini_client.send_text().
Logged under module name with suffix '+gemini' so token_usage tracks it.
"""
import logging
from contextlib import contextmanager
from contextvars import ContextVar

import anthropic

from constants import ANTHROPIC_API_KEY
from helpers import log_token_usage

_log    = logging.getLogger(__name__)
_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ── Provider override (for cascade testing) ────────────────────────────────────
# When set, every send() call routes through the named provider/model regardless
# of what the caller specified. Used by compare_cascades.py to force a whole
# multi-stage pipeline through one backend so we can compare outputs.
_forced_provider: ContextVar[str | None] = ContextVar("ai_forced_provider", default=None)
_forced_model:    ContextVar[str | None] = ContextVar("ai_forced_model",    default=None)


@contextmanager
def force_provider(provider: str | None, model: str | None = None):
    """
    Within this block, every ai_client.send() call routes through `provider`
    (anthropic|gemini|grok|cerebras|groq|openrouter) using `model`. Nested
    contexts stack/unstack cleanly via contextvars.

    Example:
        with force_provider("cerebras", "qwen-3-235b-a22b-instruct-2507"):
            result = run_call_analysis(...)
    """
    p_tok = _forced_provider.set(provider)
    m_tok = _forced_model.set(model)
    try:
        yield
    finally:
        _forced_provider.reset(p_tok)
        _forced_model.reset(m_tok)


def _messages_to_prompt(messages: list, system: str | None) -> tuple[str, str | None]:
    """
    Convert Anthropic messages list to a single prompt string for Gemini.
    Interleaves role labels so Gemini understands the conversation structure.
    Returns (prompt_text, system_text).
    """
    parts = []
    for m in messages:
        role    = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, list):
            # cached-message format: list of {type, text} blocks
            text = "\n".join(
                b.get("text", "") for b in content if isinstance(b, dict)
            )
        else:
            text = str(content)
        if text.strip():
            parts.append(f"[{role.upper()}]\n{text}")
    return "\n\n".join(parts), system


def send(module: str, model: str, messages: list, max_tokens: int,
         system: str = None, provider: str = None) -> tuple[str, int]:
    """
    Make one chat-completion call and log token usage.

    Default behavior (provider=None): Anthropic primary → Gemini fallback.

    Forced-provider mode (provider="anthropic"|"gemini"|"grok"|"cerebras"
    |"groq"|"openrouter"): bypass the Anthropic primary entirely and route
    the call through the named backend. Used by compare_cascades.py to test
    each provider in isolation. The `model` arg is the provider's specific
    model ID (e.g. "qwen-3-235b-a22b-instruct-2507" for Cerebras).

    Returns: (response_text, cached_tokens)
    """
    # Contextvar override beats explicit arg only when the explicit arg is None
    if provider is None:
        provider = _forced_provider.get()
        if provider:
            model = _forced_model.get() or model

    # --- Forced-provider mode (cascade testing) ---
    if provider and provider != "anthropic":
        return _call_via_provider(provider, module, model, messages, max_tokens, system)

    # --- Normal mode: Anthropic primary with Gemini fallback ---
    kwargs = dict(model=model, max_tokens=max_tokens, messages=messages)
    if system:
        kwargs["system"] = system

    try:
        message = _client.messages.create(**kwargs)
        text    = message.content[0].text
        cached  = getattr(message.usage, "cache_read_input_tokens", 0) or 0
        log_token_usage(module, model,
                        message.usage.input_tokens,
                        message.usage.output_tokens,
                        cached)
        return text, cached

    except anthropic.APIError as exc:
        _log.warning("Anthropic API error (%s) — falling back to Gemini: %s",
                     type(exc).__name__, exc)

    # --- Default fallback: Gemini ---
    return _call_via_provider("gemini", module, model, messages, max_tokens, system,
                              fallback_tag="+gemini")


def _call_via_provider(provider: str, module: str, model: str,
                        messages: list, max_tokens: int, system: str | None,
                        fallback_tag: str = "") -> tuple[str, int]:
    """
    Route a single call through a named non-Anthropic provider.
    Used both by Gemini fallback path and by forced-provider cascade tests.
    """
    prompt, sys_text = _messages_to_prompt(messages, system)
    try:
        if provider == "gemini":
            from gemini_client import send_text as _send
        elif provider == "grok":
            from grok_client import send_text as _send
        elif provider == "cerebras":
            from cerebras_client import send_text as _send
        elif provider == "groq":
            from groq_client import send_text as _send
        elif provider == "openrouter":
            from openrouter_client import send_text as _send
        else:
            raise ValueError(f"Unknown provider: {provider}")

        # For non-default providers, `model` is passed through verbatim
        # (e.g. "qwen-3-235b-a22b-instruct-2507"); for Gemini fallback,
        # `model` is the Anthropic model name and gets ignored (Gemini
        # picks its own from GEMINI_MODEL env).
        text = _send(prompt, system=sys_text, max_tokens=max_tokens,
                     model=(model if provider != "gemini" else None))
        if text is None:
            raise RuntimeError(f"{provider} returned None")

        # Char-based token estimation (4 chars ≈ 1 token)
        est_in  = len(prompt) // 4
        est_out = len(text)   // 4
        # For forced-provider mode, log with provider name; for fallback,
        # append +gemini tag so existing dashboards still recognise it.
        tag = fallback_tag if fallback_tag else f"+{provider}"
        log_token_usage(f"{module}{tag}", f"{provider}-{model}"[:60], est_in, est_out, 0)
        return text, 0

    except Exception as exc:
        _log.error("%s call failed: %s", provider, exc)
        raise RuntimeError(
            f"Provider={provider} failed for module={module}: {exc}"
        ) from exc
