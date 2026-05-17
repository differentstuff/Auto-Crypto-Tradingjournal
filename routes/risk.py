"""routes/risk.py -- Risk analytics endpoints (free Binance data + local DB)."""
import traceback

from flask import Blueprint, request
from helpers import _ok, _err
import bitget_client
import blofin_client

bp = Blueprint("risk", __name__)


def _get_live_positions() -> tuple:
    positions, equity = [], 0.0
    try:
        positions = bitget_client.get_open_positions()
        eq = bitget_client.get_account_equity()
        equity += float(eq.get("accountEquity") or 0)
    except Exception:
        pass
    try:
        if blofin_client.is_configured():
            positions += blofin_client.get_open_positions()
            bl_eq = blofin_client.get_account_equity()
            equity += float(bl_eq.get("equity") or 0)
    except Exception:
        pass
    return positions, equity


@bp.route("/api/risk/var")
def api_risk_var():
    """GET /api/risk/var -- Historical simulation VaR on open positions."""
    try:
        from risk_analytics import compute_portfolio_var
        positions, equity = _get_live_positions()
        return _ok(compute_portfolio_var(positions, equity=equity))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/risk/correlation")
def api_risk_correlation():
    """GET /api/risk/correlation -- Pairwise correlation matrix for open positions."""
    try:
        from risk_analytics import compute_correlation_matrix
        positions, _ = _get_live_positions()
        return _ok(compute_correlation_matrix(positions))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/risk/attribution")
def api_risk_attribution():
    """GET /api/risk/attribution?days=90 -- Alpha vs Beta P&L attribution."""
    try:
        from risk_analytics import compute_pnl_attribution
        from database import db_conn
        days = min(int(request.args.get("days", 90)), 365)
        with db_conn() as conn:
            return _ok(compute_pnl_attribution(conn, lookback_days=days))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/risk/kelly")
def api_risk_kelly():
    """GET /api/risk/kelly -- Kelly Criterion sizing by score bucket."""
    try:
        from risk_analytics import compute_kelly_by_bucket
        from database import db_conn
        with db_conn() as conn:
            return _ok(compute_kelly_by_bucket(conn))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/risk/alpha-decay")
def api_risk_alpha_decay():
    """GET /api/risk/alpha-decay -- How execution lag affects P&L."""
    try:
        from risk_analytics import compute_alpha_decay
        from database import db_conn
        with db_conn() as conn:
            return _ok(compute_alpha_decay(conn))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)
