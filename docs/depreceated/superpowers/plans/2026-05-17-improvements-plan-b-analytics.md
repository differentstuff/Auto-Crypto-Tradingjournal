# Improvements Plan B — Analytics & Features

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add portfolio-level risk view, expose P&L by setup type prominently, and close the hindsight feedback loop with automatic scanner recalibration.

**Architecture:** Three independent features. Tasks 1 and 2 add new API endpoints + UI sections. Task 3 extends the existing hindsight pipeline with a weekly recalibration step.

**Tech Stack:** Python 3.13, Flask, SQLite, pandas, existing analytics.py + ai_scanner.py + ai_hindsight.py.

**Security note:** All innerHTML use in JS renders only server-generated, numeric/alphanumeric data (PnL floats, sector names from a hardcoded map, pre-validated strings). No user-supplied freetext is interpolated. Follow the existing project pattern (all other JS modules do the same).

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `analytics.py` | **Modify** | Add `get_setup_type_stats()` with profit_factor and avg_win/avg_loss per setup type. |
| `routes/analytics.py` | **Modify** | Add `GET /api/analytics/by-setup` endpoint. |
| `routes/live.py` | **Modify** | Add `GET /api/live/portfolio-risk` endpoint. |
| `ai_hindsight.py` | **Modify** | Add `compute_feedback()` — aggregates TP/FP/TN/FN rates for recalibration. |
| `bitget_sync.py` | **Modify** | Weekly trigger for hindsight-based scanner recalibration. |
| `routes/scanner.py` | **Modify** | Expose `GET /api/scanner/feedback` showing last recalibration result. |
| `static/js/07-analytics.js` | **Modify** | Render setup-type P&L table in Analytics tab. |
| `static/js/06-live.js` | **Modify** | Render portfolio risk section in Live tab. |
| `static/js/09-scanner.js` | **Modify** | Show hindsight feedback summary in Scanner tab. |
| `tests/test_setup_pnl.py` | **Create** | Tests for setup-type P&L analytics. |
| `tests/test_portfolio_risk.py` | **Create** | Tests for portfolio risk endpoint. |
| `tests/test_hindsight_feedback.py` | **Create** | Tests for feedback loop computation. |

---

## Task 1: P&L by Setup Type (Prominent Display)

**Context:** `get_deep_stats()` in `analytics.py` already returns `by_setup` with `trade_count`, `total_pnl`, `win_rate`, `avg_pnl`. Missing: `profit_factor` (crucial for professional evaluation) and `avg_win` / `avg_loss` (for loss:win ratio). The data exists but is buried alongside 10 other breakdowns. This task adds a dedicated endpoint and a top-level UI card.

