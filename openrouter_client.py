"""openrouter_client.py — OpenRouter aggregator (free + paid routes).

OpenRouter proxies many providers behind a single OpenAI-compatible API.
We use the :free routed models (DeepSeek V4, NVIDIA Nemotron, Llama, Qwen)
to extend the cascade headroom at zero cost.

Quirk: OpenRouter requires HTTP-Referer + X-Title headers on requests
to be eligible for the free pool. We send identifying headers for the
trading journal so OpenRouter's usage dashboard groups our calls.
"""
import os
from openai_compat_client import chat_completion

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE    = "https://openrouter.ai/api/v1"
DEFAULT_MODEL      = "deepseek/deepseek-v4-flash:free"

_EXTRA_HEADERS = {
    "HTTP-Referer": "https://github.com/anvilfilbert/Auto-Crypto-Tradingjournal",
    "X-Title":      "Trading Journal",
}


def is_configured() -> bool:
    return bool(OPENROUTER_API_KEY)


def send_text(prompt: str, system: str = None,
              max_tokens: int = 2048, model: str = None) -> str | None:
    return chat_completion(
        base_url=OPENROUTER_BASE,
        api_key=OPENROUTER_API_KEY,
        model=model or DEFAULT_MODEL,
        prompt=prompt,
        system=system,
        max_tokens=max_tokens,
        extra_headers=_EXTRA_HEADERS,
    )
