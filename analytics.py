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
import yfinance as yf
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

    if filters.get('exchange') in ('bitget', 'blofin'):
        clauses.append("COALESCE(exchange, 'bitget') = ?")
        params.append(filters['exchange'])

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
    total_funding_pnl = round(
        _val(conn, f"SELECT SUM(funding_pnl) FROM positions {where}", params) or 0.0, 4
    )
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
    if sum_losses:
        profit_factor = round(sum_wins / abs(sum_losses), 2)
    elif sum_wins and sum_wins > 0:
        profit_factor = 999.0  # no losing trades — display as ∞ in UI
    else:
        profit_factor = None

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

    # current calendar month PnL — respects exchange filter
    from datetime import datetime as _dt, timedelta as _td
    month_start  = _dt.utcnow().strftime('%Y-%m-01')
    mo_where, mo_params = _build_where({**filters, "date_from": month_start})
    # _build_where turns date_from into close_time >= ?, so we get the exchange clause too
    current_month_pnl = round(_val(conn,
        f"SELECT SUM(realized_pnl) FROM positions {mo_where}",
        mo_params
    ), 4)

    # last 6 calendar months P&L for dashboard bar chart
    six_months_ago = (_dt.utcnow() - _td(days=180)).strftime('%Y-%m-01')
    mo6_where, mo6_params = _build_where({**filters, "date_from": six_months_ago} if filters else {"date_from": six_months_ago})
    monthly_pnl = _rows(conn, f"""
        SELECT strftime('%Y-%m', close_time) AS month,
               ROUND(SUM(realized_pnl), 2)   AS net_pnl,
               COUNT(*)                       AS trades
        FROM positions {mo6_where}
        GROUP BY month
        ORDER BY month ASC
    """, mo6_params)

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
        "total_funding_pnl": total_funding_pnl,
        "net_pnl":         net_pnl,
        "best_trade":      round(best_trade, 4),
        "worst_trade":     round(worst_trade, 4),
        "avg_win":         avg_win,
        "avg_loss":        avg_loss,
        "profit_factor":   profit_factor,
        "max_drawdown":       max_drawdown,
        "current_month_pnl":  current_month_pnl,
        "monthly_pnl":        monthly_pnl,
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
        {and_} duration_minutes IS NOT NULL
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


def get_setup_type_stats(filters=None, conn=None) -> list:
    """
    Returns per-setup-type performance breakdown, sorted by total P&L descending.
    Each row: setup_type, trade_count, total_pnl, win_rate, avg_pnl,
              avg_win, avg_loss, profit_factor.
    """
    if filters is None:
        filters = {}
    if conn is None:
        conn = get_conn()

    where, params = _build_where(filters)
    and_ = "AND" if where else "WHERE"

    rows = _rows(conn, f"""
        SELECT
            COALESCE(setup_type, 'Unknown') AS setup_type,
            COUNT(*) AS trade_count,
            ROUND(SUM(realized_pnl), 2) AS total_pnl,
            ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate,
            ROUND(AVG(realized_pnl), 2) AS avg_pnl,
            ROUND(AVG(CASE WHEN realized_pnl > 0 THEN realized_pnl END), 2) AS avg_win,
            ROUND(AVG(CASE WHEN realized_pnl < 0 THEN realized_pnl END), 2) AS avg_loss,
            ROUND(
                SUM(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE 0 END) /
                NULLIF(ABS(SUM(CASE WHEN realized_pnl < 0 THEN realized_pnl ELSE 0 END)), 0),
                2
            ) AS profit_factor
        FROM positions
        {where}
        {and_} setup_type IS NOT NULL AND setup_type != ''
        GROUP BY setup_type
        ORDER BY total_pnl DESC
    """, params)

    for r in rows:
        if r["profit_factor"] is None and (r["avg_win"] or 0) > 0:
            r["profit_factor"] = 999.0
        r["avg_win"]  = r["avg_win"]  or 0.0
        r["avg_loss"] = r["avg_loss"] or 0.0

    return rows


