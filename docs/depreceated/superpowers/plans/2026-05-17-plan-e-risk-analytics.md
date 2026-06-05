# Plan E — Risk Analytics

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add portfolio VaR, position correlation matrix, P&L attribution (alpha vs beta), Kelly Criterion sizing recommendations, and alpha decay measurement — all using free Binance public data via existing ccxt_client.

**Architecture:** Five independent analytics features. Each adds a new Python function + route + minimal UI. No new paid APIs. All OHLCV data from Binance futures public endpoint (already in ccxt_client). Correlation and VaR computed with numpy/pandas already installed.

**Tech Stack:** Python 3.13, Flask, SQLite, numpy, pandas, ccxt (already installed), existing ccxt_client.py, analytics.py, routes/live.py, routes/analytics.py.

**Prerequisite:** Plan D must be deployed first (provides execution_lag_minutes and signal_price columns).

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `risk_analytics.py` | **Create** | VaR, correlation, P&L attribution, Kelly, alpha decay — all in one focused module |
| `routes/risk.py` | **Create** | All 5 risk endpoints as a Flask blueprint |
| `app.py` | Modify | Register risk blueprint |
| `templates/index.html` | Modify | Add Risk tab containers |
| `tests/test_risk_analytics.py` | Create | Tests for all 5 functions |

Using one new file `risk_analytics.py` keeps risk logic isolated from the existing 888-line analytics.py.

---

## Task 1: Historical Simulation VaR

**Problem:** No forward-looking risk metric. The trader has no answer to: "What's the worst-case 24h loss on this portfolio at 95% confidence?"

**How it works:** Fetch 4H OHLCV for each open position symbol from Binance (free, already used by the scanner). Build a daily return distribution over 90 days. Apply portfolio weights. Compute 5th percentile (95% VaR) and 1st percentile (99% VaR) as dollar loss on current portfolio.

**Files:**
- Create: `risk_analytics.py` — `compute_portfolio_var(positions, equity)`
- Create: `routes/risk.py` — `GET /api/risk/var`
- Create: `tests/test_risk_analytics.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_risk_analytics.py
import sys, os, unittest.mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
import numpy as np
import pandas as pd


# ── Shared fixture: mock OHLCV fetcher ─────────────────────────────────────────
@pytest.fixture
def mock_ohlcv(monkeypatch):
    """Replace ccxt_client OHLCV fetch with deterministic 90-day returns."""
    import numpy as np, pandas as pd

    def fake_fetch(symbol, tf="4H", limit=500):
        rng = np.random.default_rng(int(hash(symbol) % 10000))
        closes = 100 * np.cumprod(1 + rng.normal(0.001, 0.02, limit))
        idx = pd.date_range(end=pd.Timestamp.now(), periods=limit, freq="4h")
        return pd.DataFrame({"close": closes, "volume": np.ones(limit) * 1e6}, index=idx)

    monkeypatch.setattr("risk_analytics._fetch_ohlcv_df", fake_fetch)
    return fake_fetch


def _make_positions(*symbols):
    return [
        {"symbol": s, "direction": "Long", "size_usdt": 500, "margin_usdt": 50}
        for s in symbols
    ]


def test_var_returns_required_keys(mock_ohlcv):
    from risk_analytics import compute_portfolio_var
    positions = _make_positions("BTCUSDT", "ETHUSDT")
    result = compute_portfolio_var(positions, equity=10000.0)
    assert "var_95_usd" in result
    assert "var_99_usd" in result
    assert "var_95_pct" in result
    assert "horizon_days" in result


def test_var_95_less_than_99(mock_ohlcv):
    from risk_analytics import compute_portfolio_var
    positions = _make_positions("BTCUSDT", "SOLUSDT")
    result = compute_portfolio_var(positions, equity=10000.0)
    # 99% VaR should be larger loss than 95% VaR
    assert result["var_99_usd"] >= result["var_95_usd"]


def test_var_empty_positions():
    from risk_analytics import compute_portfolio_var
    result = compute_portfolio_var([], equity=10000.0)
    assert result["var_95_usd"] == 0.0
    assert result["available"] is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
python3 -m pytest tests/test_risk_analytics.py::test_var_empty_positions -v
```
Expected: `ModuleNotFoundError: No module named 'risk_analytics'`

- [ ] **Step 3: Create risk_analytics.py**

