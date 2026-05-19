"""
gemini_client.py — Google Gemini API client for independent trade scoring.

Role in the multi-agent framework:
  - Pre-proof validator: independently scores a trade call BEFORE Claude's
    full analysis, using only the raw call text (no rulebook, no chart context).
  - Setup ranker: independently rates scanner finalists for consensus scoring.
  - Cross-validation source: divergence from Claude score = actionable signal.

Key design choices:
  - Lean prompts (200-400 tokens in): Gemini sees only call text + symbol +
    direction. This is intentional — two assessors with DIFFERENT information
    sets produce more meaningful divergence than two copies of the same prompt.
  - responseMimeType: application/json forces structured output.
  - urllib only — no extra dependencies.
  - 30-minute cache per (symbol, direction) — aligned to scanner cycle.

Config: GEMINI_API_KEY in .env. GEMINI_MODEL overrides default model.
"""

import json
import os
import threading
import time
import urllib.request
import urllib.error
from typing import Optional

from constants import GEMINI_FAST_MODEL, GEMINI_CACHE_TTL

GEMINI_API_KEY   = os.environ.get("GEMINI_API_KEY", "")
GEMINI_BASE      = "https://generativelanguage.googleapis.com/v1beta/models"
_ACTIVE_MODEL    = os.environ.get("GEMINI_MODEL", GEMINI_FAST_MODEL)
_TIMEOUT         = 20  # seconds

# ── Model cascade ──────────────────────────────────────────────────────────────
# Each Gemini model has its own per-project free-tier daily + per-minute quota.
# When the primary model 429s, fall through to the next available bucket.
# Order = preferred first; less-preferred models still produce usable output.
_FALLBACK_MODELS = [
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash",
]
# Build cascade: primary first, then fallbacks (deduped, order preserved)
_CASCADE: list[str] = []
for _m in [_ACTIVE_MODEL, *_FALLBACK_MODELS]:
    if _m and _m not in _CASCADE:
        _CASCADE.append(_m)

# Track when each model is next allowed to be tried (epoch sec).
# When a model 429s, we mark it cool-down for the duration Google's retryDelay
# indicates (or 60s default). Subsequent calls skip the model until it expires.
_COOLDOWN: dict[str, float] = {}
_COOLDOWN_LOCK = threading.Lock()


def _next_available_model() -> str | None:
    """Return the first cascade model that's not in active cooldown."""
    now = time.time()
    with _COOLDOWN_LOCK:
        for m in _CASCADE:
            if _COOLDOWN.get(m, 0) <= now:
                return m
    return None


def _mark_cooldown(model: str, seconds: float) -> None:
    with _COOLDOWN_LOCK:
        _COOLDOWN[model] = time.time() + max(seconds, 5)


# ── Caches ─────────────────────────────────────────────────────────────────────

_score_cache: dict = {}   # key → (ts, result)
_cache_lock        = threading.Lock()


def is_configured() -> bool:
    return bool(GEMINI_API_KEY)


# ── Raw API ────────────────────────────────────────────────────────────────────