def _bucket_durations(rows):
    # Boundaries: < 60 min | 60-239 min | 240-1439 min | 1440-10079 min | ≥ 10080 min
    buckets = {
        "< 1h":      {"label": "< 1h",       "count": 0, "total_pnl": 0},
        "1h-4h":     {"label": "1h-4h",      "count": 0, "total_pnl": 0},
        "4h-24h":    {"label": "4h-24h",     "count": 0, "total_pnl": 0},
        "1-7 days":  {"label": "1-7 days",   "count": 0, "total_pnl": 0},
        "> 7 days":  {"label": "> 7 days",   "count": 0, "total_pnl": 0},
    }
    for r in rows:
        m   = r['duration_minutes'] or 0
        pnl = r['realized_pnl'] or 0
        if m < 60:
            k = "< 1h"
        elif m < 240:
            k = "1h-4h"
        elif m < 1440:
            k = "4h-24h"
        elif m < 10080:
            k = "1-7 days"
        else:
            k = "> 7 days"
        buckets[k]['count']     += 1
        buckets[k]['total_pnl'] += pnl

    for k in buckets:
        buckets[k]['total_pnl'] = round(buckets[k]['total_pnl'], 4)
    return list(buckets.values())


def get_heatmap_data(conn=None, filters=None) -> list:
    """
    Trade stats grouped by weekday (0=Sun…6=Sat) and open hour (0-23 UTC).
    Returns list of {weekday, hour, trade_count, total_pnl, win_rate}.
    """
    if conn is None:
        conn = get_conn()
    where, params = _build_where(filters or {})
    return _rows(conn, f"""
        SELECT
            CAST(strftime('%w', close_time) AS INTEGER) AS weekday,
            CAST(strftime('%H', open_time)  AS INTEGER) AS hour,
            COUNT(*)                                     AS trade_count,
            ROUND(SUM(realized_pnl), 2)                  AS total_pnl,
            ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END)
                  / COUNT(*), 1)                         AS win_rate
        FROM positions {where}
        GROUP BY weekday, hour
        ORDER BY weekday, hour
    """, params)


def get_rr_analysis(conn=None, filters=None):
    """
    Planned vs realized R:R for trades linked to analyst calls via positions.call_id.
    Realized R:R = (actual_close - planned_entry) / abs(planned_entry - planned_sl).
    Returns list of dicts, most recent first, capped at 100 rows.
    """
    if conn is None:
        conn = get_conn()
    # Use _build_where so symbol/direction/date/exchange filters all apply
    pos_where, params = _build_where(filters or {})
    # _build_where creates "WHERE ..." clauses; adapt to fit after the JOIN condition
    extra = (" AND " + pos_where[6:]) if pos_where else ""  # strip "WHERE " prefix
    rows = _rows(conn, f"""
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
          {extra}
        ORDER BY p.close_time DESC
        LIMIT 100
    """, params)

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


def get_mfe_mae(conn=None, filters=None) -> dict:
    """
    MFE/MAE stats from positions that have mfe_pct / mae_pct populated.
    Returns distribution buckets and per-setup-type averages.
    """
    if conn is None:
        conn = get_conn()
    where, params = _build_where(filters or {})
    and_ = "AND" if where else "WHERE"

    raw = _rows(conn, f"""
        SELECT direction, mfe_pct, mae_pct, realized_pnl, setup_type
        FROM positions {where}
        {and_} mfe_pct IS NOT NULL AND mae_pct IS NOT NULL
    """, params)

    if not raw:
        return {"available": False, "message": "No MFE/MAE data yet — will populate on next sync"}

    by_setup = {}
    for r in raw:
        st = r.get("setup_type") or "Unknown"
        if st not in by_setup:
            by_setup[st] = {"mfe_sum": 0, "mae_sum": 0, "n": 0}
        by_setup[st]["mfe_sum"] += r["mfe_pct"] or 0
        by_setup[st]["mae_sum"] += r["mae_pct"] or 0
        by_setup[st]["n"]       += 1

    by_setup_list = [
        {
            "setup_type": st,
            "avg_mfe_pct": round(v["mfe_sum"] / v["n"], 2),
            "avg_mae_pct": round(v["mae_sum"] / v["n"], 2),
            "n": v["n"],
        }
        for st, v in by_setup.items()
        if v["n"] >= 3
    ]
    by_setup_list.sort(key=lambda x: -x["n"])

    all_mfe = [r["mfe_pct"] for r in raw if r["mfe_pct"] is not None]
    all_mae = [r["mae_pct"] for r in raw if r["mae_pct"] is not None]

    return {
        "available":  True,
        "count":      len(raw),
        "avg_mfe_pct": round(sum(all_mfe) / len(all_mfe), 2) if all_mfe else None,
        "avg_mae_pct": round(sum(all_mae) / len(all_mae), 2) if all_mae else None,
        "by_setup":   by_setup_list,
    }


