"""
app.py — Flask web application for the Crypto Trading Journal.

Routes:
  GET  /                         → serve SPA (index.html)
  POST /api/import               → upload & import a Bitget CSV export folder/zip
  GET  /api/positions            → list positions with filters + pagination
  POST /api/positions            → create a manual position
  GET  /api/positions/<id>       → single position detail
  PUT  /api/positions/<id>       → update notes / tags
  GET  /api/dashboard/kpis       → dashboard KPI data
  GET  /api/analytics/deep       → deep dive stats
  POST /api/ai/analyze           → trigger Claude AI analysis
  GET  /api/symbols              → distinct symbol list (for filter dropdowns)
  GET  /api/wallet/history       → wallet balance curve
  GET  /api/import/status        → import log
  POST /api/sync                 → trigger manual Bitget API sync
  GET  /api/sync/status          → last sync time, counts, account equity
"""

import json
import os
import traceback
import zipfile
import tempfile
import shutil
from datetime import datetime

from flask import Flask, jsonify, request, render_template, send_from_directory

from database     import init_db, get_conn
from importer     import import_folder
from analytics    import get_dashboard_kpis, get_deep_stats
import ai_advisor
import ai_live_trade
import ai_call_analyzer
import bitget_sync
import bitget_client

# ── app setup ──────────────────────────────────────────────────────────────────

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
DATA_DIR  = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024  # 64 MB upload limit


# ── startup ────────────────────────────────────────────────────────────────────

@app.before_request
def _ensure_db():
    pass  # init_db() is called once at the bottom

# ── helpers ────────────────────────────────────────────────────────────────────

def _filters_from_args():
    return {
        "symbol":    request.args.get("symbol",    "").strip() or None,
        "direction": request.args.get("direction", "").strip() or None,
        "date_from": request.args.get("date_from", "").strip() or None,
        "date_to":   request.args.get("date_to",   "").strip() or None,
    }

def _ok(data):
    return jsonify({"ok": True,  "data": data})

def _err(msg, code=400):
    return jsonify({"ok": False, "error": msg}), code


# ── SPA ────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ── import ─────────────────────────────────────────────────────────────────────

@app.route("/api/import", methods=["POST"])
def api_import():
    """
    Accept either:
      - A ZIP file containing the Bitget CSV exports
      - A multipart upload of individual CSV files
    Stores the files in data/ and runs import_folder().
    """
    tmp_dir = tempfile.mkdtemp()
    try:
        if "file" in request.files:
            f = request.files["file"]
            fname = f.filename or "upload.zip"
            fpath = os.path.join(tmp_dir, fname)
            f.save(fpath)
            if fname.lower().endswith(".zip"):
                with zipfile.ZipFile(fpath, "r") as zf:
                    zf.extractall(tmp_dir)
                os.remove(fpath)
            # also copy CSVs to data/ for re-import later
            for fn in os.listdir(tmp_dir):
                if fn.lower().endswith(".csv"):
                    shutil.copy(os.path.join(tmp_dir, fn), DATA_DIR)
            import_dir = tmp_dir
        else:
            # fall back to the bundled data/ directory
            import_dir = DATA_DIR

        conn    = get_conn()
        results = import_folder(import_dir, conn)
        conn.close()
        return _ok(results)
    except Exception as e:
        traceback.print_exc()
        return _err(str(e))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@app.route("/api/import/status")
def api_import_status():
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM import_log ORDER BY imported_at DESC"
    ).fetchall()]
    conn.close()
    return _ok(rows)


# ── positions ──────────────────────────────────────────────────────────────────

@app.route("/api/positions", methods=["GET"])
def api_positions_list():
    filters = _filters_from_args()
    page    = max(1, int(request.args.get("page", 1)))
    per_page= min(200, int(request.args.get("per_page", 50)))
    offset  = (page - 1) * per_page

    # build WHERE
    clauses, params = [], []
    if filters["symbol"]:
        clauses.append("symbol = ?"); params.append(filters["symbol"])
    if filters["direction"]:
        clauses.append("direction = ?"); params.append(filters["direction"])
    if filters["date_from"]:
        clauses.append("close_time >= ?"); params.append(filters["date_from"])
    if filters["date_to"]:
        clauses.append("close_time <= ?"); params.append(filters["date_to"] + " 23:59:59")

    # text search across symbol + notes
    search = request.args.get("search", "").strip()
    if search:
        clauses.append("(symbol LIKE ? OR notes LIKE ?)")
        params += [f"%{search}%", f"%{search}%"]

    # pnl filter
    pnl_side = request.args.get("pnl_side", "").strip()
    if pnl_side == "win":
        clauses.append("realized_pnl > 0")
    elif pnl_side == "loss":
        clauses.append("realized_pnl < 0")

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    conn  = get_conn()
    total = conn.execute(f"SELECT COUNT(*) FROM positions {where}", params).fetchone()[0]
    rows  = [dict(r) for r in conn.execute(
        f"""SELECT id, symbol, direction, open_time, close_time, duration_minutes,
                   entry_price, close_price, size_contracts, size_usdt,
                   position_pnl, realized_pnl, total_fees, notes, tags, is_manual
            FROM positions {where}
            ORDER BY close_time DESC
            LIMIT ? OFFSET ?""",
        params + [per_page, offset]
    ).fetchall()]
    conn.close()

    return _ok({
        "positions": rows,
        "total":     total,
        "page":      page,
        "per_page":  per_page,
        "pages":     (total + per_page - 1) // per_page,
    })


