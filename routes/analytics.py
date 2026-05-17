import traceback

from flask import Blueprint, request, render_template

from database import db_conn
from helpers import _ok, _err, _filters_from_args
from analytics import (get_dashboard_kpis, get_deep_stats, get_rr_analysis, get_heatmap_data,
                        get_mfe_mae, get_ev_by_setup, get_rolling_stats, get_sharpe_calmar,
                        get_accuracy_trend)
import ai_pattern_detector
import chart_context

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
        filters = _filters_from_args()
        with db_conn() as conn:
            data = get_heatmap_data(conn=conn, filters=filters)
        return _ok(data)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/analytics/patterns", methods=["POST"])
def api_analytics_patterns():
    try:
        body    = request.get_json(silent=True) or {}
        filters = {**_filters_from_args(), **{k: v for k, v in body.items() if k == "exchange"}}
        with db_conn() as conn:
            result = ai_pattern_detector.detect_patterns(conn=conn, filters=filters)
        return _ok(result)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/analytics/rr")
def api_analytics_rr():
    try:
        filters = _filters_from_args()
        with db_conn() as conn:
            data = get_rr_analysis(conn=conn, filters=filters)
        return _ok({"items": data})
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/analytics/by-setup")
def api_analytics_by_setup():
    """GET /api/analytics/by-setup — P&L breakdown by setup type."""
    try:
        from analytics import get_setup_type_stats
        with db_conn() as conn:
            data = get_setup_type_stats(filters=_filters_from_args(), conn=conn)
        return _ok({"setups": data})
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/analytics/benchmark")
def api_analytics_benchmark():
    """GET /api/analytics/benchmark -- trader return vs BTC buy-and-hold."""
    try:
        from analytics import get_benchmark_comparison
        with db_conn() as conn:
            data = get_benchmark_comparison(filters=_filters_from_args(), conn=conn)
        return _ok(data)
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


@bp.route("/api/chart/annotated/<symbol>")
def api_chart_annotated(symbol):
    """
    GET /api/chart/annotated/CHZUSDT?direction=Long&entry=0.044&sl=0.041&tp1=0.048&tp2=0.053
    Generate annotated chart PNG (base64) for any symbol.
    All trade level params are optional — omit for a plain S/R chart.
    Used by Hermes to send charts via Telegram.
    """
    try:
        import agent_chart_draw
        from chart_context import get_candles
        from chart_sr import detect_support_resistance

        sym       = symbol.strip().upper()
        direction = request.args.get("direction", "Long")

        def _flt(key):
            v = request.args.get(key)
            return float(v) if v else None

        entry      = _flt("entry") or 0
        entry_high = _flt("entry_high")
        sl         = _flt("sl") or 0
        tp1        = _flt("tp1") or 0
        tp2        = _flt("tp2") or 0
        tf         = request.args.get("tf", "4H")

        candles = get_candles(sym, tf)
        if candles is None or candles.empty:
            return _err(f"No candle data for {sym}")

        sr_levels = detect_support_resistance(candles)
        chart_b64 = agent_chart_draw.draw(
            candles    = candles,
            symbol     = sym,
            direction  = direction,
            entry      = entry,
            entry_high = entry_high,
            sl         = sl,
            tp1        = tp1,
            tp2        = tp2,
            sr_levels  = sr_levels,
        )
        if not chart_b64:
            return _err("Chart generation failed")
        return _ok({"symbol": sym, "direction": direction, "chart_b64": chart_b64})
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/analytics/mfe-mae")
def api_mfe_mae():
    try:
        with db_conn() as conn:
            return _ok(get_mfe_mae(conn=conn, filters=_filters_from_args()))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/analytics/ev-by-setup")
def api_ev_by_setup():
    try:
        with db_conn() as conn:
            return _ok(get_ev_by_setup(conn=conn, filters=_filters_from_args()))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/analytics/rolling")
def api_rolling_stats():
    try:
        days = int(request.args.get("days", 30))
        with db_conn() as conn:
            return _ok(get_rolling_stats(conn=conn, filters=_filters_from_args(), days=days))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/analytics/accuracy-trend")
def api_accuracy_trend():
    try:
        with db_conn() as conn:
            return _ok(get_accuracy_trend(conn=conn, filters=_filters_from_args()))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/analytics/sharpe-calmar")
def api_sharpe_calmar():
    try:
        with db_conn() as conn:
            return _ok(get_sharpe_calmar(conn=conn, filters=_filters_from_args()))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/token-usage")
def api_token_usage():
    """GET /api/token-usage?days=7  — rolling usage + per-module breakdown."""
    try:
        days = int(request.args.get("days", 7))
        with db_conn() as conn:
            rows = [dict(r) for r in conn.execute("""
                SELECT module, model,
                       COUNT(*) AS calls,
                       SUM(input_tokens)  AS total_input,
                       SUM(output_tokens) AS total_output,
                       SUM(cached_tokens) AS total_cached
                FROM token_usage
                WHERE ts >= datetime('now', ? || ' days')
                GROUP BY module, model
                ORDER BY total_input DESC
            """, (f"-{days}",)).fetchall()]

            totals = dict(conn.execute("""
                SELECT SUM(input_tokens) AS total_input,
                       SUM(output_tokens) AS total_output,
                       SUM(cached_tokens) AS total_cached,
                       COUNT(*) AS total_calls
                FROM token_usage
                WHERE ts >= datetime('now', ? || ' days')
            """, (f"-{days}",)).fetchone() or {})

            all_time = dict(conn.execute("""
                SELECT SUM(input_tokens) AS total_input,
                       SUM(output_tokens) AS total_output,
                       COUNT(*) AS total_calls
                FROM token_usage
            """).fetchone() or {})

        sonnet_in_cost  = 3.0 / 1_000_000   # $/token (input)
        sonnet_out_cost = 15.0 / 1_000_000  # $/token (output)
        haiku_in_cost   = 0.8 / 1_000_000
        haiku_out_cost  = 4.0 / 1_000_000

        def cost(row):
            if "haiku" in row.get("model", "").lower():
                return round(row["total_input"] * haiku_in_cost + row["total_output"] * haiku_out_cost, 4)
            return round(row["total_input"] * sonnet_in_cost + row["total_output"] * sonnet_out_cost, 4)

        for r in rows:
            r["est_cost_usd"] = cost(r)

        total_cost = sum(r["est_cost_usd"] for r in rows)
        return _ok({
            "days": days,
            "by_module": rows,
            "totals": totals,
            "all_time": all_time,
            "est_cost_usd": round(total_cost, 4),
        })
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