**Files:**
- Modify: `analytics.py` — add `get_setup_type_stats()`
- Modify: `routes/analytics.py` — add `GET /api/analytics/by-setup`
- Modify: `static/js/07-analytics.js` — render setup P&L table
- Create: `tests/test_setup_pnl.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_setup_pnl.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest


@pytest.fixture
def db_with_setups(tmp_path, monkeypatch):
    import database as _db
    db_file = str(tmp_path / "test_setup.db")
    monkeypatch.setattr(_db, "DB_PATH", db_file)
    _db.init_db()
    conn = _db.get_conn()
    rows = [
        ("BTCUSDT", "BTC", "Long", "Breakout",  100.0, "2026-01-01", "2026-01-02"),
        ("BTCUSDT", "BTC", "Long", "Breakout",  -40.0, "2026-01-03", "2026-01-04"),
        ("BTCUSDT", "BTC", "Long", "Breakout",   80.0, "2026-01-05", "2026-01-06"),
        ("ETHUSDT", "ETH", "Long", "Reversal",   50.0, "2026-01-07", "2026-01-08"),
        ("ETHUSDT", "ETH", "Long", "Reversal",  -60.0, "2026-01-09", "2026-01-10"),
    ]
    for sym, base, direction, setup_type, pnl, open_t, close_t in rows:
        conn.execute("""
            INSERT INTO positions
              (symbol, base_asset, direction, setup_type, realized_pnl, open_time, close_time, exchange)
            VALUES (?,?,?,?,?,?,?,'bitget')
        """, (sym, base, direction, setup_type, pnl, open_t, close_t))
    conn.commit()
    yield conn
    conn.close()


def test_setup_type_stats_returns_all_types(db_with_setups):
    from analytics import get_setup_type_stats
    result = get_setup_type_stats(conn=db_with_setups)
    setup_names = [r["setup_type"] for r in result]
    assert "Breakout" in setup_names
    assert "Reversal" in setup_names


def test_breakout_win_rate(db_with_setups):
    from analytics import get_setup_type_stats
    result = get_setup_type_stats(conn=db_with_setups)
    breakout = next(r for r in result if r["setup_type"] == "Breakout")
    assert breakout["win_rate"] == pytest.approx(66.7, abs=0.5)


def test_profit_factor_present(db_with_setups):
    from analytics import get_setup_type_stats
    result = get_setup_type_stats(conn=db_with_setups)
    for row in result:
        assert "profit_factor" in row


def test_avg_win_avg_loss_present(db_with_setups):
    from analytics import get_setup_type_stats
    result = get_setup_type_stats(conn=db_with_setups)
    for row in result:
        assert "avg_win" in row
        assert "avg_loss" in row


def test_breakout_profit_factor(db_with_setups):
    from analytics import get_setup_type_stats
    result = get_setup_type_stats(conn=db_with_setups)
    breakout = next(r for r in result if r["setup_type"] == "Breakout")
    # wins: 100+80=180, losses: 40 -> PF = 180/40 = 4.5
    assert breakout["profit_factor"] == pytest.approx(4.5, abs=0.1)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
python3 -m pytest tests/test_setup_pnl.py -v
```
Expected: `ImportError: cannot import name 'get_setup_type_stats' from 'analytics'`

- [ ] **Step 3: Add get_setup_type_stats() to analytics.py**

Add after the `get_deep_stats()` function:

```python
def get_setup_type_stats(filters=None, conn=None) -> list:
    """
    Returns per-setup-type performance breakdown, sorted by total P&L descending.
    Each row: setup_type, trade_count, total_pnl, win_rate, avg_pnl,
              avg_win, avg_loss, profit_factor.
    Only returns setup types with at least 1 trade.
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
            r["profit_factor"] = 999.0  # no losing trades
        r["avg_win"]  = r["avg_win"]  or 0.0
        r["avg_loss"] = r["avg_loss"] or 0.0

    return rows
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_setup_pnl.py -v
```
Expected: all 5 tests PASS.

- [ ] **Step 5: Add the route to routes/analytics.py**

Add after the `GET /api/analytics/rr` route:

```python
@bp.route("/api/analytics/by-setup")
def api_analytics_by_setup():
    """GET /api/analytics/by-setup -- P&L breakdown by setup type."""
    try:
        from analytics import get_setup_type_stats
        with db_conn() as conn:
            data = get_setup_type_stats(filters=_filters_from_args(), conn=conn)
        return _ok({"setups": data})
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)
```

- [ ] **Step 6: Add setup P&L table to analytics JS**

In `static/js/07-analytics.js`, add a `renderSetupBreakdown(setups)` function and call it from the analytics load path.

The function builds a table from server-numeric data (all values are floats from SQL aggregates — no user-freetext in table cells):

