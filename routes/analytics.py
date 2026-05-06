import traceback

from flask import Blueprint, request

from database import db_conn
from helpers import _ok, _err, _filters_from_args
from analytics import get_dashboard_kpis, get_deep_stats, get_rr_analysis, get_heatmap_data
import ai_pattern_detector
import market_context

bp = Blueprint("analytics", __name__)


@bp.route("/api/dashboard/kpis")
def api_dashboard_kpis():
    try:
        return _ok(get_dashboard_kpis(filters=_filters_from_args()))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/analytics/deep")
def api_analytics_deep():
    try:
        return _ok(get_deep_stats(filters=_filters_from_args()))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/analytics/heatmap")
def api_analytics_heatmap():
    try:
        with db_conn() as conn:
            data = get_heatmap_data(conn=conn)
        return _ok(data)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/analytics/patterns", methods=["POST"])
def api_analytics_patterns():
    try:
        with db_conn() as conn:
            result = ai_pattern_detector.detect_patterns(conn=conn)
        return _ok(result)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/analytics/rr")
def api_analytics_rr():
    try:
        with db_conn() as conn:
            data = get_rr_analysis(conn=conn)
        return _ok({"items": data})
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/market/context")
def api_market_context():
    try:
        symbols_raw = request.args.get("symbols", "")
        symbols = [s.strip().upper() for s in symbols_raw.split(",") if s.strip()] if symbols_raw else []
        return _ok(market_context.get_market_context(symbols or None))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/market/calendar")
def api_market_calendar():
    try:
        return _ok(market_context.get_economic_calendar())
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)
