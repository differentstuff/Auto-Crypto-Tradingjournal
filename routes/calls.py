import json
import traceback

from flask import Blueprint, request

from database import db_conn
from trade_utils import normalize_symbol, normalize_direction
from helpers import _ok, _err
from analytics import get_deep_stats
import ai_call as ai_call_analyzer
import bitget_client
import blofin_client

bp = Blueprint("calls", __name__)


def _safe_float(val, default=None):
    """Parse float from user-supplied input. Returns default on invalid input — never raises."""
    try:
        f = float(val)
        return f if f else default
    except (TypeError, ValueError):
        return default


@bp.route("/api/calls/analyze", methods=["POST"])
def api_calls_analyze():
    try:
        body      = request.get_json(force=True)
        call_text = (body.get("call_text") or "").strip()
        if not call_text:
            return _err("call_text is required")

        # Combine equity across all configured exchanges and collect all open positions
        equity, open_positions = 0.0, []
        try:
            eq_data        = bitget_client.get_account_equity()
            equity        += float(eq_data.get("accountEquity") or eq_data.get("available") or 0)
            open_positions = bitget_client.get_open_positions()
        except Exception:
            pass
        try:
            if blofin_client.is_configured():
                bl_eq   = blofin_client.get_account_equity()
                equity += float(bl_eq.get("equity") or 0)   # sum, not OR
                open_positions += blofin_client.get_open_positions()
        except Exception:
            pass
        if equity == 0:
            equity = 1000.0   # fallback so sizing calc doesn't crash

        result = ai_call_analyzer.analyze_call(
            call_text      = call_text,
            account_equity = equity,
            image_b64      = body.get("image_b64"),
            image_type     = body.get("image_type", "image/png"),
            market_regime  = (body.get("market_regime") or "").strip() or None,
            open_positions = open_positions,
        )
        return _ok(result)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/calls/saved", methods=["GET"])
def api_calls_saved():
    with db_conn() as conn:
        rows = [dict(r) for r in conn.execute("""
            SELECT id, symbol, direction, trade_type, setup_score, setup_label,
                   rr_ratio, has_dca, has_candle_close_sl, sl_price, tp1_price, tp2_price,
                   entry_price, dca_price, avg_entry, total_notional, risk_pct,
                   status, matched_at, created_at,
                   analyst, outcome, outcome_pnl, hit_tp1, hit_tp2, hit_sl, outcome_at
            FROM analyzed_calls ORDER BY created_at DESC
        """).fetchall()]
    return _ok(rows)