```javascript
function renderSetupBreakdown(setups) {
    if (!setups || !setups.length) {
        return '<p class="muted">No setup-type data yet. Tag trades with setup types in the journal.</p>';
    }
    const header = ['Setup Type','Trades','Total P&L','Win Rate','Avg P&L','Avg Win','Avg Loss','Profit Factor'];
    const headerRow = header.map(h => `<th>${h}</th>`).join('');
    const bodyRows = setups.map(s => {
        const pf = s.profit_factor === 999 ? 'INF' : (s.profit_factor != null ? s.profit_factor.toFixed(2) : '-');
        const pfPos = (s.profit_factor || 0) >= 1.5;
        const cells = [
            s.setup_type,
            s.trade_count,
            (s.total_pnl >= 0 ? '+' : '') + '$' + s.total_pnl.toFixed(2),
            s.win_rate + '%',
            (s.avg_pnl >= 0 ? '+' : '') + '$' + s.avg_pnl.toFixed(2),
            '+$' + (s.avg_win || 0).toFixed(2),
            '-$' + Math.abs(s.avg_loss || 0).toFixed(2),
            pf,
        ];
        const classes = ['', '', s.total_pnl >= 0 ? 'pnl-pos' : 'pnl-neg', '',
                         s.avg_pnl >= 0 ? 'pnl-pos' : 'pnl-neg',
                         'pnl-pos', 'pnl-neg', pfPos ? 'pnl-pos' : ''];
        return '<tr>' + cells.map((c, i) =>
            `<td class="${classes[i]}">${c}</td>`).join('') + '</tr>';
    }).join('');
    return `<table class="data-table"><thead><tr>${headerRow}</tr></thead><tbody>${bodyRows}</tbody></table>`;
}
```

In the analytics tab load function, fetch `/api/analytics/by-setup` and populate a container:

```javascript
// Add to the analytics load sequence:
const setupResp = await fetch('/api/analytics/by-setup' + filtersToQuery());
const setupData = await setupResp.json();
const setupEl = document.getElementById('setup-breakdown-body');
if (setupEl) {
    setupEl.innerHTML = renderSetupBreakdown((setupData.data || {}).setups || []);
}
```

Add a card container to the Analytics section in `templates/index.html` (or JS-rendered analytics HTML):

```html
<div class="card">
    <div class="card-header">P&amp;L by Setup Type</div>
    <div id="setup-breakdown-body">Loading...</div>
</div>
```

Bump `?v=` in `templates/index.html` for `07-analytics.js`.

- [ ] **Step 7: Commit**

```bash
git add analytics.py routes/analytics.py static/js/07-analytics.js templates/index.html tests/test_setup_pnl.py
git commit -m "feat: P&L by setup type — dedicated endpoint + analytics card with profit factor"
```

---

## Task 2: Portfolio Risk View

**Problem:** The Live tab shows individual positions but not the book as a whole. A trader with 3 positions in AVAX, SOL, NEAR (all L1s) has no visibility into sector concentration.

**Fix:** New `GET /api/live/portfolio-risk` endpoint. Returns: sector exposure USD by hardcoded map, margin utilization %, long/short split, top sector concentration %.