@app.route("/api/positions", methods=["POST"])
def api_positions_create():
    """Create a manually entered trade."""
    d = request.get_json(force=True)
    required = ["symbol", "direction", "open_time", "close_time",
                "entry_price", "close_price", "size_usdt", "realized_pnl"]
    for field in required:
        if not d.get(field) and d.get(field) != 0:
            return _err(f"Missing field: {field}")

    import re as _re
    symbol    = d["symbol"].strip().upper()
    base_asset= _re.sub(r'USDT$', '', symbol)

    # calculate duration
    try:
        fmt = "%Y-%m-%d %H:%M:%S"
        dur = int((datetime.strptime(d["close_time"], fmt) -
                   datetime.strptime(d["open_time"],  fmt)).total_seconds() / 60)
    except Exception:
        dur = None

    total_fees = float(d.get("total_fees", 0) or 0)

    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        INSERT INTO positions
          (symbol, base_asset, direction, margin_mode,
           open_time, close_time, duration_minutes,
           entry_price, close_price, size_contracts, size_usdt,
           position_pnl, realized_pnl,
           opening_fee, closing_fee, total_fees,
           notes, tags, is_manual)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
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
        total_fees,
        d.get("notes", ""),
        d.get("tags", ""),
    ))
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return _ok({"id": new_id}), 201


@app.route("/api/positions/<int:pos_id>", methods=["GET"])
def api_position_get(pos_id):
    conn = get_conn()
    row  = conn.execute("SELECT * FROM positions WHERE id = ?", (pos_id,)).fetchone()
    conn.close()
    if not row:
        return _err("Not found", 404)
    return _ok(dict(row))


@app.route("/api/positions/<int:pos_id>", methods=["PUT"])
def api_position_update(pos_id):
    """Update editable fields: notes, tags."""
    d    = request.get_json(force=True)
    conn = get_conn()
    row  = conn.execute("SELECT id FROM positions WHERE id = ?", (pos_id,)).fetchone()
    if not row:
        conn.close()
        return _err("Not found", 404)

    # Only allow safe editable fields
    editable = ["notes", "tags", "entry_price", "close_price",
                "size_usdt", "realized_pnl", "total_fees",
                "open_time", "close_time", "direction"]
    sets, vals = [], []
    for key in editable:
        if key in d:
            sets.append(f"{key} = ?")
            vals.append(d[key])
    if not sets:
        conn.close()
        return _err("No updatable fields provided")

    sets.append("updated_at = datetime('now')")
    vals.append(pos_id)
    conn.execute(f"UPDATE positions SET {', '.join(sets)} WHERE id = ?", vals)
    conn.commit()
    conn.close()
    return _ok({"updated": pos_id})


@app.route("/api/positions/<int:pos_id>", methods=["DELETE"])
def api_position_delete(pos_id):
    conn = get_conn()
    conn.execute("DELETE FROM positions WHERE id = ?", (pos_id,))
    conn.commit()
    conn.close()
    return _ok({"deleted": pos_id})


# ── dashboard ──────────────────────────────────────────────────────────────────

@app.route("/api/dashboard/kpis")
def api_dashboard_kpis():
    try:
        data = get_dashboard_kpis(filters=_filters_from_args())
        return _ok(data)
    except Exception as e:
        traceback.print_exc()
        return _err(str(e), 500)


# ── deep dive ──────────────────────────────────────────────────────────────────

@app.route("/api/analytics/deep")
def api_analytics_deep():
    try:
        data = get_deep_stats(filters=_filters_from_args())
        return _ok(data)
    except Exception as e:
        traceback.print_exc()
        return _err(str(e), 500)


# ── AI advisor ─────────────────────────────────────────────────────────────────

@app.route("/api/ai/analyze", methods=["POST"])
def api_ai_analyze():
    try:
        filters = request.get_json(force=True) if request.content_length else {}
        result  = ai_advisor.analyze(filters=filters or {})
        return _ok(result)
    except Exception as e:
        traceback.print_exc()
        return _err(str(e), 500)