def get_ev_by_setup(conn=None, filters=None) -> list:
    """
    Expected Value = (win_rate × avg_win) + (loss_rate × avg_loss)  per setup type.
    Requires positions linked to analyzed_calls via call_id for setup_type.
    Falls back to positions.setup_type when call_id is absent.
    """
    if conn is None:
        conn = get_conn()
    where, params = _build_where(filters or {})
    and_ = "AND" if where else "WHERE"

    # Build a position-qualified WHERE clause — _build_where returns bare column names
    # which become ambiguous after LEFT JOIN. Prefix positions columns with p.
    p_where = where.replace("symbol =", "p.symbol =") \
                   .replace("direction =", "p.direction =") \
                   .replace("close_time >=", "p.close_time >=") \
                   .replace("close_time <=", "p.close_time <=") \
                   .replace("COALESCE(exchange,", "COALESCE(p.exchange,") if where else ""

    rows = _rows(conn, f"""
        SELECT
            COALESCE(p.setup_type, ac.trade_type, 'Unknown') AS setup_type,
            COUNT(*)                                                         AS n,
            ROUND(100.0 * SUM(CASE WHEN p.realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate,
            ROUND(AVG(CASE WHEN p.realized_pnl > 0 THEN p.realized_pnl END), 2) AS avg_win,
            ROUND(AVG(CASE WHEN p.realized_pnl < 0 THEN p.realized_pnl END), 2) AS avg_loss,
            ROUND(SUM(p.realized_pnl), 2)                                   AS total_pnl
        FROM positions p
        LEFT JOIN analyzed_calls ac ON p.call_id = ac.id
        {p_where} {'AND' if p_where else 'WHERE'}
            ((p.setup_type IS NOT NULL AND p.setup_type != '')
             OR (ac.trade_type IS NOT NULL AND ac.trade_type != ''))
        GROUP BY setup_type
        HAVING n >= 5
        ORDER BY n DESC
    """, params)

    result = []
    for r in rows:
        wr   = (r["win_rate"] or 0) / 100
        lr   = 1 - wr
        aw   = r["avg_win"]  or 0
        al   = r["avg_loss"] or 0
        ev   = round(wr * aw + lr * al, 2)
        result.append({**r, "ev_usdt": ev, "ev_positive": ev > 0})
    return result


def get_rolling_stats(conn=None, filters=None, days: int = 30) -> dict:
    """
    Compare last `days` days vs all-time for win_rate, avg_rr, total_pnl.
    """
    if conn is None:
        conn = get_conn()
    from datetime import datetime as _dt, timedelta as _td

    def _stat(extra_where, extra_params):
        n   = _val(conn, f"SELECT COUNT(*) FROM positions {extra_where}", extra_params)
        wr  = _val(conn, f"SELECT ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/NULLIF(COUNT(*),0),1) FROM positions {extra_where}", extra_params)
        pnl = round(_val(conn, f"SELECT SUM(realized_pnl) FROM positions {extra_where}", extra_params), 4)
        aw  = round(_val(conn, f"SELECT AVG(realized_pnl) FROM positions {extra_where} {'AND' if extra_where else 'WHERE'} realized_pnl>0", extra_params), 4)
        al  = round(_val(conn, f"SELECT AVG(realized_pnl) FROM positions {extra_where} {'AND' if extra_where else 'WHERE'} realized_pnl<0", extra_params), 4)
        return {"trades": n, "win_rate": wr, "total_pnl": pnl, "avg_win": aw, "avg_loss": al}

    base_where, base_params = _build_where(filters or {})

    all_time = _stat(base_where, base_params)

    roll_from  = (_dt.utcnow() - _td(days=days)).strftime('%Y-%m-%d')
    roll_where, roll_params = _build_where({**(filters or {}), "date_from": roll_from})
    rolling = _stat(roll_where, roll_params)

    return {"days": days, "rolling": rolling, "all_time": all_time}


