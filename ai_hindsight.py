"""
ai_hindsight.py — Retroactive trade analysis.

For each of the last N closed trades, reconstructs the technical picture
visible at entry time and asks Claude:
  "Would you have recommended entering this trade? Score it 1-10."

Claude scores BLIND — the actual outcome is not revealed. After scoring,
the server computes a comparison: actual P&L vs following-recommendations P&L.

Pipeline per trade:
  1. Fetch 4H + 1D candles ending at the trade's open_time (historical snapshot)
  2. Compute indicators on that historical slice
  3. Ask Claude to score the setup and state ENTER/SKIP
  4. Store result in trade_hindsight table
  5. Compute hypothetical P&L and signal-accuracy verdict

Signal accuracy verdicts (relative to score ≥ 7 as "ENTER" threshold):
  TP — True Positive:  would enter, trade won
  FP — False Positive: would enter, trade lost
  TN — True Negative:  would skip, trade lost (good skip)
  FN — False Negative: would skip, trade won  (missed winner)
  NEUTRAL — score 5-6 (neither strong enter nor strong skip)
"""

import datetime
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic

from database import db_conn
from helpers import strip_fence
import chart_context
import ai_rulebook

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = "claude-haiku-4-5-20251001"   # Haiku sufficient for retroactive scoring
ENTER_THRESHOLD = 7   # score ≥ this = ENTER recommendation

# ── Batch scan state ───────────────────────────────────────────────────────────

_state: dict = {
    "status":     "idle",
    "progress":   0,
    "total":      0,
    "completed_at": None,
    "duration_sec": None,
    "error":      None,
}
_state_lock = threading.Lock()


def get_state() -> dict:
    with _state_lock:
        return dict(_state)


def _update(**kw):
    with _state_lock:
        _state.update(kw)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _to_ms(iso_str: str) -> int:
    """Convert ISO datetime string (assumed UTC) to Unix milliseconds."""
    dt = datetime.datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp() * 1000)


def _symbol_history_before(symbol: str, before_iso: str, conn) -> dict:
    """Closed-trade stats on this symbol BEFORE the given date (no lookahead).
    Both open_time and close_time must be before the entry date to prevent
    any data from overlapping trades leaking into the analysis."""
    rows = conn.execute("""
        SELECT realized_pnl FROM positions
        WHERE symbol = ? AND open_time < ? AND close_time < ?
        ORDER BY close_time DESC LIMIT 20
    """, (symbol, before_iso, before_iso)).fetchall()
    if not rows:
        return {"trades": 0}
    pnls = [r[0] for r in rows if r[0] is not None]
    wins = [p for p in pnls if p > 0]
    return {
        "trades":       len(rows),
        "win_rate_pct": round(len(wins) / len(pnls) * 100, 1) if pnls else 0,
        "total_pnl":    round(sum(pnls), 2),
    }


# ── Prompt ─────────────────────────────────────────────────────────────────────