@bp.route("/api/calls/save", methods=["POST"])
def api_calls_save():
    d    = request.get_json(force=True)
    sz   = d.get("_sizing", {}) or {}
    sq   = d.get("setup_quality", {}) or {}
    rr   = d.get("risk_reward", {}) or {}
    bs   = d.get("bitget_settings", {}) or {}
    sl_b = bs.get("stop_loss", {}) or {}
    tp1  = bs.get("take_profit_1", {}) or {}
    tp2  = bs.get("take_profit_2", {}) or {}

    # Extract chart PNG before storing JSON (keeps analysis_json lean)
    chart_b64 = d.pop("chart_png_b64", None) or ""

    # Guard NOT NULL columns — symbol/direction must always be present
    symbol    = (d.get("symbol") or "").strip() or "UNKNOWN"
    direction = (d.get("direction") or "Long").strip()

    # Get regime label for storage
    regime_label = None
    try:
        from market_regime import detect_regime
        reg = detect_regime()
        if reg.get("ok"):
            regime_label = reg["label"]
    except Exception:
        pass

    # ml_win_prob requires setup_score which is only known after analysis;
    # write None now — can be backfilled later if needed
    ml_win_prob = None

    with db_conn() as conn:
        cur = conn.cursor()
        con = d.get("_consensus") or {}
        cur.execute("""
            INSERT INTO analyzed_calls
              (symbol, direction, call_text, entry_price, dca_price, sl_price,
               tp1_price, tp2_price, avg_entry, total_notional, margin_needed,
               risk_pct, risk_amount, leverage, has_dca, has_candle_close_sl,
               setup_score, setup_label, rr_ratio, trade_type,
               sl_warning, entry_timing, analysis_json, analyst, cot_reasoning,
               gemini_score, consensus_score, consensus_flag, chart_png_b64,
               regime_label, ml_win_prob)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            symbol, direction,
            d.get("_call_text", ""),
            sz.get("entry_price"), sz.get("dca_price"),
            sz.get("sl_price") or _safe_float(sl_b.get("price")),
            _safe_float(tp1.get("price")),
            _safe_float(tp2.get("price")),
            sz.get("avg_entry"), sz.get("total_notional_usdt"), sz.get("margin_needed_usdt"),
            sz.get("risk_pct"), sz.get("risk_amount_usdt"), sz.get("leverage"),
            1 if d.get("has_dca") else 0,
            1 if d.get("has_candle_close_sl") else 0,
            sq.get("score"), sq.get("label"),
            rr.get("ratio"), d.get("trade_type"),
            d.get("sl_warning"), d.get("entry_timing"),
            json.dumps(d),
            (d.get("_analyst") or "").strip(),
            d.get("thinking") or d.get("cot_reasoning"),
            sq.get("gemini_score"),
            con.get("consensus_score"),
            con.get("flag"),
            chart_b64 or None,
            regime_label,
            ml_win_prob,
        ))
        new_id = cur.lastrowid
        conn.commit()

    return _ok({"id": new_id}), 201


@bp.route("/api/calls/check-matches", methods=["POST"])
def api_calls_check_matches():
    positions = []
    try:
        positions = bitget_client.get_open_positions()
    except Exception:
        pass
    try:
        if blofin_client.is_configured():
            positions += blofin_client.get_open_positions()
    except Exception:
        pass

    with db_conn() as conn:
        # Include 'saved' (needs confirmation) AND 'closed' (position may have reopened)
        calls = [dict(r) for r in conn.execute("""
            SELECT id, symbol, direction, trade_type, setup_score, setup_label,
                   rr_ratio, sl_price, tp1_price, tp2_price, entry_price, avg_entry,
                   has_dca, has_candle_close_sl, sl_warning, entry_timing,
                   risk_pct, total_notional, status, created_at, analyst
            FROM analyzed_calls WHERE status IN ('saved', 'closed')
        """).fetchall()]

    pending   = []   # needs user confirmation
    auto_ids  = []   # (call_id, exchange) — auto-confirm without prompting

    for pos in positions:
        for call in calls:
            if (normalize_symbol(call["symbol"])  == normalize_symbol(pos["symbol"]) and
                    normalize_direction(call["direction"]) == normalize_direction(pos["direction"])):

                # Auto-confirm if: (a) scanner-generated signal, or (b) position closed+reopened
                is_scanner = (call.get("analyst") or "") == "scanner"
                is_closed  = call["status"] == "closed"
                if is_scanner or is_closed:
                    auto_ids.append((call["id"], (pos.get("exchange") or "bitget")))
                else:
                    pending.append({
                        "call":     call,
                        "position": pos,
                        "exchange": pos.get("exchange", "bitget"),
                    })

    # Persist auto-confirmations to DB so the frontend finds them in /api/calls/saved
    if auto_ids:
        with db_conn() as conn:
            for call_id, exchange in auto_ids:
                conn.execute(
                    "UPDATE analyzed_calls "
                    "SET status='matched', matched_at=datetime('now'), exchange=? "
                    "WHERE id=?",
                    (exchange, call_id),
                )
            conn.commit()

    return _ok(pending)


@bp.route("/api/calls/<int:call_id>/confirm-match", methods=["POST"])
def api_calls_confirm_match(call_id):
    d        = request.get_json(silent=True) or {}
    pos_id   = d.get("position_id")
    exchange = (d.get("exchange") or "bitget").lower()
    if exchange not in ("bitget", "blofin"):
        exchange = "bitget"
    with db_conn() as conn:
        # Record exchange so auto-close only fires from the right exchange's sync
        conn.execute(
            "UPDATE analyzed_calls SET status='matched', matched_at=datetime('now'), exchange=? WHERE id=?",
            (exchange, call_id)
        )
        if pos_id:
            conn.execute("UPDATE positions SET call_id=? WHERE id=?", (call_id, pos_id))
            call_row = conn.execute(
                "SELECT trade_type FROM analyzed_calls WHERE id=?", (call_id,)
            ).fetchone()
            if call_row and call_row["trade_type"]:
                conn.execute(
                    "UPDATE positions SET setup_type=? WHERE id=? AND (setup_type IS NULL OR setup_type='')",
                    (call_row["trade_type"], pos_id)
                )
        conn.commit()
    return _ok({"matched": call_id, "position_id": pos_id, "exchange": exchange})


@bp.route("/api/calls/linkable")
def api_calls_linkable():
    """Return calls that can be manually linked to a position (saved, matched, or closed)."""
    with db_conn() as conn:
        rows = [dict(r) for r in conn.execute("""
            SELECT id, symbol, direction, trade_type, setup_score, setup_label,
                   rr_ratio, sl_price, tp1_price, tp2_price, entry_price, status, created_at
            FROM analyzed_calls WHERE status IN ('saved','matched','closed')
            ORDER BY created_at DESC LIMIT 50
        """).fetchall()]
    return _ok(rows)


@bp.route("/api/calls/<int:call_id>/dismiss", methods=["POST"])
def api_calls_dismiss(call_id):
    with db_conn() as conn:
        conn.execute("UPDATE analyzed_calls SET status='dismissed' WHERE id=?", (call_id,))
        conn.commit()
    return _ok({"dismissed": call_id})


@bp.route("/api/calls/<int:call_id>/close", methods=["POST"])
def api_calls_close(call_id):
    with db_conn() as conn:
        conn.execute("UPDATE analyzed_calls SET status='closed' WHERE id=?", (call_id,))
        conn.commit()
    return _ok({"closed": call_id})


@bp.route("/api/calls/<int:call_id>", methods=["PATCH"])
def api_calls_patch(call_id):
    d        = request.get_json(force=True)
    editable = ["analyst", "notes"]
    # Pre-built fragments — column names are hardcoded, never from request data.
    _set_sql = {k: k + " = ?" for k in editable}
    sets = [_set_sql[k] for k in editable if k in d]
    vals = [d[k]         for k in editable if k in d]
    if not sets:
        return _err("No updatable fields")
    vals.append(call_id)
    with db_conn() as conn:
        conn.execute("UPDATE analyzed_calls SET " + ", ".join(sets) + " WHERE id = ?", vals)
        conn.commit()
    return _ok({"updated": call_id})


@bp.route("/api/calls/<int:call_id>", methods=["DELETE"])
def api_calls_delete(call_id):
    with db_conn() as conn:
        conn.execute("DELETE FROM analyzed_calls WHERE id=?", (call_id,))
        conn.commit()
    return _ok({"deleted": call_id})


@bp.route("/api/calls/<int:call_id>/record-outcome", methods=["POST"])
def api_calls_record_outcome(call_id):
    d = request.get_json(force=True)
    with db_conn() as conn:
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
    return _ok({"recorded": call_id})


@bp.route("/api/calls/prediction-accuracy")
def api_calls_prediction_accuracy():
    with db_conn() as conn:
        rows = [dict(r) for r in conn.execute("""
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
    for r in rows:
        r["win_rate"] = round(r["wins"] / r["total"] * 100, 1) if r["total"] else 0
    return _ok(rows)


@bp.route("/api/calls/accuracy-progress")
def api_calls_accuracy_progress():
    from constants import ACCURACY_TARGET
    with db_conn() as conn:
        row = conn.execute("""
            SELECT
                COUNT(*)                                            AS recorded,
                SUM(CASE WHEN outcome = 'won' THEN 1 ELSE 0 END)  AS wins
            FROM analyzed_calls
            WHERE outcome IS NOT NULL
        """).fetchone()
    recorded = row[0] or 0
    wins     = row[1] or 0
    win_rate = round(wins / recorded * 100, 1) if recorded else 0.0
    return _ok({
        "recorded":    recorded,
        "target":      ACCURACY_TARGET,
        "win_rate":    win_rate,
        "remaining":   max(0, ACCURACY_TARGET - recorded),
        "enough_data": recorded >= ACCURACY_TARGET,
    })


@bp.route("/api/calls/<int:call_id>/postmortem")
def api_calls_postmortem(call_id):
    with db_conn() as conn:
        call = conn.execute("SELECT * FROM analyzed_calls WHERE id=?", (call_id,)).fetchone()
        if not call:
            return _err("Not found", 404)
        call = dict(call)
        deep = get_deep_stats(conn=conn)

    findings = []

    dir_data  = {d["direction"]: d for d in deep.get("by_direction", [])}
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

    symbol   = call.get("symbol", "")
    sym_data = next((s for s in deep.get("by_symbol", []) if s["symbol"] == symbol), None)
    if sym_data and sym_data["total_pnl"] < 0:
        findings.append(
            f"{symbol} is a net loser in your history "
            f"({sym_data['total_pnl']:+.2f} USDT over {sym_data['trade_count']} trades, "
            f"{sym_data['win_rate']}% WR) — recurring trouble spot"
        )

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

    score = call.get("setup_score")
    if score and score < 5 and call.get("status") in ("matched", "closed"):
        findings.append(
            f"Setup score was {score}/10 (below 5) but the trade was entered — "
            f"consider a minimum score threshold before entering"
        )

    dur_buckets = {b["label"]: b for b in deep.get("duration_buckets", [])}
    long_dur    = dur_buckets.get("> 7 days", {})
    if long_dur and long_dur.get("total_pnl", 0) < -500:
        findings.append(
            f"Trades held >7 days total {long_dur['total_pnl']:+.0f} USDT — "
            f"if this trade was held too long, that's a known losing pattern"
        )

    if not findings:
        findings.append("No specific pattern violations detected — this may have been an unforeseeable market move")

    return _ok({"call_id": call_id, "symbol": symbol, "findings": findings})


@bp.route("/api/calls/analyst-stats")
def api_calls_analyst_stats():
    with db_conn() as conn:
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

        trade_count    = p.get("trade_count", 0)
        wins           = p.get("wins", 0)
        total_analyzed = c.get("total_analyzed", 0)
        call_wins      = c.get("call_wins", 0)
        sl_hits        = c.get("sl_hits", 0)
        tp1_hits       = c.get("tp1_hits", 0)
        entered        = c.get("entered", 0)

        win_rate      = round(wins / trade_count * 100, 1) if trade_count else 0
        call_outcomes = call_wins + sl_hits
        call_win_rate = round(call_wins / call_outcomes * 100, 1) if call_outcomes else None
        tp1_hit_rate  = round(tp1_hits / total_analyzed * 100, 1) if total_analyzed else None
        conv_rate     = round(entered / total_analyzed * 100, 1) if total_analyzed else None

        if trade_count >= 3:
            cwr        = call_win_rate if call_win_rate is not None else win_rate
            tp1        = tp1_hit_rate  if tp1_hit_rate  is not None else 50.0
            edge_score = round(win_rate * 0.5 + cwr * 0.3 + tp1 * 0.2)
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

    result.sort(key=lambda x: (x["edge_score"] is None, -(x["edge_score"] or 0), -(x["total_pnl"] or 0)))
    return _ok(result)