```python
"""
risk_analytics.py — Portfolio risk metrics using free Binance public data.

All functions are pure (no DB access). Callers fetch positions from the live
API and pass them in. OHLCV data is fetched from Binance via ccxt (free, no auth).

Public API:
  compute_portfolio_var(positions, equity) -> dict
  compute_correlation_matrix(positions)    -> dict
  compute_pnl_attribution(positions)       -> dict  (requires DB conn)
  compute_kelly_by_bucket(conn)            -> dict
  compute_alpha_decay(conn)                -> dict
"""
import numpy as np
import pandas as pd
from typing import Optional


# ── OHLCV helper (mockable in tests) ──────────────────────────────────────────

def _fetch_ohlcv_df(symbol: str, tf: str = "4H", limit: int = 500) -> pd.DataFrame:
    """
    Fetch OHLCV from Binance futures (free, no auth).
    Returns DataFrame with columns: close, volume. Index: datetime.
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
    limit = lookback_days * 6 + 10  # 6 bars/day
    df = _fetch_ohlcv_df(symbol, tf="4H", limit=limit)
    if df.empty:
        return pd.Series(dtype=float)
    daily = df["close"].resample("D").last().dropna()
    return daily.pct_change().dropna()


# ── 1. Value at Risk ───────────────────────────────────────────────────────────

def compute_portfolio_var(positions: list, equity: float,
                          lookback_days: int = 90) -> dict:
    """
    Historical simulation VaR on the current open portfolio.

    Method: fetch 90 days of daily returns for each symbol, weight by
    size_usdt / total_notional, aggregate into a portfolio return series,
    compute 5th and 1st percentiles as dollar loss on total notional.

    Returns var_95_usd, var_99_usd, var_95_pct, var_99_pct, horizon_days,
    sample_days, available.
    """
    if not positions:
        return {"var_95_usd": 0.0, "var_99_usd": 0.0,
                "var_95_pct": 0.0, "var_99_pct": 0.0,
                "horizon_days": 1, "sample_days": 0, "available": False}

    total_notional = sum(float(p.get("size_usdt") or 0) for p in positions)
    if total_notional <= 0:
        return {"var_95_usd": 0.0, "var_99_usd": 0.0,
                "var_95_pct": 0.0, "var_99_pct": 0.0,
                "horizon_days": 1, "sample_days": 0, "available": False}

    returns_dict: dict[str, pd.Series] = {}
    for p in positions:
        sym = p.get("symbol", "")
        if not sym:
            continue
        r = _daily_returns(sym, lookback_days)
        if not r.empty:
            # Short positions have inverted returns
            direction = (p.get("direction") or "Long").lower()
            returns_dict[sym] = r if direction == "long" else -r

    if not returns_dict:
        return {"var_95_usd": 0.0, "var_99_usd": 0.0,
                "var_95_pct": 0.0, "var_99_pct": 0.0,
                "horizon_days": 1, "sample_days": 0, "available": False}

    df = pd.DataFrame(returns_dict).dropna()
    if df.empty or len(df) < 10:
        return {"var_95_usd": 0.0, "var_99_usd": 0.0,
                "var_95_pct": 0.0, "var_99_pct": 0.0,
                "horizon_days": 1, "sample_days": len(df), "available": False}

    # Weight each symbol by its share of total notional
    weights = {}
    for p in positions:
        sym = p.get("symbol", "")
        if sym in returns_dict:
            weights[sym] = float(p.get("size_usdt") or 0) / total_notional

    # Compute weighted portfolio return series
    portfolio_returns = sum(
        df[sym] * w for sym, w in weights.items() if sym in df.columns
    )

    pct_95 = float(np.percentile(portfolio_returns, 5))   # 5th pct = worst 5% days
    pct_99 = float(np.percentile(portfolio_returns, 1))   # 1st pct = worst 1% days

    return {
        "var_95_usd":    round(abs(pct_95) * total_notional, 2),
        "var_99_usd":    round(abs(pct_99) * total_notional, 2),
        "var_95_pct":    round(abs(pct_95) * 100, 2),
        "var_99_pct":    round(abs(pct_99) * 100, 2),
        "total_notional": round(total_notional, 2),
        "horizon_days":  1,
        "sample_days":   len(portfolio_returns),
        "available":     True,
    }


# ── 2. Correlation Matrix ──────────────────────────────────────────────────────

def compute_correlation_matrix(positions: list, lookback_days: int = 30) -> dict:
    """
    Compute pairwise Pearson correlation between open position symbols using
    30-day daily returns from Binance 4H data.

    Returns matrix [{symbol_a, symbol_b, correlation}], high_risk_pairs
    (pairs with correlation > 0.70 in same direction), and available.
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
            matrix.append({
                "symbol_a":    sa,
                "symbol_b":    sb,
                "correlation": round(float(corr.loc[sa, sb]), 3),
            })
    matrix.sort(key=lambda x: abs(x["correlation"]), reverse=True)

    # Direction map for high-risk flagging
    dir_map = {p["symbol"]: (p.get("direction") or "Long").lower() for p in positions}
    high_risk_pairs = [
        m for m in matrix
        if abs(m["correlation"]) > 0.70
        and dir_map.get(m["symbol_a"]) == dir_map.get(m["symbol_b"])
    ]

    return {
        "matrix":          matrix,
        "high_risk_pairs": high_risk_pairs,
        "symbols":         symbols,
        "lookback_days":   lookback_days,
        "sample_days":     len(df),
        "available":       True,
    }


# ── 3. P&L Attribution ────────────────────────────────────────────────────────

def compute_pnl_attribution(conn, lookback_days: int = 90) -> dict:
    """
    Decompose realized P&L into:
      beta_pnl  — what you would have made just holding BTC (market beta)
      alpha_pnl — your actual P&L minus the BTC component (skill)

    Method: for each closed position, fetch BTC return over the trade duration.
    beta contribution = position_size * leverage_factor * btc_return.
    alpha = realized_pnl - beta_pnl.

    Uses yfinance for BTC (free). Only covers trades with size_usdt data.
    """
    import yfinance as yf
    import datetime as _dt

    rows = conn.execute("""
        SELECT id, symbol, direction, realized_pnl, size_usdt,
               date(open_time) AS open_date, date(close_time) AS close_date
        FROM positions
        WHERE realized_pnl IS NOT NULL AND size_usdt > 0
          AND open_time IS NOT NULL AND close_time IS NOT NULL
          AND close_time >= datetime('now', ? || ' days')
        ORDER BY close_time DESC
        LIMIT 200
    """, (str(-lookback_days),)).fetchall()

    if not rows:
        return {"alpha_pnl": 0.0, "beta_pnl": 0.0, "total_pnl": 0.0,
                "alpha_pct": 0.0, "sample_size": 0, "available": False}

    # Fetch BTC daily closes once for the full window
    min_date = min(r["open_date"] for r in rows)
    max_date = max(r["close_date"] for r in rows)
    try:
        end_plus1 = (_dt.datetime.strptime(max_date, "%Y-%m-%d") +
                     _dt.timedelta(days=1)).strftime("%Y-%m-%d")
        btc = yf.download("BTC-USD", start=min_date, end=end_plus1,
                          progress=False, auto_adjust=True)
        btc_close = btc["Close"].dropna() if not btc.empty else pd.Series(dtype=float)
    except Exception:
        btc_close = pd.Series(dtype=float)

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
            open_ts  = pd.Timestamp(r["open_date"])
            close_ts = pd.Timestamp(r["close_date"])
            btc_open  = float(btc_close.asof(open_ts))
            btc_close_ = float(btc_close.asof(close_ts))
            if btc_open and btc_close_:
                btc_ret = (btc_close_ - btc_open) / btc_open
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

    return {
        "alpha_pnl":   round(alpha_pnl, 2),
        "beta_pnl":    round(beta_pnl,  2),
        "total_pnl":   round(total_pnl, 2),
        "alpha_pct":   alpha_pct,
        "sample_size": len(rows),
        "attributed":  attributed,
        "available":   attributed > 0,
        "lookback_days": lookback_days,
    }


# ── 4. Kelly Criterion by Score Bucket ────────────────────────────────────────

def compute_kelly_by_bucket(conn) -> dict:
    """
    Compute Kelly fraction (optimal bet size as % of capital) per setup score bucket.
    Uses historical win rate and avg win/loss from the hindsight + positions DB.

    Kelly f = (win_rate * avg_win - loss_rate * avg_loss) / avg_win
    Half-Kelly applied for safety.

    Returns buckets [{score_range, win_rate, avg_win, avg_loss, kelly_full,
    kelly_half, recommended_size_pct}] and available.
    """
    rows = conn.execute("""
        SELECT
            CASE
                WHEN p.setup_score <= 6 THEN '6'
                WHEN p.setup_score <= 8 THEN '7-8'
                ELSE '9-10'
            END AS bucket,
            COUNT(*) AS n,
            AVG(CASE WHEN p.realized_pnl > 0 THEN 1.0 ELSE 0.0 END) AS win_rate,
            AVG(CASE WHEN p.realized_pnl > 0 THEN p.realized_pnl END) AS avg_win,
            AVG(CASE WHEN p.realized_pnl < 0 THEN p.realized_pnl END) AS avg_loss
        FROM positions p
        WHERE p.setup_score IS NOT NULL AND p.realized_pnl IS NOT NULL
        GROUP BY bucket
        HAVING COUNT(*) >= 5
        ORDER BY bucket
    """).fetchall()

    if not rows:
        # Fall back to overall stats if no setup_score data
        overall = conn.execute("""
            SELECT COUNT(*) AS n,
                   AVG(CASE WHEN realized_pnl > 0 THEN 1.0 ELSE 0.0 END) AS win_rate,
                   AVG(CASE WHEN realized_pnl > 0 THEN realized_pnl END)  AS avg_win,
                   AVG(CASE WHEN realized_pnl < 0 THEN realized_pnl END)  AS avg_loss
            FROM positions WHERE realized_pnl IS NOT NULL
        """).fetchone()
        if not overall or not overall["n"] or overall["n"] < 5:
            return {"buckets": [], "available": False,
                    "reason": "Need at least 5 trades with setup_score to compute Kelly"}
        rows = [{"bucket": "all", **dict(overall)}]

    buckets = []
    for r in rows:
        wr   = float(r["win_rate"] or 0)
        lr   = 1 - wr
        aw   = float(r["avg_win"]  or 0)
        al   = abs(float(r["avg_loss"] or 1))

        if aw <= 0:
            kelly_full = 0.0
        else:
            kelly_full = max(0.0, (wr * aw - lr * al) / aw)

        kelly_half = kelly_full / 2
        # Cap at 20% of capital — hard safety limit
        recommended = min(round(kelly_half * 100, 1), 20.0)

        buckets.append({
            "score_range":         r["bucket"],
            "trade_count":         r["n"],
            "win_rate":            round(wr * 100, 1),
            "avg_win_usd":         round(aw, 2),
            "avg_loss_usd":        round(al, 2),
            "kelly_full_pct":      round(kelly_full * 100, 1),
            "kelly_half_pct":      round(kelly_half * 100, 1),
            "recommended_size_pct": recommended,
        })

    return {"buckets": buckets, "available": True}


# ── 5. Alpha Decay ────────────────────────────────────────────────────────────

def compute_alpha_decay(conn) -> dict:
    """
    Measure how execution lag affects realized P&L.
    Groups trades by execution_lag_minutes bucket and shows avg P&L per bucket.
    A decaying edge (P&L drops as lag increases) is a sign that entering
    faster would improve returns.

    Returns lag_buckets [{lag_range, avg_pnl, trade_count, win_rate}] and
    correlation (Pearson r between lag and PnL — negative = edge decays).
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

    # Pearson correlation between lag and P&L
    try:
        corr = float(np.corrcoef(lags, pnls)[0, 1])
    except Exception:
        corr = None

    # Group into lag buckets
    buckets_raw = {
        "< 30m":    [],
        "30m-2h":   [],
        "2h-8h":    [],
        "> 8h":     [],
    }
    for lag, pnl in zip(lags, pnls):
        if lag < 30:
            buckets_raw["< 30m"].append(pnl)
        elif lag < 120:
            buckets_raw["30m-2h"].append(pnl)
        elif lag < 480:
            buckets_raw["2h-8h"].append(pnl)
        else:
            buckets_raw["> 8h"].append(pnl)

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

    return {
        "lag_buckets":  lag_buckets,
        "correlation":  round(corr, 3) if corr is not None else None,
        "edge_decays":  corr is not None and corr < -0.15,
        "sample_size":  len(rows),
        "available":    True,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_risk_analytics.py::test_var_empty_positions \
    tests/test_risk_analytics.py::test_var_returns_required_keys \
    tests/test_risk_analytics.py::test_var_95_less_than_99 -v
```
Expected: all 3 PASS.