# ── helpers ────────────────────────────────────────────────────────────────────

@app.route("/api/symbols")
def api_symbols():
    conn  = get_conn()
    rows  = conn.execute(
        "SELECT DISTINCT symbol FROM positions ORDER BY symbol ASC"
    ).fetchall()
    conn.close()
    return _ok([r[0] for r in rows])


@app.route("/api/wallet/history")
def api_wallet_history():
    conn = get_conn()
    rows = [dict(r) for r in conn.execute("""
        SELECT date, wallet_balance
        FROM wallet_snapshots
        WHERE wallet_balance IS NOT NULL
        ORDER BY date ASC
    """).fetchall()]
    conn.close()
    # downsample
    step = max(1, len(rows) // 300)
    return _ok(rows[::step])


# ── Bitget live sync ────────────────────────────────────────────────────────────

@app.route("/api/sync", methods=["POST"])
def api_sync():
    """Trigger an immediate sync from the Bitget API. Runs synchronously."""
    try:
        result = bitget_sync.run_sync()
        if "error" in result:
            return _err(result["error"])
        return _ok(result)
    except Exception as e:
        traceback.print_exc()
        return _err(str(e), 500)


@app.route("/api/calls/analyze", methods=["POST"])
def api_calls_analyze():
    """
    Analyze a trade call. Accepts JSON:
      { call_text, image_b64 (optional), image_type (optional) }
    Fetches current account equity from Bitget to calculate position sizing.
    """
    try:
        body       = request.get_json(force=True)
        call_text  = (body.get("call_text") or "").strip()
        if not call_text:
            return _err("call_text is required")
        image_b64  = body.get("image_b64")
        image_type = body.get("image_type", "image/png")

        # Live equity for accurate position sizing
        try:
            eq_data = bitget_client.get_account_equity()
            equity  = float(eq_data.get("accountEquity") or eq_data.get("available") or 1000)
        except Exception:
            equity  = 1000.0   # fallback if API unreachable

        result = ai_call_analyzer.analyze_call(
            call_text      = call_text,
            account_equity = equity,
            image_b64      = image_b64,
            image_type     = image_type,
        )
        return _ok(result)
    except Exception as e:
        traceback.print_exc()
        return _err(str(e), 500)


@app.route("/api/calls/saved", methods=["GET"])
def api_calls_saved():
    """List all saved call analyses, newest first."""
    conn = get_conn()
    rows = [dict(r) for r in conn.execute("""
        SELECT id, symbol, direction, trade_type, setup_score, setup_label,
               rr_ratio, has_dca, has_candle_close_sl, sl_price, tp1_price, tp2_price,
               entry_price, dca_price, avg_entry, total_notional, risk_pct,
               status, matched_at, created_at
        FROM analyzed_calls ORDER BY created_at DESC
    """).fetchall()]
    conn.close()
    return _ok(rows)


@app.route("/api/calls/save", methods=["POST"])
def api_calls_save():
    """Save a call analysis result to the DB."""
    d    = request.get_json(force=True)
    sz   = d.get("_sizing", {})
    sq   = d.get("setup_quality", {})
    rr   = d.get("risk_reward", {})
    bs   = d.get("bitget_settings", {})
    sl_b = bs.get("stop_loss", {})
    tp1  = bs.get("take_profit_1", {})
    tp2  = bs.get("take_profit_2", {})

    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        INSERT INTO analyzed_calls
          (symbol, direction, call_text, entry_price, dca_price, sl_price,
           tp1_price, tp2_price, avg_entry, total_notional, margin_needed,
           risk_pct, risk_amount, leverage, has_dca, has_candle_close_sl,
           setup_score, setup_label, rr_ratio, trade_type,
           sl_warning, entry_timing, analysis_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        d.get("symbol"), d.get("direction"),
        d.get("_call_text", ""),
        sz.get("entry_price"), sz.get("dca_price"), sz.get("sl_price") or (float(sl_b.get("price") or 0) or None),
        float(tp1.get("price") or 0) or None,
        float(tp2.get("price") or 0) or None,
        sz.get("avg_entry"), sz.get("total_notional_usdt"), sz.get("margin_needed_usdt"),
        sz.get("risk_pct"), sz.get("risk_amount_usdt"), sz.get("leverage"),
        1 if d.get("has_dca") else 0,
        1 if d.get("has_candle_close_sl") else 0,
        sq.get("score"), sq.get("label"),
        rr.get("ratio"), d.get("trade_type"),
        d.get("sl_warning"), d.get("entry_timing"),
        json.dumps(d),
    ))
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return _ok({"id": new_id}), 201