def _build_prompt(trade: dict, ctx: dict, conf: dict, history: dict,
                  rulebook_str: str) -> str:
    sym       = trade["symbol"]
    direction = trade["direction"]
    entry_px  = trade.get("entry_price", 0)
    open_time = trade.get("open_time", "")[:16]  # "YYYY-MM-DD HH:MM"

    inds_4h = ctx.get("4H", {}).get("indicators", {})
    sr_4h   = inds_4h.get("support_resistance", [])
    sr_text = "\n".join(
        f"  {l['type'].upper():12s} {l['price']:.6g} "
        f"(strength {l.get('strength',1)}, {l.get('touches',1)} touches)"
        for l in sorted(sr_4h, key=lambda x: -x.get("touches", 1))[:6]
    ) or "  None detected"

    pt_4h = ctx.get("4H", {}).get("prompt_text", "No 4H data")
    pt_1d = ctx.get("1D", {}).get("prompt_text", "No 1D data")
    conf_line = (
        f"{conf['label']} ({conf['score']:+d}/{conf['max']} — "
        f"{conf['bullish']} bullish / {conf['bearish']} bearish signals)"
    ) if conf else "N/A"

    hist_txt  = json.dumps(history) if history.get("trades") else "No prior trades on this symbol"
    rb_block  = f"\nTRADER RULEBOOK (patterns known before this trade):\n{rulebook_str}\n" if rulebook_str else ""

    return f"""You are reviewing a historical trade setup AS IT APPEARED AT ENTRY TIME.

IMPORTANT: This is a hindsight review. Score the setup BLIND — pretend you have NOT seen what happened after entry. Your goal is to evaluate the quality of the setup at the moment the trader entered.

TRADE BEING REVIEWED:
Symbol:    {sym}
Direction: {direction} (what the trader did)
Entry time (UTC): {open_time}
Entry price: {entry_px}

TECHNICAL PICTURE AT ENTRY TIME (reconstructed from historical candles):
{pt_4h}
{pt_1d}

CONFLUENCE AT ENTRY: {conf_line}

KEY S/R LEVELS AT ENTRY (4H):
{sr_text}

TRADER'S HISTORY ON {sym} BEFORE THIS TRADE:
{hist_txt}
{rb_block}
NOTE: Historical funding rates / Fear & Greed are unavailable — base your score on technicals only.

YOUR TASK:
1. Score this setup 1-10 as if you were seeing it live at entry time
2. State ENTER or SKIP (use score ≥ 7 as ENTER threshold)
3. State which direction you would recommend (may differ from what trader did)
4. If ENTER: provide your recommended entry zone, stop loss, TP1, TP2
5. If SKIP: one sentence explaining why

Respond with ONLY valid JSON (no markdown, no code fences):

If ENTER:
{{"setup_score":8,"setup_label":"Strong","would_enter":true,"rec_direction":"{direction}",
  "entry_zone":{{"low":0.0,"high":0.0,"rationale":"one sentence"}},
  "rec_sl":0.0,"sl_rationale":"structural reason","rec_tp1":0.0,"rec_tp2":0.0,"rec_rr":"1:X.X",
  "key_conditions":["condition 1","condition 2","condition 3"],
  "risks":["risk 1","risk 2"],
  "summary":"2-3 sentence honest assessment of the setup quality at entry time"}}

If SKIP:
{{"setup_score":4,"setup_label":"Weak","would_enter":false,"rec_direction":null,
  "skip_reason":"one sentence explaining why this was not worth entering",
  "key_conditions":[],"risks":[],"summary":"brief assessment"}}"""


# ── Core per-trade analysis ────────────────────────────────────────────────────

def _analyze_one(trade: dict, rulebook_str: str) -> dict | None:
    """Analyze one trade retroactively. Opens its own DB connection."""
    try:
        end_ms = _to_ms(trade["open_time"])
        ctx    = chart_context.get_historical_context(trade["symbol"], ["4H", "1D"], end_ms)
        conf   = chart_context.confluence_score(trade["symbol"], ["4H", "1D"], ctx=ctx)

        with db_conn() as conn:
            hist = _symbol_history_before(trade["symbol"], trade["open_time"], conn)

        prompt  = _build_prompt(trade, ctx, conf, hist, rulebook_str)
        client  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model=MODEL, max_tokens=512,
            messages=[{"role": "user", "content": prompt}]
        )
        result = json.loads(strip_fence(message.content[0].text.strip()))
        result["_input_tokens"]  = message.usage.input_tokens
        result["_output_tokens"] = message.usage.output_tokens
        return result
    except Exception:
        return None