- [ ] **Step 5: Commit risk_analytics.py**

```bash
git add risk_analytics.py tests/test_risk_analytics.py
git commit -m "feat: risk_analytics.py -- VaR, correlation, P&L attribution, Kelly, alpha decay"
```

---

## Task 2: Remaining Tests + Risk Routes Blueprint

**Files:**
- Modify: `tests/test_risk_analytics.py` — add tests for correlation, attribution, Kelly, decay
- Create: `routes/risk.py` — Flask blueprint with 5 endpoints
- Modify: `app.py` — register risk blueprint

- [ ] **Step 1: Add tests for the remaining 4 functions**

Append to `tests/test_risk_analytics.py`:

```python
# ── Correlation matrix ────────────────────────────────────────────────────────

def test_correlation_matrix_empty_on_single_position(mock_ohlcv):
    from risk_analytics import compute_correlation_matrix
    result = compute_correlation_matrix([{"symbol": "BTCUSDT", "direction": "Long", "size_usdt": 500}])
    assert result["available"] is False


def test_correlation_matrix_returns_pairs(mock_ohlcv):
    from risk_analytics import compute_correlation_matrix
    positions = _make_positions("BTCUSDT", "ETHUSDT", "SOLUSDT")
    result = compute_correlation_matrix(positions, lookback_days=30)
    if result["available"]:
        assert len(result["matrix"]) == 3  # C(3,2) = 3 pairs
        for item in result["matrix"]:
            assert -1.0 <= item["correlation"] <= 1.0


# ── P&L attribution ───────────────────────────────────────────────────────────

def test_pnl_attribution_returns_keys(tmp_path, monkeypatch):
    import database as _db, unittest.mock, pandas as pd
    db_file = str(tmp_path / "attr.db")
    monkeypatch.setattr(_db, "DB_PATH", db_file)
    _db.init_db()
    conn = _db.get_conn()
    conn.execute("""
        INSERT INTO positions (symbol, base_asset, direction, realized_pnl, size_usdt,
               open_time, close_time, exchange)
        VALUES ('BTCUSDT','BTC','Long',50.0,300.0,'2026-01-01','2026-01-02','bitget')
    """)
    conn.commit()

    # Mock yfinance
    fake_btc = pd.DataFrame(
        {"Close": [40000.0, 41000.0]},
        index=pd.date_range("2026-01-01", periods=2),
    )
    monkeypatch.setattr("risk_analytics.yf.download",
                        unittest.mock.MagicMock(return_value=fake_btc))

    from risk_analytics import compute_pnl_attribution
    result = compute_pnl_attribution(conn, lookback_days=90)
    assert "alpha_pnl" in result
    assert "beta_pnl" in result
    assert "total_pnl" in result
    conn.close()


# ── Kelly Criterion ───────────────────────────────────────────────────────────

def test_kelly_buckets_returns_structure(tmp_path, monkeypatch):
    import database as _db
    db_file = str(tmp_path / "kelly.db")
    monkeypatch.setattr(_db, "DB_PATH", db_file)
    _db.init_db()
    conn = _db.get_conn()
    # Insert 10 trades with setup_score 7
    for i in range(10):
        pnl = 20.0 if i < 7 else -30.0
        conn.execute("""
            INSERT INTO positions (symbol, base_asset, direction, realized_pnl,
                   setup_score, open_time, close_time, exchange)
            VALUES ('BTCUSDT','BTC','Long',?,7,'2026-01-01','2026-01-02','bitget')
        """, (pnl,))
    conn.commit()
    from risk_analytics import compute_kelly_by_bucket
    result = compute_kelly_by_bucket(conn)
    assert "buckets" in result
    if result["available"]:
        for b in result["buckets"]:
            assert "kelly_half_pct" in b
            assert b["recommended_size_pct"] <= 20.0
    conn.close()


def test_kelly_caps_at_20_percent(tmp_path, monkeypatch):
    import database as _db
    db_file = str(tmp_path / "kelly2.db")
    monkeypatch.setattr(_db, "DB_PATH", db_file)
    _db.init_db()
    conn = _db.get_conn()
    # Insert 10 all-wins (very high Kelly) with setup_score 9
    for i in range(10):
        conn.execute("""
            INSERT INTO positions (symbol, base_asset, direction, realized_pnl,
                   setup_score, open_time, close_time, exchange)
            VALUES ('ETHUSDT','ETH','Long',100.0,9,'2026-01-01','2026-01-02','bitget')
        """)
    conn.commit()
    from risk_analytics import compute_kelly_by_bucket
    result = compute_kelly_by_bucket(conn)
    if result["available"]:
        for b in result["buckets"]:
            assert b["recommended_size_pct"] <= 20.0
    conn.close()


# ── Alpha decay ───────────────────────────────────────────────────────────────

def test_alpha_decay_no_data(tmp_path, monkeypatch):
    import database as _db
    db_file = str(tmp_path / "decay.db")
    monkeypatch.setattr(_db, "DB_PATH", db_file)
    _db.init_db()
    conn = _db.get_conn()
    from risk_analytics import compute_alpha_decay
    result = compute_alpha_decay(conn)
    assert result["available"] is False
    conn.close()


def test_alpha_decay_with_data(tmp_path, monkeypatch):
    import database as _db
    db_file = str(tmp_path / "decay2.db")
    monkeypatch.setattr(_db, "DB_PATH", db_file)
    _db.init_db()
    conn = _db.get_conn()
    for i, (lag, pnl) in enumerate([(10, 30), (15, 25), (60, 10), (90, 5), (300, -10), (400, -15)]):
        conn.execute("""
            INSERT INTO positions (symbol, base_asset, direction, realized_pnl,
                   execution_lag_minutes, open_time, close_time, exchange)
            VALUES ('BTCUSDT','BTC','Long',?,?,'2026-01-01','2026-01-02','bitget')
        """, (float(pnl), lag))
    conn.commit()
    from risk_analytics import compute_alpha_decay
    result = compute_alpha_decay(conn)
    assert result["available"] is True
    assert "correlation" in result
    assert len(result["lag_buckets"]) > 0
    conn.close()
```

