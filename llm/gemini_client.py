""" llm/gemini_client.py -- Google Gemini client for the LLM router.

Single-provider client: takes an API key and returns text or raises.
Uses urllib only — no extra SDK dependencies. The router owns the
KeyManager and decides which provider to call.

Contract:
  send(key, prompt, system, max_tokens, model) -> str
  Raises LLMClientError with .status_code on any HTTP error.
  The router catches these, calls km.report_error(), and tries fallback.

Port of: gemini_client.py (stripped of score_call/score_setup/cache/cascade logic)
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Optional

_log = logging.getLogger(__name__)

_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
_TIMEOUT = 20  # seconds

# Re-use the same error class from anthropic_client for a uniform contract.
# Each client module defines it so there's no circular import risk.
class LLMClientError(Exception):
    """Raised when an LLM provider call fails. Carries the HTTP status code."""

    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


def send(
    key: str,
    prompt: str,
    system: Optional[str] = None,
    max_tokens: int = 2048,
    model: str = "gemini-2.5-flash",
) -> str:
    """
    Make a single Gemini generateContent call.

    Args:
        key:        API key (from KeyManager, not from env).
        prompt:     User prompt text.
        system:     Optional system prompt (prepended to user prompt).
        max_tokens: Maximum output tokens.
        model:      Gemini model identifier (e.g. "gemini-2.5-flash").

    Returns:
        Response text string.

    Raises:
        LLMClientError with .status_code on any HTTP error.
    """
    full_prompt = f"{system}\n\n{prompt}" if system else prompt

    body = {
        "contents": [{"role": "user", "parts": [{"text": full_prompt}]}],
        "generationConfig": {
            "temperature": 0.15,
            "maxOutputTokens": max_tokens,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    payload = json.dumps(body).encode()

    url = f"{_GEMINI_BASE}/{model}:generateContent?key={key}"
    req = urllib.request.Request(url, data=payload)
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            resp = json.loads(r.read())

        candidates = resp.get("candidates") or [{}]
        cand = candidates[0]
        parts = (cand.get("content") or {}).get("parts") or []

        if not parts:
            finish = cand.get("finishReason", "unknown")
            raise LLMClientError(
                f"Gemini returned empty parts (finishReason={finish})",
                status_code=0,
            )

        return parts[0].get("text", "")

    except urllib.error.HTTPError as exc:
        status = exc.code
        if status in (429, 503):
            _log.warning("Gemini rate limit/server error (status %d) for model %s", status, model)
        elif status in (401, 403):
            _log.error("Gemini auth error (status %d) — key may be invalid", status)
        else:
            _log.warning("Gemini HTTP error (status %d): %s", status, exc)
        raise LLMClientError(f"Gemini HTTP error ({status}): {exc}", status_code=status) from exc

    except LLMClientError:
        raise  # re-raise our own errors

    except (urllib.error.URLError, KeyError, IndexError, json.JSONDecodeError) as exc:
        _log.warning("Gemini response error: %s: %s", type(exc).__name__, exc)
        raise LLMClientError(f"Gemini response error: {exc}", status_code=0) from exc

    except Exception as exc:
        _log.error("Unexpected Gemini error: %s", exc)
        raise LLMClientError(f"Unexpected error: {exc}", status_code=0) from exc