def _compute_comparison(result: dict, trade: dict) -> dict:
    """
    Compute hypothetical P&L and signal verdict from the recommendation.

    hypothetical_pnl:
      SKIP (score < ENTER_THRESHOLD)       → 0 (stayed out)
      ENTER + direction matches actual     → actual_pnl (same outcome)
      ENTER + direction conflicts          → 0 (conflict, stay out)
      Score 5–6 (neutral)                  → actual_pnl (no strong signal)

    verdict (signal accuracy):
      TP — score ≥ ENTER_THRESHOLD, direction match, trade profitable
      FP — score ≥ ENTER_THRESHOLD, direction match, trade lost
      TN — score < ENTER_THRESHOLD, trade lost (correct skip)
      FN — score < ENTER_THRESHOLD, trade profitable (missed winner)
      NEUTRAL — score 5–6
    """
    actual_pnl     = float(trade.get("realized_pnl") or 0)
    score          = result.get("setup_score", 5)
    would_enter    = result.get("would_enter", score >= ENTER_THRESHOLD)
    rec_dir        = (result.get("rec_direction") or "").lower()
    actual_dir     = (trade.get("direction") or "").lower()
    direction_match = (rec_dir == actual_dir) if rec_dir else True

    # hypothetical P&L
    if score < 5:
        hyp_pnl = 0.0
    elif score <= 6:
        hyp_pnl = actual_pnl        # neutral, no filter
    elif would_enter and direction_match:
        hyp_pnl = actual_pnl        # entered same trade
    elif would_enter and not direction_match:
        hyp_pnl = 0.0               # conflict, skip
    else:
        hyp_pnl = 0.0               # SKIP

    # verdict
    if score <= 6:
        verdict = "NEUTRAL"
    elif would_enter and direction_match and actual_pnl > 0:
        verdict = "TP"
    elif would_enter and direction_match and actual_pnl <= 0:
        verdict = "FP"
    elif not (would_enter and direction_match) and actual_pnl <= 0:
        verdict = "TN"
    elif not (would_enter and direction_match) and actual_pnl > 0:
        verdict = "FN"
    else:
        verdict = "NEUTRAL"

    return {
        "hypothetical_pnl": round(hyp_pnl, 2),
        "verdict":          verdict,
        "direction_match":  1 if direction_match else 0,
    }


