"""
ai_trade_grader.py — Auto-grade trade execution quality using Claude.

Grades:
  A — Excellent: entry near/better than planned, disciplined exit, strong R:R
  B — Good: minor flaw only (small slippage, slightly early exit while in profit)
  C — Average: one clear flaw (chased entry, moved SL, cut winner too early)
  D — Poor: multiple or severe flaws (no SL, reckless size, avoidable full loss)

For trades linked to an analyst call (positions.call_id), uses planned vs actual
comparison for richer grading. Works on standalone trades too.
"""

import json

import anthropic
from database import get_conn
from helpers import log_token_usage
import market_context


def grade_trade(position_id: int, conn=None) -> dict:
    """
    Grade a closed trade and persist the result.
    Returns {"grade": "A|B|C|D", "reason": str} or {"error": str}.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    try:
        pos = conn.execute("SELECT * FROM positions WHERE id = ?", (position_id,)).fetchone()
        if not pos:
            return {"error": "Position not found"}
        pos = dict(pos)

        call = None
        if pos.get("call_id"):
            row = conn.execute(
                "SELECT * FROM analyzed_calls WHERE id = ?", (pos["call_id"],)
            ).fetchone()
            if row:
                call = dict(row)

        fg     = market_context.get_fear_greed()
        result = _ask_claude(pos, call, fg)

        conn.execute(
            """UPDATE positions
               SET execution_grade = ?, execution_grade_reason = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (result["grade"], result["reason"], position_id),
        )
        conn.commit()
        return result

    except Exception:
        traceback.print_exc()
        return {"error": "Grading failed — see server logs"}
    finally:
        if own_conn:
            conn.close()


def _ask_claude(pos: dict, call: dict | None, fg: dict | None = None) -> dict:
    direction  = pos.get("direction", "")
    entry      = pos.get("entry_price") or 0
    close_p    = pos.get("close_price") or 0
    pnl        = pos.get("realized_pnl") or 0
    size_usdt  = pos.get("size_usdt") or 0
    duration   = pos.get("duration_minutes") or 0
    setup_type = pos.get("setup_type") or "not specified"
    notes      = pos.get("notes") or "none"

    trade_section = (
        f"TRADE:\n"
        f"Symbol: {pos.get('symbol')} | Direction: {direction}\n"
        f"Entry: {entry} | Close: {close_p} | Size: {size_usdt} USDT\n"
        f"Duration: {duration} min | Realized P&L: {pnl} USDT\n"
        f"Setup type: {setup_type} | Notes: {notes}"
    )

    if call:
        p_entry = call.get("entry_price") or 0
        p_sl    = call.get("sl_price") or 0
        p_tp1   = call.get("tp1_price") or 0
        p_tp2   = call.get("tp2_price") or 0
        p_rr    = call.get("rr_ratio") or "unknown"
        outcome = call.get("outcome") or "unknown"
        score   = call.get("setup_score") or "unknown"

        slip_str = "N/A"
        if p_entry and entry:
            slip_str = f"{abs(entry - p_entry) / p_entry * 100:.2f}%"

        real_rr = "N/A"
        if p_sl and p_entry and abs(p_entry - p_sl) > 0:
            risk    = abs(p_entry - p_sl)
            reward  = (close_p - p_entry) if direction == "Long" else (p_entry - close_p)
            real_rr = f"{reward / risk:.2f}R"

        call_section = (
            f"\nLINKED ANALYST CALL:\n"
            f"Planned entry: {p_entry} | Actual entry: {entry} | Entry slippage: {slip_str}\n"
            f"Planned SL: {p_sl} | Planned TP1: {p_tp1} | Planned TP2: {p_tp2}\n"
            f"Planned R:R: {p_rr} | Realized R:R: {real_rr}\n"
            f"Setup score: {score}/10 | Recorded outcome: {outcome}"
        )
    else:
        if entry and close_p:
            move = (close_p - entry) / entry * 100
            if direction == "Short":
                move = -move
            call_section = f"\nNo analyst call linked. Price moved {move:.2f}% from entry to close."
        else:
            call_section = "\nNo analyst call linked."

    fg_block = ""
    if fg and fg.get("ok"):
        fg_block = f"\nMARKET CONTEXT AT TIME OF GRADING:\nFear & Greed Index: {fg['value']}/100 — {fg['classification']}\n"

    prompt = (
        "Grade the EXECUTION QUALITY of this crypto futures trade. "
        "Focus on HOW it was executed, not purely on P&L outcome.\n\n"
        f"{trade_section}\n"
        f"{call_section}\n"
        f"{fg_block}\n"
        "Grading rubric:\n"
        "A — Excellent: entry at/near planned level, exit disciplined (TP hit or clear "
        "rule-based close), risk managed throughout, strong realized R:R\n"
        "B — Good: minor flaw only (slippage <1%, slightly early profitable exit, "
        "small justified plan deviation)\n"
        "C — Average: one significant flaw (chased entry >1-2%, moved SL against rules, "
        "cut winner under 0.5R, took poor R:R setup that luckily won)\n"
        "D — Poor: multiple or severe flaws (no SL set, reckless position size, full "
        "avoidable loss, FOMO entry well outside plan)\n\n"
        "Return ONLY valid JSON — no markdown, no extra text:\n"
        '{"grade": "A|B|C|D", "reason": "2-3 sentences citing specific numbers that justify the grade."}'
    )

    client = anthropic.Anthropic()
    resp   = client.messages.create(
        model      = MODEL,
        max_tokens = 350,
        messages   = [{"role": "user", "content": prompt}],
    )
    cached = getattr(resp.usage, "cache_read_input_tokens", 0) or 0
    log_token_usage("trade_grader", MODEL,
                    resp.usage.input_tokens, resp.usage.output_tokens, cached)
    raw = resp.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    parsed = json.loads(raw)
    grade  = str(parsed.get("grade", "C")).upper()
    if grade not in ("A", "B", "C", "D"):
        grade = "C"
    return {"grade": grade, "reason": parsed.get("reason", "")}
