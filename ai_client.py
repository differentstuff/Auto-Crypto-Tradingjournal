# ai_client.py
"""
Singleton Anthropic client with automatic token logging.
All AI modules import send() instead of constructing their own client.

Production cascade (when Anthropic fails):
  1. Groq Llama 4 Scout       (best |Δ|/sound, fast)
  2. Cerebras Qwen 3 235B     (lowest avg |Δ|, different family)
  3. Cerebras Llama 3.1 8B    (economy backup)
  4. OpenRouter DeepSeek V4   (slow but reliable)
  5. Gemini (internal cascade across 4 models)
Each step skipped if the provider's per-model rate-limit cooldown is active.
Order derived from docs/cascade_comparison.md (2026-05-19 clean run).
"""
import logging
from contextlib import contextmanager
from contextvars import ContextVar

import anthropic

from constants import ANTHROPIC_API_KEY
from helpers import log_token_usage

_log    = logging.getLogger(__name__)
_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ── Production cascade order ───────────────────────────────────────────────────
# (provider_name, model_for_provider)
# Walked sequentially when Anthropic raises APIError. Each entry is skipped if
# openai_compat_client.is_in_cooldown() reports an active rate-limit cooldown.
_PROVIDER_CASCADE: list[tuple[str, str | None]] = [
    ("groq",       "meta-llama/llama-4-scout-17b-16e-instruct"),
    ("cerebras",   "qwen-3-235b-a22b-instruct-2507"),
    ("cerebras",   "llama3.1-8b"),
    ("openrouter", "deepseek/deepseek-v4-flash:free"),
    ("gemini",     None),   # Gemini has its own internal model cascade
]

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
        _log.warning("Anthropic API error (%s) — walking provider cascade: %s",
                     type(exc).__name__, exc)

    # --- Production cascade: try ranked providers in order, skipping cooldowns ---
    last_err: Exception | None = None
    for prov, prov_model in _PROVIDER_CASCADE:
        if not _provider_available(prov, prov_model):
            continue
        try:
            return _call_via_provider(prov, module, prov_model or model,
                                      messages, max_tokens, system,
                                      fallback_tag=f"+{prov}")
        except Exception as e:
            last_err = e
            _log.warning("Cascade step %s failed, trying next: %s", prov, e)
            continue
    raise RuntimeError(
        f"All cascade providers exhausted for module={module}. Last error: {last_err}"
    )


def _provider_available(provider: str, model: str | None) -> bool:
    """Return False if the provider isn't configured OR is currently in cooldown."""
    try:
        if provider == "gemini":
            import gemini_client
            return gemini_client.is_configured()
        if provider == "grok":
            import grok_client
            return grok_client.is_configured()
        if provider in ("cerebras", "groq", "openrouter"):
            mod_map = {"cerebras": "cerebras_client",
                       "groq":     "groq_client",
                       "openrouter": "openrouter_client"}
            import importlib
            client = importlib.import_module(mod_map[provider])
            if not client.is_configured():
                return False
            # Check the cooldown registry for this specific (base_url, model)
            from openai_compat_client import is_in_cooldown
            base_attr_map = {"cerebras": "CEREBRAS_BASE",
                             "groq":     "GROQ_BASE",
                             "openrouter": "OPENROUTER_BASE"}
            base_url = getattr(client, base_attr_map[provider])
            target_model = model or getattr(client, "DEFAULT_MODEL", "")
            return not is_in_cooldown(base_url, target_model)
    except Exception:
        pass
    return False


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