**Files:**
- Modify: `routes/live.py` — add `_classify_sector`, `_compute_portfolio_risk`, and route
- Modify: `static/js/06-live.js` — render risk card
- Create: `tests/test_portfolio_risk.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_portfolio_risk.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest


def test_sector_classification_known_symbol():
    from routes.live import _classify_sector
    assert _classify_sector("SOLUSDT") == "L1"
    assert _classify_sector("UNIUSDT") == "DeFi"
    assert _classify_sector("PEPEUSDT") == "Meme"
    assert _classify_sector("ARBUSDT") == "L2"
    assert _classify_sector("UNKNOWN123USDT") == "Other"


def test_portfolio_risk_empty_positions():
    from routes.live import _compute_portfolio_risk
    result = _compute_portfolio_risk([], equity=1000.0)
    assert result["total_long_usd"] == 0
    assert result["total_short_usd"] == 0
    assert result["margin_used_pct"] == 0


def test_portfolio_risk_long_short_split():
    from routes.live import _compute_portfolio_risk
    positions = [
        {"symbol": "BTCUSDT", "direction": "Long",  "size_usdt": 500, "margin_usdt": 50},
        {"symbol": "ETHUSDT", "direction": "Long",  "size_usdt": 300, "margin_usdt": 30},
        {"symbol": "SOLUSDT", "direction": "Short", "size_usdt": 200, "margin_usdt": 20},
    ]
    result = _compute_portfolio_risk(positions, equity=1000.0)
    assert result["total_long_usd"] == 800
    assert result["total_short_usd"] == 200
    assert result["margin_used_pct"] == pytest.approx(10.0, abs=0.1)


def test_sector_exposure_grouping():
    from routes.live import _compute_portfolio_risk
    positions = [
        {"symbol": "BTCUSDT", "direction": "Long", "size_usdt": 500, "margin_usdt": 50},
        {"symbol": "ETHUSDT", "direction": "Long", "size_usdt": 300, "margin_usdt": 30},
        {"symbol": "UNIUSDT", "direction": "Long", "size_usdt": 200, "margin_usdt": 20},
    ]
    result = _compute_portfolio_risk(positions, equity=2000.0)
    sectors = {s["sector"]: s["usd"] for s in result["by_sector"]}
    assert sectors.get("L1", 0) == 800
    assert sectors.get("DeFi", 0) == 200
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_portfolio_risk.py -v
```
Expected: `ImportError: cannot import name '_classify_sector' from 'routes.live'`

- [ ] **Step 3: Add sector map, helpers, and route to routes/live.py**

Add before `bp = Blueprint(...)`:

```python
_SECTOR_MAP: dict[str, str] = {
    "BTCUSDT": "BTC",
    "ETHUSDT": "ETH", "LDOUSDT": "ETH", "STRKUSDT": "ETH", "ENSUSDT": "ETH",
    "SOLUSDT": "L1",  "BNBUSDT": "L1",  "XRPUSDT": "L1",  "ADAUSDT": "L1",
    "AVAXUSDT": "L1", "DOTUSDT": "L1",  "ATOMUSDT": "L1", "NEARUSDT": "L1",
    "TRXUSDT": "L1",  "XLMUSDT": "L1",  "TONUSDT": "L1",  "FTMUSDT": "L1",
    "ALGOUSDT": "L1", "EGLDUSDT": "L1", "SUIUSDT": "L1",  "APTUSDT": "L1",
    "INJUSDT": "L1",  "SEIUSDT": "L1",  "ICPUSDT": "L1",  "HBARUSDT": "L1",
    "KASUSDT": "L1",  "LTCUSDT": "L1",  "BCHUSDT": "L1",  "MINAUSDT": "L1",
    "TIAUSDT": "L1",  "STXUSDT": "L1",
    "MATICUSDT": "L2", "ARBUSDT": "L2", "OPUSDT": "L2",   "ZKUSDT": "L2",
    "METISUSDT": "L2",
    "UNIUSDT": "DeFi", "AAVEUSDT": "DeFi", "LINKUSDT": "DeFi", "CRVUSDT": "DeFi",
    "MKRUSDT": "DeFi", "SNXUSDT": "DeFi",  "COMPUSDT": "DeFi", "DYDXUSDT": "DeFi",
    "CAKEUSDT": "DeFi","GMXUSDT": "DeFi",  "PENDLEUSDT": "DeFi","JUPUSDT": "DeFi",
    "SUSHIUSDT": "DeFi","RUNEUSDT": "DeFi",
    "FETUSDT": "AI",   "RENDERUSDT": "AI", "WLDUSDT": "AI",  "TAOUSDT": "AI",
    "GRTUSDT": "AI",   "AGIXUSDT": "AI",   "OCEANUSDT": "AI","ARKMUSDT": "AI",
    "DOGEUSDT": "Meme","SHIBUSDT": "Meme", "PEPEUSDT": "Meme","WIFUSDT": "Meme",
    "BONKUSDT": "Meme","BOMEUSDT": "Meme", "FLOKIUSDT": "Meme","MOGUSDT": "Meme",
    "POPCATUSDT": "Meme","TURBOUSDT": "Meme",
    "ORDIUSDT": "BTC Eco","SATSUSDT": "BTC Eco",
    "SANDUSDT": "Gaming","AXSUSDT": "Gaming","GALAUSDT": "Gaming","IMXUSDT": "Gaming",
    "MANAUSDT": "Gaming","APEUSDT": "Gaming",
}


def _classify_sector(symbol: str) -> str:
    return _SECTOR_MAP.get(symbol.upper(), "Other")


def _compute_portfolio_risk(positions: list, equity: float) -> dict:
    total_long   = sum(p.get("size_usdt", 0) for p in positions if p.get("direction") == "Long")
    total_short  = sum(p.get("size_usdt", 0) for p in positions if p.get("direction") == "Short")
    total_margin = sum(float(p.get("margin_usdt") or 0) for p in positions)
    margin_pct   = round(total_margin / equity * 100, 1) if equity else 0.0

    sector_usd: dict[str, float] = {}
    for p in positions:
        sec = _classify_sector(p.get("symbol", ""))
        sector_usd[sec] = sector_usd.get(sec, 0) + float(p.get("size_usdt") or 0)

    by_sector = sorted(
        [{"sector": k, "usd": round(v, 2)} for k, v in sector_usd.items()],
        key=lambda x: x["usd"], reverse=True,
    )
    total_notional = total_long + total_short
    top_sector_pct = round(by_sector[0]["usd"] / total_notional * 100, 1) if total_notional and by_sector else 0.0

    return {
        "total_long_usd":   round(total_long, 2),
        "total_short_usd":  round(total_short, 2),
        "net_exposure_usd": round(total_long - total_short, 2),
        "total_margin_usd": round(total_margin, 2),
        "margin_used_pct":  margin_pct,
        "top_sector_pct":   top_sector_pct,
        "by_sector":        by_sector,
        "position_count":   len(positions),
    }
```

