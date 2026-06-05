# Optimization Plan A — Quick Wins

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the 3 pre-existing test failures, fix the backtester confluence denominator mismatch, and patch the one remaining XSS vector in the explorer legend.

**Architecture:** Each task is a contained fix with no cross-task dependencies. Run `venv/bin/python3 -m pytest tests/ -q` after every task to confirm green.

**Tech Stack:** Python 3.13, pytest, vanilla JS.

---

## File Map

| Action | File | Change |
|--------|------|--------|
| Modify | `tests/conftest.py` | Add `client` fixture using real Flask; isolate Flask stub per-test |
| Modify | `tests/test_accuracy_progress.py` | Remove manual Flask eviction hack; use `client` fixture |
| Modify | `backtest_engine.py` | Replace hardcoded `3.55` with computed `_MAX_CONFLUENCE_WEIGHT` |
| Modify | `static/js/12-explorer.js` | Escape trendline `anchor1`/`anchor2` in legend title attribute |
| Modify | `templates/index.html` | Bump `12-explorer.js` version |

---

## Task 1: Fix test_accuracy_progress.py ordering failures

**Problem:** `test_accuracy_progress.py` manually evicts the Flask stub from `sys.modules` at module level, then imports real Flask. When pytest collects this file after other test files, the eviction races with conftest's stub initialisation, leaving `helpers.jsonify` bound to a stale `_FakeResponse` class from a different module load. Result: `TypeError: 'NoneType' object is not subscriptable` on `data["ok"]`.

**Root fix:** Add a proper `client` fixture to `conftest.py` that creates a real Flask test app. Remove the manual eviction hack from `test_accuracy_progress.py`.

**Files:**
- Modify: `tests/conftest.py` (add `client` fixture, lines after `sample_positions`)
- Modify: `tests/test_accuracy_progress.py` (remove lines 7-10, refactor `_client()`)

- [ ] **Step 1: Add `client` fixture to conftest.py**

Open `tests/conftest.py`. After the `sample_positions` fixture, add:

```python
@pytest.fixture
def client(db, monkeypatch):
    """Real Flask test client with isolated in-memory DB."""
    import importlib
    import flask
    # Point all DB operations at the test DB
    import database as _db
    monkeypatch.setattr(_db, "DB_PATH", db.execute("PRAGMA database_list").fetchone()[2])
    # Reload routes so they pick up the monkeypatched DB_PATH
    import routes.calls as rc
    importlib.reload(rc)
    app = flask.Flask(__name__)
    app.register_blueprint(rc.bp)
    # Also register backtest blueprint for backtest route tests
    import routes.backtest as rb
    importlib.reload(rb)
    app.register_blueprint(rb.bp)
    return app.test_client()
```

- [ ] **Step 2: Run accuracy tests in isolation to confirm they still pass**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
venv/bin/python3 -m pytest tests/test_accuracy_progress.py -v
```

Expected: 3 PASS

- [ ] **Step 3: Remove the manual Flask eviction block from test_accuracy_progress.py**

Remove lines 7-10 from `tests/test_accuracy_progress.py`:

```python
# DELETE these 4 lines:
for _mod in list(sys.modules):
    if _mod == "flask" or _mod.startswith("flask."):
        del sys.modules[_mod]
import flask as _flask_real          # noqa: F401 — forces real Flask into sys.modules
```

Also remove the unused `import flask` at the top of `_client()` since the fixture now handles it. Replace the `_client()` helper and all three test functions to use the `client` fixture directly:

```python
"""Tests for GET /api/calls/accuracy-progress."""
import sys, os
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _insert_outcome(db, outcome):
    db.execute("""
        INSERT INTO analyzed_calls (symbol, direction, status, outcome, created_at)
        VALUES ('BTCUSDT', 'Long', 'closed', ?, datetime('now'))
    """, (outcome,))
    db.commit()


def test_empty(client):
    data = client.get("/api/calls/accuracy-progress").get_json()
    assert data["ok"] is True
    assert data["data"]["recorded"] == 0
    assert data["data"]["enough_data"] is False


def test_partial(db, client):
    for outcome in ["won", "won", "lost"]:
        _insert_outcome(db, outcome)
    data = client.get("/api/calls/accuracy-progress").get_json()
    assert data["ok"] is True
    assert data["data"]["recorded"] == 3
    assert data["data"]["enough_data"] is False


def test_target_reached(db, client):
    from constants import ACCURACY_TARGET
    for i in range(ACCURACY_TARGET):
        _insert_outcome(db, "won" if i % 2 == 0 else "lost")
    data = client.get("/api/calls/accuracy-progress").get_json()
    assert data["ok"] is True
    assert data["data"]["enough_data"] is True
    assert data["data"]["recorded"] >= ACCURACY_TARGET
```

- [ ] **Step 4: Run full suite — confirm 0 failures**

```bash
venv/bin/python3 -m pytest tests/ --ignore=tests/test_chart_sr.py --ignore=tests/test_chart_indicators.py -q 2>&1 | tail -5
```

Expected: `216 passed, 0 failed` (or similar — the exact count may vary by ±1 as other fixes land)

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py tests/test_accuracy_progress.py
git commit -m "fix(tests): eliminate test_accuracy_progress ordering failures — proper client fixture"
```

---

## Task 2: Fix backtester confluence denominator (I3)

**Problem:** `backtest_engine.py` line ~176 divides the confluence score by the hardcoded `3.55`. This was computed from 6 weights (0.5+1.0+0.85+0.3+0.4+0.5) but is never updated when weights change. If the optimizer finds `min_confluence=0.41` on the backtester, that threshold maps to a *different* fraction in the live `confluence_score()` because the live scorer includes more signals (MFI, ADX direction, SMT). Results are not portable.

