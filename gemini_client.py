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
    Forces JSON output via responseMimeType.
    """
    if not GEMINI_API_KEY:
        return None
    mdl = model or _ACTIVE_MODEL
    url = f"{GEMINI_BASE}/{mdl}:generateContent?key={GEMINI_API_KEY}"
    payload = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature":       temperature,
            "maxOutputTokens":   max_tokens,
            "responseMimeType":  "application/json",
        },
    }).encode()
    req = urllib.request.Request(url, data=payload)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            resp = json.loads(r.read())
        # Extract text from candidates[0].content.parts[0].text
        text = resp["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        print(f"[Gemini] HTTP error: {exc}", flush=True)
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        print(f"[Gemini] Parse error: {exc}", flush=True)
    except Exception as exc:
        print(f"[Gemini] Unexpected error: {exc}", flush=True)
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
