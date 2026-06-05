# Plan D — Data Quality & Quick Wins

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add BTC benchmark comparison, professional tearsheets, automatic setup-type classification, funding fee visibility, and execution quality tracking — all using free data already in the stack.

**Architecture:** Five independent backend+frontend tasks. No new paid APIs. New DB columns use the existing idempotent migration system (next: migration 35). quantstats 0.0.81 and yfinance already installed.

**Tech Stack:** Python 3.13, Flask, SQLite, yfinance (installed), quantstats 0.0.81 (installed), pandas, existing analytics.py + bitget_sync.py.

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `analytics.py` | Modify | Add get_benchmark_comparison(), get_tearsheet_metrics(), get_execution_quality() |
| `routes/analytics.py` | Modify | Add benchmark, tearsheet, execution-quality endpoints |
| `database.py` | Modify | Migrations 35-37: funding_pnl, signal_price, execution_lag_minutes |
| `bitget_sync.py` | Modify | Write funding_pnl from totalFunding; backfill setup_type |
| `sync_base.py` | Modify | _populate_setup_type_from_call(); execution lag in auto_match_calls() |
| `templates/index.html` | Modify | Benchmark card, tearsheet card containers |
| `tests/test_benchmark.py` | Create | BTC benchmark tests |
| `tests/test_tearsheet.py` | Create | Tearsheet metrics tests |
| `tests/test_execution_quality.py` | Create | Funding + execution lag tests |
| `tests/test_setup_autoclassify.py` | Create | Auto-classify setup_type tests |

---

## Task 1: BTC Benchmark Comparison

**Problem:** Every PnL number is absolute. Without knowing what BTC did during the same period, a 40% win rate could mean you underperformed simple buy-and-hold. This is the single highest-ROI fix.

**How it works:** Fetch BTC-USD daily closes from yfinance for the same date range as the trader's closed positions. Compute BTC return. Compare against cumulative trader PnL relative to an assumed capital base.

**Files:**
- Modify: `analytics.py` — add `get_benchmark_comparison()`
- Modify: `routes/analytics.py` — add `GET /api/analytics/benchmark`
- Modify: `templates/index.html` — add benchmark card container
- Create: `tests/test_benchmark.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_benchmark.py
import sys, os, unittest.mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
import pandas as pd


@pytest.fixture
def db_bench(tmp_path, monkeypatch):
    import database as _db
    db_file = str(tmp_path / "bench.db")
    monkeypatch.setattr(_db, "DB_PATH", db_file)
    _db.init_db()
    conn = _db.get_conn()
    trades = [
        ("BTCUSDT", "Long",  50.0, "2026-01-10", "2026-01-11"),
        ("ETHUSDT", "Long", -20.0, "2026-01-15", "2026-01-16"),
        ("SOLUSDT", "Long",  80.0, "2026-02-01", "2026-02-02"),
    ]
    for sym, direction, pnl, ot, ct in trades:
        conn.execute("""
            INSERT INTO positions (symbol, base_asset, direction, realized_pnl,
                                   open_time, close_time, exchange)
            VALUES (?,?,?,?,?,?,'bitget')
        """, (sym, sym[:-4], direction, pnl, ot, ct))
    conn.commit()
    yield conn
    conn.close()


def test_benchmark_returns_required_keys(db_bench, monkeypatch):
    fake_btc = pd.DataFrame(
        {"Close": [40000.0, 41000.0, 42000.0, 43000.0, 44000.0]},
        index=pd.date_range("2026-01-10", periods=5),
    )
    monkeypatch.setattr("analytics.yf.download", unittest.mock.MagicMock(return_value=fake_btc))
    from analytics import get_benchmark_comparison
    result = get_benchmark_comparison(conn=db_bench)
    assert "trader_return_pct" in result
    assert "btc_return_pct" in result
    assert "alpha_pct" in result
    assert "period_days" in result


def test_alpha_is_trader_minus_btc(db_bench, monkeypatch):
    fake_btc = pd.DataFrame(
        {"Close": [40000.0, 44000.0]},
        index=pd.date_range("2026-01-10", periods=2),
    )
    monkeypatch.setattr("analytics.yf.download", unittest.mock.MagicMock(return_value=fake_btc))
    from analytics import get_benchmark_comparison
    result = get_benchmark_comparison(conn=db_bench)
    expected_alpha = round(result["trader_return_pct"] - result["btc_return_pct"], 2)
    assert result["alpha_pct"] == pytest.approx(expected_alpha, abs=0.1)


def test_benchmark_handles_no_trades(tmp_path, monkeypatch):
    import database as _db
    db_file = str(tmp_path / "empty.db")
    monkeypatch.setattr(_db, "DB_PATH", db_file)
    _db.init_db()
    conn = _db.get_conn()
    from analytics import get_benchmark_comparison
    result = get_benchmark_comparison(conn=conn)
    assert result["trader_return_pct"] == 0.0
    assert result["btc_return_pct"] == 0.0
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
python3 -m pytest tests/test_benchmark.py -v
```
Expected: `ImportError: cannot import name 'get_benchmark_comparison' from 'analytics'`

