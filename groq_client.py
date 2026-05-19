"""groq_client.py — Groq Cloud LPU inference (free tier).

NOT the same as grok_client.py (which talks to X.AI's Grok models).
Groq is the LPU-inference startup; hosts Llama 3.3 70B, Llama 4 Scout,
GPT-OSS 120B, etc. OpenAI-compatible API.

Free tier: ~14,400 RPD per model, 30 RPM.
"""
import os
from openai_compat_client import chat_completion

GROQ_API_KEY  = os.environ.get("GROQ_API_KEY", "")
GROQ_BASE     = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "llama-3.3-70b-versatile"


def is_configured() -> bool:
    return bool(GROQ_API_KEY)


def send_text(prompt: str, system: str = None,
              max_tokens: int = 2048, model: str = None) -> str | None:
    return chat_completion(
        base_url=GROQ_BASE,
        api_key=GROQ_API_KEY,
        model=model or DEFAULT_MODEL,
        prompt=prompt,
        system=system,
        max_tokens=max_tokens,
    )