- [ ] **Step 2: Run all risk tests**

```bash
python3 -m pytest tests/test_risk_analytics.py -v --tb=short
```
Expected: all 12 tests PASS.

- [ ] **Step 3: Create routes/risk.py**

```python
"""
routes/risk.py — Risk analytics endpoints.
All functions use free Binance public data and on-device DB.
"""
import traceback

from flask import Blueprint, request

from database import db_conn
from helpers import _ok, _err
import bitget_client
import blofin_client

bp = Blueprint("risk", __name__)


def _get_live_positions() -> tuple[list, float]:
    """Fetch open positions and equity from configured exchanges."""
    positions = []
    equity = 0.0
    try:
        positions = bitget_client.get_open_positions()
        eq = bitget_client.get_account_equity()
        equity += float(eq.get("accountEquity") or 0)
    except Exception:
        pass
    try:
        if blofin_client.is_configured():
            positions += blofin_client.get_open_positions()
            bl_eq = blofin_client.get_account_equity()
            equity += float(bl_eq.get("equity") or 0)
    except Exception:
        pass
    return positions, equity


@bp.route("/api/risk/var")
def api_risk_var():
    """
    GET /api/risk/var
    Historical simulation VaR on current open positions.
    Uses 90 days of Binance 4H OHLCV (free, no auth).
    """
    try:
        from risk_analytics import compute_portfolio_var
        positions, equity = _get_live_positions()
        data = compute_portfolio_var(positions, equity=equity)
        return _ok(data)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/risk/correlation")
def api_risk_correlation():
    """
    GET /api/risk/correlation
    Pairwise correlation matrix for open positions (30-day daily returns).
    """
    try:
        from risk_analytics import compute_correlation_matrix
        positions, _ = _get_live_positions()
        data = compute_correlation_matrix(positions)
        return _ok(data)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/risk/attribution")
def api_risk_attribution():
    """
    GET /api/risk/attribution?days=90
    P&L attribution: alpha (skill) vs beta (BTC market move).
    Uses yfinance BTC-USD (free).
    """
    try:
        from risk_analytics import compute_pnl_attribution
        days = min(int(request.args.get("days", 90)), 365)
        with db_conn() as conn:
            data = compute_pnl_attribution(conn, lookback_days=days)
        return _ok(data)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/risk/kelly")
def api_risk_kelly():
    """
    GET /api/risk/kelly
    Kelly Criterion position sizing by setup score bucket.
    Derived from historical win rate and avg win/loss in your trade DB.
    """
    try:
        from risk_analytics import compute_kelly_by_bucket
        with db_conn() as conn:
            data = compute_kelly_by_bucket(conn)
        return _ok(data)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/risk/alpha-decay")
def api_risk_alpha_decay():
    """
    GET /api/risk/alpha-decay
    How execution lag affects P&L — shows if edge decays as entry delay grows.
    Requires Plan D execution quality tracking columns (execution_lag_minutes).
    """
    try:
        from risk_analytics import compute_alpha_decay
        with db_conn() as conn:
            data = compute_alpha_decay(conn)
        return _ok(data)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)
```

