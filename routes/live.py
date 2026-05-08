import traceback

from flask import Blueprint, request

from database import db_conn
from helpers import _ok, _err
import ai_live_trade
import bitget_client
import blofin_client

bp = Blueprint("live", __name__)


@bp.route("/api/live/positions")
def api_live_positions():
    try:
        positions   = []
        total_eq    = 0.0
        total_avail = 0.0
        raw_equity  = {}

        try:
            positions  = bitget_client.get_open_positions()
            raw_equity = bitget_client.get_account_equity()
            total_eq    += float(raw_equity.get("accountEquity") or raw_equity.get("equity") or 0)
            total_avail += float(raw_equity.get("available") or 0)
        except Exception:
            pass
        try:
            if blofin_client.is_configured():
                positions += blofin_client.get_open_positions()
                bl_eq      = blofin_client.get_account_equity()
                total_eq    += float(bl_eq.get("equity") or 0)
                total_avail += float(bl_eq.get("available") or 0)
        except Exception:
            pass

        # Normalize to a consistent shape the frontend always expects
        equity = {
            **raw_equity,
            "accountEquity": str(round(total_eq,    8)),
            "available":     str(round(total_avail, 8)),
        }
        return _ok({"positions": positions, "equity": equity})
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/live/pending-orders")
def api_live_pending_orders():
    try:
        orders = bitget_client.get_pending_orders()
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)

    with db_conn() as conn:
        tracked = [r[0] for r in conn.execute(
            "SELECT bitget_order_id FROM pending_limits WHERE bitget_order_id IS NOT NULL"
        ).fetchall()]

    return _ok({"bitget_orders": orders, "tracked_ids": tracked})


@bp.route("/api/live/analyze", methods=["POST"])
def api_live_analyze():
    try:
        position = request.get_json(force=True)
        if not position or not position.get("symbol"):
            return _err("position data with symbol required")
        return _ok(ai_live_trade.analyze_position(position))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)
