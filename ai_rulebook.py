"""
ai_rulebook.py — Personalised trader rulebook derived from trade history.

Self-learning loop:
  1. Collect stats from DB (patterns, calibration, symbol performance)
  2. Claude synthesises 5-10 actionable rules grounded in real numbers
  3. Rules stored in trader_rulebook table
  4. Injected into every AI prompt — Claude knows your edge leaks before it speaks

Three helpers for prompt injection:
  get_rulebook_for_prompt(conn)     → rules block for live trade + call analyzer
  get_calibration_for_prompt(conn)  → score accuracy block for call analyzer
  get_similar_trades_for_prompt(symbol, setup_type, direction, conn) → past trades block

Auto-update: triggered by POST /api/rulebook/update or weekly on sync.
Minimum 15 trades required before generating a rulebook.
"""

import json
import traceback
from datetime import datetime, timezone

import anthropic
from database import get_conn

MODEL        = "claude-sonnet-4-6"
MIN_TRADES   = 15
MAX_SIMILAR  = 8


# ── Calibration ────────────────────────────────────────────────────────────────

def get_calibration_data(conn) -> list:
    """How accurate have setup scores been? Groups analyzed_calls by score tier."""
    rows = conn.execute("""
        SELECT
            CASE
                WHEN setup_score >= 8 THEN 'high (8-10)'
                WHEN setup_score >= 6 THEN 'good (6-7)'
                WHEN setup_score >= 4 THEN 'moderate (4-5)'
                ELSE 'weak (1-3)'
            END AS tier,
            MIN(setup_score) AS min_score,
            COUNT(*) AS n,
            ROUND(100.0 * SUM(CASE WHEN hit_tp1=1 THEN 1 ELSE 0 END) / COUNT(*), 1) AS tp1_rate,
            ROUND(100.0 * SUM(CASE WHEN hit_sl=1  THEN 1 ELSE 0 END) / COUNT(*), 1) AS sl_rate,
            ROUND(AVG(outcome_pnl), 2) AS avg_pnl
        FROM analyzed_calls
        WHERE outcome IS NOT NULL AND setup_score IS NOT NULL
        GROUP BY tier
        ORDER BY min_score DESC
    """).fetchall()
    return [dict(r) for r in rows]


def get_calibration_for_prompt(conn=None) -> str:
    """Formatted calibration block for Claude prompts."""
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    try:
        rows = get_calibration_data(conn)
        if not rows:
            return ""
        lines = ["YOUR SETUP SCORE CALIBRATION (actual outcomes for past calls):"]
        for r in rows:
            lines.append(
                f"  Score {r['tier']}: {r['n']} calls — "
                f"TP1 hit {r['tp1_rate']}%, SL hit {r['sl_rate']}%, avg P&L {r['avg_pnl']} USDT"
            )
        return "\n".join(lines)
    finally:
        if own_conn:
            conn.close()


# ── Similar trades ─────────────────────────────────────────────────────────────

def get_similar_trades(symbol: str, setup_type: str, direction: str,
                       conn, limit: int = MAX_SIMILAR) -> list:
    """
    Fetch recent closed trades for same symbol + setup type + direction.
    Falls back to symbol-only if fewer than 3 matching trades found.
    """
    rows = conn.execute("""
        SELECT realized_pnl, direction, setup_type, duration_minutes,
               entry_price, close_price, open_time
        FROM positions
        WHERE symbol = ?
          AND direction = ?
          AND (setup_type = ? OR ? IS NULL OR ? = '')
        ORDER BY close_time DESC LIMIT ?
    """, (symbol, direction, setup_type, setup_type, setup_type, limit)).fetchall()

    if len(rows) < 3:
        rows = conn.execute("""
            SELECT realized_pnl, direction, setup_type, duration_minutes,
                   entry_price, close_price, open_time
            FROM positions
            WHERE symbol = ?
            ORDER BY close_time DESC LIMIT ?
        """, (symbol, limit)).fetchall()

    return [dict(r) for r in rows]