- [ ] **Step 4: Register risk blueprint in app.py**

Find the blueprint registrations in `app.py` (the section with `app.register_blueprint(...)` calls). Add:

```python
from routes.risk import bp as risk_bp
app.register_blueprint(risk_bp)
```

- [ ] **Step 5: Verify routes are registered**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
python3 -c "
import os; os.environ['ANTHROPIC_API_KEY']='test'
os.environ['BITGET_API_KEY']='test'; os.environ['BITGET_SECRET_KEY']='test'
os.environ['BITGET_PASSPHRASE']='test'
import app as a
rules = [str(r) for r in a.app.url_map.iter_rules() if '/api/risk/' in str(r)]
print('Risk routes:', rules)
"
```
Expected: prints 5 risk routes.

- [ ] **Step 6: Commit**

```bash
git add routes/risk.py app.py tests/test_risk_analytics.py
git commit -m "feat: risk blueprint -- VaR, correlation, attribution, Kelly, alpha-decay endpoints"
```

---

## Task 3: Risk Dashboard UI

**Files:**
- Modify: `templates/index.html` — add Risk tab + containers
- Modify: one of the existing JS files (read `static/js/` directory, pick the highest-numbered free one, or create `static/js/17-risk.js`)

- [ ] **Step 1: Create static/js/17-risk.js**

Read existing JS files to understand patterns. Then create `static/js/17-risk.js`:

```javascript
/* 17-risk.js — Risk analytics tab */