Add the route after the existing `GET /api/live/positions` route:

```python
@bp.route("/api/live/portfolio-risk")
def api_portfolio_risk():
    """GET /api/live/portfolio-risk -- aggregated book risk metrics."""
    try:
        positions  = []
        total_eq   = 0.0
        try:
            positions  = bitget_client.get_open_positions()
            eq         = bitget_client.get_account_equity()
            total_eq  += float(eq.get("accountEquity") or 0)
        except Exception:
            pass
        try:
            if blofin_client.is_configured():
                positions += blofin_client.get_open_positions()
                bl_eq      = blofin_client.get_account_equity()
                total_eq  += float(bl_eq.get("equity") or 0)
        except Exception:
            pass
        return _ok(_compute_portfolio_risk(positions, equity=total_eq))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)
```

- [ ] **Step 4: Run tests**

```bash
python3 -m pytest tests/test_portfolio_risk.py -v
```
Expected: all 4 tests PASS.

- [ ] **Step 5: Add portfolio risk card to Live tab JS**

In `static/js/06-live.js`, add `loadPortfolioRisk()` called when Live tab opens:

```javascript
async function loadPortfolioRisk() {
    const el = document.getElementById('portfolio-risk-card');
    if (!el) return;
    try {
        const r = await fetch('/api/live/portfolio-risk');
        const d = await r.json();
        if (!d.ok) { el.textContent = 'Risk data unavailable.'; return; }
        const risk = d.data;
        // All values below are server-generated numbers or hardcoded sector name strings
        const marginBad = risk.margin_used_pct > 30;
        const topBad    = risk.top_sector_pct > 70;
        const sectors   = (risk.by_sector || [])
            .map(s => `<tr><td>${s.sector}</td><td>$${s.usd.toLocaleString('en-US',{maximumFractionDigits:0})}</td></tr>`)
            .join('');
        el.innerHTML = `
            <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin-bottom:12px">
                <div class="risk-stat"><span class="risk-label">Net Exposure</span>
                    <span class="risk-value ${risk.net_exposure_usd>=0?'pnl-pos':'pnl-neg'}">
                        $${risk.net_exposure_usd.toLocaleString('en-US',{maximumFractionDigits:0})}</span></div>
                <div class="risk-stat"><span class="risk-label">Margin Used</span>
                    <span class="risk-value ${marginBad?'pnl-neg':''}">${risk.margin_used_pct}%</span></div>
                <div class="risk-stat"><span class="risk-label">Long / Short</span>
                    <span class="risk-value">
                        $${risk.total_long_usd.toFixed(0)} / $${risk.total_short_usd.toFixed(0)}</span></div>
                <div class="risk-stat"><span class="risk-label">Top Sector</span>
                    <span class="risk-value ${topBad?'pnl-neg':''}">${risk.top_sector_pct}%</span></div>
            </div>
            <table class="data-table">
                <thead><tr><th>Sector</th><th>Notional</th></tr></thead>
                <tbody>${sectors}</tbody>
            </table>`;
    } catch(e) {
        el.textContent = 'Could not load portfolio risk.';
    }
}
```

