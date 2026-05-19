"""cerebras_client.py — Cerebras Cloud LPU inference (free tier).

Hosts: Qwen 3 235B, Llama 3.1 8B, GPT-OSS 120B, GLM 4.7, others.
Free tier: 14,400 RPD on most models, 30 RPM. OpenAI-compatible API.
"""
import os
from openai_compat_client import chat_completion

CEREBRAS_API_KEY = os.environ.get("CEREBRAS_API_KEY", "")
CEREBRAS_BASE    = "https://api.cerebras.ai/v1"
DEFAULT_MODEL    = "qwen-3-235b-a22b-instruct-2507"


def is_configured() -> bool:
    return bool(CEREBRAS_API_KEY)


def send_text(prompt: str, system: str = None,
              max_tokens: int = 2048, model: str = None) -> str | None:
    return chat_completion(
        base_url=CEREBRAS_BASE,
        api_key=CEREBRAS_API_KEY,
        model=model or DEFAULT_MODEL,
        prompt=prompt,
        system=system,
        max_tokens=max_tokens,
    )