async function loadRiskDashboard() {
    loadVar();
    loadCorrelation();
    loadAttribution();
    loadKelly();
    loadAlphaDecay();
}

async function loadVar() {
    const el = document.getElementById('risk-var');
    if (!el) return;
    try {
        const r = await fetch('/api/risk/var');
        const d = await r.json();
        if (!d.ok || !d.data || !d.data.available) {
            el.textContent = 'No open positions.';
            return;
        }
        const v = d.data;
        while (el.firstChild) el.removeChild(el.firstChild);
        const grid = document.createElement('div');
        grid.style.cssText = 'display:grid;grid-template-columns:repeat(2,1fr);gap:12px';
        [
            ['95% VaR (1-day)', '$' + v.var_95_usd.toLocaleString('en-US',{maximumFractionDigits:0}), 'pnl-neg'],
            ['99% VaR (1-day)', '$' + v.var_99_usd.toLocaleString('en-US',{maximumFractionDigits:0}), 'pnl-neg'],
            ['95% VaR %',       v.var_95_pct + '% of notional', ''],
            ['Portfolio Size',  '$' + v.total_notional.toLocaleString('en-US',{maximumFractionDigits:0}), ''],
        ].forEach(([label, value, cls]) => {
            const stat = document.createElement('div');
            stat.style.cssText = 'background:var(--bg-secondary,#1a1a2e);padding:12px;border-radius:6px';
            const lbl = document.createElement('div');
            lbl.style.cssText = 'font-size:11px;color:var(--text-muted,#888);text-transform:uppercase';
            lbl.textContent = label;
            const val = document.createElement('div');
            val.style.cssText = 'font-size:18px;font-weight:700;margin-top:4px';
            val.textContent = value;
            if (cls) val.className = cls;
            stat.appendChild(lbl);
            stat.appendChild(val);
            grid.appendChild(stat);
        });
        el.appendChild(grid);
        const note = document.createElement('div');
        note.style.cssText = 'font-size:11px;color:var(--text-muted,#888);margin-top:8px';
        note.textContent = v.sample_days + ' days of historical data used';
        el.appendChild(note);
    } catch(e) { if (el) el.textContent = 'VaR unavailable.'; }
}

