"""
openai_compat_client.py — Shared client base for OpenAI-compatible chat APIs.

Used by Cerebras, Groq, OpenRouter, DeepSeek, Mistral and any other provider
that exposes a `/v1/chat/completions` endpoint with the same request/response
shape as OpenAI's. Each provider gets a tiny wrapper module that pins:
  - the base URL
  - the API key env var
  - the default model
  - any provider-specific quirks (extra headers, response field names)

This module deliberately mirrors the gemini_client.send_text() interface so
ai_client.send() can route to any provider with no per-provider branching.

urllib only — no extra deps, matches gemini_client.
"""
import json
import os
import urllib.request
import urllib.error
from typing import Optional, Iterable

_TIMEOUT = 30  # seconds (some free-tier providers are slow under load)


def chat_completion(
    *,
    base_url:    str,
    api_key:     str,
    model:       str,
    prompt:      str,
    system:      Optional[str] = None,
    max_tokens:  int = 2048,
    temperature: float = 0.15,
    extra_headers: Optional[dict] = None,
) -> str | None:
    """
    Send a chat-completion request to any OpenAI-compatible endpoint.
    Returns the assistant message text, or None on any failure (logged).

    Designed to be called from per-provider wrappers — those pass the right
    base_url + api_key + model and we do the rest.
    """
    if not api_key:
        return None

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = json.dumps({
        "model":       model,
        "messages":    messages,
        "max_tokens":  max_tokens,
        "temperature": temperature,
    }).encode()

    url = f"{base_url.rstrip('/')}/chat/completions"
    req = urllib.request.Request(url, data=payload)
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type",  "application/json")
    if extra_headers:
        for k, v in extra_headers.items():
            req.add_header(k, v)

    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            resp = json.loads(r.read())
        choices = resp.get("choices") or []
        if not choices:
            print(f"[{_short(base_url)}] Empty choices on {model}", flush=True)
            return None
        msg = choices[0].get("message", {})
        text = msg.get("content") or ""
        if not text:
            # Some reasoning models (gpt-oss, glm, R1) return content in
            # reasoning_content while content stays empty
            text = msg.get("reasoning_content") or msg.get("reasoning") or ""
        if not text:
            finish = choices[0].get("finish_reason", "?")
            print(f"[{_short(base_url)}] No content on {model} (finish={finish})", flush=True)
            return None
        return text
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode()[:200] if hasattr(exc, "read") else ""
        except Exception:
            pass
        print(f"[{_short(base_url)}] HTTP {exc.code} on {model}: {body}", flush=True)
    except urllib.error.URLError as exc:
        print(f"[{_short(base_url)}] URL error on {model}: {exc}", flush=True)
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        print(f"[{_short(base_url)}] Parse error on {model}: {exc}", flush=True)
    except Exception as exc:
        print(f"[{_short(base_url)}] Unexpected error on {model}: {exc}", flush=True)
    return None


def _short(url: str) -> str:
    """Tag for log lines — e.g. 'api.cerebras.ai' → 'cerebras'."""
    try:
        host = url.split("//", 1)[1].split("/", 1)[0]
        return host.split(".")[1] if host.startswith("api.") else host.split(".")[0]
    except Exception:
        return "?"
