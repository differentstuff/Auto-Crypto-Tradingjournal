import re
import traceback
import zipfile
import tempfile
import shutil
import os
from datetime import datetime

from flask import Blueprint, request
from werkzeug.utils import secure_filename

from database import db_conn
from helpers import _ok, _err, _filters_from_args
from importer import import_folder
import ai_trade_grader

bp = Blueprint("journal", __name__)

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(_BASE, "data")


# ── import ─────────────────────────────────────────────────────────────────────

@bp.route("/api/import", methods=["POST"])
def api_import():
    tmp_dir = tempfile.mkdtemp()
    try:
        if "file" in request.files:
            f     = request.files["file"]
            fname = secure_filename(f.filename) or "upload.zip"
            fpath = os.path.join(tmp_dir, fname)
            f.save(fpath)
            if fname.lower().endswith(".zip"):
                with zipfile.ZipFile(fpath, "r") as zf:
                    zf.extractall(tmp_dir)
                os.remove(fpath)
            for fn in os.listdir(tmp_dir):
                if fn.lower().endswith(".csv"):
                    shutil.copy(os.path.join(tmp_dir, fn), DATA_DIR)
            import_dir = tmp_dir
        else:
            import_dir = DATA_DIR

        with db_conn() as conn:
            results = import_folder(import_dir, conn)
        return _ok(results)
    except Exception:
        traceback.print_exc()
        return _err("Import failed — check server logs for details")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@bp.route("/api/import/status")
def api_import_status():
    with db_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM import_log ORDER BY imported_at DESC"
        ).fetchall()]
    return _ok(rows)


# ── positions ──────────────────────────────────────────────────────────────────

@bp.route("/api/positions", methods=["GET"])
def api_positions_list():
    filters  = _filters_from_args()
    page     = max(1, int(request.args.get("page", 1)))
    per_page = min(200, int(request.args.get("per_page", 50)))
    offset   = (page - 1) * per_page

    clauses, params = [], []
    if filters["symbol"]:
        clauses.append("symbol = ?"); params.append(filters["symbol"])
    if filters["direction"]:
        clauses.append("direction = ?"); params.append(filters["direction"])
    if filters["date_from"]:
        clauses.append("close_time >= ?"); params.append(filters["date_from"])
    if filters["date_to"]:
        clauses.append("close_time <= ?"); params.append(filters["date_to"] + " 23:59:59")

    search = request.args.get("search", "").strip()
    if search:
        clauses.append("(symbol LIKE ? OR notes LIKE ?)")
        params += [f"%{search}%", f"%{search}%"]

    pnl_side = request.args.get("pnl_side", "").strip()
    if pnl_side == "win":
        clauses.append("realized_pnl > 0")
    elif pnl_side == "loss":
        clauses.append("realized_pnl < 0")

    setup = request.args.get("setup", "").strip()
    allowed_setups = {"Breakout", "Pullback", "Trend Continuation",
                      "Range Fade", "Reversal", "News/Event", "Other"}
    if setup == "untagged":
        clauses.append("(setup_type IS NULL OR setup_type = '')")
    elif setup in allowed_setups:
        clauses.append("setup_type = ?")
        params.append(setup)

    exchange = request.args.get("exchange", "").strip().lower()
    if exchange in ("bitget", "blofin"):
        clauses.append("COALESCE(exchange, 'bitget') = ?")
        params.append(exchange)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    with db_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM positions {where}", params).fetchone()[0]
        rows  = [dict(r) for r in conn.execute(
            f"""SELECT id, symbol, direction, open_time, close_time, duration_minutes,
                       entry_price, close_price, size_contracts, size_usdt,
                       position_pnl, realized_pnl, total_fees, notes, tags, is_manual, analyst,
                       setup_type, call_id, execution_grade, execution_grade_reason,
                       COALESCE(exchange, 'bitget') AS exchange
                FROM positions {where}
                ORDER BY close_time DESC
                LIMIT ? OFFSET ?""",
            params + [per_page, offset]
        ).fetchall()]

    return _ok({
        "positions": rows,
        "total":     total,
        "page":      page,
        "per_page":  per_page,
        "pages":     (total + per_page - 1) // per_page,
    })