def get_similar_trades_for_prompt(symbol: str, setup_type: str, direction: str,
                                   conn=None) -> str:
    """Formatted similar-trades block for call analyzer prompt."""
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    try:
        trades = get_similar_trades(symbol, setup_type, direction, conn)
        if not trades:
            return ""

        label = f"{direction} {setup_type}" if setup_type else direction
        lines = [f"YOUR SIMILAR PAST TRADES — {symbol} ({label}, last {len(trades)}):"]
        wins = [t for t in trades if (t["realized_pnl"] or 0) > 0]
        losses = [t for t in trades if (t["realized_pnl"] or 0) < 0]

        for t in trades:
            pnl   = t["realized_pnl"] or 0
            dur   = t["duration_minutes"] or 0
            dur_s = f"{dur//60}h{dur%60:02d}m" if dur >= 60 else f"{dur}m"
            result = "WIN" if pnl > 0 else "LOSS"
            setup  = f" [{t['setup_type']}]" if t.get("setup_type") else ""
            date   = (t["open_time"] or "")[:10]
            lines.append(
                f"  {date}{setup}: entry {t['entry_price']} → close {t['close_price']}, "
                f"{'+'if pnl>=0 else ''}{pnl:.2f} USDT, {dur_s} ({result})"
            )

        avg_win  = round(sum(t["realized_pnl"] for t in wins)  / len(wins),  2) if wins  else 0
        avg_loss = round(sum(t["realized_pnl"] for t in losses) / len(losses), 2) if losses else 0
        lines.append(
            f"  → {len(wins)}W / {len(losses)}L | "
            f"avg win +{avg_win} USDT | avg loss {avg_loss} USDT"
        )
        return "\n".join(lines)
    finally:
        if own_conn:
            conn.close()


# ── Stats collection ───────────────────────────────────────────────────────────

def _collect_stats(conn) -> dict:
    def rows(sql):
        return [dict(r) for r in conn.execute(sql).fetchall()]

    by_setup = rows("""
        SELECT setup_type, COUNT(*) AS n,
               ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_rate,
               ROUND(AVG(realized_pnl),2) AS avg_pnl,
               ROUND(SUM(realized_pnl),2) AS total_pnl
        FROM positions WHERE setup_type IS NOT NULL AND setup_type != ''
        GROUP BY setup_type HAVING n >= 5 ORDER BY n DESC
    """)
    by_weekday = rows("""
        SELECT CASE strftime('%w',close_time)
                 WHEN '0' THEN 'Sunday' WHEN '1' THEN 'Monday' WHEN '2' THEN 'Tuesday'
                 WHEN '3' THEN 'Wednesday' WHEN '4' THEN 'Thursday' WHEN '5' THEN 'Friday'
                 ELSE 'Saturday' END AS day,
               COUNT(*) AS n,
               ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_rate,
               ROUND(AVG(realized_pnl),2) AS avg_pnl
        FROM positions GROUP BY day HAVING n >= 5 ORDER BY win_rate DESC
    """)
    by_session = rows("""
        SELECT CASE
                 WHEN CAST(strftime('%H',open_time) AS INT) BETWEEN 0  AND 7  THEN 'Asia (00-08)'
                 WHEN CAST(strftime('%H',open_time) AS INT) BETWEEN 8  AND 12 THEN 'London (08-13)'
                 WHEN CAST(strftime('%H',open_time) AS INT) BETWEEN 13 AND 20 THEN 'NY/Overlap (13-21)'
                 ELSE 'Late/Off-hours (21-24)'
               END AS session,
               COUNT(*) AS n,
               ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_rate,
               ROUND(AVG(realized_pnl),2) AS avg_pnl
        FROM positions GROUP BY session HAVING n >= 5 ORDER BY win_rate DESC
    """)
    by_direction = rows("""
        SELECT direction, COUNT(*) AS n,
               ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_rate,
               ROUND(AVG(realized_pnl),2) AS avg_pnl,
               ROUND(SUM(realized_pnl),2) AS total_pnl
        FROM positions GROUP BY direction
    """)
    by_duration = rows("""
        SELECT CASE
                 WHEN duration_minutes < 60    THEN '< 1h'
                 WHEN duration_minutes < 240   THEN '1-4h'
                 WHEN duration_minutes < 1440  THEN '4-24h'
                 WHEN duration_minutes < 10080 THEN '1-7 days'
                 ELSE '> 7 days'
               END AS bucket,
               COUNT(*) AS n,
               ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_rate,
               ROUND(AVG(realized_pnl),2) AS avg_pnl
        FROM positions WHERE duration_minutes IS NOT NULL
        GROUP BY bucket HAVING n >= 5 ORDER BY win_rate DESC
    """)
    by_grade = rows("""
        SELECT execution_grade AS grade, COUNT(*) AS n,
               ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_rate,
               ROUND(AVG(realized_pnl),2) AS avg_pnl
        FROM positions WHERE execution_grade IS NOT NULL
        GROUP BY grade HAVING n >= 3 ORDER BY grade
    """)
    worst = rows("""
        SELECT symbol, COUNT(*) AS n,
               ROUND(SUM(realized_pnl),2) AS total_pnl,
               ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_rate
        FROM positions GROUP BY symbol HAVING n >= 5
        ORDER BY total_pnl ASC LIMIT 5
    """)
    best = rows("""
        SELECT symbol, COUNT(*) AS n,
               ROUND(SUM(realized_pnl),2) AS total_pnl,
               ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_rate
        FROM positions GROUP BY symbol HAVING n >= 5
        ORDER BY total_pnl DESC LIMIT 5
    """)
    overall = dict(conn.execute("""
        SELECT COUNT(*) AS n,
               ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_rate,
               ROUND(SUM(realized_pnl),2) AS total_pnl,
               ROUND(AVG(CASE WHEN realized_pnl>0 THEN realized_pnl END),2) AS avg_win,
               ROUND(AVG(CASE WHEN realized_pnl<0 THEN realized_pnl END),2) AS avg_loss
        FROM positions
    """).fetchone())
    recent = dict(conn.execute("""
        SELECT COUNT(*) AS n,
               ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_rate,
               ROUND(SUM(realized_pnl),2) AS total_pnl
        FROM (SELECT realized_pnl FROM positions ORDER BY close_time DESC LIMIT 20)
    """).fetchone())

    return {
        "overall": overall, "recent_20": recent,
        "by_setup": by_setup, "by_weekday": by_weekday, "by_session": by_session,
        "by_direction": by_direction, "by_duration": by_duration, "by_grade": by_grade,
        "worst_symbols": worst, "best_symbols": best,
        "score_calibration": get_calibration_data(conn),
    }


