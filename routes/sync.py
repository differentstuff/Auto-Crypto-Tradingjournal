import traceback

from flask import Blueprint, request

from database import db_conn
from helpers import _ok, _err
import ai_advisor
import ai_rulebook
import bitget_sync
import telegram_notify
import scanner_scheduler

bp = Blueprint("sync", __name__)


@bp.route("/api/sync", methods=["POST"])
def api_sync():
    try:
        result = bitget_sync.run_sync()
        if "error" in result:
            if result.get("error") == "Sync already running":
                return _err("Sync already running", 409)
            return _err("Sync failed — check server logs", 500)
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
    except Exception as e:
        traceback.print_exc()
        return _err(f"{type(e).__name__}: {e}", 500)


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
        body  = request.get_json(force=True, silent=True) or {}
        force = bool(body.get("force", False))
        with db_conn() as conn:
            return _ok(ai_rulebook.update_rulebook(conn, force=force))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/telegram/status")
def api_telegram_status():
    """GET /api/telegram/status — check whether Telegram alerts are configured."""
    return _ok({
        "configured": telegram_notify.is_configured(),
        "interval_min": scanner_scheduler.INTERVAL // 60,
        "first_delay_min": scanner_scheduler.FIRST_DELAY // 60,
    })


@bp.route("/api/telegram/test", methods=["POST"])
def api_telegram_test():
    """POST /api/telegram/test — send a test message to verify configuration."""
    if not telegram_notify.is_configured():
        return _err("Telegram not configured — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
    ok = telegram_notify.send_test_message()
    if ok:
        return _ok({"message": "Test message sent — check your Telegram"})
    return _err("Failed to send test message — check your token and chat ID", 500)
