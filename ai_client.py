# ai_client.py
"""
Singleton Anthropic client with automatic token logging.
All AI modules import send() instead of constructing their own client.

Gemini fallback: if Anthropic raises ANY APIError (billing, auth, rate-limit,
overload), send() transparently retries through gemini_client.send_text().
Logged under module name with suffix '+gemini' so token_usage tracks it.
"""
import logging

import anthropic

from constants import ANTHROPIC_API_KEY
from helpers import log_token_usage

_log    = logging.getLogger(__name__)
_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


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
         system: str = None) -> tuple[str, int]:
    """
    Make one Anthropic messages.create call and log token usage.
    Falls back to Gemini on any Anthropic APIError.
    Returns: (response_text, cached_tokens)
    """
    kwargs = dict(model=model, max_tokens=max_tokens, messages=messages)
    if system:
        kwargs["system"] = system

    # --- Primary: Anthropic ---
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

    # --- Fallback: Gemini ---
    try:
        from gemini_client import send_text as _gemini_send
        prompt, sys_text = _messages_to_prompt(messages, system)
        text = _gemini_send(prompt, system=sys_text, max_tokens=max_tokens)
        if text is None:
            raise RuntimeError("Gemini fallback returned None")
        # Estimate token count from character length (rough: 4 chars ≈ 1 token)
        est_in  = len(prompt) // 4
        est_out = len(text)   // 4
        log_token_usage(f"{module}+gemini", "gemini-fallback", est_in, est_out, 0)
        return text, 0

    except Exception as fallback_exc:
        _log.error("Gemini fallback also failed: %s", fallback_exc)
        raise RuntimeError(
            f"Both Anthropic and Gemini failed for module={module}. "
            f"Gemini error: {fallback_exc}"
        ) from fallback_exc
