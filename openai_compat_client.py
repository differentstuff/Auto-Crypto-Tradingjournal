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
import re
import threading
import time
import urllib.request
import urllib.error
from typing import Optional, Iterable

_TIMEOUT = 30  # seconds (some free-tier providers are slow under load)

# ── Per-(provider,model) rate-limit cooldown ──────────────────────────────────
# When a model returns 429, we mark it cool down for the retry-delay window
# from the response (defaulting to 60s). is_in_cooldown() lets ai_client.py
# skip a model entirely instead of wasting a roundtrip when we know it'll fail.
_COOLDOWN: dict[str, float] = {}        # key → epoch expiry
_COOLDOWN_LOCK = threading.Lock()


def _cooldown_key(base_url: str, model: str) -> str:
    return f"{base_url}|{model}"


def is_in_cooldown(base_url: str, model: str) -> bool:
    """True if this (provider, model) is currently rate-limited."""
    key = _cooldown_key(base_url, model)
    with _COOLDOWN_LOCK:
        return _COOLDOWN.get(key, 0) > time.time()


def mark_cooldown(base_url: str, model: str, seconds: float) -> None:
    key = _cooldown_key(base_url, model)
    with _COOLDOWN_LOCK:
        _COOLDOWN[key] = time.time() + max(float(seconds), 5)


def cooldown_remaining(base_url: str, model: str) -> int:
    """Seconds left in cooldown, or 0 if not cooling down."""
    key = _cooldown_key(base_url, model)
    with _COOLDOWN_LOCK:
        return max(0, int(_COOLDOWN.get(key, 0) - time.time()))


_RETRY_DELAY_RE = re.compile(r"retry[- ]?(?:after|delay)['\":\s]+(\d+)", re.IGNORECASE)


def _parse_retry_seconds(body: str, default: int = 60) -> int:
    """Pull a retry-after hint from a 429 body. Default 60s if not found."""
    if not body:
        return default
    m = _RETRY_DELAY_RE.search(body)
    if m:
        try:
            return max(int(m.group(1)), 5)
        except ValueError:
            pass
    return default


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

    # Skip if this provider/model is currently rate-limited
    if is_in_cooldown(base_url, model):
        remaining = cooldown_remaining(base_url, model)
        print(f"[{_short(base_url)}] {model} in cooldown ({remaining}s left) — skip",
              flush=True)
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
    # Cerebras and Groq sit behind Cloudflare; default urllib UA
    # ("Python-urllib/3.x") gets HTTP 403 with code 1010 (ASN ban).
    # Identifying ourselves as a normal browser bypasses the heuristic.
    req.add_header("User-Agent",
                   "Mozilla/5.0 (X11; Linux aarch64) trading-journal/1.6")
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
            body = exc.read().decode()[:400] if hasattr(exc, "read") else ""
        except Exception:
            pass
        if exc.code == 429:
            retry = _parse_retry_seconds(body, default=60)
            mark_cooldown(base_url, model, retry)
            print(f"[{_short(base_url)}] 429 on {model} — cooldown {retry}s", flush=True)
        else:
            print(f"[{_short(base_url)}] HTTP {exc.code} on {model}: {body[:120]}",
                  flush=True)
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
