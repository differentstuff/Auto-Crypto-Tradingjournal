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
from database import db_conn
import ai_scanner
import nansen_client

bp = Blueprint("scanner", __name__)


@bp.route("/api/scanner/run", methods=["POST"])
def api_scanner_run():
    try:
        body      = request.get_json(silent=True) or {}
        force     = request.args.get("force") == "1" or body.get("force")
        symbols   = body.get("symbols")
        min_score = max(1, min(10, int(body.get("min_score", 6))))

        # Validate and merge criteria — only accept known keys, values must be bool
        raw_criteria = body.get("criteria") or {}
        criteria = {
            k: bool(raw_criteria.get(k, v))
            for k, v in ai_scanner.CRITERIA_DEFAULTS.items()
        }

        if force:
            started = ai_scanner.force_scan(symbols, min_score, criteria=criteria)
        else:
            started = ai_scanner.start_scan(symbols, min_score, criteria=criteria)

        state = ai_scanner.get_state()
        if not started and state["status"] == "running":
            return _ok({"message": "Scan already running", **state})
        if not started and state["status"] == "completed":
            return _ok({"message": "Returning cached results (< 30 min old). Use force=1 to rescan.", **state})
        return _ok({"message": "Scan started", **state})
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/scanner/cancel", methods=["POST"])
def api_scanner_cancel():
    """POST /api/scanner/cancel — Request cancellation of the running scan."""
    try:
        cancelled = ai_scanner.cancel_scan()
        if cancelled:
            return _ok({"message": "Cancellation requested — scan will stop at the next stage boundary"})
        return _ok({"message": "No scan is currently running"})
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/nansen/movers")
def api_nansen_movers():
    """
    GET /api/nansen/movers
    Returns top smart money accumulators and distributors from Nansen screener.
    Cached for 30 minutes — no extra API credit used if scanner already ran.
    """
    try:
        if not nansen_client.is_configured():
            return _ok({"configured": False, "accumulators": [], "distributors": []})
        data = nansen_client.get_top_movers()
        return _ok({**data, "configured": True})
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/nansen/signal/<symbol>")
def api_nansen_signal(symbol):
    """
    GET /api/nansen/signal/BTCUSDT
    Returns smart money signal for a single symbol.
    """
    try:
        if not nansen_client.is_configured():
            return _ok({"configured": False, "ok": False})
        return _ok({**nansen_client.get_smart_money_signal(symbol), "configured": True})
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/scanner/criteria-defaults")
def api_scanner_criteria_defaults():
    """Return the full criteria defaults dict for the frontend configurator."""
    try:
        return _ok(ai_scanner.CRITERIA_DEFAULTS)
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


@bp.route("/api/scanner/calibrate", methods=["POST"])
def api_scanner_calibrate():
    """
    Analyse the last 30 days of scanner setups vs outcomes and
    suggest (or apply) an adjusted ENTER_THRESHOLD.
    Returns current threshold, TP/FP rates, and recommended new threshold.
    Apply with ?apply=1 to persist to settings.
    """
    try:
        apply = request.args.get("apply") == "1"
        with db_conn() as conn:
            rows = [dict(r) for r in conn.execute("""
                SELECT setup_score,
                       SUM(CASE WHEN hit_tp1=1 THEN 1 ELSE 0 END) AS tp,
                       SUM(CASE WHEN hit_sl=1  THEN 1 ELSE 0 END) AS fp,
                       COUNT(*) AS n
                FROM analyzed_calls
                WHERE outcome IS NOT NULL
                  AND created_at >= datetime('now', '-30 days')
                  AND setup_score IS NOT NULL
                GROUP BY setup_score
                ORDER BY setup_score DESC
            """).fetchall()]

            stored_thresh = conn.execute(
                "SELECT value FROM settings WHERE key='enter_threshold'"
            ).fetchone()
            current_thresh = int(stored_thresh[0]) if stored_thresh else 6

            # Find score tier where TP rate first exceeds 55%
            recommended = current_thresh
            for r in rows:
                if r["n"] >= 5 and r["tp"] / r["n"] >= 0.55:
                    recommended = r["setup_score"]
                    break

            if apply and recommended != current_thresh:
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key,value) VALUES ('enter_threshold',?)",
                    (str(recommended),)
                )
                conn.commit()

            return _ok({
                "current_threshold": current_thresh,
                "recommended":       recommended,
                "applied":           apply and recommended != current_thresh,
                "by_score":          rows,
            })
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/scanner/feedback")
def api_scanner_feedback():
    """GET /api/scanner/feedback — last hindsight recalibration result."""
    try:
        import json
        with db_conn() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key='hindsight_feedback_json'"
            ).fetchone()
        if not row:
            return _ok({"available": False})
        return _ok({"available": True, **json.loads(row[0])})
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)
