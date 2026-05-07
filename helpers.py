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
    # Never expose internal details on server errors (CWE-209)
    safe = msg if code < 500 else "Internal server error"
    return jsonify({"ok": False, "error": safe}), code


def _filters_from_args():
    return {
        "symbol":    request.args.get("symbol",    "").strip() or None,
        "direction": request.args.get("direction", "").strip() or None,
        "date_from": request.args.get("date_from", "").strip() or None,
        "date_to":   request.args.get("date_to",   "").strip() or None,
    }