async function loadCorrelation() {
    const el = document.getElementById('risk-correlation');
    if (!el) return;
    try {
        const r = await fetch('/api/risk/correlation');
        const d = await r.json();
        if (!d.ok || !d.data || !d.data.available) {
            el.textContent = d.data?.reason || 'Need 2+ open positions.';
            return;
        }
        const c = d.data;
        while (el.firstChild) el.removeChild(el.firstChild);
        if (c.high_risk_pairs && c.high_risk_pairs.length > 0) {
            const warn = document.createElement('div');
            warn.style.cssText = 'background:#3a1a1a;border-left:3px solid #ef5350;padding:10px;border-radius:4px;margin-bottom:10px;font-size:13px';
            warn.textContent = 'High correlation pairs (same direction, r > 0.70): ' +
                c.high_risk_pairs.map(p => p.symbol_a + '/' + p.symbol_b + ' (' + p.correlation + ')').join(', ');
            el.appendChild(warn);
        }
        const tbl = document.createElement('table');
        tbl.className = 'data-table';
        const thead = tbl.createTHead();
        const hr = thead.insertRow();
        ['Symbol A', 'Symbol B', 'Correlation', 'Risk'].forEach(h => {
            const th = document.createElement('th');
            th.textContent = h;
            hr.appendChild(th);
        });
        const tbody = tbl.createTBody();
        (c.matrix || []).slice(0, 10).forEach(row => {
            const tr = tbody.insertRow();
            const corr = row.correlation;
            const riskLabel = Math.abs(corr) > 0.80 ? 'Very High' : Math.abs(corr) > 0.60 ? 'High' : Math.abs(corr) > 0.40 ? 'Medium' : 'Low';
            const riskClass = Math.abs(corr) > 0.70 ? 'pnl-neg' : '';
            [row.symbol_a, row.symbol_b, corr.toFixed(3), riskLabel].forEach((val, i) => {
                const td = tr.insertCell();
                td.textContent = val;
                if (i >= 2 && riskClass) td.className = riskClass;
            });
        });
        el.appendChild(tbl);
    } catch(e) { if (el) el.textContent = 'Correlation unavailable.'; }
}

async function loadAttribution() {
    const el = document.getElementById('risk-attribution');
    if (!el) return;
    try {
        const r = await fetch('/api/risk/attribution?days=90');
        const d = await r.json();
        if (!d.ok || !d.data || !d.data.available) {
            el.textContent = 'No trade history for attribution.';
            return;
        }
        const a = d.data;
        while (el.firstChild) el.removeChild(el.firstChild);
        const grid = document.createElement('div');
        grid.style.cssText = 'display:grid;grid-template-columns:repeat(3,1fr);gap:12px';
        [
            ['Total P&L', '$' + a.total_pnl.toFixed(2), a.total_pnl >= 0 ? 'pnl-pos' : 'pnl-neg'],
            ['Alpha (Skill)', '$' + a.alpha_pnl.toFixed(2), a.alpha_pnl >= 0 ? 'pnl-pos' : 'pnl-neg'],
            ['Beta (BTC Move)', '$' + a.beta_pnl.toFixed(2), a.beta_pnl >= 0 ? 'pnl-pos' : 'pnl-neg'],
        ].forEach(([label, value, cls]) => {
            const stat = document.createElement('div');
            stat.style.cssText = 'background:var(--bg-secondary,#1a1a2e);padding:12px;border-radius:6px;text-align:center';
            const lbl = document.createElement('div');
            lbl.style.cssText = 'font-size:11px;color:var(--text-muted,#888);text-transform:uppercase;margin-bottom:4px';
            lbl.textContent = label;
            const val = document.createElement('div');
            val.style.cssText = 'font-size:18px;font-weight:700';
            val.textContent = value;
            val.className = cls;
            stat.appendChild(lbl);
            stat.appendChild(val);
            grid.appendChild(stat);
        });
        el.appendChild(grid);
        const note = document.createElement('div');
        note.style.cssText = 'font-size:11px;color:var(--text-muted,#888);margin-top:8px';
        note.textContent = a.alpha_pct + '% of P&L is alpha (skill). ' + a.sample_size + ' trades analyzed.';
        el.appendChild(note);
    } catch(e) { if (el) el.textContent = 'Attribution unavailable.'; }
}

async function loadKelly() {
    const el = document.getElementById('risk-kelly');
    if (!el) return;
    try {
        const r = await fetch('/api/risk/kelly');
        const d = await r.json();
        if (!d.ok || !d.data || !d.data.available) {
            el.textContent = d.data?.reason || 'Need trade history with setup scores.';
            return;
        }
        while (el.firstChild) el.removeChild(el.firstChild);
        const tbl = document.createElement('table');
        tbl.className = 'data-table';
        const thead = tbl.createTHead();
        const hr = thead.insertRow();
        ['Score', 'Trades', 'Win%', 'Avg Win', 'Avg Loss', 'Kelly', 'Recommended %'].forEach(h => {
            const th = document.createElement('th'); th.textContent = h; hr.appendChild(th);
        });
        const tbody = tbl.createTBody();
        (d.data.buckets || []).forEach(b => {
            const tr = tbody.insertRow();
            [b.score_range, b.trade_count, b.win_rate + '%',
             '$' + b.avg_win_usd.toFixed(1), '$' + b.avg_loss_usd.toFixed(1),
             b.kelly_full_pct + '%', b.recommended_size_pct + '%'].forEach((val, i) => {
                const td = tr.insertCell();
                td.textContent = val;
                if (i === 6) {
                    td.className = b.recommended_size_pct > 10 ? 'pnl-pos' : '';
                    td.style.fontWeight = '700';
                }
            });
        });
        el.appendChild(tbl);
        const note = document.createElement('div');
        note.style.cssText = 'font-size:11px;color:var(--text-muted,#888);margin-top:8px';
        note.textContent = 'Half-Kelly applied for safety. Hard cap at 20% of capital.';
        el.appendChild(note);
    } catch(e) { if (el) el.textContent = 'Kelly unavailable.'; }
}