def get_accuracy_trend(conn=None, filters=None, window_days: int = 30) -> list:
    """
    Rolling analyst accuracy: for each calendar month, compute what % of
    setup scores >= threshold actually hit TP1 (True Positive rate).
    Returns list of {month, tp_rate, fp_rate, n} sorted ascending.
    threshold defaults to 6 (min tradeable score).
    Note: only the exchange filter is safe to apply here — other filters
    (symbol, direction, date_from/to) reference positions columns that
    don't exist on analyzed_calls.
    """
    if conn is None:
        conn = get_conn()
    # Only apply exchange filter — analyzed_calls has its own date column (created_at)
    # and does not have close_time, so the standard filters would cause OperationalError.
    exch = (filters or {}).get("exchange")
    if exch in ("bitget", "blofin"):
        where  = "WHERE COALESCE(exchange, 'bitget') = ?"
        params = [exch]
        and_   = "AND"
    else:
        where  = ""
        params = []
        and_   = "WHERE"

    rows = _rows(conn, f"""
        SELECT strftime('%Y-%m', created_at) AS month,
               COUNT(*) AS n,
               SUM(CASE WHEN hit_tp1=1 THEN 1 ELSE 0 END) AS tp_count,
               SUM(CASE WHEN hit_sl=1  THEN 1 ELSE 0 END) AS fp_count
        FROM analyzed_calls
        {where} {and_} outcome IS NOT NULL AND setup_score >= 6
        GROUP BY month
        ORDER BY month ASC
    """, params)

    result = []
    for r in rows:
        n = r["n"] or 0
        if n < 3:
            continue
        result.append({
            "month":    r["month"],
            "n":        n,
            "tp_rate":  round(r["tp_count"] / n * 100, 1),
            "fp_rate":  round(r["fp_count"] / n * 100, 1),
        })
    return result


def get_sharpe_calmar(conn=None, filters=None) -> dict:
    """
    Compute Sharpe ratio (annualised daily returns / std) and
    Calmar ratio (annualised return / max drawdown) from wallet_snapshots.
    """
    import math
    if conn is None:
        conn = get_conn()

    rows = _rows(conn, """
        SELECT date, wallet_balance
        FROM wallet_snapshots
        WHERE wallet_balance IS NOT NULL AND wallet_balance > 1
        ORDER BY date ASC
    """)

    if len(rows) < 10:
        return {"sharpe": None, "calmar": None, "message": "Insufficient wallet history"}

    balances = [r["wallet_balance"] for r in rows]
    # Daily returns — take last balance per calendar day; skip transitions from dust (<$1)
    daily: dict = {}
    for r in rows:
        d = r["date"][:10]
        daily[d] = r["wallet_balance"]  # last balance of each day

    sorted_days = sorted(daily.keys())
    if len(sorted_days) < 10:
        return {"sharpe": None, "calmar": None, "message": "Insufficient daily data"}

    daily_returns = []
    for i in range(1, len(sorted_days)):
        prev = daily[sorted_days[i - 1]]
        curr = daily[sorted_days[i]]
        if prev > 1.0:  # guard against dust/zero balances corrupting returns
            daily_returns.append((curr - prev) / prev)

    if not daily_returns:
        return {"sharpe": None, "calmar": None}

    n        = len(daily_returns)
    mean_ret = sum(daily_returns) / n
    # sample variance (N-1) — standard for estimating population std from a sample
    variance = sum((r - mean_ret) ** 2 for r in daily_returns) / (n - 1) if n > 1 else 0
    std_ret  = math.sqrt(variance) if variance > 0 else 0

    ann_return = mean_ret * 365
    ann_std    = std_ret  * math.sqrt(365)
    sharpe = round(ann_return / ann_std, 3) if ann_std > 0 else None

    # Max drawdown from wallet curve — percentage relative to the running peak at each step
    peak = balances[0]
    max_dd_pct_raw = 0.0
    for b in balances:
        if b > peak:
            peak = b
        if peak > 0:
            dd_pct = (peak - b) / peak * 100
            if dd_pct > max_dd_pct_raw:
                max_dd_pct_raw = dd_pct

    max_dd_pct = round(max_dd_pct_raw, 2)
    calmar     = round(ann_return * 100 / max_dd_pct, 3) if max_dd_pct > 0 else None

    return {
        "sharpe":       sharpe,
        "calmar":       calmar,
        "ann_return_pct": round(ann_return * 100, 2),
        "ann_volatility_pct": round(ann_std * 100, 2),
        "max_drawdown_pct":   max_dd_pct,
        "days_analyzed":      len(sorted_days),
    }