Add CSS at end of `<style>` block in `templates/index.html`:
```css
.risk-stat{background:var(--bg-secondary);padding:12px;border-radius:6px}
.risk-label{display:block;font-size:11px;color:var(--text-muted);text-transform:uppercase}
.risk-value{display:block;font-size:18px;font-weight:600;margin-top:4px}
```

Add container in Live tab HTML:
```html
<div class="card" style="margin-top:16px">
    <div class="card-header">Portfolio Risk</div>
    <div id="portfolio-risk-card">Loading...</div>
</div>
```

Bump `?v=` in `templates/index.html` for `06-live.js`.

- [ ] **Step 6: Commit**

```bash
git add routes/live.py static/js/06-live.js templates/index.html tests/test_portfolio_risk.py
git commit -m "feat: portfolio risk view -- sector exposure, margin %, net long/short"
```

---

## Task 3: Hindsight Feedback Loop

**Problem:** Hindsight scores TP/FP/TN/FN but results never influence the scanner. This task computes per-score-bucket FP rates weekly and exposes a recommendation (raise/lower threshold) via a new endpoint.

**Files:**
- Modify: `ai_hindsight.py` — add `compute_feedback()`
- Modify: `database.py` — migration 34: `positions.setup_score`
- Modify: `bitget_sync.py` — weekly feedback trigger in `_maybe_update_rulebook()`
- Modify: `routes/scanner.py` — add `GET /api/scanner/feedback`
- Create: `tests/test_hindsight_feedback.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hindsight_feedback.py
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest


@pytest.fixture
def db_with_hindsight(tmp_path, monkeypatch):
    import database as _db
    db_file = str(tmp_path / "test_hs.db")
    monkeypatch.setattr(_db, "DB_PATH", db_file)
    _db.init_db()
    conn = _db.get_conn()

    # 6 TP (correct bullish score 7), 4 FP (wrong bullish score 7) = 40% FP
    for i in range(10):
        conn.execute("""
            INSERT INTO positions
              (symbol, base_asset, direction, realized_pnl, open_time, close_time, exchange)
            VALUES (?,?,?,?,?,?,?)
        """, ("BTCUSDT","BTC","Long", 50.0 if i<6 else -30.0,
              "2026-01-01","2026-01-02","bitget"))
    conn.commit()
    positions = conn.execute("SELECT id FROM positions").fetchall()
    for idx, (pos_id,) in enumerate(positions):
        verdict = "TP" if idx < 6 else "FP"
        conn.execute("""
            INSERT INTO trade_hindsight
              (position_id, setup_score, would_enter, verdict, actual_pnl)
            VALUES (?,7,1,?,?)
        """, (pos_id, verdict, 50.0 if verdict=="TP" else -30.0))
    conn.commit()
    yield conn
    conn.close()


def test_compute_feedback_returns_buckets(db_with_hindsight):
    from ai_hindsight import compute_feedback
    result = compute_feedback(conn=db_with_hindsight)
    assert "buckets" in result
    assert "recommendation" in result
    assert len(result["buckets"]) > 0


def test_fp_rate_computed_correctly(db_with_hindsight):
    from ai_hindsight import compute_feedback
    result = compute_feedback(conn=db_with_hindsight)
    bucket = next((b for b in result["buckets"] if b["score_range"] == "7-8"), None)
    assert bucket is not None
    assert bucket["fp_rate"] == pytest.approx(40.0, abs=1.0)


def test_high_fp_triggers_raise_recommendation(db_with_hindsight):
    from ai_hindsight import compute_feedback
    result = compute_feedback(conn=db_with_hindsight)
    # 40% FP rate with 10 samples -> raise_threshold
    assert result["recommendation"] == "raise_threshold"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_hindsight_feedback.py -v
```
Expected: `ImportError: cannot import name 'compute_feedback' from 'ai_hindsight'`