async function loadAlphaDecay() {
    const el = document.getElementById('risk-alpha-decay');
    if (!el) return;
    try {
        const r = await fetch('/api/risk/alpha-decay');
        const d = await r.json();
        if (!d.ok || !d.data || !d.data.available) {
            el.textContent = d.data?.reason || 'No execution lag data yet. Trade with scanner and sync to build this.';
            return;
        }
        const a = d.data;
        while (el.firstChild) el.removeChild(el.firstChild);
        if (a.edge_decays) {
            const warn = document.createElement('div');
            warn.style.cssText = 'background:#1a2a3a;border-left:3px solid #64b5f6;padding:10px;border-radius:4px;margin-bottom:10px;font-size:13px';
            warn.textContent = 'Edge decay detected (r=' + a.correlation + '). Entering faster would likely improve returns.';
            el.appendChild(warn);
        }
        const tbl = document.createElement('table');
        tbl.className = 'data-table';
        const thead = tbl.createTHead();
        const hr = thead.insertRow();
        ['Entry Lag', 'Trades', 'Avg P&L', 'Win Rate'].forEach(h => {
            const th = document.createElement('th'); th.textContent = h; hr.appendChild(th);
        });
        const tbody = tbl.createTBody();
        (a.lag_buckets || []).forEach(b => {
            const tr = tbody.insertRow();
            [b.lag_range, b.trade_count,
             (b.avg_pnl >= 0 ? '+' : '') + '$' + b.avg_pnl.toFixed(2),
             b.win_rate + '%'].forEach((val, i) => {
                const td = tr.insertCell();
                td.textContent = val;
                if (i === 2) td.className = b.avg_pnl >= 0 ? 'pnl-pos' : 'pnl-neg';
            });
        });
        el.appendChild(tbl);
    } catch(e) { if (el) el.textContent = 'Alpha decay unavailable.'; }
}
```

- [ ] **Step 2: Add Risk tab to templates/index.html**

Read `templates/index.html` to find where tabs are defined (look for tab buttons and tab content divs). Add a "Risk" tab button alongside existing tabs. Add the risk tab content div:

```html
<!-- Risk tab button (add alongside existing tab buttons): -->
<button class="tab-btn" data-tab="risk" onclick="switchTab('risk')">Risk</button>

<!-- Risk tab content (add alongside existing tab content divs): -->
<div id="tab-risk" class="tab-content" style="display:none">
    <div class="card">
        <div class="card-header">Value at Risk (1-Day, Historical Simulation)</div>
        <div id="risk-var" style="padding:14px">Loading...</div>
    </div>
    <div class="card" style="margin-top:16px">
        <div class="card-header">Position Correlation Matrix</div>
        <div id="risk-correlation" style="padding:14px">Loading...</div>
    </div>
    <div class="card" style="margin-top:16px">
        <div class="card-header">P&amp;L Attribution — Alpha vs Beta (Last 90 Days)</div>
        <div id="risk-attribution" style="padding:14px">Loading...</div>
    </div>
    <div class="card" style="margin-top:16px">
        <div class="card-header">Kelly Criterion — Optimal Position Size by Score</div>
        <div id="risk-kelly" style="padding:14px">Loading...</div>
    </div>
    <div class="card" style="margin-top:16px">
        <div class="card-header">Alpha Decay — Does Edge Fade with Entry Delay?</div>
        <div id="risk-alpha-decay" style="padding:14px">Loading...</div>
    </div>
</div>
```

Add to the `switchTab()` function handling: when `tab === 'risk'`, call `loadRiskDashboard()`.

Add `<script src="/static/js/17-risk.js?v=1.0"></script>` to the `<head>` alongside other JS includes.

Bump the `?v=` on `templates/index.html` itself.

- [ ] **Step 3: Run all tests**

```bash
python3 -m pytest tests/test_risk_analytics.py -v --tb=short
python3 -m pytest tests/ -q --tb=no 2>&1 | tail -5
```
Expected: 12 risk tests PASS, overall suite no new failures.

- [ ] **Step 4: Verify routes load cleanly**

```bash
python3 -c "
import os; os.environ['ANTHROPIC_API_KEY']='test'
os.environ['BITGET_API_KEY']='test'; os.environ['BITGET_SECRET_KEY']='test'
os.environ['BITGET_PASSPHRASE']='test'
import app as a
risk_rules = [str(r) for r in a.app.url_map.iter_rules() if 'risk' in str(r)]
print('Risk routes:', risk_rules)
assert len(risk_rules) == 5, f'Expected 5 routes, got {len(risk_rules)}'
print('OK')
"
```

- [ ] **Step 5: Commit**

```bash
git add static/js/17-risk.js templates/index.html tests/test_risk_analytics.py
git commit -m "feat: Risk tab UI -- VaR, correlation matrix, P&L attribution, Kelly sizing, alpha decay"
```

---

## Final Checks

```bash
python3 -m pytest tests/test_risk_analytics.py tests/test_benchmark.py \
    tests/test_tearsheet.py tests/test_setup_autoclassify.py \
    tests/test_execution_quality.py -v

python3 -m pytest tests/ -q --tb=no 2>&1 | tail -5
```

Deploy:
```bash
git push origin main
# SSH to Pi:
cd /home/fbauer/trading-journal && git pull origin main
pip3 install --break-system-packages quantstats statsmodels scikit-learn
sudo systemctl restart trading-journal
```