- [ ] **Step 3: Add import + function to analytics.py**

Add `import yfinance as yf` at top of analytics.py (after existing imports).

Add after `get_setup_type_stats()`:

```python
def get_benchmark_comparison(filters=None, conn=None) -> dict:
    """
    Compare trader cumulative P&L against BTC buy-and-hold over the same period.
    Uses yfinance BTC-USD daily closes — free, no auth.

    Returns trader_return_pct, btc_return_pct, alpha_pct, period_days,
    start_date, end_date, btc_start, btc_end, assumed_capital, available.
    """
    if filters is None:
        filters = {}
    if conn is None:
        conn = get_conn()

    row = conn.execute("""
        SELECT MIN(date(close_time)) AS first_date,
               MAX(date(close_time)) AS last_date,
               SUM(realized_pnl)     AS total_pnl,
               AVG(size_usdt)        AS avg_size
        FROM positions
        WHERE realized_pnl IS NOT NULL
    """).fetchone()

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

    try:
        import datetime as _dt
        end_plus1 = (_dt.datetime.strptime(end_date, "%Y-%m-%d") +
                     _dt.timedelta(days=1)).strftime("%Y-%m-%d")
        btc_data = yf.download("BTC-USD", start=start_date, end=end_plus1,
                               progress=False, auto_adjust=True)
        if btc_data.empty or "Close" not in btc_data.columns:
            raise ValueError("no data")
        close = btc_data["Close"].dropna()
        btc_start = float(close.iloc[0])
        btc_end   = float(close.iloc[-1])
        btc_return_pct = round((btc_end - btc_start) / btc_start * 100, 2)
    except Exception:
        btc_start = btc_end = None
        btc_return_pct = 0.0

    import datetime as _dt2
    try:
        period_days = (_dt2.datetime.strptime(end_date, "%Y-%m-%d") -
                       _dt2.datetime.strptime(start_date, "%Y-%m-%d")).days + 1
    except Exception:
        period_days = 0

    return {
        "trader_return_pct": trader_return_pct,
        "btc_return_pct":    btc_return_pct,
        "alpha_pct":         round(trader_return_pct - btc_return_pct, 2),
        "period_days":       period_days,
        "start_date":        start_date,
        "end_date":          end_date,
        "btc_start":         btc_start,
        "btc_end":           btc_end,
        "assumed_capital":   round(assumed_capital, 0),
        "available":         btc_return_pct != 0.0 or trader_return_pct != 0.0,
    }
```

- [ ] **Step 4: Add route to routes/analytics.py**

After `api_analytics_by_setup`:

```python
@bp.route("/api/analytics/benchmark")
def api_analytics_benchmark():
    """GET /api/analytics/benchmark -- trader return vs BTC buy-and-hold."""
    try:
        from analytics import get_benchmark_comparison
        with db_conn() as conn:
            data = get_benchmark_comparison(filters=_filters_from_args(), conn=conn)
        return _ok(data)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)
```

- [ ] **Step 5: Run tests**

```bash
python3 -m pytest tests/test_benchmark.py -v
```
Expected: all 3 PASS.