def get_backtest_context(conn, symbol: str = None, direction: str = None,
                         setup_type: str = None) -> str:
    """
    Compact historical performance summary for injection into AI prompts.

    Returns a ~200-400 char block that gives Claude pattern insights from
    actual trade history: setup accuracy, symbol-specific WR, timing warnings.
    The caller decides whether to include this in the cached or dynamic section.
    Empty string if fewer than 5 historical trades exist.
    """
    try:
        lines = []

        # 1. Overall recent performance (last 20 closed trades)
        recent = _rows(conn, """
            SELECT realized_pnl FROM positions
            WHERE realized_pnl IS NOT NULL
            ORDER BY close_time DESC LIMIT 20
        """)
        if len(recent) < 5:
            return ""

        pnl_list = [r["realized_pnl"] for r in recent]
        recent_wins = sum(1 for p in pnl_list if p > 0)
        recent_wr   = round(recent_wins / len(pnl_list) * 100)
        recent_avg  = round(sum(pnl_list) / len(pnl_list), 2)
        streak_last5 = "".join("W" if p > 0 else "L" for p in pnl_list[:5])
        lines.append(f"Recent form: {recent_wr}% WR last {len(pnl_list)} · streak {streak_last5} · avg ${recent_avg:+.2f}")

        # 2. Setup-type performance (if known)
        if setup_type:
            st_rows = _rows(conn, """
                SELECT realized_pnl FROM positions
                WHERE setup_type = ? AND realized_pnl IS NOT NULL
            """, (setup_type,))
            if len(st_rows) >= 3:
                st_wins = sum(1 for r in st_rows if r["realized_pnl"] > 0)
                st_wr   = round(st_wins / len(st_rows) * 100)
                st_avg  = round(sum(r["realized_pnl"] for r in st_rows) / len(st_rows), 2)
                lines.append(f"{setup_type}: {st_wr}% WR ({len(st_rows)} trades) avg ${st_avg:+.2f}")

        # 3. Symbol+direction history (if provided)
        if symbol:
            sym_rows = _rows(conn, """
                SELECT realized_pnl FROM positions
                WHERE symbol = ? AND direction = ? AND realized_pnl IS NOT NULL
                ORDER BY close_time DESC LIMIT 15
            """, (symbol, direction or "Long"))
            if len(sym_rows) >= 2:
                sw = sum(1 for r in sym_rows if r["realized_pnl"] > 0)
                sw_wr  = round(sw / len(sym_rows) * 100)
                sw_avg = round(sum(r["realized_pnl"] for r in sym_rows) / len(sym_rows), 2)
                lines.append(f"{symbol} {direction or 'Long'}: {sw_wr}% WR ({len(sym_rows)} trades) avg ${sw_avg:+.2f}")

        # 4. Worst weekday / hour warnings (only if meaningfully bad)
        import datetime as _dt
        now       = _dt.datetime.utcnow()
        weekdays  = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
        today_str = weekdays[now.weekday()]

        day_row = conn.execute("""
            SELECT SUM(realized_pnl) AS total, COUNT(*) AS n,
                   AVG(CASE WHEN realized_pnl > 0 THEN 1.0 ELSE 0.0 END) * 100 AS wr
            FROM positions
            WHERE strftime('%w', close_time) = ? AND realized_pnl IS NOT NULL
        """, (str((now.weekday() + 1) % 7),)).fetchone()  # SQLite %w: 0=Sun
        if day_row and day_row["n"] and day_row["n"] >= 5:
            day_pnl = round(day_row["total"] or 0, 2)
            day_wr  = round(day_row["wr"] or 0)
            if day_pnl < -100 or day_wr < 55:
                lines.append(f"⚠ {today_str}: caution ({day_wr}% WR, ${day_pnl:+.0f} total)")

        hour_row = conn.execute("""
            SELECT SUM(realized_pnl) AS total, COUNT(*) AS n,
                   AVG(CASE WHEN realized_pnl > 0 THEN 1.0 ELSE 0.0 END) * 100 AS wr
            FROM positions
            WHERE CAST(strftime('%H', close_time) AS INTEGER) = ? AND realized_pnl IS NOT NULL
        """, (now.hour,)).fetchone()
        if hour_row and hour_row["n"] and hour_row["n"] >= 5:
            hr_pnl = round(hour_row["total"] or 0, 2)
            hr_wr  = round(hour_row["wr"] or 0)
            if hr_pnl < -100 or hr_wr < 55:
                lines.append(f"⚠ {now.hour:02d}:00 UTC: weak hour ({hr_wr}% WR, ${hr_pnl:+.0f} total)")

        if not lines:
            return ""
        return "BACKTEST INSIGHTS:\n" + "\n".join(f"  {l}" for l in lines)

    except Exception:
        return ""


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