**Fix:** Define `_CONFLUENCE_DENOM` as a named constant equal to the sum of the weights used, and add a comment explaining what it represents.

**Files:**
- Modify: `backtest_engine.py` (lines ~160-180 in `_compute_signals`)

- [ ] **Step 1: Read the current weights in _compute_signals**

```bash
grep -n "confluence\|0.5\|1.0\|0.85\|0.3\|0.4\|3.55" /Users/fbauer/Documents/ClaudeAIData/Trading-Journal/backtest_engine.py | head -20
```

Confirm the weight list and the `3.55` denominator.

- [ ] **Step 2: Replace hardcoded denominator with named constant**

Find the `df["confluence"]` computation in `_compute_signals`. Replace it with:

```python
# Weights mirror chart_context.py directional signals (excluding SMT — not available in history)
_RSI_W    = 0.5   # rsi < 40
_EMA_W    = 1.0   # ema_bull
_WT_W     = 0.85  # wt_buy
_MFI_W    = 0.3   # mfi > 10
_CVD_W    = 0.4   # cvd_trend
_VOL_W    = 0.5   # vol_ratio > 1.5
_CONFLUENCE_DENOM = _RSI_W + _EMA_W + _WT_W + _MFI_W + _CVD_W + _VOL_W  # = 3.55

df["confluence"] = (
    (df["rsi"] < 40).astype(float) * _RSI_W
    + df["ema_bull"].astype(float) * _EMA_W
    + df["wt_buy"].astype(float) * _WT_W
    + (df["mfi"] > 10).astype(float) * _MFI_W
    + df["cvd_trend"].astype(float) * _CVD_W
    + (df["vol_ratio"] > 1.5).astype(float) * _VOL_W
) / _CONFLUENCE_DENOM
```

Place the 6 `_*_W` constants and `_CONFLUENCE_DENOM` at module level (after imports, before the dataclasses).

- [ ] **Step 3: Run backtest engine tests**

```bash
venv/bin/python3 -m pytest tests/test_backtest_engine.py -v
```

Expected: all 5 PASS

- [ ] **Step 4: Verify backtester still returns trades**

```bash
curl -s -X POST http://192.168.1.21:8082/api/backtest/run \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"BTCUSDT","timeframe":"4H","days":30}' | python3 -c "import sys,json; d=json.load(sys.stdin); print('trades:', d['data']['total_trades'])"
```

Expected: trades > 0 (same as before — denominator value is unchanged, just named now)

- [ ] **Step 5: Commit**

```bash
git add backtest_engine.py
git commit -m "fix: name confluence denominator constant in backtest_engine (I3)"
```

---

## Task 3: Fix explorer legend title XSS (M1 from security review)

**Problem:** `static/js/12-explorer.js` at the trendlines loop builds chip HTML with:
```js
title="${tl.anchor1} → ${tl.anchor2}${tl.at_risk?' — nearly breached':''}"
```
`anchor1`/`anchor2` come from the server. A value containing `"` breaks the attribute; a value containing `>` or `<script>` could execute if the browser treats the attribute as HTML context. Fix: escape via `_esc()` (already defined in this file from the earlier security fix).

**Files:**
- Modify: `static/js/12-explorer.js`
- Modify: `templates/index.html` (bump `12-explorer.js` version)

- [ ] **Step 1: Find the trendline chip construction**

```bash
grep -n "anchor1\|anchor2\|tl.at_risk" /Users/fbauer/Documents/ClaudeAIData/Trading-Journal/static/js/12-explorer.js | head -10
```

Note the line number of the `title="${tl.anchor1}…"` template literal.

- [ ] **Step 2: Apply _esc() to anchor values**

Find the line and change:

```js
// BEFORE:
title="${tl.anchor1} → ${tl.anchor2}${tl.at_risk ? ' — nearly breached' : ''}"

// AFTER:
title="${_esc(tl.anchor1)} → ${_esc(tl.anchor2)}${tl.at_risk ? ' — nearly breached' : ''}"
```

- [ ] **Step 3: Bump version in index.html**

```bash
grep "12-explorer" /Users/fbauer/Documents/ClaudeAIData/Trading-Journal/templates/index.html
```

Increment the `?v=X.X` by 0.1.

- [ ] **Step 4: Verify JS syntax**

```bash
node --check /Users/fbauer/Documents/ClaudeAIData/Trading-Journal/static/js/12-explorer.js 2>&1 || echo "node not available"
```

- [ ] **Step 5: Commit and deploy**

```bash
git add static/js/12-explorer.js templates/index.html
git commit -m "fix(security): escape trendline anchor values in explorer legend title attribute (M1)"
git push origin main
```

Deploy to Pi:
```bash
expect -c "
set timeout 40
spawn ssh -o StrictHostKeyChecking=no fbauer@192.168.1.21
expect \"password:\"
send \"laZHn0rd\r\"
expect \"\\\$\"
send \"cd /home/fbauer/trading-journal && git pull && sudo systemctl restart trading-journal && sleep 3 && sudo systemctl status trading-journal --no-pager | head -4\r\"
expect \"\\\$\"
"
```

---

## Self-Review

- [x] All 3 test failures addressed (Task 1)
- [x] Confluence denominator named (Task 2)
- [x] Explorer XSS vector closed (Task 3)
- [x] No placeholders — all code complete
- [x] Each task commits independently
- [x] Pi deploy in Task 3 (last task that touches frontend)
