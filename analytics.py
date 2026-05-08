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
    from datetime import datetime as _dt
    month_start  = _dt.utcnow().strftime('%Y-%m-01')
    mo_where, mo_params = _build_where({**filters, "date_from": month_start})
    # _build_where turns date_from into close_time >= ?, so we get the exchange clause too
    current_month_pnl = round(_val(conn,
        f"SELECT SUM(realized_pnl) FROM positions {mo_where}",
        mo_params
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
        {where} {and_} (p.setup_type IS NOT NULL AND p.setup_type != '')
                     OR (ac.trade_type IS NOT NULL AND ac.trade_type != '')
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
    from datetime import datetime as _dt
    cutoff = _dt.utcnow().strftime(f'%Y-%m-%d')

    def _stat(extra_where, extra_params):
        n   = _val(conn, f"SELECT COUNT(*) FROM positions {extra_where}", extra_params)
        wr  = _val(conn, f"SELECT ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/NULLIF(COUNT(*),0),1) FROM positions {extra_where}", extra_params)
        pnl = round(_val(conn, f"SELECT SUM(realized_pnl) FROM positions {extra_where}", extra_params), 4)
        aw  = round(_val(conn, f"SELECT AVG(realized_pnl) FROM positions {extra_where} {'AND' if extra_where else 'WHERE'} realized_pnl>0", extra_params), 4)
        al  = round(_val(conn, f"SELECT AVG(realized_pnl) FROM positions {extra_where} {'AND' if extra_where else 'WHERE'} realized_pnl<0", extra_params), 4)
        return {"trades": n, "win_rate": wr, "total_pnl": pnl, "avg_win": aw, "avg_loss": al}

    base_where, base_params = _build_where(filters or {})
    and_ = "AND" if base_where else "WHERE"

    all_time = _stat(base_where, base_params)

    # Build rolling filter by appending date constraint
    roll_filters = {**(filters or {}), "date_from": _dt.utcnow().strftime(f'%Y-%m-%d')}
    from datetime import timedelta as _td
    roll_from = (_dt.utcnow() - _td(days=days)).strftime('%Y-%m-%d')
    roll_where, roll_params = _build_where({**(filters or {}), "date_from": roll_from})
    rolling = _stat(roll_where, roll_params)

    return {"days": days, "rolling": rolling, "all_time": all_time}


def get_accuracy_trend(conn=None, filters=None, window_days: int = 30) -> list:
    """
    Rolling analyst accuracy: for each calendar month, compute what % of
    setup scores >= threshold actually hit TP1 (True Positive rate).
    Returns list of {month, tp_rate, fp_rate, n} sorted ascending.
    threshold defaults to 6 (min tradeable score).
    """
    if conn is None:
        conn = get_conn()
    where, params = _build_where(filters or {})
    and_ = "AND" if where else "WHERE"

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
        WHERE wallet_balance IS NOT NULL
        ORDER BY date ASC
    """)

    if len(rows) < 10:
        return {"sharpe": None, "calmar": None, "message": "Insufficient wallet history"}

    balances = [r["wallet_balance"] for r in rows]
    # Daily returns (approximate — wallet_snapshots may have multiple entries per day)
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
        if prev and prev > 0:
            daily_returns.append((curr - prev) / prev)

    if not daily_returns:
        return {"sharpe": None, "calmar": None}

    mean_ret = sum(daily_returns) / len(daily_returns)
    variance = sum((r - mean_ret) ** 2 for r in daily_returns) / len(daily_returns)
    std_ret  = math.sqrt(variance) if variance > 0 else 0

    ann_return = mean_ret * 365
    ann_std    = std_ret  * math.sqrt(365)
    sharpe = round(ann_return / ann_std, 3) if ann_std > 0 else None

    # Max drawdown from wallet curve
    peak = balances[0]
    max_dd_abs = 0
    for b in balances:
        if b > peak:
            peak = b
        dd = peak - b
        if dd > max_dd_abs:
            max_dd_abs = dd

    max_dd_pct = round(max_dd_abs / peak * 100, 2) if peak > 0 else 0
    calmar     = round(ann_return * 100 / max_dd_pct, 3) if max_dd_pct > 0 else None

    return {
        "sharpe":       sharpe,
        "calmar":       calmar,
        "ann_return_pct": round(ann_return * 100, 2),
        "ann_volatility_pct": round(ann_std * 100, 2),
        "max_drawdown_pct":   max_dd_pct,
        "days_analyzed":      len(sorted_days),
    }


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
