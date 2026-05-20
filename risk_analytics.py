"""
risk_analytics.py — Portfolio risk metrics using free Binance public data.

All functions are pure (no DB access except compute_pnl_attribution,
compute_kelly_by_bucket, compute_alpha_decay which need historical trade data).
OHLCV data from Binance futures public endpoint via ccxt.

Public API:
  compute_portfolio_var(positions, equity) -> dict
  compute_correlation_matrix(positions)    -> dict
  compute_pnl_attribution(conn, days)      -> dict
  compute_kelly_by_bucket(conn)            -> dict
  compute_alpha_decay(conn)                -> dict
"""
import numpy as np
import pandas as pd
import yfinance as yf


def _fetch_ohlcv_df(symbol: str, tf: str = "4H", limit: int = 500) -> pd.DataFrame:
    """
    Fetch OHLCV from Binance futures public API (free, no auth).
    Returns DataFrame with columns: close, volume. Index: datetime.
    Mockable in tests via monkeypatch("risk_analytics._fetch_ohlcv_df", ...).
    """
    try:
        import ccxt as _ccxt
        ex = _ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "future"}})
        ccxt_sym = symbol.replace("USDT", "/USDT:USDT")
        raw = ex.fetch_ohlcv(ccxt_sym, tf, limit=limit)
        if not raw:
            return pd.DataFrame()
        df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
        df.index = pd.to_datetime(df["ts"], unit="ms")
        return df[["close", "volume"]].astype(float)
    except Exception:
        return pd.DataFrame()


def _daily_returns(symbol: str, lookback_days: int = 90) -> pd.Series:
    """Return daily return series for a symbol, resampled from 4H OHLCV."""
    limit = lookback_days * 6 + 10
    df = _fetch_ohlcv_df(symbol, tf="4H", limit=limit)
    if df.empty:
        return pd.Series(dtype=float)
    daily = df["close"].resample("D").last().dropna()
    return daily.pct_change().dropna()


def compute_portfolio_var(positions: list, equity: float,
                          lookback_days: int = 90) -> dict:
    """
    Historical simulation VaR on the current open portfolio.
    Fetches 90 days of Binance 4H OHLCV (free, public endpoint).
    Returns var_95_usd, var_99_usd, var_95_pct, var_99_pct, total_notional,
    horizon_days, sample_days, available.
    """
    if not positions:
        return {"var_95_usd": 0.0, "var_99_usd": 0.0,
                "var_95_pct": 0.0, "var_99_pct": 0.0,
                "total_notional": 0.0, "horizon_days": 1,
                "sample_days": 0, "available": False}

    total_notional = sum(float(p.get("size_usdt") or 0) for p in positions)
    if total_notional <= 0:
        return {"var_95_usd": 0.0, "var_99_usd": 0.0,
                "var_95_pct": 0.0, "var_99_pct": 0.0,
                "total_notional": 0.0, "horizon_days": 1,
                "sample_days": 0, "available": False}

    returns_dict: dict[str, pd.Series] = {}
    for p in positions:
        sym = p.get("symbol", "")
        if not sym:
            continue
        r = _daily_returns(sym, lookback_days)
        if not r.empty:
            direction = (p.get("direction") or "Long").lower()
            returns_dict[sym] = r if direction == "long" else -r

    if not returns_dict:
        return {"var_95_usd": 0.0, "var_99_usd": 0.0,
                "var_95_pct": 0.0, "var_99_pct": 0.0,
                "total_notional": round(total_notional, 2),
                "horizon_days": 1, "sample_days": 0, "available": False}

    df = pd.DataFrame(returns_dict).dropna()
    if df.empty or len(df) < 10:
        return {"var_95_usd": 0.0, "var_99_usd": 0.0,
                "var_95_pct": 0.0, "var_99_pct": 0.0,
                "total_notional": round(total_notional, 2),
                "horizon_days": 1, "sample_days": len(df), "available": False}

    weights = {sym: float(p.get("size_usdt") or 0) / total_notional
               for p in positions
               for sym in [p.get("symbol", "")]
               if sym in returns_dict}

    portfolio_returns = sum(df[sym] * w for sym, w in weights.items() if sym in df.columns)

    pct_95 = float(np.percentile(portfolio_returns, 5))
    pct_99 = float(np.percentile(portfolio_returns, 1))

    return {
        "var_95_usd":     round(abs(pct_95) * total_notional, 2),
        "var_99_usd":     round(abs(pct_99) * total_notional, 2),
        "var_95_pct":     round(abs(pct_95) * 100, 2),
        "var_99_pct":     round(abs(pct_99) * 100, 2),
        "total_notional": round(total_notional, 2),
        "horizon_days":   1,
        "sample_days":    len(portfolio_returns),
        "available":      True,
    }


