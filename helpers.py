from flask import jsonify, request


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
                          image_b64: str = None, image_type: str = "image/png") -> list:
    """
    Build Anthropic messages with prompt caching on the shared context block.
    Cache only activates when context >= 1024 tokens (~4096 chars) for Sonnet.
    Context must come before the image so it forms a stable cache key.
    """
    content = []
    if context:
        block = {"type": "text", "text": context}
        if len(context) >= 4096:          # ~1024 tokens — Sonnet minimum
            block["cache_control"] = {"type": "ephemeral"}
        content.append(block)
    if image_b64:
        content.append({"type": "image",
                         "source": {"type": "base64", "media_type": image_type, "data": image_b64}})
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