@app.route("/api/calls/check-matches")
def api_calls_check_matches():
    """
    Compare live open positions against saved (unmatched) calls.
    Returns list of {call, position_symbol} for any symbol+direction match.
    """
    try:
        positions = bitget_client.get_open_positions()
    except Exception as e:
        return _err(str(e), 500)

    conn  = get_conn()
    calls = [dict(r) for r in conn.execute("""
        SELECT id, symbol, direction, trade_type, setup_score, setup_label,
               rr_ratio, sl_price, tp1_price, tp2_price, entry_price, avg_entry,
               has_dca, has_candle_close_sl, sl_warning, entry_timing,
               risk_pct, total_notional, status, created_at
        FROM analyzed_calls WHERE status = 'saved'
    """).fetchall()]
    conn.close()

    matches = []
    for pos in positions:
        for call in calls:
            if (call["symbol"]    == pos["symbol"] and
                call["direction"] == pos["direction"]):
                matches.append({"call": call, "position": pos})
    return _ok(matches)


@app.route("/api/calls/<int:call_id>/confirm-match", methods=["POST"])
def api_calls_confirm_match(call_id):
    """Mark a saved call as matched to an open position."""
    conn = get_conn()
    conn.execute(
        "UPDATE analyzed_calls SET status='matched', matched_at=datetime('now') WHERE id=?",
        (call_id,)
    )
    conn.commit()
    conn.close()
    return _ok({"matched": call_id})


@app.route("/api/calls/<int:call_id>/dismiss", methods=["POST"])
def api_calls_dismiss(call_id):
    """Dismiss a match (not this trade) — keeps call as 'saved' but won't re-prompt."""
    conn = get_conn()
    conn.execute("UPDATE analyzed_calls SET status='dismissed' WHERE id=?", (call_id,))
    conn.commit()
    conn.close()
    return _ok({"dismissed": call_id})


@app.route("/api/calls/<int:call_id>/close", methods=["POST"])
def api_calls_close(call_id):
    """Mark a matched call as closed (trade exited)."""
    conn = get_conn()
    conn.execute("UPDATE analyzed_calls SET status='closed' WHERE id=?", (call_id,))
    conn.commit()
    conn.close()
    return _ok({"closed": call_id})


@app.route("/api/calls/<int:call_id>", methods=["DELETE"])
def api_calls_delete(call_id):
    conn = get_conn()
    conn.execute("DELETE FROM analyzed_calls WHERE id=?", (call_id,))
    conn.commit()
    conn.close()
    return _ok({"deleted": call_id})


@app.route("/api/live/positions")
def api_live_positions():
    """Real-time open positions from Bitget API (never from DB — always live)."""
    try:
        positions = bitget_client.get_open_positions()
        equity    = bitget_client.get_account_equity()
        return _ok({"positions": positions, "equity": equity})
    except Exception as e:
        traceback.print_exc()
        return _err(str(e), 500)


@app.route("/api/live/analyze", methods=["POST"])
def api_live_analyze():
    """Run Claude AI analysis on a single open position sent in the request body."""
    try:
        position = request.get_json(force=True)
        if not position or not position.get("symbol"):
            return _err("position data with symbol required")
        result = ai_live_trade.analyze_position(position)
        return _ok(result)
    except Exception as e:
        traceback.print_exc()
        return _err(str(e), 500)


@app.route("/api/sync/status")
def api_sync_status():
    """Return current sync state + last account equity from DB settings."""
    status = bitget_sync.get_status()
    conn   = get_conn()
    bitget_sync._ensure_settings_table(conn)
    status["account_equity"]    = conn.execute(
        "SELECT value FROM settings WHERE key='account_equity'"
    ).fetchone()
    status["available_balance"] = conn.execute(
        "SELECT value FROM settings WHERE key='available_balance'"
    ).fetchone()
    status["last_sync_ms"] = conn.execute(
        "SELECT value FROM settings WHERE key='last_sync_ms'"
    ).fetchone()
    conn.close()
    # sqlite3.Row to plain value
    for k in ("account_equity", "available_balance", "last_sync_ms"):
        if status[k]:
            status[k] = status[k][0]
    return _ok(status)


# ── run ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()

    # Auto-import CSV data if DB is empty
    conn = get_conn()
    count = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    conn.close()
    if count == 0:
        csv_files = [f for f in os.listdir(DATA_DIR) if f.endswith(".csv")]
        if csv_files:
            print(f"[Startup] DB empty, auto-importing {len(csv_files)} CSV files from data/")
            conn = get_conn()
            import_folder(DATA_DIR, conn)
            conn.close()

    # Start live Bitget sync in background (syncs every 15 minutes)
    bitget_sync.start_background_sync()

    port = int(os.environ.get("PORT", 8082))
    print(f"[App] Trading Journal running on http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