def get_benchmark_comparison(filters=None, conn=None) -> dict:
    """
    Compare trader cumulative P&L against BTC buy-and-hold over the same period.
    Uses yfinance BTC-USD daily closes — free, no auth required.

    Returns trader_return_pct, btc_return_pct, alpha_pct (trader - BTC),
    period_days, start_date, end_date, btc_start, btc_end,
    assumed_capital, available.
    """
    import datetime as _dt
    if filters is None:
        filters = {}
    if conn is None:
        conn = get_conn()

    # Apply exchange filter so benchmark respects the same filter as other analytics
    exchange_clause = ""
    exchange_params = []
    if filters.get("exchange") in ("bitget", "blofin"):
        exchange_clause = "AND COALESCE(exchange, 'bitget') = ?"
        exchange_params = [filters["exchange"]]

    row = conn.execute(f"""
        SELECT MIN(date(close_time)) AS first_date,
               MAX(date(close_time)) AS last_date,
               SUM(realized_pnl)     AS total_pnl,
               AVG(size_usdt)        AS avg_size
        FROM positions
        WHERE realized_pnl IS NOT NULL {exchange_clause}
    """, exchange_params).fetchone()

    if not row or not row["first_date"]:
        return {"trader_return_pct": 0.0, "btc_return_pct": 0.0,
                "alpha_pct": 0.0, "period_days": 0,
                "start_date": None, "end_date": None,
                "btc_start": None, "btc_end": None,
                "assumed_capital": 1000.0, "available": False}

    start_date = row["first_date"]
    end_date   = row["last_date"]
    total_pnl  = float(row["total_pnl"] or 0)
    avg_size   = float(row["avg_size"] or 200)
    assumed_capital = max(avg_size * 5, 1000.0)
    trader_return_pct = round(total_pnl / assumed_capital * 100, 2)

    btc_start = btc_end = None
    btc_return_pct = 0.0
    try:
        end_plus1 = (_dt.datetime.strptime(end_date, "%Y-%m-%d") +
                     _dt.timedelta(days=1)).strftime("%Y-%m-%d")
        btc_data = yf.download("BTC-USD", start=start_date, end=end_plus1,
                               progress=False, auto_adjust=True)
        if not btc_data.empty and "Close" in btc_data.columns:
            close = btc_data["Close"].dropna()
            btc_start = float(close.iloc[0])
            btc_end   = float(close.iloc[-1])
            btc_return_pct = round((btc_end - btc_start) / btc_start * 100, 2)
    except Exception:
        pass

    try:
        period_days = (_dt.datetime.strptime(end_date, "%Y-%m-%d") -
                       _dt.datetime.strptime(start_date, "%Y-%m-%d")).days + 1
    except Exception:
        period_days = 0

    # available = True if we have trade data, regardless of BTC fetch success
    return {
        "trader_return_pct": trader_return_pct,
        "btc_return_pct":    btc_return_pct,
        "alpha_pct":         round(trader_return_pct - btc_return_pct, 2),
        "period_days":       period_days,
        "start_date":        start_date,
        "end_date":          end_date,
        "btc_start":         btc_start,
        "btc_end":           btc_end,
        "assumed_capital":   int(round(assumed_capital, 0)),
        "available":         row["first_date"] is not None,
    }