# ── Claude synthesis ───────────────────────────────────────────────────────────

def _ask_claude(stats: dict, total: int) -> list:
    sections = []
    ov = stats["overall"]
    sections.append(
        f"OVERALL: {total} trades, {ov.get('win_rate')}% WR, "
        f"total P&L {ov.get('total_pnl')} USDT, "
        f"avg win {ov.get('avg_win')} / avg loss {ov.get('avg_loss')} USDT"
    )
    r20 = stats["recent_20"]
    sections.append(f"RECENT 20: {r20.get('win_rate')}% WR, {r20.get('total_pnl')} USDT P&L")

    for label, key in [
        ("BY SETUP TYPE", "by_setup"),
        ("BY DAY", "by_weekday"),
        ("BY SESSION", "by_session"),
        ("BY DURATION", "by_duration"),
        ("BY EXECUTION GRADE", "by_grade"),
        ("WORST SYMBOLS", "worst_symbols"),
        ("BEST SYMBOLS", "best_symbols"),
    ]:
        rows = stats.get(key, [])
        if rows:
            sections.append(label + ":\n" + "\n".join(
                "  " + " | ".join(f"{k}: {v}" for k, v in r.items() if k != "min_score")
                for r in rows
            ))

    sections.append("BY DIRECTION:\n" + "\n".join(
        f"  {r['direction']}: {r['n']} trades, {r['win_rate']}% WR, total {r['total_pnl']} USDT"
        for r in stats["by_direction"]
    ))

    cal = stats.get("score_calibration", [])
    if cal:
        sections.append("SCORE CALIBRATION:\n" + "\n".join(
            f"  Score {r['tier']}: {r['n']} calls — TP1 {r['tp1_rate']}%, SL {r['sl_rate']}%, avg {r['avg_pnl']} USDT"
            for r in cal
        ))

    prompt = (
        f"Build a personalised trading rulebook for this crypto futures trader ({total} closed trades).\n\n"
        + "\n\n".join(sections) + "\n\n"
        "Extract 5-10 concise, actionable rules. Each rule must cite specific numbers from the data.\n"
        "Rule types:\n"
        "  warning     — losing pattern the trader must stop\n"
        "  strength    — winning pattern to exploit more\n"
        "  habit       — execution discipline note (positive or negative)\n"
        "  calibration — note about how accurate setup scores have been\n\n"
        "Only write rules supported by the data (min 5 trades per pattern). "
        "Skip categories with insufficient data.\n\n"
        "Return ONLY valid JSON array:\n"
        '[{"type":"warning|strength|habit|calibration","title":"max 7 words",'
        '"rule":"1-2 sentences with specific numbers","confidence":"high|medium|low","data_points":0}]'
    )

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=MODEL, max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)