def compute_correlation_matrix(positions: list, lookback_days: int = 30) -> dict:
    """
    Pairwise Pearson correlation between open positions using 30-day daily returns.
    Flags high-risk pairs (correlation > 0.70, same direction).
    Returns matrix, high_risk_pairs, symbols, lookback_days, sample_days, available.
    """
    if len(positions) < 2:
        return {"matrix": [], "high_risk_pairs": [], "available": False,
                "reason": "Need at least 2 open positions"}

    returns_dict = {}
    for p in positions:
        sym = p.get("symbol", "")
        if not sym or sym in returns_dict:
            continue
        r = _daily_returns(sym, lookback_days)
        if not r.empty:
            returns_dict[sym] = r

    if len(returns_dict) < 2:
        return {"matrix": [], "high_risk_pairs": [], "available": False,
                "reason": "Insufficient price history for correlation"}

    df = pd.DataFrame(returns_dict).dropna()
    if len(df) < 5:
        return {"matrix": [], "high_risk_pairs": [], "available": False,
                "reason": f"Only {len(df)} days of aligned data"}

    corr = df.corr()
    symbols = list(corr.columns)
    matrix = []
    for i, sa in enumerate(symbols):
        for sb in symbols[i+1:]:
            matrix.append({"symbol_a": sa, "symbol_b": sb,
                            "correlation": round(float(corr.loc[sa, sb]), 3)})
    matrix.sort(key=lambda x: abs(x["correlation"]), reverse=True)

    dir_map = {p["symbol"]: (p.get("direction") or "Long").lower() for p in positions}
    high_risk_pairs = [
        m for m in matrix
        if abs(m["correlation"]) > 0.70
        and dir_map.get(m["symbol_a"]) == dir_map.get(m["symbol_b"])
    ]

    return {"matrix": matrix, "high_risk_pairs": high_risk_pairs, "symbols": symbols,
            "lookback_days": lookback_days, "sample_days": len(df), "available": True}


def compute_pnl_attribution(conn, lookback_days: int = 90) -> dict:
    """
    Decompose P&L into alpha (skill) and beta (BTC market move).
    For each closed position: beta_contribution = size_usdt * btc_return_during_trade.
    alpha = realized_pnl - beta_contribution.
    Uses yfinance BTC-USD (free). Returns alpha_pnl, beta_pnl, total_pnl,
    alpha_pct, sample_size, attributed, available.
    """
    import datetime as _dt

    rows = conn.execute("""
        SELECT id, symbol, direction, realized_pnl, size_usdt,
               date(open_time) AS open_date, date(close_time) AS close_date
        FROM positions
        WHERE realized_pnl IS NOT NULL AND size_usdt > 0
          AND open_time IS NOT NULL AND close_time IS NOT NULL
          AND close_time >= datetime('now', ? || ' days')
        ORDER BY close_time DESC LIMIT 200
    """, (str(-lookback_days),)).fetchall()

    if not rows:
        return {"alpha_pnl": 0.0, "beta_pnl": 0.0, "total_pnl": 0.0,
                "alpha_pct": 0.0, "sample_size": 0, "attributed": 0, "available": False}

    min_date = min(r["open_date"] for r in rows)
    max_date = max(r["close_date"] for r in rows)
    btc_close = pd.Series(dtype=float)
    try:
        end_plus1 = (_dt.datetime.strptime(max_date, "%Y-%m-%d") +
                     _dt.timedelta(days=1)).strftime("%Y-%m-%d")
        btc = yf.download("BTC-USD", start=min_date, end=end_plus1,
                          progress=False, auto_adjust=True)
        # yfinance 1.x returns MultiIndex columns for single-ticker downloads — flatten
        import pandas as _pd
        if isinstance(btc.columns, _pd.MultiIndex):
            btc.columns = btc.columns.get_level_values(0)
        if not btc.empty and "Close" in btc.columns:
            btc_close = btc["Close"].dropna()
    except Exception:
        pass

    alpha_pnl = beta_pnl = total_pnl = 0.0
    attributed = 0
    for r in rows:
        pnl  = float(r["realized_pnl"])
        size = float(r["size_usdt"])
        total_pnl += pnl
        if btc_close.empty:
            alpha_pnl += pnl
            continue
        try:
            btc_o = float(btc_close.asof(pd.Timestamp(r["open_date"])))
            btc_c = float(btc_close.asof(pd.Timestamp(r["close_date"])))
            if btc_o and btc_c:
                btc_ret = (btc_c - btc_o) / btc_o
                is_long = (r["direction"] or "Long").lower() == "long"
                beta_contribution = size * btc_ret * (1 if is_long else -1)
                beta_pnl  += beta_contribution
                alpha_pnl += pnl - beta_contribution
                attributed += 1
            else:
                alpha_pnl += pnl
        except Exception:
            alpha_pnl += pnl

    alpha_pct = round(alpha_pnl / abs(total_pnl) * 100, 1) if total_pnl else 0.0
    return {"alpha_pnl": round(alpha_pnl, 2), "beta_pnl": round(beta_pnl, 2),
            "total_pnl": round(total_pnl, 2), "alpha_pct": alpha_pct,
            "sample_size": len(rows), "attributed": attributed,
            "available": attributed > 0, "lookback_days": lookback_days}


