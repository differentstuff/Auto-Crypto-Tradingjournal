# ai_client.py
"""
Singleton Anthropic client with automatic token logging.
All AI modules import send() instead of constructing their own client.
"""
import anthropic
from constants import ANTHROPIC_API_KEY
from helpers import log_token_usage

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def send(module: str, model: str, messages: list, max_tokens: int,
         system: str = None) -> tuple[str, int]:
    """
    Make one Anthropic messages.create call and log token usage.
    Returns: (response_text, cached_tokens)
    Raises: anthropic.APIError — callers handle retry/fallback themselves.
    """
    kwargs = dict(model=model, max_tokens=max_tokens, messages=messages)
    if system:
        kwargs["system"] = system

    message = _client.messages.create(**kwargs)
    text = message.content[0].text
    cached = getattr(message.usage, "cache_read_input_tokens", 0) or 0

    log_token_usage(module, model,
                    message.usage.input_tokens,
                    message.usage.output_tokens,
                    cached)

    return text, cached
