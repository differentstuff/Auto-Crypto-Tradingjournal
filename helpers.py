from constants import PROMPT_CACHE_MIN_CHARS
from flask import jsonify, request

from token_log import log_token_usage  # noqa: F401  backward-compat re-export


def strip_fence(raw: str) -> str:
    """Strip markdown code fences (```json ... ```) from Claude responses."""
    if raw.startswith("```"):
        lines = raw.split("\n")[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return raw


def _ok(data):
    return jsonify({"ok": True, "data": data})


def build_cached_messages(context: str, prompt: str,
                          image_b64: str = None, image_type: str = "image/png",
                          stable_prefix: str = None) -> list:
    """
    Build Anthropic messages with split-context prompt caching.

    stable_prefix (optional): rulebook + calibration + scoring rules — changes
      weekly at most. Gets cache_control=ephemeral so it is cached across calls.

    context: market data, chart indicators, similar trades — changes every 5-30 min.
      NOT cached (cache key would differ on nearly every call, wasting write credits).

    prompt: per-call variable content (call text, sizing, etc.). Never cached.

    The cache checkpoint is placed at the END of stable_prefix so Anthropic's
    cache key covers exactly the stable portion and nothing more.
    """
    content = []

    # Stable block — cache this (rulebook, calibration, prompt fragments)
    if stable_prefix and len(stable_prefix) >= PROMPT_CACHE_MIN_CHARS:
        content.append({
            "type":          "text",
            "text":          stable_prefix,
            "cache_control": {"type": "ephemeral"},
        })
    elif stable_prefix:
        # Too short for caching minimum — still include it, just uncached
        content.append({"type": "text", "text": stable_prefix})

    # Dynamic block — market/chart/similar trades (no cache_control)
    if context:
        content.append({"type": "text", "text": context})

    # Image (call analyzer chart screenshot)
    if image_b64:
        content.append({"type": "image",
                         "source": {"type": "base64", "media_type": image_type,
                                    "data": image_b64}})

    content.append({"type": "text", "text": prompt})
    return [{"role": "user", "content": content}]


def _err(msg, code=400):
    # CWE-209: never expose stack traces or internal exception details.
    # 5xx errors always return a generic message.
    # 4xx errors use the caller-supplied msg, which must be a safe pre-approved literal
    # (never str(exception) — callers must log exceptions server-side instead).
    if code >= 500:
        safe = "Internal server error"
    else:
        # Limit length and strip any characters that could reveal internal paths or stack frames
        safe = str(msg)[:200]
    return jsonify({"ok": False, "error": safe}), code


def _filters_from_args():
    exchange = request.args.get("exchange", "").strip().lower()
    return {
        "symbol":    request.args.get("symbol",    "").strip() or None,
        "direction": request.args.get("direction", "").strip() or None,
        "date_from": request.args.get("date_from", "").strip() or None,
        "date_to":   request.args.get("date_to",   "").strip() or None,
        "exchange":  exchange if exchange in ("bitget", "blofin") else None,
    }
