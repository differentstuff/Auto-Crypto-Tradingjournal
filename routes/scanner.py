"""
routes/scanner.py — Setup Scanner Blueprint.

POST /api/scanner/run          Start a background scan (returns immediately).
POST /api/scanner/run?force=1  Force re-scan even if cache is fresh.
GET  /api/scanner/status       Current scan state + results.
GET  /api/scanner/watchlist    Default watchlist.
"""

import traceback
from flask import Blueprint, request
from helpers import _ok, _err
import ai_scanner

bp = Blueprint("scanner", __name__)


@bp.route("/api/scanner/run", methods=["POST"])
def api_scanner_run():
    try:
        force = request.args.get("force") == "1" or (request.get_json(silent=True) or {}).get("force")
        symbols = (request.get_json(silent=True) or {}).get("symbols")

        if force:
            started = ai_scanner.force_scan(symbols)
        else:
            started = ai_scanner.start_scan(symbols)

        state = ai_scanner.get_state()
        if not started and state["status"] == "running":
            return _ok({"message": "Scan already running", **state})
        if not started and state["status"] == "completed":
            return _ok({"message": "Returning cached results (< 30 min old). Use force=1 to rescan.", **state})
        return _ok({"message": "Scan started", **state})
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/scanner/status")
def api_scanner_status():
    try:
        return _ok(ai_scanner.get_state())
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/scanner/watchlist")
def api_scanner_watchlist():
    try:
        return _ok({"symbols": ai_scanner.DEFAULT_WATCHLIST})
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)
