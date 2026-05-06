"""
app.py — Flask web application for the Crypto Trading Journal.

Routes:
  GET  /                         → serve SPA (index.html)
  POST /api/import               → upload & import a Bitget CSV export folder/zip
  GET  /api/positions            → list positions with filters + pagination
  POST /api/positions            → create a manual position
  GET  /api/positions/<id>       → single position detail
  PUT  /api/positions/<id>       → update notes / tags / setup_type / call_id
  DELETE /api/positions/<id>     → delete a position
  POST /api/positions/<id>/grade → auto-grade execution quality via Claude
  GET  /api/dashboard/kpis       → dashboard KPI data
  GET  /api/analytics/deep       → deep dive stats (incl. by_setup, by_grade)
  GET  /api/analytics/rr         → planned vs realized R:R for linked trades
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
from werkzeug.utils import secure_filename

from database     import init_db, get_conn
from importer     import import_folder
from analytics    import get_dashboard_kpis, get_deep_stats, get_rr_analysis, get_heatmap_data
import ai_advisor
import ai_live_trade
import ai_call_analyzer
import ai_trade_grader
import ai_pattern_detector
import market_context
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
            fname = secure_filename(f.filename) or "upload.zip"
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
        return _err("Import failed — check server logs for details")
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

    # setup type filter
    setup = request.args.get("setup", "").strip()
    allowed_setups = {"Breakout", "Pullback", "Trend Continuation",
                      "Range Fade", "Reversal", "News/Event", "Other"}
    if setup == "untagged":
        clauses.append("(setup_type IS NULL OR setup_type = '')")
    elif setup in allowed_setups:
        clauses.append("setup_type = ?")
        params.append(setup)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    conn  = get_conn()
    total = conn.execute(f"SELECT COUNT(*) FROM positions {where}", params).fetchone()[0]
    rows  = [dict(r) for r in conn.execute(
        f"""SELECT id, symbol, direction, open_time, close_time, duration_minutes,
                   entry_price, close_price, size_contracts, size_usdt,
                   position_pnl, realized_pnl, total_fees, notes, tags, is_manual, analyst,
                   setup_type, call_id, execution_grade, execution_grade_reason
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
        total_fees,
        d.get("notes", ""),
        d.get("tags", ""),
        d.get("setup_type", ""),
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
    editable = ["notes", "tags", "analyst", "entry_price", "close_price",
                "size_usdt", "realized_pnl", "total_fees",
                "open_time", "close_time", "direction",
                "setup_type", "call_id"]
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


@app.route("/api/positions/<int:pos_id>/grade", methods=["POST"])
def api_position_grade(pos_id):
    try:
        result = ai_trade_grader.grade_trade(pos_id)
        if "error" in result:
            return _err(result["error"])
        return _ok(result)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


# ── dashboard ──────────────────────────────────────────────────────────────────

@app.route("/api/dashboard/kpis")
def api_dashboard_kpis():
    try:
        data = get_dashboard_kpis(filters=_filters_from_args())
        return _ok(data)
    except Exception as e:
        traceback.print_exc()
        return _err("Internal server error", 500)


# ── deep dive ──────────────────────────────────────────────────────────────────

@app.route("/api/analytics/deep")
def api_analytics_deep():
    try:
        data = get_deep_stats(filters=_filters_from_args())
        return _ok(data)
    except Exception as e:
        traceback.print_exc()
        return _err("Internal server error", 500)


@app.route("/api/market/calendar")
def api_market_calendar():
    try:
        return _ok(market_context.get_economic_calendar())
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@app.route("/api/analytics/heatmap")
def api_analytics_heatmap():
    try:
        conn = get_conn()
        data = get_heatmap_data(conn=conn)
        conn.close()
        return _ok(data)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@app.route("/api/market/context")
def api_market_context():
    try:
        symbols_raw = request.args.get("symbols", "")
        symbols = [s.strip().upper() for s in symbols_raw.split(",") if s.strip()] if symbols_raw else []
        data = market_context.get_market_context(symbols or None)
        return _ok(data)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@app.route("/api/analytics/patterns", methods=["POST"])
def api_analytics_patterns():
    try:
        conn   = get_conn()
        result = ai_pattern_detector.detect_patterns(conn=conn)
        conn.close()
        return _ok(result)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@app.route("/api/analytics/rr")
def api_analytics_rr():
    try:
        conn = get_conn()
        data = get_rr_analysis(conn=conn)
        conn.close()
        return _ok({"items": data})
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


# ── AI advisor ─────────────────────────────────────────────────────────────────