def _call(prompt: str, model: str = None, max_tokens: int = 256,
          temperature: float = 0.15) -> dict | None:
    """
    POST to Gemini generateContent. Returns parsed JSON dict or None on failure.
    Forces JSON output via responseMimeType. Cascades through fallback models
    if the preferred one is rate-limited (per-model daily quota is independent).
    """
    if not GEMINI_API_KEY:
        return None

    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature":       temperature,
            "maxOutputTokens":   max_tokens,
            "responseMimeType":  "application/json",
            "thinkingConfig":    {"thinkingBudget": 0},
        },
    }
    payload = json.dumps(body).encode()

    # If caller pinned a model, only try that one. Else use the cascade.
    candidates = [model] if model else list(_CASCADE)
    for mdl in candidates:
        if not mdl:
            continue
        if not model and _COOLDOWN.get(mdl, 0) > time.time():
            continue  # skip exhausted bucket
        url = f"{GEMINI_BASE}/{mdl}:generateContent?key={GEMINI_API_KEY}"
        req = urllib.request.Request(url, data=payload)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
                resp = json.loads(r.read())
            text = resp["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                retry = _parse_retry_seconds(exc) or 60
                _mark_cooldown(mdl, retry)
                print(f"[Gemini] 429 on {mdl} — cooldown {retry}s, trying next model", flush=True)
                continue
            print(f"[Gemini] HTTP error ({mdl}): {exc}", flush=True)
            return None
        except (urllib.error.URLError, KeyError, IndexError, json.JSONDecodeError) as exc:
            print(f"[Gemini] {type(exc).__name__} ({mdl}): {exc}", flush=True)
            return None
        except Exception as exc:
            print(f"[Gemini] Unexpected error ({mdl}): {exc}", flush=True)
            return None
    print("[Gemini] All cascade models exhausted (cooldown)", flush=True)
    return None


def _parse_retry_seconds(http_err) -> int:
    """Extract retryDelay seconds from a Gemini 429 error body. None if absent."""
    try:
        body = http_err.read().decode() if hasattr(http_err, "read") else ""
        data = json.loads(body or "{}")
        for d in data.get("error", {}).get("details", []):
            rd = d.get("retryDelay")
            if rd:
                # e.g. "56s" → 56
                return int(str(rd).rstrip("s") or 0)
    except Exception:
        pass
    return None


def send_text(prompt: str, system: str = None,
              max_tokens: int = 2048, model: str = None) -> str | None:
    """
    General-purpose plain-text Gemini call. Used as Anthropic fallback.
    Cascades through fallback models when the primary is rate-limited.
    Returns text string or None on failure.
    """
    if not GEMINI_API_KEY:
        return None
    full_prompt = f"{system}\n\n{prompt}" if system else prompt
    body = {
        "contents": [{"role": "user", "parts": [{"text": full_prompt}]}],
        "generationConfig": {
            "temperature":     0.15,
            "maxOutputTokens": max_tokens,
            "thinkingConfig":  {"thinkingBudget": 0},
        },
    }
    payload = json.dumps(body).encode()

    candidates = [model] if model else list(_CASCADE)
    for mdl in candidates:
        if not mdl:
            continue
        if not model and _COOLDOWN.get(mdl, 0) > time.time():
            continue
        url = f"{GEMINI_BASE}/{mdl}:generateContent?key={GEMINI_API_KEY}"
        req = urllib.request.Request(url, data=payload)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
                resp = json.loads(r.read())
            cand  = (resp.get("candidates") or [{}])[0]
            parts = (cand.get("content") or {}).get("parts") or []
            if not parts:
                finish = cand.get("finishReason", "?")
                print(f"[Gemini fallback] Empty response on {mdl} (finishReason={finish})", flush=True)
                continue  # try next model — empty parts likely thinking-exhaustion
            return parts[0].get("text")
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                retry = _parse_retry_seconds(exc) or 60
                _mark_cooldown(mdl, retry)
                print(f"[Gemini fallback] 429 on {mdl} — cooldown {retry}s, trying next model", flush=True)
                continue
            print(f"[Gemini fallback] HTTP error ({mdl}): {exc}", flush=True)
            return None
        except (urllib.error.URLError, KeyError, IndexError, json.JSONDecodeError) as exc:
            print(f"[Gemini fallback] {type(exc).__name__} ({mdl}): {exc}", flush=True)
            return None
        except Exception as exc:
            print(f"[Gemini fallback] Unexpected error ({mdl}): {exc}", flush=True)
            return None
    print("[Gemini fallback] All cascade models exhausted (cooldown)", flush=True)
    return None


# ── Trade call pre-proof ───────────────────────────────────────────────────────

def score_call(call_text: str, symbol: str, direction: str) -> dict | None:
    """
    Independent pre-proof scoring of a trade call.

    Gemini receives ONLY the raw call text — no rulebook, no chart context,
    no calibration data. This deliberate information asymmetry means any
    agreement with Claude's full-context score is a strong confirmation signal.

    Returns {"score": 1-10, "label": str, "reasoning": str, "concerns": [...]}
    or None on failure.
    """
    if not GEMINI_API_KEY:
        return None

    cache_key = f"call_{symbol}_{direction}_{hash(call_text[:200])}"
    with _cache_lock:
        entry = _score_cache.get(cache_key)
        if entry and (time.time() - entry[0]) < GEMINI_CACHE_TTL:
            return entry[1]

    prompt = f"""Independent crypto futures trade call evaluation.

Symbol: {symbol} | Direction: {direction}

CALL TEXT:
{call_text[:800]}

Score this setup 1-10 using these criteria:
- Structural anchor: is entry near a named S/R level, EMA, or trendline?
- SL placement: is the stop outside typical noise range?
- R:R ratio: is potential reward ≥ 1.5× the risk?
- Setup completeness: are entry, SL, and TP clearly defined?

Return JSON only:
{{"score": <1-10>, "label": "<Poor|Weak|Moderate|Good|Strong|Excellent>", "reasoning": "<one sentence: key positive factor>", "concerns": ["<concern if any>"]}}"""

    result = _call(prompt, max_tokens=200, temperature=0.1)
    if result and isinstance(result.get("score"), (int, float)):
        result["score"] = int(max(1, min(10, result["score"])))
        with _cache_lock:
            _score_cache[cache_key] = (time.time(), result)
    return result


# ── Setup scanner ranking ─────────────────────────────────────────────────────

def score_setup(symbol: str, direction: str, indicators_compact: str,
                key_conditions: list = None) -> dict | None:
    """
    Independent setup scoring for scanner finalists.

    Uses a compressed indicator summary (the same compact format from
    format_for_prompt) rather than raw chart data — minimises Gemini tokens.

    Returns {"score": 0-10, "label": str, "key_factor": str} or None.
    """
    if not GEMINI_API_KEY:
        return None

    cache_key = f"setup_{symbol}_{direction}_{hash(indicators_compact[:150])}"
    with _cache_lock:
        entry = _score_cache.get(cache_key)
        if entry and (time.time() - entry[0]) < GEMINI_CACHE_TTL:
            return entry[1]

    cond_text = ""
    if key_conditions:
        cond_text = "\nKey conditions: " + "; ".join(key_conditions[:3])

    prompt = f"""Rate this crypto futures setup 1-10.

{symbol} {direction.upper()}
Indicators: {indicators_compact}{cond_text}

Scoring: 1-5=weak setup, 6=acceptable R:R≥1.5, 7=good R:R≥2, 8=strong R:R≥2.5, 9-10=excellent multi-TF+high R:R.

Return JSON only:
{{"score": <0-10>, "label": "<Poor|Weak|Moderate|Good|Strong|Excellent>", "key_factor": "<one sentence>"}}"""

    result = _call(prompt, max_tokens=120, temperature=0.1)
    if result and isinstance(result.get("score"), (int, float)):
        result["score"] = int(max(0, min(10, result["score"])))
        with _cache_lock:
            _score_cache[cache_key] = (time.time(), result)
    return result