- [ ] **Step 6: Add benchmark card to templates/index.html**

Find the analytics/dashboard tab section. Add:

```html
<div class="card" style="margin-top:16px">
    <div class="card-header">vs BTC Buy-and-Hold</div>
    <div id="benchmark-body" style="padding:14px">Loading...</div>
</div>
```

In the JS file that handles the analytics tab (read the file to find the exact function name — look for `/api/analytics/deep` or `/api/dashboard/kpis`), add a `loadBenchmark()` function:

```javascript
async function loadBenchmark() {
    const el = document.getElementById('benchmark-body');
    if (!el) return;
    try {
        const r = await fetch('/api/analytics/benchmark');
        const d = await r.json();
        if (!d.ok || !d.data || !d.data.available) {
            el.textContent = 'Not enough trade history yet.';
            return;
        }
        const b = d.data;
        while (el.firstChild) el.removeChild(el.firstChild);
        const grid = document.createElement('div');
        grid.style.cssText = 'display:grid;grid-template-columns:repeat(3,1fr);gap:12px';
        [
            ['Your Return', (b.trader_return_pct >= 0 ? '+' : '') + b.trader_return_pct.toFixed(1) + '%',
             b.trader_return_pct >= 0 ? 'pnl-pos' : 'pnl-neg'],
            ['BTC Return',  (b.btc_return_pct >= 0 ? '+' : '') + b.btc_return_pct.toFixed(1) + '%',
             b.btc_return_pct >= 0 ? 'pnl-pos' : 'pnl-neg'],
            ['Alpha',       (b.alpha_pct >= 0 ? '+' : '') + b.alpha_pct.toFixed(1) + '%',
             b.alpha_pct >= 0 ? 'pnl-pos' : 'pnl-neg'],
        ].forEach(([label, value, cls]) => {
            const stat = document.createElement('div');
            stat.style.cssText = 'background:var(--bg-secondary,#1a1a2e);padding:12px;border-radius:6px;text-align:center';
            const lbl = document.createElement('div');
            lbl.style.cssText = 'font-size:11px;color:var(--text-muted,#888);text-transform:uppercase;margin-bottom:4px';
            lbl.textContent = label;
            const val = document.createElement('div');
            val.style.cssText = 'font-size:22px;font-weight:700';
            val.textContent = value;
            if (cls) val.className = cls;
            stat.appendChild(lbl);
            stat.appendChild(val);
            grid.appendChild(stat);
        });
        const note = document.createElement('div');
        note.style.cssText = 'font-size:11px;color:var(--text-muted,#888);margin-top:10px';
        note.textContent = b.period_days + ' days  assumed capital $' + (b.assumed_capital || 0).toLocaleString('en-US', {maximumFractionDigits:0});
        el.appendChild(grid);
        el.appendChild(note);
    } catch(e) {
        if (el) el.textContent = 'Benchmark unavailable.';
    }
}
```

Call `loadBenchmark()` when the analytics tab opens. Bump `?v=` for the modified JS file.

- [ ] **Step 7: Commit**

```bash
git add analytics.py routes/analytics.py templates/index.html tests/test_benchmark.py
git commit -m "feat: BTC buy-and-hold benchmark -- trader alpha vs market beta"
```

---

## Task 2: quantstats Professional Tearsheet

**Problem:** The dashboard shows individual metrics. A professional tearsheet shows everything together — rolling Sharpe, drawdown, monthly heatmap — in one view.

