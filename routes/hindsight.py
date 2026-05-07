"""
routes/hindsight.py — Hindsight Analysis Blueprint.

POST /api/hindsight/run            Start batch analysis (default 50 trades).
POST /api/hindsight/run?n=25       Analyze last N trades.
GET  /api/hindsight/status         Current run state (progress/total).
GET  /api/hindsight/results        Stored results + summary comparison.
DELETE /api/hindsight/results      Clear all stored results.
"""

import traceback
from flask import Blueprint, request
from helpers import _ok, _err
import ai_hindsight

bp = Blueprint("hindsight", __name__)


@bp.route("/api/hindsight/run", methods=["POST"])
def api_hindsight_run():
    try:
        n = int(request.args.get("n", 50))
        n = max(5, min(n, 200))
        started = ai_hindsight.start_batch(n)
        state = ai_hindsight.get_state()
        if not started:
            return _ok({"message": "Analysis already running", **state})
        return _ok({"message": f"Analyzing last {n} trades in background", **state})
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/hindsight/status")
def api_hindsight_status():
    try:
        return _ok(ai_hindsight.get_state())
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/hindsight/results")
def api_hindsight_results():
    try:
        limit = int(request.args.get("limit", 200))
        return _ok(ai_hindsight.get_results(limit))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/hindsight/results", methods=["DELETE"])
def api_hindsight_clear():
    try:
        from database import db_conn
        with db_conn() as conn:
            conn.execute("DELETE FROM trade_hindsight")
            conn.commit()
        return _ok({"message": "Hindsight results cleared"})
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)
