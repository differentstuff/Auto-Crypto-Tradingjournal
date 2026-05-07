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
    return jsonify({"ok": False, "error": msg}), code


def _filters_from_args():
    return {
        "symbol":    request.args.get("symbol",    "").strip() or None,
        "direction": request.args.get("direction", "").strip() or None,
        "date_from": request.args.get("date_from", "").strip() or None,
        "date_to":   request.args.get("date_to",   "").strip() or None,
    }