- [ ] **Step 3: Add migration 34 to database.py**

In `database.py`, after migration 33, add:

```python
_apply(34, "positions.setup_score", "ALTER TABLE positions ADD COLUMN setup_score INTEGER DEFAULT NULL")
```

- [ ] **Step 4: Add compute_feedback() to ai_hindsight.py**

Add at the end of `ai_hindsight.py`:

```python
def compute_feedback(conn=None) -> dict:
    """
    Aggregate hindsight TP/FP/TN/FN into per-score-bucket accuracy.
    Writes result to settings['hindsight_feedback_json'].
    Returns {"buckets", "recommendation", "sample_size", "computed_at"}.

    Score buckets: 6 | 7-8 | 9-10
    raise_threshold: FP rate > 40% in any entered bucket with sample >= 5
    lower_threshold: FP rate < 10% and TP rate > 60% across all entered buckets
    ok: otherwise
    """
    import json, datetime
    from database import get_conn
    if conn is None:
        conn = get_conn()

    rows = conn.execute("""
        SELECT h.verdict, h.would_enter, h.setup_score, p.realized_pnl
        FROM trade_hindsight h
        JOIN positions p ON p.id = h.position_id
        WHERE h.verdict IS NOT NULL
    """).fetchall()

    if not rows:
        return {"buckets": [], "recommendation": "ok", "sample_size": 0}

    raw: dict[str, dict] = {
        "6":    {"TP": 0, "FP": 0, "TN": 0, "FN": 0},
        "7-8":  {"TP": 0, "FP": 0, "TN": 0, "FN": 0},
        "9-10": {"TP": 0, "FP": 0, "TN": 0, "FN": 0},
    }
    for verdict, would_enter, score, pnl in rows:
        s = int(score or 0)
        key = "6" if s <= 6 else ("7-8" if s <= 8 else "9-10")
        raw[key][verdict] = raw[key].get(verdict, 0) + 1

    buckets = []
    for key, b in raw.items():
        total   = sum(b.values())
        entered = b["TP"] + b["FP"]
        if total == 0:
            continue
        buckets.append({
            "score_range": key,
            "tp": b["TP"], "fp": b["FP"], "tn": b["TN"], "fn": b["FN"],
            "total": total, "entered": entered,
            "fp_rate": round(b["FP"] / entered * 100, 1) if entered else 0.0,
            "tp_rate": round(b["TP"] / entered * 100, 1) if entered else 0.0,
        })

    entered_buckets = [b for b in buckets if b["entered"] >= 5]
    max_fp = max((b["fp_rate"] for b in entered_buckets), default=0)
    min_tp = min((b["tp_rate"] for b in entered_buckets), default=100)

    if max_fp > 40:
        recommendation = "raise_threshold"
    elif entered_buckets and min_tp > 60 and max_fp < 10:
        recommendation = "lower_threshold"
    else:
        recommendation = "ok"

    result = {
        "buckets": buckets,
        "recommendation": recommendation,
        "sample_size": len(rows),
        "computed_at": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    }
    try:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('hindsight_feedback_json', ?)",
            (json.dumps(result),)
        )
        conn.commit()
    except Exception:
        pass
    return result
```

