"""
analytics.py — All KPI and statistics calculations.

Two public functions:
  get_dashboard_kpis(conn, filters)  →  dict with top-level numbers + chart data
  get_deep_stats(conn, filters)      →  dict with advanced breakdowns

filters is a dict with optional keys:
  symbol, direction, date_from, date_to
"""

import re
import sqlite3
from database import get_conn


# ── helpers ────────────────────────────────────────────────────────────────────

def _build_where(filters):
    """Return (where_clause_string, params_list) from a filters dict."""
    clauses = []
    params  = []

    if filters.get('symbol'):
        sym = filters['symbol'].strip().upper()
        if re.match(r'^[A-Z0-9]+$', sym):
            clauses.append("symbol = ?")
            params.append(sym)
    if filters.get('direction'):
        if filters['direction'] in ('Long', 'Short'):
            clauses.append("direction = ?")
            params.append(filters['direction'])
    if filters.get('date_from'):
        if re.match(r'^\d{4}-\d{2}-\d{2}$', filters['date_from']):
            clauses.append("close_time >= ?")
            params.append(filters['date_from'])
    if filters.get('date_to'):
        if re.match(r'^\d{4}-\d{2}-\d{2}$', filters['date_to']):
            clauses.append("close_time <= ?")
            params.append(filters['date_to'] + ' 23:59:59')

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def _rows(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _val(conn, sql, params=(), default=0):
    r = conn.execute(sql, params).fetchone()
    if r is None:
        return default
    v = r[0]
    return v if v is not None else default


# ── dashboard KPIs ─────────────────────────────────────────────────────────────

def get_dashboard_kpis(filters=None, conn=None):
    """
    Returns a dict ready to be JSON-serialized for the Dashboard module.

    Calculated fields:
      total_trades, win_trades, loss_trades, win_rate
      total_realized_pnl, total_fees, net_pnl
      best_trade, worst_trade, avg_win, avg_loss
      profit_factor  = sum(wins) / abs(sum(losses))
      max_drawdown   = largest peak-to-trough drop on cumulative PnL curve
      pnl_curve      = [{date, cumulative_pnl}, ...] sorted ascending
      top_symbols    = [{symbol, total_pnl, trade_count, win_rate}, ...]  top 5
      recent_trades  = last 10 closed positions
      wallet_curve   = [{date, balance}, ...]  from wallet_snapshots
    """
    if filters is None:
        filters = {}
    if conn is None:
        conn = get_conn()

    where, params = _build_where(filters)

    total_trades = _val(conn, f"SELECT COUNT(*) FROM positions {where}", params)
    win_trades   = _val(conn, f"SELECT COUNT(*) FROM positions {where} {'AND' if where else 'WHERE'} realized_pnl > 0", params)
    loss_trades  = _val(conn, f"SELECT COUNT(*) FROM positions {where} {'AND' if where else 'WHERE'} realized_pnl < 0", params)
    win_rate     = round(win_trades / total_trades * 100, 1) if total_trades else 0

    total_pnl  = _val(conn, f"SELECT SUM(realized_pnl) FROM positions {where}", params)
    total_fees = _val(conn, f"SELECT SUM(total_fees)   FROM positions {where}", params)
    # position_pnl is gross; realized_pnl already net — total_fees is additional context
    net_pnl    = round(total_pnl, 4)

    best_trade  = _val(conn, f"SELECT MAX(realized_pnl) FROM positions {where}", params)
    worst_trade = _val(conn, f"SELECT MIN(realized_pnl) FROM positions {where}", params)

    # avg win / avg loss
    w_sql  = f"SELECT AVG(realized_pnl) FROM positions {where} {'AND' if where else 'WHERE'} realized_pnl > 0"
    l_sql  = f"SELECT AVG(realized_pnl) FROM positions {where} {'AND' if where else 'WHERE'} realized_pnl < 0"
    avg_win  = round(_val(conn, w_sql, params), 4)
    avg_loss = round(_val(conn, l_sql, params), 4)

    # profit factor
    sum_wins   = _val(conn, f"SELECT SUM(realized_pnl) FROM positions {where} {'AND' if where else 'WHERE'} realized_pnl > 0", params)
    sum_losses = _val(conn, f"SELECT SUM(realized_pnl) FROM positions {where} {'AND' if where else 'WHERE'} realized_pnl < 0", params)
    profit_factor = round(sum_wins / abs(sum_losses), 2) if sum_losses else None

    # cumulative PnL curve (sorted by close_time)
    pnl_rows = _rows(conn, f"SELECT close_time, realized_pnl FROM positions {where} ORDER BY close_time ASC", params)
    cumulative = 0
    pnl_curve  = []
    for r in pnl_rows:
        cumulative += (r['realized_pnl'] or 0)
        pnl_curve.append({"date": r['close_time'][:10], "cumulative_pnl": round(cumulative, 4)})

    # max drawdown from PnL curve
    max_drawdown = _calc_max_drawdown(pnl_curve)

    # top 5 symbols by total realized PnL
    top_symbols = _rows(conn, f"""
        SELECT symbol,
               SUM(realized_pnl)  AS total_pnl,
               COUNT(*)           AS trade_count,
               ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate
        FROM positions {where}
        GROUP BY symbol
        ORDER BY total_pnl DESC
        LIMIT 5
    """, params)

    # recent 10 trades
    recent_trades = _rows(conn, f"""
        SELECT id, symbol, direction, open_time, close_time,
               entry_price, close_price, size_usdt,
               realized_pnl, total_fees, notes
        FROM positions {where}
        ORDER BY close_time DESC
        LIMIT 10
    """, params)

    # current calendar month PnL (always UTC, no filter applied)
    from datetime import datetime as _dt
    month_start = _dt.utcnow().strftime('%Y-%m-01')
    current_month_pnl = round(_val(conn,
        "SELECT SUM(realized_pnl) FROM positions WHERE close_time >= ?",
        [month_start]
    ), 4)

    # wallet balance curve (sample every 50th row to keep payload small)
    wallet_curve = _rows(conn, """
        SELECT date, wallet_balance
        FROM wallet_snapshots
        WHERE wallet_balance IS NOT NULL
        ORDER BY date ASC
    """)
    # downsample: take every Nth row so chart stays < 200 points
    step = max(1, len(wallet_curve) // 200)
    wallet_curve = wallet_curve[::step]

    return {
        "total_trades":    total_trades,
        "win_trades":      win_trades,
        "loss_trades":     loss_trades,
        "win_rate":        win_rate,
        "total_pnl":       round(total_pnl, 4),
        "total_fees":      round(total_fees, 4),
        "net_pnl":         net_pnl,
        "best_trade":      round(best_trade, 4),
        "worst_trade":     round(worst_trade, 4),
        "avg_win":         avg_win,
        "avg_loss":        avg_loss,
        "profit_factor":   profit_factor,
        "max_drawdown":       max_drawdown,
        "current_month_pnl":  current_month_pnl,
        "pnl_curve":          pnl_curve,
        "top_symbols":        top_symbols,
        "recent_trades":      recent_trades,
        "wallet_curve":       wallet_curve,
    }


def _calc_max_drawdown(pnl_curve):
    """Return maximum peak-to-trough drawdown (as a negative number) from the PnL curve."""
    if not pnl_curve:
        return 0
    peak     = pnl_curve[0]['cumulative_pnl']
    max_dd   = 0
    for point in pnl_curve:
        v = point['cumulative_pnl']
        if v > peak:
            peak = v
        dd = v - peak
        if dd < max_dd:
            max_dd = dd
    return round(max_dd, 4)


# ── deep dive stats ─────────────────────────────────────────────────────────────

def get_deep_stats(filters=None, conn=None):
    """
    Returns extended analytics for the Deep Dive module.

    Sections:
      by_symbol          — PnL, win rate, trade count per symbol
      by_month           — PnL + trade count per calendar month
      by_weekday         — PnL + win rate per weekday (Mon-Sun)
      by_hour            — PnL + win rate per open hour (0-23)
      by_direction       — Long vs Short comparison
      duration_buckets   — trade count grouped by hold time
      streaks            — longest win/loss streak
      fee_analysis       — total fees, avg fee per trade, fees as % of gross PnL
      rr_distribution    — histogram of realized_pnl / abs(avg_loss) buckets
    """
    if filters is None:
        filters = {}
    if conn is None:
        conn = get_conn()

    where, params = _build_where(filters)
    and_ = "AND" if where else "WHERE"

    by_symbol = _rows(conn, f"""
        SELECT symbol,
               COUNT(*)                                          AS trade_count,
               ROUND(SUM(realized_pnl), 4)                      AS total_pnl,
               ROUND(SUM(total_fees), 4)                        AS total_fees,
               ROUND(100.0 * SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*), 1) AS win_rate,
               ROUND(AVG(realized_pnl), 4)                      AS avg_pnl,
               ROUND(MAX(realized_pnl), 4)                      AS best,
               ROUND(MIN(realized_pnl), 4)                      AS worst
        FROM positions {where}
        GROUP BY symbol
        ORDER BY total_pnl DESC
    """, params)

    by_month = _rows(conn, f"""
        SELECT strftime('%Y-%m', close_time)                    AS month,
               COUNT(*)                                         AS trade_count,
               ROUND(SUM(realized_pnl), 4)                     AS total_pnl,
               ROUND(100.0 * SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*), 1) AS win_rate
        FROM positions {where}
        GROUP BY month
        ORDER BY month ASC
    """, params)

    by_weekday = _rows(conn, f"""
        SELECT CAST(strftime('%w', close_time) AS INTEGER)      AS weekday_num,
               CASE strftime('%w', close_time)
                 WHEN '0' THEN 'Sun' WHEN '1' THEN 'Mon' WHEN '2' THEN 'Tue'
                 WHEN '3' THEN 'Wed' WHEN '4' THEN 'Thu' WHEN '5' THEN 'Fri'
                 ELSE 'Sat' END                                 AS weekday,
               COUNT(*)                                         AS trade_count,
               ROUND(SUM(realized_pnl), 4)                     AS total_pnl,
               ROUND(100.0 * SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*), 1) AS win_rate
        FROM positions {where}
        GROUP BY weekday_num
        ORDER BY weekday_num ASC
    """, params)

    by_hour = _rows(conn, f"""
        SELECT CAST(strftime('%H', open_time) AS INTEGER)       AS hour,
               COUNT(*)                                         AS trade_count,
               ROUND(SUM(realized_pnl), 4)                     AS total_pnl,
               ROUND(100.0 * SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*), 1) AS win_rate
        FROM positions {where}
        GROUP BY hour
        ORDER BY hour ASC
    """, params)

    by_direction = _rows(conn, f"""
        SELECT direction,
               COUNT(*)                                         AS trade_count,
               ROUND(SUM(realized_pnl), 4)                     AS total_pnl,
               ROUND(AVG(realized_pnl), 4)                     AS avg_pnl,
               ROUND(100.0 * SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*), 1) AS win_rate,
               ROUND(SUM(total_fees), 4)                       AS total_fees
        FROM positions {where}
        GROUP BY direction
    """, params)

    # duration buckets: < 1h, 1-4h, 4-24h, 1-7 days, > 7 days
    dur_rows = _rows(conn, f"""
        SELECT duration_minutes,
               realized_pnl
        FROM positions {where}
        WHERE duration_minutes IS NOT NULL
    """, params)
    duration_buckets = _bucket_durations(dur_rows)

    # streaks
    pnl_series = [r['realized_pnl'] or 0 for r in _rows(conn,
        f"SELECT realized_pnl FROM positions {where} ORDER BY close_time ASC", params)]
    streaks = _calc_streaks(pnl_series)

    # fee analysis
    gross_pnl  = _val(conn, f"SELECT SUM(position_pnl) FROM positions {where}", params)
    total_fees = _val(conn, f"SELECT SUM(total_fees)   FROM positions {where}", params)
    avg_fee    = _val(conn, f"SELECT AVG(total_fees)   FROM positions {where}", params)
    total_count= _val(conn, f"SELECT COUNT(*)          FROM positions {where}", params)
    fee_pct    = round(abs(total_fees) / abs(gross_pnl) * 100, 2) if gross_pnl else 0

    fee_analysis = {
        "total_fees":   round(total_fees, 4),
        "avg_fee":      round(avg_fee, 4),
        "fee_pct_gross": fee_pct,
        "total_trades": total_count,
    }

    # top losing symbols (useful for risk review)
    worst_symbols = _rows(conn, f"""
        SELECT symbol,
               COUNT(*)                    AS trade_count,
               ROUND(SUM(realized_pnl),4)  AS total_pnl
        FROM positions {where}
        GROUP BY symbol
        ORDER BY total_pnl ASC
        LIMIT 5
    """, params)

    # by setup type
    by_setup = _rows(conn, f"""
        SELECT setup_type,
               COUNT(*) AS trade_count,
               ROUND(SUM(realized_pnl), 4) AS total_pnl,
               ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate,
               ROUND(AVG(realized_pnl), 4) AS avg_pnl
        FROM positions {where} {and_} setup_type IS NOT NULL AND setup_type != ''
        GROUP BY setup_type
        ORDER BY total_pnl DESC
    """, params)

    # by execution grade
    by_grade = _rows(conn, f"""
        SELECT execution_grade AS grade,
               COUNT(*) AS trade_count,
               ROUND(SUM(realized_pnl), 4) AS total_pnl,
               ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate,
               ROUND(AVG(realized_pnl), 4) AS avg_pnl
        FROM positions {where} {and_} execution_grade IS NOT NULL
        GROUP BY grade
        ORDER BY grade ASC
    """, params)

    return {
        "by_symbol":        by_symbol,
        "by_month":         by_month,
        "by_weekday":       by_weekday,
        "by_hour":          by_hour,
        "by_direction":     by_direction,
        "duration_buckets": duration_buckets,
        "streaks":          streaks,
        "fee_analysis":     fee_analysis,
        "worst_symbols":    worst_symbols,
        "by_setup":         by_setup,
        "by_grade":         by_grade,
    }


def _bucket_durations(rows):
    buckets = {
        "< 1h":      {"label": "< 1h",      "count": 0, "total_pnl": 0},
        "1-4h":      {"label": "1-4h",      "count": 0, "total_pnl": 0},
        "4-24h":     {"label": "4-24h",     "count": 0, "total_pnl": 0},
        "1-7 days":  {"label": "1-7 days",  "count": 0, "total_pnl": 0},
        "> 7 days":  {"label": "> 7 days",  "count": 0, "total_pnl": 0},
    }
    for r in rows:
        m   = r['duration_minutes'] or 0
        pnl = r['realized_pnl'] or 0
        if m < 60:
            k = "< 1h"
        elif m < 240:
            k = "1-4h"
        elif m < 1440:
            k = "4-24h"
        elif m < 10080:
            k = "1-7 days"
        else:
            k = "> 7 days"
        buckets[k]['count']     += 1
        buckets[k]['total_pnl'] += pnl

    for k in buckets:
        buckets[k]['total_pnl'] = round(buckets[k]['total_pnl'], 4)
    return list(buckets.values())


def get_heatmap_data(conn=None) -> list:
    """
    Trade stats grouped by weekday (0=Sun…6=Sat) and open hour (0-23 UTC).
    Returns list of {weekday, hour, trade_count, total_pnl, win_rate}.
    """
    if conn is None:
        conn = get_conn()
    return _rows(conn, """
        SELECT
            CAST(strftime('%w', close_time) AS INTEGER) AS weekday,
            CAST(strftime('%H', open_time)  AS INTEGER) AS hour,
            COUNT(*)                                     AS trade_count,
            ROUND(SUM(realized_pnl), 2)                  AS total_pnl,
            ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END)
                  / COUNT(*), 1)                         AS win_rate
        FROM positions
        GROUP BY weekday, hour
        ORDER BY weekday, hour
    """)


def get_rr_analysis(conn=None):
    """
    Planned vs realized R:R for trades linked to analyst calls via positions.call_id.
    Realized R:R = (actual_close - planned_entry) / abs(planned_entry - planned_sl).
    Returns list of dicts, most recent first, capped at 100 rows.
    """
    if conn is None:
        conn = get_conn()
    rows = _rows(conn, """
        SELECT p.id, p.symbol, p.direction,
               p.entry_price   AS actual_entry,
               p.close_price   AS actual_close,
               p.realized_pnl,
               p.execution_grade,
               p.setup_type,
               c.entry_price   AS planned_entry,
               c.sl_price      AS planned_sl,
               c.tp1_price     AS planned_tp1,
               c.rr_ratio      AS planned_rr_text,
               c.outcome       AS call_outcome
        FROM positions p
        JOIN analyzed_calls c ON p.call_id = c.id
        WHERE c.sl_price IS NOT NULL AND c.sl_price > 0
          AND p.entry_price IS NOT NULL AND p.entry_price > 0
        ORDER BY p.close_time DESC
        LIMIT 100
    """)

    result = []
    for r in rows:
        p_entry = r["planned_entry"] or r["actual_entry"]
        p_sl    = r["planned_sl"]
        close   = r["actual_close"]
        real_rr = None
        if p_sl and p_entry and abs(p_entry - p_sl) > 0:
            risk    = abs(p_entry - p_sl)
            reward  = (close - p_entry) if r["direction"] == "Long" else (p_entry - close)
            real_rr = round(reward / risk, 2)
        result.append({
            "id":          r["id"],
            "symbol":      r["symbol"],
            "direction":   r["direction"],
            "planned_rr":  r["planned_rr_text"],
            "realized_rr": real_rr,
            "outcome":     r["call_outcome"],
            "grade":       r["execution_grade"],
            "pnl":         r["realized_pnl"],
            "setup_type":  r["setup_type"],
        })
    return result


def _calc_streaks(pnl_series):
    max_win = cur_win = max_loss = cur_loss = 0
    for pnl in pnl_series:
        if pnl > 0:
            cur_win  += 1
            cur_loss  = 0
        elif pnl < 0:
            cur_loss += 1
            cur_win   = 0
        max_win  = max(max_win,  cur_win)
        max_loss = max(max_loss, cur_loss)
    return {"max_win_streak": max_win, "max_loss_streak": max_loss}