# ── Public API ─────────────────────────────────────────────────────────────────

def update_rulebook(conn=None) -> dict:
    """Generate fresh rules and persist to trader_rulebook. Returns result dict."""
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
        if total < MIN_TRADES:
            return {
                "rules": [], "trade_count": total, "insufficient_data": True,
                "message": f"Need at least {MIN_TRADES} trades — you have {total}.",
            }

        stats = _collect_stats(conn)
        rules = _ask_claude(stats, total)

        conn.execute("DELETE FROM trader_rulebook")
        for r in rules:
            conn.execute(
                "INSERT INTO trader_rulebook (rule_type, title, rule, confidence, data_points) "
                "VALUES (?,?,?,?,?)",
                (r.get("type","insight"), r.get("title",""),
                 r.get("rule",""), r.get("confidence","medium"), r.get("data_points",0))
            )
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        conn.execute(
            "INSERT OR REPLACE INTO settings (key,value) VALUES ('rulebook_updated_at',?)", (now,)
        )
        conn.commit()
        return {"rules": rules, "trade_count": total, "updated_at": now, "insufficient_data": False}

    except Exception:
        traceback.print_exc()
        return {"error": "Rulebook update failed — see server logs"}
    finally:
        if own_conn:
            conn.close()


def get_rulebook(conn=None) -> dict:
    """Return rulebook rules + metadata for API response."""
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    try:
        rules = [dict(r) for r in conn.execute("""
            SELECT id, rule_type, title, rule, confidence, data_points, generated_at
            FROM trader_rulebook
            ORDER BY CASE rule_type
                WHEN 'warning' THEN 1 WHEN 'calibration' THEN 2
                WHEN 'habit' THEN 3 WHEN 'strength' THEN 4 ELSE 5 END
        """).fetchall()]
        updated = conn.execute(
            "SELECT value FROM settings WHERE key='rulebook_updated_at'"
        ).fetchone()
        return {"rules": rules, "updated_at": updated[0] if updated else None, "count": len(rules)}
    finally:
        if own_conn:
            conn.close()


def get_rulebook_for_prompt(conn=None) -> str:
    """
    Concise text block for Claude prompt injection.
    Warnings and calibration first — most safety-critical.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT rule_type, title, rule, confidence FROM trader_rulebook
            ORDER BY CASE rule_type
                WHEN 'warning' THEN 1 WHEN 'calibration' THEN 2
                WHEN 'habit' THEN 3 WHEN 'strength' THEN 4 ELSE 5 END
        """).fetchall()
        if not rows:
            return ""
        updated = conn.execute(
            "SELECT value FROM settings WHERE key='rulebook_updated_at'"
        ).fetchone()
        ts = updated[0] if updated else "unknown"
        icon = {"warning": "⚠", "strength": "✓", "habit": "→", "calibration": "~"}
        lines = [f"TRADER RULEBOOK (personalised from trade history, updated {ts}):"]
        for r in rows:
            lines.append(f"  {icon.get(r[0],'•')} [{r[0].upper()}] {r[1]}: {r[2]}")
        return "\n".join(lines)
    finally:
        if own_conn:
            conn.close()