- [ ] **Step 5: Run tests**

```bash
python3 -m pytest tests/test_hindsight_feedback.py -v
```
Expected: all 3 tests PASS.

- [ ] **Step 6: Add weekly trigger in bitget_sync.py**

In `_maybe_update_rulebook()` inside `bitget_sync.py`, add after the existing rulebook update block:

```python
        # Compute hindsight feedback if enough data
        try:
            from database import get_conn as _gc
            _c = _gc()
            n = _c.execute("SELECT COUNT(*) FROM trade_hindsight WHERE verdict IS NOT NULL").fetchone()[0]
            _c.close()
            if n >= 10:
                from ai_hindsight import compute_feedback as _fb
                fb = _fb()
                print(f"[Sync] Hindsight feedback: {fb['recommendation']} (n={fb['sample_size']})", flush=True)
        except Exception as _e:
            print(f"[Sync] Hindsight feedback skipped: {_e}", flush=True)
```

- [ ] **Step 7: Add GET /api/scanner/feedback**

In `routes/scanner.py`, add:

```python
@bp.route("/api/scanner/feedback")
def api_scanner_feedback():
    """GET /api/scanner/feedback -- last hindsight recalibration result."""
    try:
        import json
        with db_conn() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key='hindsight_feedback_json'"
            ).fetchone()
        if not row:
            return _ok({"available": False})
        return _ok({"available": True, **json.loads(row[0])})
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)
```

- [ ] **Step 8: Add feedback summary to Scanner tab**

In `static/js/09-scanner.js`, add a call to fetch feedback and populate a notice bar:

```javascript
async function loadScannerFeedback() {
    const el = document.getElementById('scanner-feedback');
    if (!el) return;
    try {
        const r = await fetch('/api/scanner/feedback');
        const d = await r.json();
        if (!d.ok || !d.data || !d.data.available) {
            el.textContent = 'No hindsight calibration data yet (run Hindsight to generate).';
            return;
        }
        const fb = d.data;
        const msgMap = {
            raise_threshold: 'Warning: High false-positive rate detected -- consider raising scanner min score',
            lower_threshold: 'Strong accuracy -- you can lower min score to surface more setups',
            ok: 'Signal accuracy within normal range',
        };
        // msg is one of three hardcoded strings -- no user-controlled content
        const msg = msgMap[fb.recommendation] || fb.recommendation;
        el.textContent = msg + ' (' + fb.sample_size + ' trades analyzed)';
        el.className = fb.recommendation === 'raise_threshold' ? 'notice notice-warn' : 'notice notice-ok';
    } catch(e) {
        // non-fatal -- scanner still works without feedback
    }
}
```

Add to scanner tab HTML:
```html
<div id="scanner-feedback" class="muted" style="margin-bottom:10px;font-size:13px"></div>
```

Add CSS:
```css
.notice{padding:8px 12px;border-radius:4px;font-size:13px}
.notice-warn{background:#3a2a00;color:#f0a030;border-left:3px solid #f0a030}
.notice-ok{background:#0a2a0a;color:#4caf50;border-left:3px solid #4caf50}
```

Bump `?v=` in `templates/index.html` for `09-scanner.js`.

- [ ] **Step 9: Commit**

```bash
git add ai_hindsight.py database.py bitget_sync.py routes/scanner.py static/js/09-scanner.js templates/index.html tests/test_hindsight_feedback.py
git commit -m "feat: hindsight feedback loop -- weekly FP/TP analysis + scanner recalibration signal"
```

---

## Final Checks

```bash
python3 -m pytest tests/test_setup_pnl.py tests/test_portfolio_risk.py tests/test_hindsight_feedback.py tests/test_analytics.py -v
```
Expected: all pass.