def get_execution_quality(conn=None) -> dict:
    """
    Execution quality: time lag between scanner signal and actual entry,
    and price slippage (entry_price vs signal_price).
    Positive slippage for longs = you paid more than the signal price (bad).
    """
    import statistics as _st
    if conn is None:
        conn = get_conn()

    rows = _rows(conn, """
        SELECT execution_lag_minutes, signal_price, entry_price, direction
        FROM positions
        WHERE execution_lag_minutes IS NOT NULL AND realized_pnl IS NOT NULL
        ORDER BY close_time DESC LIMIT 200
    """)

    if not rows:
        return {"avg_lag_minutes": None, "median_lag_minutes": None,
                "avg_slippage_pct": None, "sample_size": 0, "available": False}

    lags = [r["execution_lag_minutes"] for r in rows if r["execution_lag_minutes"] is not None]
    slippages = []
    for r in rows:
        sp = r.get("signal_price")
        ep = r.get("entry_price")
        if sp and ep and float(sp) > 0:
            is_long = (r.get("direction") or "Long").lower() == "long"
            raw = (float(ep) - float(sp)) / float(sp) * 100
            slippages.append(raw if is_long else -raw)

    return {
        "avg_lag_minutes":    round(_st.mean(lags), 1)      if lags else None,
        "median_lag_minutes": round(_st.median(lags), 1)    if lags else None,
        "avg_slippage_pct":   round(_st.mean(slippages), 3) if slippages else None,
        "sample_size":        len(lags),
        "available":          bool(lags),
        "lag_distribution": {
            "under_30m": sum(1 for l in lags if l < 30),
            "30m_to_2h": sum(1 for l in lags if 30 <= l < 120),
            "2h_to_8h":  sum(1 for l in lags if 120 <= l < 480),
            "over_8h":   sum(1 for l in lags if l >= 480),
        },
    }


def get_tearsheet_metrics(conn=None) -> dict:
    """
    Build a daily returns series from wallet_snapshots and compute
    professional performance metrics. Full HTML tearsheet via /tearsheet/download.
    Requires at least 20 distinct trading days of wallet snapshot data.
    """
    import pandas as pd, statistics as _st

    if conn is None:
        conn = get_conn()

    rows = _rows(conn, """
        SELECT date(date) AS day, MAX(wallet_balance) AS balance
        FROM wallet_snapshots
        WHERE wallet_balance IS NOT NULL AND wallet_balance > 1
        GROUP BY day
        ORDER BY day ASC
    """)

    if len(rows) < 20:
        return {"available": False, "reason": f"Need 20+ days of data, have {len(rows)}"}

    balances = pd.Series(
        [float(r["balance"]) for r in rows],
        index=pd.to_datetime([r["day"] for r in rows]),
        dtype=float,
    )
    returns = balances.pct_change().dropna()

    if returns.empty or float(returns.std()) == 0:
        return {"available": False, "reason": "Insufficient variance in returns"}

    ann = 365
    mean_d = float(returns.mean())
    std_d  = float(returns.std())
    sharpe = round(mean_d / std_d * (ann ** 0.5), 2) if std_d > 0 else 0.0

    cum   = (1 + returns).cumprod()
    peak  = cum.cummax()
    max_dd = round(float(((cum - peak) / peak).min()) * 100, 2)

    n_years = len(returns) / ann
    cagr = round((float(cum.iloc[-1]) ** (1 / n_years) - 1) * 100, 2) if n_years > 0 else 0.0
    vol  = round(std_d * (ann ** 0.5) * 100, 2)

    wins     = returns[returns > 0]
    losses   = returns[returns < 0]
    win_rate = round(len(wins) / len(returns) * 100, 1) if len(returns) else 0.0
    pf       = round(float(wins.sum()) / abs(float(losses.sum())), 2) if float(losses.sum()) != 0 else 999.0

    monthly = returns.resample("ME").apply(lambda x: float((1 + x).prod() - 1))
    monthly_dict = {str(k.date())[:7]: round(float(v) * 100, 2) for k, v in monthly.items()}

    return {
        "available":          True,
        "sharpe":             sharpe,
        "max_drawdown_pct":   max_dd,
        "cagr_pct":           cagr,
        "volatility_pct":     vol,
        "win_rate_daily":     win_rate,
        "profit_factor":      pf,
        "total_days":         len(returns),
        "start_balance":      round(float(balances.iloc[0]), 2),
        "end_balance":        round(float(balances.iloc[-1]), 2),
        "total_return_pct":   round((float(balances.iloc[-1]) / float(balances.iloc[0]) - 1) * 100, 2),
        "monthly_returns":    monthly_dict,
    }