def _save_result(position_id: int, result: dict, comparison: dict, actual_pnl: float, conn):
    ent = result.get("entry_zone") or {}
    conn.execute("""
        INSERT OR REPLACE INTO trade_hindsight
        (position_id, analyzed_at, setup_score, setup_label, would_enter,
         rec_direction, direction_match, rec_entry_low, rec_entry_high,
         rec_sl, rec_tp1, rec_tp2, rec_rr, key_conditions, risks, skip_reason,
         actual_pnl, hypothetical_pnl, verdict, analysis_json, input_tokens, output_tokens)
        VALUES (?,datetime('now'),?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        position_id,
        result.get("setup_score"),
        result.get("setup_label"),
        1 if result.get("would_enter") else 0,
        result.get("rec_direction"),
        comparison["direction_match"],
        ent.get("low"),  ent.get("high"),
        result.get("rec_sl"),  result.get("rec_tp1"),  result.get("rec_tp2"),
        result.get("rec_rr"),
        json.dumps(result.get("key_conditions") or []),
        json.dumps(result.get("risks") or []),
        result.get("skip_reason"),
        actual_pnl,
        comparison["hypothetical_pnl"],
        comparison["verdict"],
        json.dumps(result),
        result.get("_input_tokens"),
        result.get("_output_tokens"),
    ))
    conn.commit()


# ── Batch thread ───────────────────────────────────────────────────────────────

def _batch_thread(n: int):
    t0 = time.time()
    _update(status="running", progress=0, total=n, error=None)

    try:
        with db_conn() as conn:
            trades = [dict(r) for r in conn.execute("""
                SELECT id, symbol, direction, open_time, entry_price, realized_pnl
                FROM positions
                ORDER BY close_time DESC
                LIMIT ?
            """, (n,)).fetchall()]
            rulebook_str = ai_rulebook.get_rulebook_for_prompt(conn)

        _update(total=len(trades))

        done = 0
        with ThreadPoolExecutor(max_workers=5) as ex:
            fs = {ex.submit(_analyze_one, trade, rulebook_str): trade for trade in trades}

            for f in as_completed(fs):
                trade = fs[f]
                done += 1
                _update(progress=done)
                try:
                    result = f.result()
                except Exception:
                    continue

                if result is None:
                    continue

                comp = _compute_comparison(result, trade)
                with db_conn() as conn:
                    _save_result(trade["id"], result, comp,
                                 float(trade.get("realized_pnl") or 0), conn)

        _update(status="completed", completed_at=time.time(),
                duration_sec=round(time.time() - t0, 1))

    except Exception as e:
        _update(status="error", error=str(e),
                completed_at=time.time(), duration_sec=round(time.time() - t0, 1))


# ── Public API ─────────────────────────────────────────────────────────────────

def start_batch(n: int = 50) -> bool:
    """Start batch analysis in background. Returns False if already running."""
    with _state_lock:
        if _state["status"] == "running":
            return False
    t = threading.Thread(target=_batch_thread, args=(n,), daemon=True)
    t.start()
    return True


def get_results(limit: int = 100) -> dict:
    """
    Fetch stored hindsight results + compute summary comparison metrics.
    Returns dict with {rows, summary}.
    """
    with db_conn() as conn:
        rows = [dict(r) for r in conn.execute("""
            SELECT h.*, p.symbol, p.direction, p.open_time, p.close_time,
                   p.entry_price, p.close_price, p.duration_minutes, p.setup_type,
                   p.size_usdt
            FROM trade_hindsight h
            JOIN positions p ON p.id = h.position_id
            ORDER BY p.close_time DESC
            LIMIT ?
        """, (limit,)).fetchall()]

    if not rows:
        return {"rows": [], "summary": None}

    actual_pnls = [r["actual_pnl"] or 0 for r in rows]
    hyp_pnls    = [r["hypothetical_pnl"] or 0 for r in rows]
    scores      = [r["setup_score"] or 0 for r in rows]

    n = len(rows)
    actual_winners  = sum(1 for p in actual_pnls if p > 0)
    hyp_winners     = sum(1 for p in hyp_pnls if p > 0)
    hyp_non_zero    = sum(1 for p in hyp_pnls if p != 0)

    # Signal accuracy (only on strong signals, score != 5-6)
    signal_rows = [r for r in rows if r["verdict"] not in ("NEUTRAL", None)]
    tp = sum(1 for r in signal_rows if r["verdict"] == "TP")
    fp = sum(1 for r in signal_rows if r["verdict"] == "FP")
    tn = sum(1 for r in signal_rows if r["verdict"] == "TN")
    fn = sum(1 for r in signal_rows if r["verdict"] == "FN")
    sig_total = tp + fp + tn + fn
    sig_accuracy = round((tp + tn) / sig_total * 100, 1) if sig_total else None

    high_conf = [r for r in rows if (r["setup_score"] or 0) >= 8 and r.get("direction_match")]
    skipped   = [r for r in rows if not r.get("would_enter") and (r["setup_score"] or 0) < 5]
    avg_score_wins   = round(sum(s for s, p in zip(scores, actual_pnls) if p > 0) / max(actual_winners, 1), 1)
    avg_score_losses = round(sum(s for s, p in zip(scores, actual_pnls) if p <= 0) / max(n - actual_winners, 1), 1)

    summary = {
        "total":                 n,
        "actual_win_rate":       round(actual_winners / n * 100, 1) if n else 0,
        "actual_total_pnl":      round(sum(actual_pnls), 2),
        "hyp_total_pnl":         round(sum(hyp_pnls), 2),
        "hyp_win_rate":          round(hyp_winners / hyp_non_zero * 100, 1) if hyp_non_zero else 0,
        "hyp_trades_taken":      hyp_non_zero,
        "hyp_trades_skipped":    n - hyp_non_zero,
        "skipped_pnl":           round(sum(r["actual_pnl"] or 0 for r in skipped), 2),
        "high_conf_count":       len(high_conf),
        "high_conf_win_rate":    round(sum(1 for r in high_conf if (r["actual_pnl"] or 0) > 0) / max(len(high_conf), 1) * 100, 1),
        "signal_accuracy":       sig_accuracy,
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "avg_score_winners":     avg_score_wins,
        "avg_score_losers":      avg_score_losses,
        "avg_score_all":         round(sum(scores) / n, 1) if n else 0,
    }

    return {"rows": rows, "summary": summary}