**How it works:** Build a daily returns series from `wallet_snapshots`. Compute metrics manually (so tests don't need quantstats). Expose full HTML tearsheet via quantstats at a download endpoint.

**Files:**
- Modify: `analytics.py` — add `get_tearsheet_metrics()`
- Modify: `routes/analytics.py` — add `GET /api/analytics/tearsheet` + `GET /api/analytics/tearsheet/download`
- Create: `tests/test_tearsheet.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tearsheet.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
import datetime as dt


@pytest.fixture
def db_ts(tmp_path, monkeypatch):
    import database as _db
    db_file = str(tmp_path / "ts.db")
    monkeypatch.setattr(_db, "DB_PATH", db_file)
    _db.init_db()
    conn = _db.get_conn()
    balance = 1000.0
    base = dt.date(2026, 1, 1)
    for i in range(30):
        d = (base + dt.timedelta(days=i)).isoformat()
        balance += (5 if i % 3 != 0 else -8)
        conn.execute(
            "INSERT INTO wallet_snapshots (date, wallet_balance, symbol, type) VALUES (?,?,'USDT','trade')",
            (d + " 12:00:00", balance)
        )
    conn.commit()
    yield conn
    conn.close()


def test_tearsheet_metrics_has_required_keys(db_ts):
    from analytics import get_tearsheet_metrics
    result = get_tearsheet_metrics(conn=db_ts)
    for key in ("sharpe", "max_drawdown_pct", "cagr_pct", "volatility_pct", "available"):
        assert key in result, f"Missing key: {key}"


def test_tearsheet_available_with_enough_data(db_ts):
    from analytics import get_tearsheet_metrics
    result = get_tearsheet_metrics(conn=db_ts)
    assert result["available"] is True


def test_tearsheet_unavailable_with_no_data(tmp_path, monkeypatch):
    import database as _db
    db_file = str(tmp_path / "empty.db")
    monkeypatch.setattr(_db, "DB_PATH", db_file)
    _db.init_db()
    conn = _db.get_conn()
    from analytics import get_tearsheet_metrics
    result = get_tearsheet_metrics(conn=conn)
    assert result["available"] is False
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_tearsheet.py -v
```
Expected: `ImportError: cannot import name 'get_tearsheet_metrics' from 'analytics'`

- [ ] **Step 3: Add get_tearsheet_metrics() to analytics.py**

```python
def get_tearsheet_metrics(conn=None) -> dict:
    """
    Build a daily returns series from wallet_snapshots and compute
    professional performance metrics. Full HTML via /tearsheet/download.
    Requires at least 20 distinct trading days.
    """
    import pandas as pd, numpy as np, statistics as _st

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
    pf       = round(wins.sum() / abs(losses.sum()), 2) if losses.sum() != 0 else 999.0

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
```

- [ ] **Step 4: Add routes to routes/analytics.py**

```python
@bp.route("/api/analytics/tearsheet")
def api_analytics_tearsheet():
    """GET /api/analytics/tearsheet -- professional performance metrics."""
    try:
        from analytics import get_tearsheet_metrics
        with db_conn() as conn:
            data = get_tearsheet_metrics(conn=conn)
        return _ok(data)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/analytics/tearsheet/download")
def api_analytics_tearsheet_download():
    """GET /api/analytics/tearsheet/download -- full quantstats HTML report."""
    try:
        import io, pandas as pd
        import quantstats as qs
        from flask import Response
        rows = []
        with db_conn() as conn:
            rows = conn.execute("""
                SELECT date(date) AS day, MAX(wallet_balance) AS balance
                FROM wallet_snapshots
                WHERE wallet_balance IS NOT NULL AND wallet_balance > 1
                GROUP BY day ORDER BY day ASC
            """).fetchall()
        if len(rows) < 20:
            return _err("Need at least 20 days of wallet data", 400)
        balances = pd.Series(
            [float(r["balance"]) for r in rows],
            index=pd.to_datetime([r["day"] for r in rows]),
        )
        returns = balances.pct_change().dropna()
        buf = io.StringIO()
        qs.reports.html(returns, output=buf, title="Trading Journal Tearsheet",
                        benchmark=None, download_filename=None)
        return Response(buf.getvalue(), mimetype="text/html",
                        headers={"Content-Disposition": "attachment; filename=tearsheet.html"})
    except Exception:
        traceback.print_exc()
        return _err("Tearsheet generation failed", 500)
```

- [ ] **Step 5: Run tests**

```bash
python3 -m pytest tests/test_tearsheet.py -v
```
Expected: all 3 PASS.

- [ ] **Step 6: Add tearsheet card to Analytics tab**

Add container in `templates/index.html`:
```html
<div class="card" style="margin-top:16px">
    <div class="card-header">Professional Performance Metrics</div>
    <div id="tearsheet-summary" style="padding:14px">Loading...</div>
</div>
```

Add to analytics JS (find the file that loads `/api/analytics/deep` and add alongside it):

```javascript
async function loadTearsheetSummary() {
    const el = document.getElementById('tearsheet-summary');
    if (!el) return;
    try {
        const r = await fetch('/api/analytics/tearsheet');
        const d = await r.json();
        if (!d.ok || !d.data || !d.data.available) {
            el.textContent = d.data?.reason || 'Need 20+ trading days of wallet history.';
            return;
        }
        const m = d.data;
        while (el.firstChild) el.removeChild(el.firstChild);
        const grid = document.createElement('div');
        grid.style.cssText = 'display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:12px';
        [
            ['Sharpe',       m.sharpe != null ? m.sharpe.toFixed(2) : '--', m.sharpe >= 1 ? 'pnl-pos' : m.sharpe < 0 ? 'pnl-neg' : ''],
            ['Max Drawdown', m.max_drawdown_pct + '%', 'pnl-neg'],
            ['CAGR',         (m.cagr_pct >= 0 ? '+' : '') + m.cagr_pct + '%', m.cagr_pct >= 0 ? 'pnl-pos' : 'pnl-neg'],
            ['Volatility',   m.volatility_pct + '%/yr', ''],
            ['Daily Win%',   m.win_rate_daily + '%', m.win_rate_daily >= 50 ? 'pnl-pos' : 'pnl-neg'],
            ['Total Return', (m.total_return_pct >= 0 ? '+' : '') + m.total_return_pct + '%', m.total_return_pct >= 0 ? 'pnl-pos' : 'pnl-neg'],
        ].forEach(([label, value, cls]) => {
            const stat = document.createElement('div');
            stat.style.cssText = 'background:var(--bg-secondary,#1a1a2e);padding:10px;border-radius:6px;text-align:center';
            const lbl = document.createElement('div');
            lbl.style.cssText = 'font-size:10px;color:var(--text-muted,#888);text-transform:uppercase';
            lbl.textContent = label;
            const val = document.createElement('div');
            val.style.cssText = 'font-size:16px;font-weight:700;margin-top:4px';
            val.textContent = value;
            if (cls) val.className = cls;
            stat.appendChild(lbl);
            stat.appendChild(val);
            grid.appendChild(stat);
        });
        el.appendChild(grid);
        const link = document.createElement('a');
        link.href = '/api/analytics/tearsheet/download';
        link.target = '_blank';
        link.className = 'btn btn-secondary';
        link.textContent = 'Download Full Tearsheet (HTML)';
        el.appendChild(link);
    } catch(e) {
        if (el) el.textContent = 'Could not load tearsheet.';
    }
}
```

Bump `?v=` for the modified JS file.

- [ ] **Step 7: Commit**

```bash
git add analytics.py routes/analytics.py templates/index.html tests/test_tearsheet.py
git commit -m "feat: quantstats tearsheet -- Sharpe/CAGR/drawdown metrics + downloadable HTML report"
```

---

## Task 3: Automatic Setup-Type Classification

**Problem:** `positions.setup_type` is manually tagged (usually empty). The scanner writes `trade_type` into `analyzed_calls.analysis_json`. Auto-populate it from the linked call.

**Files:**
- Modify: `sync_base.py` — add `_populate_setup_type_from_call()`; call it from `auto_match_calls()` and `auto_close_calls()`
- Modify: `bitget_sync.py` — backfill on each sync for existing linked positions with empty setup_type
- Create: `tests/test_setup_autoclassify.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_setup_autoclassify.py
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest


@pytest.fixture
def db_cls(tmp_path, monkeypatch):
    import database as _db
    db_file = str(tmp_path / "cls.db")
    monkeypatch.setattr(_db, "DB_PATH", db_file)
    _db.init_db()
    conn = _db.get_conn()
    yield conn
    conn.close()


def _insert_call_with_type(conn, symbol, direction, trade_type, status="saved"):
    analysis = json.dumps({"trade_type": trade_type, "setup_score": 7})
    conn.execute("""
        INSERT INTO analyzed_calls
          (symbol, direction, entry_price, sl_price, tp1_price, status,
           created_at, analysis_json)
        VALUES (?,?,0.04,0.038,0.045,?,datetime('now','-2 hours'),?)
    """, (symbol, direction, status, analysis))
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_position(conn, symbol, direction, call_id=None):
    conn.execute("""
        INSERT INTO positions
          (symbol, base_asset, direction, realized_pnl, exchange,
           open_time, close_time, call_id)
        VALUES (?,?,?,10.0,'bitget','2026-05-01','2026-05-02',?)
    """, (symbol, symbol[:-4], direction, call_id))
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_setup_type_populated_from_call(db_cls):
    from sync_base import _populate_setup_type_from_call
    call_id = _insert_call_with_type(db_cls, "BTCUSDT", "Long", "Breakout")
    pos_id  = _insert_position(db_cls, "BTCUSDT", "Long", call_id=call_id)
    _populate_setup_type_from_call(db_cls, pos_id, call_id)
    row = db_cls.execute("SELECT setup_type FROM positions WHERE id=?", (pos_id,)).fetchone()
    assert row[0] == "Breakout"


def test_missing_analysis_json_does_not_crash(db_cls):
    from sync_base import _populate_setup_type_from_call
    db_cls.execute("""
        INSERT INTO analyzed_calls
          (symbol, direction, entry_price, sl_price, tp1_price, status, created_at)
        VALUES ('ETHUSDT','Long',0.04,0.038,0.045,'saved',datetime('now'))
    """)
    db_cls.commit()
    call_id = db_cls.execute("SELECT last_insert_rowid()").fetchone()[0]
    pos_id  = _insert_position(db_cls, "ETHUSDT", "Long", call_id=call_id)
    _populate_setup_type_from_call(db_cls, pos_id, call_id)
    row = db_cls.execute("SELECT setup_type FROM positions WHERE id=?", (pos_id,)).fetchone()
    assert row[0] is None or row[0] == ""
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_setup_autoclassify.py -v
```
Expected: `ImportError: cannot import name '_populate_setup_type_from_call' from 'sync_base'`

- [ ] **Step 3: Add _populate_setup_type_from_call() to sync_base.py**

Add after `auto_match_calls()`:

```python
def _populate_setup_type_from_call(conn, position_id: int, call_id: int) -> None:
    """
    Read trade_type from analyzed_calls.analysis_json and write to positions.setup_type.
    No-op if analysis_json is absent or contains no trade_type field.
    """
    import json as _json
    try:
        row = conn.execute(
            "SELECT analysis_json FROM analyzed_calls WHERE id=?", (call_id,)
        ).fetchone()
        if not row or not row[0]:
            return
        data = _json.loads(row[0])
        trade_type = (data.get("trade_type") or data.get("setup_type")
                      or data.get("setup_label") or "")
        if trade_type:
            conn.execute(
                "UPDATE positions SET setup_type=? WHERE id=? AND (setup_type IS NULL OR setup_type='')",
                (trade_type, position_id),
            )
            conn.commit()
    except Exception:
        pass
```

In `auto_match_calls()`, after `cur.execute("UPDATE analyzed_calls SET status='matched'...")`, add:
```python
        _populate_setup_type_from_call(conn, pos_id, call_id)
```

In `auto_close_calls()`, at the end of each call's UPDATE block (before `closed += 1`), add:
```python
        # Backfill setup_type on the position if blank
        linked = cur.execute(
            "SELECT id FROM positions WHERE call_id=? AND (setup_type IS NULL OR setup_type='')",
            (call_id,)
        ).fetchone()
        if linked:
            _populate_setup_type_from_call(conn, linked[0], call_id)
```

- [ ] **Step 4: Add startup backfill in bitget_sync.run_sync()**

After the existing `auto_match_calls` call:

```python
        try:
            from sync_base import _populate_setup_type_from_call as _pst
            unclassified = conn.execute("""
                SELECT p.id, p.call_id FROM positions p
                WHERE p.call_id IS NOT NULL
                  AND (p.setup_type IS NULL OR p.setup_type = '')
                LIMIT 100
            """).fetchall()
            for pos_id, call_id in unclassified:
                _pst(conn, pos_id, call_id)
            if unclassified:
                print(f"[Sync] Backfilled setup_type for {len(unclassified)} positions", flush=True)
        except Exception as e:
            print(f"[Sync] setup_type backfill skipped: {e}", flush=True)
```

- [ ] **Step 5: Run tests**

```bash
python3 -m pytest tests/test_setup_autoclassify.py tests/test_sync_base.py -v --tb=short
```
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add sync_base.py bitget_sync.py tests/test_setup_autoclassify.py
git commit -m "feat: auto-classify setup_type from analyzed_call trade_type -- no manual tagging"
```

---

## Task 4: Funding Fee Visibility + Execution Quality Columns

**Problem:** Funding is buried in total_fees. Execution quality (signal_price, entry_lag) is not tracked at all.

**Files:**
- Modify: `database.py` — migrations 35, 36, 37
- Modify: `bitget_sync.py` — write funding to new `funding_pnl` column
- Modify: `analytics.py` — add `total_funding_pnl` to KPIs + `get_execution_quality()`
- Modify: `routes/analytics.py` — add `GET /api/analytics/execution-quality`
- Create: `tests/test_execution_quality.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_execution_quality.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest


@pytest.fixture
def db_exec(tmp_path, monkeypatch):
    import database as _db
    db_file = str(tmp_path / "exec.db")
    monkeypatch.setattr(_db, "DB_PATH", db_file)
    _db.init_db()
    conn = _db.get_conn()
    yield conn
    conn.close()


def test_funding_pnl_column_exists(db_exec):
    cols = [r[1] for r in db_exec.execute("PRAGMA table_info(positions)").fetchall()]
    assert "funding_pnl" in cols


def test_signal_price_column_exists(db_exec):
    cols = [r[1] for r in db_exec.execute("PRAGMA table_info(positions)").fetchall()]
    assert "signal_price" in cols


def test_execution_lag_column_exists(db_exec):
    cols = [r[1] for r in db_exec.execute("PRAGMA table_info(positions)").fetchall()]
    assert "execution_lag_minutes" in cols


def test_dashboard_kpis_has_total_funding_pnl(db_exec):
    db_exec.execute("""
        INSERT INTO positions (symbol, base_asset, direction, realized_pnl,
               open_time, close_time, exchange, funding_pnl)
        VALUES ('BTCUSDT','BTC','Long',50.0,'2026-01-01','2026-01-02','bitget',-2.5)
    """)
    db_exec.commit()
    from analytics import get_dashboard_kpis
    kpis = get_dashboard_kpis(conn=db_exec)
    assert "total_funding_pnl" in kpis
    assert kpis["total_funding_pnl"] == pytest.approx(-2.5, abs=0.01)


def test_get_execution_quality_returns_stats(db_exec):
    db_exec.execute("""
        INSERT INTO positions
          (symbol, base_asset, direction, realized_pnl, exchange,
           open_time, close_time, execution_lag_minutes, signal_price, entry_price)
        VALUES ('BTCUSDT','BTC','Long',30.0,'bitget',
                '2026-01-01','2026-01-02', 45, 50000.0, 50200.0)
    """)
    db_exec.commit()
    from analytics import get_execution_quality
    result = get_execution_quality(conn=db_exec)
    assert "avg_lag_minutes" in result
    assert "avg_slippage_pct" in result
    assert result["sample_size"] >= 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_execution_quality.py::test_funding_pnl_column_exists -v
```
Expected: `AssertionError: funding_pnl not in columns`

- [ ] **Step 3: Add migrations 35-37 to database.py**

After migration 34:

```python
_apply(35, "positions.funding_pnl",
       "ALTER TABLE positions ADD COLUMN funding_pnl REAL DEFAULT NULL")
_apply(36, "positions.signal_price",
       "ALTER TABLE positions ADD COLUMN signal_price REAL DEFAULT NULL")
_apply(37, "positions.execution_lag_minutes",
       "ALTER TABLE positions ADD COLUMN execution_lag_minutes INTEGER DEFAULT NULL")
```

- [ ] **Step 4: Update bitget_sync._sync_positions() to write funding_pnl**

Find the `cur.execute("""INSERT INTO positions...""")` in `_sync_positions()`. Add `funding_pnl` to the column list and value tuple. The `funding` variable already exists in that scope (`funding = _f(r.get("totalFunding"), 0)`). Replace the INSERT with:

```python
        cur.execute("""
            INSERT INTO positions
              (symbol, base_asset, direction, margin_mode,
               open_time, close_time, duration_minutes,
               entry_price, close_price,
               size_contracts, size_usdt,
               position_pnl, realized_pnl,
               opening_fee, closing_fee, total_fees,
               funding_pnl,
               external_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            symbol, base_asset, direction, margin,
            open_time, close_time, duration,
            entry_price, close_price,
            size_raw + base_asset, size_usdt,
            position_pnl, realized_pnl,
            opening_fee, closing_fee, total_fees,
            funding if funding else None,
            ext_id,
        ))
```

- [ ] **Step 5: Add total_funding_pnl to get_dashboard_kpis() in analytics.py**

After `total_fees` calculation in `get_dashboard_kpis()`, add:

```python
    total_funding_pnl = round(
        _val(conn, f"SELECT SUM(funding_pnl) FROM positions {where}", params) or 0.0, 4
    )
```

Add to the return dict:
```python
        "total_funding_pnl":  total_funding_pnl,
```

- [ ] **Step 6: Add get_execution_quality() to analytics.py**

```python
def get_execution_quality(conn=None) -> dict:
    """
    Execution quality: time lag between scanner signal and actual entry,
    and price slippage (entry_price vs signal_price).
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
```

- [ ] **Step 7: Add route to routes/analytics.py**

```python
@bp.route("/api/analytics/execution-quality")
def api_analytics_execution_quality():
    """GET /api/analytics/execution-quality -- signal lag and slippage stats."""
    try:
        from analytics import get_execution_quality
        with db_conn() as conn:
            data = get_execution_quality(conn=conn)
        return _ok(data)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)
```

- [ ] **Step 8: Also update auto_match_calls() in sync_base.py to compute execution lag**

In `auto_match_calls()`, after linking a call to a position, add:

```python
        # Compute execution lag: minutes between call created_at and position open_time
        try:
            from datetime import datetime as _dt
            call_row = conn.execute(
                "SELECT created_at, entry_price FROM analyzed_calls WHERE id=?", (call_id,)
            ).fetchone()
            pos_row = conn.execute(
                "SELECT open_time FROM positions WHERE id=?", (pos_id,)
            ).fetchone()
            if call_row and pos_row and call_row[0] and pos_row[0]:
                fmt = "%Y-%m-%d %H:%M:%S"
                call_dt = _dt.strptime(call_row[0][:19], fmt)
                pos_dt  = _dt.strptime(pos_row[0][:19], fmt)
                lag_min = max(0, int((pos_dt - call_dt).total_seconds() / 60))
                signal_price = float(call_row[1]) if call_row[1] else None
                cur.execute("""
                    UPDATE positions
                    SET execution_lag_minutes=?, signal_price=?
                    WHERE id=? AND execution_lag_minutes IS NULL
                """, (lag_min, signal_price, pos_id))
        except Exception:
            pass
```

- [ ] **Step 9: Run all execution quality tests**

```bash
python3 -m pytest tests/test_execution_quality.py -v --tb=short
```
Expected: all 5 PASS.

- [ ] **Step 10: Commit**

```bash
git add database.py bitget_sync.py analytics.py routes/analytics.py sync_base.py tests/test_execution_quality.py
git commit -m "feat: funding_pnl column, signal_price, execution lag tracking + /api/analytics/execution-quality"
```

---

## Final Checks

```bash
python3 -m pytest tests/test_benchmark.py tests/test_tearsheet.py \
    tests/test_setup_autoclassify.py tests/test_execution_quality.py -v

python3 -m pytest tests/ -q --tb=no 2>&1 | tail -5
git push origin main
```

Deploy to Pi:
```bash
# SSH to Pi:
cd /home/fbauer/trading-journal && git pull origin main && sudo systemctl restart trading-journal
```