@bp.route("/api/positions", methods=["POST"])
def api_positions_create():
    d = request.get_json(force=True)
    required = ["symbol", "direction", "open_time", "close_time",
                "entry_price", "close_price", "size_usdt", "realized_pnl"]
    for field in required:
        if not d.get(field) and d.get(field) != 0:
            return _err(f"Missing field: {field}")

    symbol     = d["symbol"].strip().upper()
    base_asset = re.sub(r"USDT$", "", symbol)

    try:
        dur = int((datetime.strptime(d["close_time"], "%Y-%m-%d %H:%M:%S") -
                   datetime.strptime(d["open_time"],  "%Y-%m-%d %H:%M:%S")).total_seconds() / 60)
    except Exception:
        dur = None

    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO positions
              (symbol, base_asset, direction, margin_mode,
               open_time, close_time, duration_minutes,
               entry_price, close_price, size_contracts, size_usdt,
               position_pnl, realized_pnl,
               opening_fee, closing_fee, total_fees,
               notes, tags, setup_type, is_manual)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
        """, (
            symbol, base_asset,
            d["direction"],
            d.get("margin_mode", "Cross"),
            d["open_time"], d["close_time"], dur,
            float(d["entry_price"]),
            float(d["close_price"]),
            d.get("size_contracts", ""),
            float(d["size_usdt"]),
            float(d.get("position_pnl") or d["realized_pnl"]),
            float(d["realized_pnl"]),
            float(d.get("opening_fee", 0) or 0),
            float(d.get("closing_fee", 0) or 0),
            float(d.get("total_fees", 0) or 0),
            d.get("notes", ""),
            d.get("tags", ""),
            d.get("setup_type", ""),
        ))
        new_id = cur.lastrowid
        conn.commit()

    return _ok({"id": new_id}), 201


@bp.route("/api/positions/<int:pos_id>", methods=["GET"])
def api_position_get(pos_id):
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM positions WHERE id = ?", (pos_id,)).fetchone()
    if not row:
        return _err("Not found", 404)
    return _ok(dict(row))


@bp.route("/api/positions/<int:pos_id>", methods=["PUT"])
def api_position_update(pos_id):
    d        = request.get_json(force=True)
    editable = ["notes", "tags", "analyst", "entry_price", "close_price",
                "size_usdt", "realized_pnl", "total_fees",
                "open_time", "close_time", "direction", "setup_type", "call_id"]
    # Pre-built fragments: column names are hardcoded here, never derived from request data.
    # This makes the SQL fragments provably untainted for static analysis.
    _set_sql = {k: k + " = ?" for k in editable}
    sets = [_set_sql[k] for k in editable if k in d]
    vals = [d[k]         for k in editable if k in d]
    if not sets:
        return _err("No updatable fields provided")

    sets.append("updated_at = datetime('now')")
    vals.append(pos_id)

    with db_conn() as conn:
        if not conn.execute("SELECT id FROM positions WHERE id = ?", (pos_id,)).fetchone():
            return _err("Not found", 404)
        conn.execute("UPDATE positions SET " + ", ".join(sets) + " WHERE id = ?", vals)
        conn.commit()

    return _ok({"updated": pos_id})


@bp.route("/api/positions/<int:pos_id>", methods=["DELETE"])
def api_position_delete(pos_id):
    with db_conn() as conn:
        conn.execute("DELETE FROM positions WHERE id = ?", (pos_id,))
        conn.commit()
    return _ok({"deleted": pos_id})


@bp.route("/api/positions/<int:pos_id>/grade", methods=["POST"])
def api_position_grade(pos_id):
    try:
        result = ai_trade_grader.grade_trade(pos_id)
        if "error" in result:
            if result.get("error") == "Position not found":
                return _err("Position not found", 404)
            return _err("Grading failed — check server logs", 500)
        return _ok(result)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


# ── helpers ────────────────────────────────────────────────────────────────────

@bp.route("/api/symbols")
def api_symbols():
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM positions ORDER BY symbol ASC"
        ).fetchall()
    return _ok([r[0] for r in rows])


@bp.route("/api/wallet/history")
def api_wallet_history():
    with db_conn() as conn:
        rows = [dict(r) for r in conn.execute("""
            SELECT date, wallet_balance
            FROM wallet_snapshots
            WHERE wallet_balance IS NOT NULL
            ORDER BY date ASC
        """).fetchall()]
    step = max(1, len(rows) // 300)
    return _ok(rows[::step])
