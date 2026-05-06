import json
import traceback

from flask import Blueprint, request

from database import db_conn
from helpers import _ok, _err
import ai_call_analyzer
import bitget_client

bp = Blueprint("limits", __name__)


@bp.route("/api/limits", methods=["GET"])
def api_limits_list():
    status = request.args.get("status", "waiting")
    with db_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM pending_limits WHERE status = ? ORDER BY created_at DESC",
            (status,)
        ).fetchall()]
    return _ok(rows)


@bp.route("/api/limits", methods=["POST"])
def api_limits_create():
    d = request.get_json(force=True)
    if not d.get("symbol") or not d.get("limit_price") or not d.get("direction"):
        return _err("symbol, direction, and limit_price are required")
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO pending_limits
              (call_id, symbol, direction, limit_price, size_usdt,
               leverage, sl_price, tp1_price, tp2_price, analyst, notes, bitget_order_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            d.get("call_id") or None,
            d["symbol"].strip().upper(),
            d["direction"],
            float(d["limit_price"]),
            float(d["size_usdt"])  if d.get("size_usdt")  else None,
            int(d.get("leverage", 10)),
            float(d["sl_price"])   if d.get("sl_price")   else None,
            float(d["tp1_price"])  if d.get("tp1_price")  else None,
            float(d["tp2_price"])  if d.get("tp2_price")  else None,
            (d.get("analyst") or "").strip(),
            (d.get("notes")   or "").strip(),
            d.get("bitget_order_id") or None,
        ))
        new_id = cur.lastrowid
        conn.commit()
    return _ok({"id": new_id}), 201


@bp.route("/api/limits/bulk-update", methods=["POST"])
def api_limits_bulk_update():
    d   = request.get_json(force=True)
    ids = [int(x) for x in (d.get("ids") or [])]
    if not ids:
        return _err("ids list is required")

    editable = ["status", "sl_price", "tp1_price", "tp2_price", "call_id", "analyst", "notes"]
    sets, vals = [], []
    for key in editable:
        if key in d:
            sets.append(f"{key} = ?")
            vals.append(d[key])
    if d.get("status") == "triggered":
        sets.append("triggered_at = datetime('now')")
    if not sets:
        return _err("No updatable fields")

    placeholders = ",".join(["?"] * len(ids))
    with db_conn() as conn:
        conn.execute(
            f"UPDATE pending_limits SET {', '.join(sets)} WHERE id IN ({placeholders})",
            vals + ids,
        )
        conn.commit()
    return _ok({"updated_count": len(ids)})


@bp.route("/api/limits/risk-summary")
def api_limits_risk_summary():
    with db_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT symbol, direction, limit_price, size_usdt, sl_price FROM pending_limits WHERE status='waiting'"
        ).fetchall()]
    total_notional = sum(r["size_usdt"] or 0 for r in rows)
    by_symbol = {}
    for r in rows:
        by_symbol[r["symbol"]] = by_symbol.get(r["symbol"], 0) + (r["size_usdt"] or 0)
    return _ok({
        "pending_count":       len(rows),
        "total_notional_usdt": round(total_notional, 2),
        "by_symbol": [{"symbol": k, "notional": round(v, 2)} for k, v in by_symbol.items()],
    })


@bp.route("/api/limits/<int:lim_id>", methods=["PATCH"])
def api_limits_update(lim_id):
    d        = request.get_json(force=True)
    editable = ["status", "limit_price", "size_usdt", "leverage", "sl_price",
                "tp1_price", "tp2_price", "analyst", "notes", "analysis_json",
                "call_id", "bitget_order_id"]
    sets, vals = [], []
    for key in editable:
        if key in d:
            sets.append(f"{key} = ?")
            vals.append(d[key])
    if d.get("status") == "triggered":
        sets.append("triggered_at = datetime('now')")
    if not sets:
        return _err("No updatable fields")
    vals.append(lim_id)
    with db_conn() as conn:
        conn.execute(f"UPDATE pending_limits SET {', '.join(sets)} WHERE id = ?", vals)
        conn.commit()
    return _ok({"updated": lim_id})


@bp.route("/api/limits/<int:lim_id>", methods=["DELETE"])
def api_limits_delete(lim_id):
    with db_conn() as conn:
        conn.execute("DELETE FROM pending_limits WHERE id = ?", (lim_id,))
        conn.commit()
    return _ok({"deleted": lim_id})


@bp.route("/api/limits/<int:lim_id>/analyze", methods=["POST"])
def api_limits_analyze(lim_id):
    try:
        with db_conn() as conn:
            lim = conn.execute("SELECT * FROM pending_limits WHERE id=?", (lim_id,)).fetchone()
            if not lim:
                return _err("Not found", 404)
            lim = dict(lim)
            other_limits = [dict(r) for r in conn.execute(
                "SELECT symbol, direction, limit_price, size_usdt FROM pending_limits WHERE status='waiting' AND id != ?",
                (lim_id,)
            ).fetchall()]

        try:
            eq_data        = bitget_client.get_account_equity()
            equity         = float(eq_data.get("accountEquity") or eq_data.get("available") or 1000)
            open_positions = bitget_client.get_open_positions()
        except Exception:
            equity, open_positions = 1000.0, []

        result = ai_call_analyzer.analyze_pending_limit(lim, equity, open_positions, other_limits)

        with db_conn() as conn:
            conn.execute(
                "UPDATE pending_limits SET analysis_json = ? WHERE id = ?",
                (json.dumps(result), lim_id)
            )
            conn.commit()

        return _ok(result)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)
