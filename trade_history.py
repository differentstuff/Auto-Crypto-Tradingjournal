"""
Unified symbol trade history queries.
Replaces the four private _symbol_history() copies in:
  ai_call.py, ai_scanner.py, ai_hindsight.py, ai_live_trade.py
"""
from __future__ import annotations
import sqlite3


def get_recent_trades(
    symbol: str,
    conn: sqlite3.Connection,
    before_iso: str | None = None,
    exchange: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """
    Return the most recent closed trades for a symbol.

    Args:
        symbol:     e.g. "BTCUSDT"
        conn:       open SQLite connection
        before_iso: if set, only trades whose open_time < before_iso (for blind scoring)
        exchange:   if set, filter to one exchange
        limit:      max rows to return
    """
    conditions = ["close_time IS NOT NULL", "symbol = ?"]
    params: list = [symbol]

    if before_iso:
        conditions.append("open_time < ?")
        params.append(before_iso)

    if exchange:
        conditions.append("(COALESCE(exchange,'bitget') = ?)")
        params.append(exchange)

    where = " AND ".join(conditions)
    params.append(limit)

    _cols = ["symbol", "direction", "realized_pnl", "duration_minutes",
             "entry_price", "close_price", "open_time", "close_time", "exchange"]
    cur = conn.execute(
        f"SELECT symbol, direction, realized_pnl, duration_minutes, "
        f"       entry_price, close_price, open_time, close_time, exchange "
        f"FROM positions WHERE {where} "
        f"ORDER BY close_time DESC LIMIT ?",
        params,
    )
    rows = cur.fetchall()

    # Support both sqlite3.Row (dict-like) and plain tuple rows
    if rows and isinstance(rows[0], sqlite3.Row):
        return [dict(r) for r in rows]
    return [dict(zip(_cols, r)) for r in rows]


def get_trade_stats(trades: list[dict]) -> dict:
    """
    Compute win-rate and P&L stats from a trade list.

    Args:
        trades: output of get_recent_trades()

    Returns:
        {trades, wins, losses, win_rate_pct, total_pnl, avg_pnl, avg_win, avg_loss}
    """
    if not trades:
        return {
            "trades": 0, "wins": 0, "losses": 0,
            "win_rate_pct": 0, "total_pnl": 0.0,
            "avg_pnl": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
        }

    pnls   = [t.get("realized_pnl") or 0.0 for t in trades]
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    n      = len(pnls)

    return {
        "trades":       n,
        "wins":         len(wins),
        "losses":       len(losses),
        "win_rate_pct": round(len(wins) / n * 100, 1) if n else 0,
        "total_pnl":    round(sum(pnls), 2),
        "avg_pnl":      round(sum(pnls) / n, 2) if n else 0.0,
        "avg_win":      round(sum(wins)   / len(wins),   2) if wins   else 0.0,
        "avg_loss":     round(sum(losses) / len(losses), 2) if losses else 0.0,
    }


def get_symbol_summary(
    symbol: str,
    conn: sqlite3.Connection,
    before_iso: str | None = None,
    exchange: str | None = None,
    limit: int = 20,
) -> dict:
    """
    Convenience: get_recent_trades + get_trade_stats in one call.
    Drop-in replacement for the old _symbol_history() return shape.
    """
    trades = get_recent_trades(symbol, conn, before_iso=before_iso,
                               exchange=exchange, limit=limit)
    stats  = get_trade_stats(trades)
    return {"recent_trades": trades, **stats}