def compute_kelly_by_bucket(conn) -> dict:
    """
    Compute half-Kelly fraction per setup score bucket from historical trade data.
    Kelly f = (win_rate * avg_win - loss_rate * avg_loss) / avg_win, capped at 20%.
    Returns buckets [{score_range, trade_count, win_rate, avg_win_usd, avg_loss_usd,
    kelly_full_pct, kelly_half_pct, recommended_size_pct}] and available.
    """
    rows = conn.execute("""
        SELECT
            CASE
                WHEN setup_score <= 6 THEN '6'
                WHEN setup_score <= 8 THEN '7-8'
                ELSE '9-10'
            END AS bucket,
            COUNT(*) AS n,
            AVG(CASE WHEN realized_pnl > 0 THEN 1.0 ELSE 0.0 END) AS win_rate,
            AVG(CASE WHEN realized_pnl > 0 THEN realized_pnl END)  AS avg_win,
            AVG(CASE WHEN realized_pnl < 0 THEN realized_pnl END)  AS avg_loss
        FROM positions
        WHERE setup_score IS NOT NULL AND realized_pnl IS NOT NULL
        GROUP BY bucket HAVING COUNT(*) >= 5 ORDER BY bucket
    """).fetchall()

    if not rows:
        overall = conn.execute("""
            SELECT COUNT(*) AS n,
                   AVG(CASE WHEN realized_pnl > 0 THEN 1.0 ELSE 0.0 END) AS win_rate,
                   AVG(CASE WHEN realized_pnl > 0 THEN realized_pnl END)  AS avg_win,
                   AVG(CASE WHEN realized_pnl < 0 THEN realized_pnl END)  AS avg_loss
            FROM positions WHERE realized_pnl IS NOT NULL
        """).fetchone()
        if not overall or not overall["n"] or overall["n"] < 5:
            return {"buckets": [], "available": False,
                    "reason": "Need at least 5 trades with setup_score"}
        rows = [{"bucket": "all", **dict(overall)}]

    buckets = []
    for r in rows:
        wr = float(r["win_rate"] or 0)
        lr = 1 - wr
        aw = float(r["avg_win"]  or 0)
        al = abs(float(r["avg_loss"] or 1))
        kelly_full = max(0.0, (wr * aw - lr * al) / aw) if aw > 0 else 0.0
        kelly_half = kelly_full / 2
        buckets.append({
            "score_range":          r["bucket"],
            "trade_count":          r["n"],
            "win_rate":             round(wr * 100, 1),
            "avg_win_usd":          round(aw, 2),
            "avg_loss_usd":         round(al, 2),
            "kelly_full_pct":       round(kelly_full * 100, 1),
            "kelly_half_pct":       round(kelly_half * 100, 1),
            "recommended_size_pct": min(round(kelly_half * 100, 1), 20.0),
        })
    return {"buckets": buckets, "available": True}


def compute_alpha_decay(conn) -> dict:
    """
    Measure how execution lag affects P&L. Groups trades by lag bucket.
    A negative correlation (P&L drops as lag increases) = edge decays.
    Returns lag_buckets, correlation, edge_decays, sample_size, available.
    """
    rows = conn.execute("""
        SELECT execution_lag_minutes, realized_pnl
        FROM positions
        WHERE execution_lag_minutes IS NOT NULL AND realized_pnl IS NOT NULL
        ORDER BY close_time DESC LIMIT 200
    """).fetchall()

    if len(rows) < 5:
        return {"lag_buckets": [], "correlation": None, "available": False,
                "reason": f"Need 5+ trades with execution lag data, have {len(rows)}"}

    lags = [float(r["execution_lag_minutes"]) for r in rows]
    pnls = [float(r["realized_pnl"]) for r in rows]

    try:
        corr = float(np.corrcoef(lags, pnls)[0, 1])
    except Exception:
        corr = None

    buckets_raw = {"< 30m": [], "30m-2h": [], "2h-8h": [], "> 8h": []}
    for lag, pnl in zip(lags, pnls):
        if lag < 30:             buckets_raw["< 30m"].append(pnl)
        elif lag < 120:          buckets_raw["30m-2h"].append(pnl)
        elif lag < 480:          buckets_raw["2h-8h"].append(pnl)
        else:                    buckets_raw["> 8h"].append(pnl)

    lag_buckets = []
    for label, ps in buckets_raw.items():
        if not ps:
            continue
        wins = sum(1 for p in ps if p > 0)
        lag_buckets.append({
            "lag_range":   label,
            "trade_count": len(ps),
            "avg_pnl":     round(sum(ps) / len(ps), 2),
            "win_rate":    round(wins / len(ps) * 100, 1),
        })

    return {"lag_buckets": lag_buckets, "correlation": round(corr, 3) if corr is not None else None,
            "edge_decays": corr is not None and corr < -0.15,
            "sample_size": len(rows), "available": True}
