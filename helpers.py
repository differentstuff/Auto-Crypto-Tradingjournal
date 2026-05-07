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
