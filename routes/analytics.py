import time
import traceback

from flask import Blueprint, request, render_template

from database import db_conn
from helpers import _ok, _err, _filters_from_args
from analytics import get_dashboard_kpis, get_deep_stats, get_rr_analysis, get_heatmap_data
import ai_pattern_detector
import market_context
import chart_context
import bitget_client

_exchange_symbols_cache: list = []
_exchange_symbols_ts: float = 0

bp = Blueprint("analytics", __name__)


@bp.route("/chart")
def chart_page():
    return render_template("chart.html")


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


@bp.route("/api/exchange/symbols")
def api_exchange_symbols():
    """GET /api/exchange/symbols — all USDT-M futures symbols on Bitget (1-hour cache)."""
    global _exchange_symbols_cache, _exchange_symbols_ts
    try:
        if not _exchange_symbols_cache or (time.time() - _exchange_symbols_ts) > 3600:
            _exchange_symbols_cache = bitget_client.get_exchange_symbols()
            _exchange_symbols_ts = time.time()
        return _ok(_exchange_symbols_cache)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/chart/candles")
def api_chart_candles():
    """
    GET /api/chart/candles?symbol=BTCUSDT&timeframe=4H&limit=200
    Returns OHLCV candles + detected S/R levels for the frontend chart modal.
    """
    try:
        symbol = request.args.get("symbol", "").strip().upper()
        if not symbol:
            return _err("symbol is required")
        timeframe = request.args.get("timeframe", "4H").strip()
        limit     = int(request.args.get("limit", 200))
        return _ok(chart_context.get_candles_for_chart(symbol, timeframe, limit))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/chart/indicators")
def api_chart_indicators():
    """
    GET /api/chart/indicators?symbol=BTCUSDT&timeframes=4H,1D
    Returns computed indicator suite for a symbol + timeframe(s).
    """
    try:
        symbol = request.args.get("symbol", "").strip().upper()
        if not symbol:
            return _err("symbol is required")
        tf_raw     = request.args.get("timeframes", "4H,1D")
        timeframes = [t.strip() for t in tf_raw.split(",") if t.strip()]
        ctx = chart_context.get_chart_context(symbol, timeframes)
        return _ok(ctx)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)
