import traceback

from flask import Blueprint, request

from database import db_conn
from helpers import _ok, _err
import ai_advisor
import ai_rulebook
import bitget_sync

bp = Blueprint("sync", __name__)


@bp.route("/api/sync", methods=["POST"])
def api_sync():
    try:
        result = bitget_sync.run_sync()
        if "error" in result:
            return _err(result["error"])
        return _ok(result)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/sync/status")
def api_sync_status():
    status = bitget_sync.get_status()
    with db_conn() as conn:
        bitget_sync._ensure_settings_table(conn)
        for key in ("account_equity", "available_balance", "last_sync_ms"):
            row = conn.execute(
                "SELECT value FROM settings WHERE key=?", (key,)
            ).fetchone()
            status[key] = row[0] if row else None
    return _ok(status)


@bp.route("/api/ai/analyze", methods=["POST"])
def api_ai_analyze():
    try:
        filters = request.get_json(force=True) if request.content_length else {}
        return _ok(ai_advisor.analyze(filters=filters or {}))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/rulebook")
def api_rulebook_get():
    try:
        with db_conn() as conn:
            return _ok(ai_rulebook.get_rulebook(conn))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/rulebook/update", methods=["POST"])
def api_rulebook_update():
    try:
        with db_conn() as conn:
            return _ok(ai_rulebook.update_rulebook(conn))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)