@app.route("/api/ai/analyze", methods=["POST"])
def api_ai_analyze():
    try:
        filters = request.get_json(force=True) if request.content_length else {}
        result  = ai_advisor.analyze(filters=filters or {})
        return _ok(result)
    except Exception as e:
        traceback.print_exc()
        return _err("Internal server error", 500)


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
        return _err("Internal server error", 500)


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

        market_regime = (body.get("market_regime") or "").strip() or None
        result = ai_call_analyzer.analyze_call(
            call_text      = call_text,
            account_equity = equity,
            image_b64      = image_b64,
            image_type     = image_type,
            market_regime  = market_regime,
        )
        return _ok(result)
    except Exception as e:
        traceback.print_exc()
        return _err("Internal server error", 500)


@app.route("/api/calls/saved", methods=["GET"])
def api_calls_saved():
    """List all saved call analyses, newest first."""
    conn = get_conn()
    rows = [dict(r) for r in conn.execute("""
        SELECT id, symbol, direction, trade_type, setup_score, setup_label,
               rr_ratio, has_dca, has_candle_close_sl, sl_price, tp1_price, tp2_price,
               entry_price, dca_price, avg_entry, total_notional, risk_pct,
               status, matched_at, created_at,
               analyst, outcome, outcome_pnl, hit_tp1, hit_tp2, hit_sl, outcome_at
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
           sl_warning, entry_timing, analysis_json, analyst)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
        (d.get("_analyst") or "").strip(),
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
        return _err("Internal server error", 500)

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


@app.route("/api/calls/<int:call_id>", methods=["PATCH"])
def api_calls_patch(call_id):
    """Update editable call fields: analyst, notes."""
    d = request.get_json(force=True)
    editable = ["analyst", "notes"]
    sets, vals = [], []
    for key in editable:
        if key in d:
            sets.append(f"{key} = ?")
            vals.append(d[key])
    if not sets:
        return _err("No updatable fields")
    vals.append(call_id)
    conn = get_conn()
    conn.execute(f"UPDATE analyzed_calls SET {', '.join(sets)} WHERE id = ?", vals)
    conn.commit()
    conn.close()
    return _ok({"updated": call_id})


@app.route("/api/calls/<int:call_id>", methods=["DELETE"])
def api_calls_delete(call_id):
    conn = get_conn()
    conn.execute("DELETE FROM analyzed_calls WHERE id=?", (call_id,))
    conn.commit()
    conn.close()
    return _ok({"deleted": call_id})


@app.route("/api/calls/<int:call_id>/record-outcome", methods=["POST"])
def api_calls_record_outcome(call_id):
    """Record the actual outcome of a trade linked to a saved call."""
    d    = request.get_json(force=True)
    conn = get_conn()
    conn.execute("""
        UPDATE analyzed_calls
        SET outcome     = ?,
            outcome_pnl = ?,
            hit_tp1     = ?,
            hit_tp2     = ?,
            hit_sl      = ?,
            outcome_at  = datetime('now'),
            status      = 'closed'
        WHERE id = ?
    """, (
        d.get("outcome"),
        d.get("outcome_pnl"),
        1 if d.get("hit_tp1") else 0,
        1 if d.get("hit_tp2") else 0,
        1 if d.get("hit_sl") else 0,
        call_id,
    ))
    conn.commit()
    conn.close()
    return _ok({"recorded": call_id})


@app.route("/api/calls/prediction-accuracy")
def api_calls_prediction_accuracy():
    """How well do setup scores predict actual trade outcomes?"""
    conn  = get_conn()
    rows  = [dict(r) for r in conn.execute("""
        SELECT
          CASE WHEN setup_score >= 8 THEN '8-10 Excellent/Strong'
               WHEN setup_score >= 6 THEN '6-7 Good/Moderate'
               WHEN setup_score >= 4 THEN '4-5 Weak/Moderate'
               ELSE '1-3 Poor/Weak' END            AS score_band,
          MIN(setup_score)                          AS min_score,
          COUNT(*)                                  AS total,
          SUM(CASE WHEN outcome='won' THEN 1 ELSE 0 END)    AS wins,
          SUM(CASE WHEN outcome='lost' THEN 1 ELSE 0 END)   AS losses,
          ROUND(AVG(CASE WHEN outcome_pnl IS NOT NULL THEN outcome_pnl END), 2) AS avg_pnl,
          SUM(CASE WHEN hit_tp1=1 THEN 1 ELSE 0 END)        AS tp1_hits
        FROM analyzed_calls
        WHERE outcome IS NOT NULL AND setup_score IS NOT NULL
        GROUP BY score_band
        ORDER BY min_score DESC
    """).fetchall()]
    conn.close()
    for r in rows:
        r["win_rate"] = round(r["wins"] / r["total"] * 100, 1) if r["total"] else 0
    return _ok(rows)


@app.route("/api/calls/<int:call_id>/postmortem")
def api_calls_postmortem(call_id):
    """
    Rule-based post-mortem: compare call attributes against trader's known
    weak patterns and return a list of findings.
    """
    conn  = get_conn()
    call  = conn.execute("SELECT * FROM analyzed_calls WHERE id=?", (call_id,)).fetchone()
    if not call:
        conn.close()
        return _err("Not found", 404)
    call = dict(call)

    from analytics import get_deep_stats
    deep = get_deep_stats(conn=conn)
    conn.close()

    findings = []

    # Direction weakness
    dir_data = {d["direction"]: d for d in deep.get("by_direction", [])}
    direction = call.get("direction", "")
    if direction in dir_data:
        all_wrs = [d["win_rate"] for d in dir_data.values()]
        d = dir_data[direction]
        if d["win_rate"] == min(all_wrs) and d["win_rate"] < 55:
            findings.append(
                f"{direction} is your weaker direction ({d['win_rate']}% WR, "
                f"{'+' if d['total_pnl']>=0 else ''}{d['total_pnl']:.0f} USDT total) — "
                f"consider tighter sizing on {direction.lower()} trades"
            )

    # Symbol is a net loser
    symbol = call.get("symbol", "")
    sym_data = next((s for s in deep.get("by_symbol", []) if s["symbol"] == symbol), None)
    if sym_data and sym_data["total_pnl"] < 0:
        findings.append(
            f"{symbol} is a net loser in your history "
            f"({sym_data['total_pnl']:+.2f} USDT over {sym_data['trade_count']} trades, "
            f"{sym_data['win_rate']}% WR) — recurring trouble spot"
        )

    # Low R:R
    rr = call.get("rr_ratio", "")
    if rr:
        try:
            rr_val = float(str(rr).split(":")[-1])
            if rr_val < 1.5:
                findings.append(
                    f"R:R was {rr} — below the 1:1.5 minimum. Low R:R means losses hurt more than wins help"
                )
        except Exception:
            pass

    # Low setup score entered anyway
    score = call.get("setup_score")
    if score and score < 5 and call.get("status") in ("matched", "closed"):
        findings.append(
            f"Setup score was {score}/10 (below 5) but the trade was entered — "
            f"consider a minimum score threshold before entering"
        )

    # Hold-time pattern from deep stats
    dur_buckets = {b["label"]: b for b in deep.get("duration_buckets", [])}
    long_dur = dur_buckets.get("> 7 days", {})
    if long_dur and long_dur.get("total_pnl", 0) < -500:
        findings.append(
            f"Trades held >7 days total {long_dur['total_pnl']:+.0f} USDT — "
            f"if this trade was held too long, that's a known losing pattern"
        )

    if not findings:
        findings.append("No specific pattern violations detected — this may have been an unforeseeable market move")

    return _ok({"call_id": call_id, "symbol": symbol, "findings": findings})


@app.route("/api/limits", methods=["GET"])
def api_limits_list():
    """List pending limit orders, filtered by status (default: waiting)."""
    status = request.args.get("status", "waiting")
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM pending_limits WHERE status = ? ORDER BY created_at DESC",
        (status,)
    ).fetchall()]
    conn.close()
    return _ok(rows)


@app.route("/api/limits", methods=["POST"])
def api_limits_create():
    """Create a new pending limit order (shadow trade)."""
    d = request.get_json(force=True)
    if not d.get("symbol") or not d.get("limit_price") or not d.get("direction"):
        return _err("symbol, direction, and limit_price are required")
    conn = get_conn()
    cur  = conn.cursor()
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
    conn.close()
    return _ok({"id": new_id}), 201


@app.route("/api/limits/bulk-update", methods=["POST"])
def api_limits_bulk_update():
    """Bulk update multiple pending limits at once (sl_price, tp_prices, call_id, status…)."""
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
    conn = get_conn()
    conn.execute(
        f"UPDATE pending_limits SET {', '.join(sets)} WHERE id IN ({placeholders})",
        vals + ids,
    )
    conn.commit()
    conn.close()
    return _ok({"updated_count": len(ids)})


@app.route("/api/limits/risk-summary")
def api_limits_risk_summary():
    """Total capital exposure if all waiting limits fill."""
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT symbol, direction, limit_price, size_usdt, sl_price FROM pending_limits WHERE status='waiting'"
    ).fetchall()]
    conn.close()
    total_notional = sum(r["size_usdt"] or 0 for r in rows)
    by_symbol = {}
    for r in rows:
        sym = r["symbol"]
        by_symbol[sym] = by_symbol.get(sym, 0) + (r["size_usdt"] or 0)
    return _ok({
        "pending_count":      len(rows),
        "total_notional_usdt": round(total_notional, 2),
        "by_symbol": [{"symbol": k, "notional": round(v, 2)} for k, v in by_symbol.items()],
    })


@app.route("/api/limits/<int:lim_id>", methods=["PATCH"])
def api_limits_update(lim_id):
    """Update a pending limit (status, prices, notes)."""
    d    = request.get_json(force=True)
    conn = get_conn()
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
        conn.close()
        return _err("No updatable fields")
    vals.append(lim_id)
    conn.execute(f"UPDATE pending_limits SET {', '.join(sets)} WHERE id = ?", vals)
    conn.commit()
    conn.close()
    return _ok({"updated": lim_id})


@app.route("/api/limits/<int:lim_id>", methods=["DELETE"])
def api_limits_delete(lim_id):
    conn = get_conn()
    conn.execute("DELETE FROM pending_limits WHERE id = ?", (lim_id,))
    conn.commit()
    conn.close()
    return _ok({"deleted": lim_id})


@app.route("/api/limits/<int:lim_id>/analyze", methods=["POST"])
def api_limits_analyze(lim_id):
    """Run AI risk/setup analysis on a pending limit order."""
    try:
        conn = get_conn()
        lim  = conn.execute("SELECT * FROM pending_limits WHERE id=?", (lim_id,)).fetchone()
        if not lim:
            conn.close()
            return _err("Not found", 404)
        lim = dict(lim)

        other_limits = [dict(r) for r in conn.execute(
            "SELECT symbol, direction, limit_price, size_usdt FROM pending_limits WHERE status='waiting' AND id != ?",
            (lim_id,)
        ).fetchall()]
        conn.close()

        try:
            eq_data        = bitget_client.get_account_equity()
            equity         = float(eq_data.get("accountEquity") or eq_data.get("available") or 1000)
            open_positions = bitget_client.get_open_positions()
        except Exception:
            equity, open_positions = 1000.0, []

        result = ai_call_analyzer.analyze_pending_limit(lim, equity, open_positions, other_limits)

        conn2 = get_conn()
        conn2.execute(
            "UPDATE pending_limits SET analysis_json = ? WHERE id = ?",
            (json.dumps(result), lim_id)
        )
        conn2.commit()
        conn2.close()
        return _ok(result)
    except Exception as e:
        traceback.print_exc()
        return _err("Internal server error", 500)


@app.route("/api/calls/analyst-stats")
def api_calls_analyst_stats():
    """
    Per-analyst stats combining three sources:
      - positions   (analyst field set in journal) → actual trade performance
      - analyzed_calls (analyst set at analysis time) → call quality metrics
      - pending_limits (analyst field set)         → open/waiting trade count
    """
    conn = get_conn()

    # Ground truth: actual closed trades by analyst
    pos_rows = {r["analyst"]: dict(r) for r in conn.execute("""
        SELECT analyst,
               COUNT(*)                                                            AS trade_count,
               SUM(CASE WHEN realized_pnl > 0  THEN 1 ELSE 0 END)                AS wins,
               SUM(CASE WHEN realized_pnl <= 0 THEN 1 ELSE 0 END)                AS losses,
               ROUND(SUM(realized_pnl), 2)                                        AS total_pnl,
               ROUND(AVG(realized_pnl), 2)                                        AS avg_pnl
        FROM positions
        WHERE analyst IS NOT NULL AND analyst != ''
        GROUP BY analyst
    """).fetchall()}

    # Call analysis metrics
    call_rows = {r["analyst"]: dict(r) for r in conn.execute("""
        SELECT analyst,
               COUNT(*)                                                            AS total_analyzed,
               SUM(CASE WHEN status IN ('matched','closed') THEN 1 ELSE 0 END)    AS entered,
               SUM(CASE WHEN outcome='won'  THEN 1 ELSE 0 END)                    AS call_wins,
               SUM(CASE WHEN hit_tp1=1      THEN 1 ELSE 0 END)                    AS tp1_hits,
               SUM(CASE WHEN hit_tp2=1      THEN 1 ELSE 0 END)                    AS tp2_hits,
               SUM(CASE WHEN hit_sl=1       THEN 1 ELSE 0 END)                    AS sl_hits,
               ROUND(AVG(setup_score), 1)                                          AS avg_setup_score
        FROM analyzed_calls
        WHERE analyst IS NOT NULL AND analyst != ''
        GROUP BY analyst
    """).fetchall()}

    # Pending/waiting trades
    lim_rows = {r["analyst"]: dict(r) for r in conn.execute("""
        SELECT analyst,
               COUNT(*) AS pending_count
        FROM pending_limits
        WHERE analyst IS NOT NULL AND analyst != '' AND status = 'waiting'
        GROUP BY analyst
    """).fetchall()}

    all_analysts = set(pos_rows) | set(call_rows) | set(lim_rows)
    result = []
    for analyst in all_analysts:
        p = pos_rows.get(analyst, {})
        c = call_rows.get(analyst, {})
        l = lim_rows.get(analyst, {})
        trade_count   = p.get("trade_count", 0)
        wins          = p.get("wins", 0)
        total_analyzed= c.get("total_analyzed", 0)
        call_wins     = c.get("call_wins", 0)
        sl_hits       = c.get("sl_hits", 0)
        tp1_hits      = c.get("tp1_hits", 0)
        entered       = c.get("entered", 0)

        win_rate      = round(wins / trade_count * 100, 1) if trade_count else 0
        call_outcomes = call_wins + sl_hits
        call_win_rate = round(call_wins / call_outcomes * 100, 1) if call_outcomes else None
        tp1_hit_rate  = round(tp1_hits / total_analyzed * 100, 1) if total_analyzed else None
        conv_rate     = round(entered / total_analyzed * 100, 1) if total_analyzed else None

        # Edge score (0-100): only meaningful with ≥3 closed trades
        if trade_count >= 3:
            wr  = win_rate
            cwr = call_win_rate if call_win_rate is not None else win_rate
            tp1 = tp1_hit_rate  if tp1_hit_rate  is not None else 50.0
            edge_score = round(wr * 0.5 + cwr * 0.3 + tp1 * 0.2)
        else:
            edge_score = None

        result.append({
            "analyst":        analyst,
            "trade_count":    trade_count,
            "wins":           wins,
            "losses":         p.get("losses", 0),
            "win_rate":       win_rate,
            "total_pnl":      p.get("total_pnl", 0),
            "avg_pnl":        p.get("avg_pnl", 0),
            "total_analyzed": total_analyzed,
            "entered":        entered,
            "call_win_rate":  call_win_rate,
            "tp1_hit_rate":   tp1_hit_rate,
            "tp2_hits":       c.get("tp2_hits", 0),
            "sl_hits":        sl_hits,
            "conv_rate":      conv_rate,
            "avg_setup_score":c.get("avg_setup_score"),
            "pending_count":  l.get("pending_count", 0),
            "edge_score":     edge_score,
        })

    # Sort by edge_score (ranked analysts first), then total_pnl
    result.sort(key=lambda x: (x["edge_score"] is None, -(x["edge_score"] or 0), -(x["total_pnl"] or 0)))
    conn.close()
    return _ok(result)


@app.route("/api/live/positions")
def api_live_positions():
    """Real-time open positions from Bitget API (never from DB — always live)."""
    try:
        positions = bitget_client.get_open_positions()
        equity    = bitget_client.get_account_equity()
        return _ok({"positions": positions, "equity": equity})
    except Exception as e:
        traceback.print_exc()
        return _err("Internal server error", 500)


@app.route("/api/live/pending-orders")
def api_live_pending_orders():
    """
    Fetch unfilled limit orders from Bitget + tracked pending_limits for cross-reference.
    Returns:
      bitget_orders  — live orders split into entry/exit lists
      tracked_ids    — set of bitget_order_id values already in pending_limits
    """
    try:
        orders = bitget_client.get_pending_orders()
    except Exception as e:
        traceback.print_exc()
        return _err("Internal server error", 500)

    conn = get_conn()
    tracked = [r[0] for r in conn.execute(
        "SELECT bitget_order_id FROM pending_limits WHERE bitget_order_id IS NOT NULL"
    ).fetchall()]
    conn.close()

    return _ok({
        "bitget_orders": orders,
        "tracked_ids":   tracked,
    })


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
        return _err("Internal server error", 500)


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

    # Schema migrations — add columns that may be missing from older DBs
    _mig_conn = get_conn()
    _cols = [r[1] for r in _mig_conn.execute("PRAGMA table_info(positions)").fetchall()]
    if "analyst" not in _cols:
        _mig_conn.execute("ALTER TABLE positions ADD COLUMN analyst TEXT DEFAULT ''")
        _mig_conn.commit()
    _mig_conn.close()

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
