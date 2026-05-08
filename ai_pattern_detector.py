"""
ai_pattern_detector.py — Find statistical patterns in trade history using Claude.

Requires at least 20 closed trades to produce meaningful results.
Minimum 5 trades in a category before flagging it as a pattern.

Returns a list of findings:
  [{"type": "warning|insight|strength", "title": str, "finding": str,
    "recommendation": str, "confidence": "high|medium|low"}]
"""

import json
import traceback

import anthropic
from database import get_conn


MIN_TOTAL_TRADES = 20
MIN_CATEGORY     = 5


def detect_patterns(conn=None, filters=None) -> dict:
    """
    Analyse trade history and return pattern findings from Claude.
    Returns {"findings": [...], "trade_count": int, "insufficient_data": bool}
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    # Parameterized exchange filter — never interpolate values into SQL
    exch_val    = (filters or {}).get('exchange')
    exch_clause = " AND COALESCE(exchange, 'bitget') = ?" if exch_val in ('bitget', 'blofin') else ""
    exch_params = (exch_val,) if exch_clause else ()
    try:
        total = conn.execute(
            f"SELECT COUNT(*) FROM positions WHERE 1=1{exch_clause}", exch_params
        ).fetchone()[0]
        if total < MIN_TOTAL_TRADES:
            return {
                "findings":          [],
                "trade_count":       total,
                "insufficient_data": True,
                "message":           f"Need at least {MIN_TOTAL_TRADES} trades — you have {total}.",
            }

        stats = _collect_stats(conn, exch_clause, exch_params)
        findings = _ask_claude(stats, total)
        return {
            "findings":          findings,
            "trade_count":       total,
            "insufficient_data": False,
        }
    except Exception:
        traceback.print_exc()
        return {"error": "Pattern detection failed — see server logs"}
    finally:
        if own_conn:
            conn.close()


def _collect_stats(conn, exch_clause: str = "", exch_params: tuple = ()) -> dict:
    def rows(sql, extra_params=()):
        return [dict(r) for r in conn.execute(sql, exch_params + extra_params).fetchall()]

    by_setup = rows(f"""
        SELECT setup_type, COUNT(*) AS n,
               ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_rate,
               ROUND(AVG(realized_pnl),2) AS avg_pnl,
               ROUND(SUM(realized_pnl),2) AS total_pnl
        FROM positions WHERE setup_type IS NOT NULL AND setup_type != ''{exch_clause}
        GROUP BY setup_type ORDER BY n DESC
    """)

    by_weekday = rows(f"""
        SELECT CASE strftime('%w',close_time)
                 WHEN '0' THEN 'Sunday' WHEN '1' THEN 'Monday' WHEN '2' THEN 'Tuesday'
                 WHEN '3' THEN 'Wednesday' WHEN '4' THEN 'Thursday' WHEN '5' THEN 'Friday'
                 ELSE 'Saturday' END AS day,
               COUNT(*) AS n,
               ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_rate,
               ROUND(AVG(realized_pnl),2) AS avg_pnl
        FROM positions WHERE 1=1{exch_clause} GROUP BY day ORDER BY win_rate DESC
    """)

    by_session = rows(f"""
        SELECT CASE
                 WHEN CAST(strftime('%H',open_time) AS INT) BETWEEN 0  AND 7  THEN 'Asia (00-08)'
                 WHEN CAST(strftime('%H',open_time) AS INT) BETWEEN 8  AND 12 THEN 'London (08-13)'
                 WHEN CAST(strftime('%H',open_time) AS INT) BETWEEN 13 AND 20 THEN 'NY/Overlap (13-21)'
                 ELSE 'Late/Off-hours (21-24)'
               END AS session,
               COUNT(*) AS n,
               ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_rate,
               ROUND(AVG(realized_pnl),2) AS avg_pnl
        FROM positions WHERE 1=1{exch_clause} GROUP BY session ORDER BY win_rate DESC
    """)

    by_direction = rows(f"""
        SELECT direction, COUNT(*) AS n,
               ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_rate,
               ROUND(AVG(realized_pnl),2) AS avg_pnl,
               ROUND(SUM(realized_pnl),2) AS total_pnl
        FROM positions WHERE 1=1{exch_clause} GROUP BY direction
    """)

    by_duration = rows(f"""
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
        FROM positions WHERE duration_minutes IS NOT NULL{exch_clause}
        GROUP BY bucket ORDER BY win_rate DESC
    """)

    by_grade = rows(f"""
        SELECT execution_grade AS grade, COUNT(*) AS n,
               ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_rate,
               ROUND(AVG(realized_pnl),2) AS avg_pnl
        FROM positions WHERE execution_grade IS NOT NULL{exch_clause}
        GROUP BY grade ORDER BY grade
    """)

    # Recent trend: last 20 vs all-time (subquery needs params passed explicitly)
    recent = conn.execute(f"""
        SELECT COUNT(*) AS n,
               ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_rate,
               ROUND(SUM(realized_pnl),2) AS total_pnl
        FROM (SELECT realized_pnl FROM positions WHERE 1=1{exch_clause} ORDER BY close_time DESC LIMIT 20)
    """, exch_params).fetchone()
    recent_stats = dict(recent) if recent else {}

    return {
        "by_setup":     by_setup,
        "by_weekday":   by_weekday,
        "by_session":   by_session,
        "by_direction": by_direction,
        "by_duration":  by_duration,
        "by_grade":     by_grade,
        "recent_20":    recent_stats,
    }


def _ask_claude(stats: dict, total_trades: int) -> list:
    def fmt(rows, min_n=MIN_CATEGORY):
        return [r for r in rows if r.get("n", 0) >= min_n]

    sections = []

    if fmt(stats["by_setup"]):
        sections.append("BY SETUP TYPE:\n" + "\n".join(
            f"  {r['setup_type']}: {r['n']} trades, {r['win_rate']}% WR, avg {r['avg_pnl']} USDT"
            for r in fmt(stats["by_setup"])
        ))

    if fmt(stats["by_weekday"]):
        sections.append("BY DAY OF WEEK:\n" + "\n".join(
            f"  {r['day']}: {r['n']} trades, {r['win_rate']}% WR, avg {r['avg_pnl']} USDT"
            for r in fmt(stats["by_weekday"])
        ))

    if fmt(stats["by_session"]):
        sections.append("BY TRADING SESSION:\n" + "\n".join(
            f"  {r['session']}: {r['n']} trades, {r['win_rate']}% WR, avg {r['avg_pnl']} USDT"
            for r in fmt(stats["by_session"])
        ))

    sections.append("BY DIRECTION:\n" + "\n".join(
        f"  {r['direction']}: {r['n']} trades, {r['win_rate']}% WR, avg {r['avg_pnl']} USDT, total {r['total_pnl']} USDT"
        for r in stats["by_direction"]
    ))

    if fmt(stats["by_duration"]):
        sections.append("BY TRADE DURATION:\n" + "\n".join(
            f"  {r['bucket']}: {r['n']} trades, {r['win_rate']}% WR, avg {r['avg_pnl']} USDT"
            for r in fmt(stats["by_duration"])
        ))

    if fmt(stats["by_grade"]):
        sections.append("BY EXECUTION GRADE:\n" + "\n".join(
            f"  Grade {r['grade']}: {r['n']} trades, {r['win_rate']}% WR, avg {r['avg_pnl']} USDT"
            for r in fmt(stats["by_grade"])
        ))

    r20 = stats.get("recent_20", {})
    if r20:
        sections.append(
            f"RECENT FORM (last 20 trades): {r20.get('win_rate')}% WR, "
            f"total P&L {r20.get('total_pnl')} USDT"
        )

    data_block = "\n\n".join(sections) if sections else "Insufficient categorised data."

    prompt = (
        f"You are analysing {total_trades} closed crypto futures trades to find statistically meaningful patterns.\n\n"
        f"Only flag a pattern if it has at least {MIN_CATEGORY} trades in the category. "
        "Ignore categories with too few trades.\n\n"
        f"{data_block}\n\n"
        "Identify up to 6 of the most actionable patterns. For each:\n"
        "- type: 'warning' (clear losing pattern to stop), 'insight' (neutral/mixed worth noting), "
        "or 'strength' (clear winning pattern to exploit)\n"
        "- title: short headline (max 8 words)\n"
        "- finding: 2-3 sentences explaining the pattern with specific numbers\n"
        "- recommendation: 1 concrete sentence on what to do differently\n"
        "- confidence: 'high' (large sample, clear signal), 'medium' (moderate sample), "
        "'low' (borderline sample size)\n\n"
        "Return ONLY valid JSON array — no markdown, no extra text:\n"
        '[{"type":"warning|insight|strength","title":"...","finding":"...","recommendation":"...","confidence":"high|medium|low"}]'
    )

    client   = anthropic.Anthropic()
    response = client.messages.create(
        model      = "claude-sonnet-4-6",
        max_tokens = 1200,
        messages   = [{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)
