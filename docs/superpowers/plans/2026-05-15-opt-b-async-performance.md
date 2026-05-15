# Optimization Plan B — Async Optimizer + Performance

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/api/backtest/optimize` non-blocking so the Pi journal stays responsive during long Optuna runs, and lock in performance baselines for the backtester and confluence scorer.

**Architecture:** The optimizer runs in a daemon thread. The route returns a job ID immediately. A new `GET /api/backtest/optimize/status` endpoint polls progress. Results stored in a module-level dict (no DB needed — optimizer results are ephemeral). Performance tests run against the Pi endpoint to capture real ARM execution times.

**Tech Stack:** Python 3.13 threading, Optuna ≥3.0, Flask, pytest.

---

## File Map

| Action | File | Change |
|--------|------|--------|
| Modify | `backtest_optimizer.py` | Add `_OptJob` dataclass + thread-safe job dict |
| Modify | `routes/backtest.py` | `GET /api/backtest/optimize` starts job, returns job_id; add `GET /api/backtest/optimize/<job_id>` for polling |
| Create | `tests/test_optimizer_async.py` | Unit tests for job lifecycle |
| Create | `tests/test_performance_baseline.py` | Timing assertions for backtester + confluence |

---

## Task 1: Add async job infrastructure to backtest_optimizer.py

**Files:**
- Modify: `backtest_optimizer.py`

- [ ] **Step 1: Write failing test for job creation**

Create `tests/test_optimizer_async.py`:

```python
"""Tests for async optimizer job lifecycle."""
import time
import threading
from unittest.mock import patch, MagicMock
from backtest_engine import BacktestResult


def _fast_optimizer(symbol, timeframe, days, n_trials):
    """Returns immediately for testing."""
    return {"wt_oversold": -60.0, "rsi_max": 58.0, "adx_min": 18.0,
            "min_confluence": 0.40, "sl_pct": 0.09, "tp1_pct": 0.05, "tp2_pct": 0.12}


def test_start_job_returns_job_id():
    from backtest_optimizer import start_optimizer_job, get_job_status
    with patch('backtest_optimizer.run_optimizer', side_effect=_fast_optimizer):
        job_id = start_optimizer_job("BTCUSDT", "4H", 30, 1)
    assert isinstance(job_id, str)
    assert len(job_id) > 0


def test_job_status_pending_then_complete():
    from backtest_optimizer import start_optimizer_job, get_job_status
    import backtest_optimizer as bo

    completed = threading.Event()
    original_run = bo.run_optimizer

    def slow_run(*a, **kw):
        time.sleep(0.05)
        return {"wt_oversold": -60.0}

    with patch.object(bo, 'run_optimizer', side_effect=slow_run):
        job_id = start_optimizer_job("BTCUSDT", "4H", 30, 1)
        status = get_job_status(job_id)
        assert status["status"] in ("running", "complete")

    # Wait for thread to finish
    time.sleep(0.2)
    status = get_job_status(job_id)
    assert status["status"] == "complete"
    assert "wt_oversold" in status["result"]


def test_job_not_found_returns_none():
    from backtest_optimizer import get_job_status
    assert get_job_status("nonexistent-job-id") is None


def test_multiple_jobs_are_independent():
    from backtest_optimizer import start_optimizer_job, get_job_status
    import backtest_optimizer as bo

    with patch.object(bo, 'run_optimizer', return_value={"wt_oversold": -55.0}):
        id1 = start_optimizer_job("BTCUSDT", "4H", 30, 1)
        id2 = start_optimizer_job("ETHUSDT", "4H", 30, 1)
    assert id1 != id2
    time.sleep(0.1)
    assert get_job_status(id1)["result"]["wt_oversold"] == -55.0
    assert get_job_status(id2)["result"]["wt_oversold"] == -55.0
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
venv/bin/python3 -m pytest tests/test_optimizer_async.py -v
```

Expected: `ImportError: cannot import name 'start_optimizer_job'`

- [ ] **Step 3: Add job infrastructure to backtest_optimizer.py**

Add the following to `backtest_optimizer.py` (after the existing imports):

```python
import threading
import uuid
from dataclasses import dataclass, field
from typing import Optional

# ── Async job registry ────────────────────────────────────────────────────────
# Maps job_id → _OptJob. Never grows unboundedly: jobs older than 1 hour
# are evicted on the next start_optimizer_job() call.
_jobs: dict = {}
_jobs_lock = threading.Lock()
_JOB_TTL = 3600  # seconds


@dataclass
class _OptJob:
    job_id:   str
    symbol:   str
    status:   str = "running"   # running | complete | error
    result:   Optional[dict] = None
    error:    Optional[str] = None
    started:  float = field(default_factory=lambda: __import__('time').time())


def _evict_old_jobs():
    import time
    cutoff = time.time() - _JOB_TTL
    stale = [jid for jid, j in _jobs.items() if j.started < cutoff]
    for jid in stale:
        del _jobs[jid]


def start_optimizer_job(symbol: str, timeframe: str = "4H",
                        days: int = 180, n_trials: int = 50) -> str:
    """Start an async optimizer run. Returns job_id for polling."""
    job_id = str(uuid.uuid4())
    job = _OptJob(job_id=job_id, symbol=symbol)
    with _jobs_lock:
        _evict_old_jobs()
        _jobs[job_id] = job

    def _run():
        import time
        try:
            result = run_optimizer(symbol, timeframe, days, n_trials)
            with _jobs_lock:
                if job_id in _jobs:
                    _jobs[job_id].status = "complete"
                    _jobs[job_id].result = result
        except Exception as e:
            with _jobs_lock:
                if job_id in _jobs:
                    _jobs[job_id].status = "error"
                    _jobs[job_id].error = "Optimizer failed — check server logs"
            import logging
            logging.getLogger(__name__).exception("Optimizer job %s failed", job_id)

    t = threading.Thread(target=_run, daemon=True, name=f"optuna-{job_id[:8]}")
    t.start()
    return job_id


def get_job_status(job_id: str) -> Optional[dict]:
    """Return job dict or None if job_id not found."""
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        return None
    return {
        "job_id": job.job_id,
        "symbol": job.symbol,
        "status": job.status,
        "result": job.result,
        "error":  job.error,
    }
```

- [ ] **Step 4: Run tests to confirm passing**

```bash
venv/bin/python3 -m pytest tests/test_optimizer_async.py -v
```

Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add backtest_optimizer.py tests/test_optimizer_async.py
git commit -m "feat: async optimizer job infrastructure (start_optimizer_job, get_job_status)"
```

---

## Task 2: Update Flask route to use async jobs

**Files:**
- Modify: `routes/backtest.py`

- [ ] **Step 1: Read current backtest_optimize route**

```bash
grep -n "backtest_optimize\|optimize" /Users/fbauer/Documents/ClaudeAIData/Trading-Journal/routes/backtest.py
```

- [ ] **Step 2: Replace the blocking GET handler and add polling endpoint**

In `routes/backtest.py`, replace the existing `backtest_optimize` function and add the status endpoint:

```python
@bp.get("/api/backtest/optimize")
def backtest_optimize():
    """Start an async optimizer run. Returns job_id immediately."""
    symbol    = request.args.get("symbol",    "BTCUSDT")
    timeframe = request.args.get("timeframe", "4H")

    try:
        n_trials = min(int(request.args.get("n_trials", 50)), 200)
        days     = min(int(request.args.get("days",     180)), 365)
    except (ValueError, TypeError):
        return _err("invalid parameter value")

    from backtest_optimizer import start_optimizer_job
    job_id = start_optimizer_job(symbol, timeframe, days, n_trials)
    return _ok({"job_id": job_id, "status": "running",
                "message": f"Optimizer started for {symbol} ({n_trials} trials). Poll /api/backtest/optimize/{job_id} for results."})


@bp.get("/api/backtest/optimize/<job_id>")
def backtest_optimize_status(job_id: str):
    """Poll optimizer job status. Returns status=running|complete|error + result when done."""
    from backtest_optimizer import get_job_status
    job = get_job_status(job_id)
    if job is None:
        return _err("job not found", 404)
    if job["status"] == "error":
        return _err(job["error"] or "Optimizer failed")
    return _ok(job)
```

- [ ] **Step 3: Update the UI loadOptimizer() function in 09-analysis.js**

The JS currently awaits the full optimize response. Change it to poll:

In `static/js/09-analysis.js`, replace `loadOptimizer()` with:

```javascript
async function loadOptimizer() {
  const sym = (document.getElementById('backtestSymbol') || {}).value?.trim() || 'BTCUSDT';
  const container = document.getElementById('optimizerResult');
  if (!container) return;

  _setBtBtnsDisabled(true);
  container.textContent = '';
  const msg = document.createElement('small');
  msg.style.color = 'var(--muted)';
  msg.textContent = '⧗ Starting optimizer for ' + sym + '…';
  container.appendChild(msg);

  try {
    const startRes = await api('/api/backtest/optimize?symbol=' + encodeURIComponent(sym) + '&n_trials=50');
    if (!startRes.ok) throw new Error(startRes.error || 'Failed to start optimizer');

    const jobId = startRes.data.job_id;
    msg.textContent = '⧗ Optimizer running for ' + sym + ' (~5-10 min)…';

    // Poll every 10 seconds until complete or error
    const pollInterval = setInterval(async () => {
      try {
        const pollRes = await api('/api/backtest/optimize/' + jobId);
        if (!pollRes.ok) {
          clearInterval(pollInterval);
          _setBtBtnsDisabled(false);
          msg.style.color = 'var(--red)';
          msg.textContent = 'Optimizer error: ' + (pollRes.error || 'unknown');
          return;
        }
        if (pollRes.data.status === 'complete') {
          clearInterval(pollInterval);
          _setBtBtnsDisabled(false);
          _renderOptimizerResult(container, pollRes.data.result, sym);
          notify('Optimizer complete for ' + sym, 'success');
        }
      } catch (e) {
        clearInterval(pollInterval);
        _setBtBtnsDisabled(false);
        msg.style.color = 'var(--red)';
        msg.textContent = 'Poll error: ' + e.message;
      }
    }, 10000);

  } catch (e) {
    container.textContent = '';
    const err = document.createElement('small');
    err.style.color = 'var(--red)';
    err.textContent = e.message;
    container.appendChild(err);
    _setBtBtnsDisabled(false);
    notify('Optimizer error: ' + e.message, 'danger');
  }
}

function _renderOptimizerResult(container, params, sym) {
  container.textContent = '';
  const title = document.createElement('div');
  title.style.cssText = 'font-size:.75rem;font-weight:600;color:var(--muted);margin-bottom:6px';
  title.textContent = 'Best params (' + sym + ')';
  container.appendChild(title);
  const paramLabels = {
    wt_oversold: 'WT oversold', rsi_max: 'RSI max', adx_min: 'ADX min',
    min_confluence: 'Confluence', sl_pct: 'SL %', tp1_pct: 'TP1 %', tp2_pct: 'TP2 %',
  };
  const grid = document.createElement('div');
  grid.style.cssText = 'display:flex;flex-wrap:wrap;gap:6px';
  for (const [key, label] of Object.entries(paramLabels)) {
    if (!(key in params)) continue;
    const val = typeof params[key] === 'number' ? params[key].toFixed(2) : String(params[key]);
    const chip = document.createElement('div');
    chip.style.cssText = 'padding:3px 8px;background:var(--bg);border:1px solid var(--border);border-radius:6px;font-size:.75rem';
    const k = document.createElement('span');
    k.style.color = 'var(--muted)';
    k.textContent = label + ': ';
    const v = document.createElement('span');
    v.style.fontWeight = '600';
    v.textContent = val;
    chip.appendChild(k);
    chip.appendChild(v);
    grid.appendChild(chip);
  }
  container.appendChild(grid);
}
```

Also bump `09-analysis.js` version in `templates/index.html` by 0.1.

- [ ] **Step 4: Run backtest route tests**

```bash
venv/bin/python3 -m pytest tests/test_backtest_routes.py tests/test_optimizer_async.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit and deploy**

```bash
git add routes/backtest.py static/js/09-analysis.js templates/index.html
git commit -m "feat: async /api/backtest/optimize — returns job_id, poll /<job_id> for results"
git push origin main
```

Deploy to Pi (standard expect SSH pattern).

---

## Task 3: Lock in performance baseline tests

**Files:**
- Create: `tests/test_performance_baseline.py`

These tests run against the live Pi — they test real execution times, not mocked behaviour.

- [ ] **Step 1: Create the test file**

```python
"""
Performance baseline tests — run against live Pi.
These tests define MAXIMUM acceptable execution times.
Run with: pytest tests/test_performance_baseline.py -v -s --host=192.168.1.21:8082

If --host is not provided, tests are skipped (they require a live server).
"""
import time
import pytest
import requests


def pytest_addoption(parser):
    parser.addoption("--host", action="store", default=None,
                     help="Host:port of live journal (e.g. 192.168.1.21:8082)")


@pytest.fixture
def host(request):
    h = request.config.getoption("--host")
    if not h:
        pytest.skip("--host not provided; skipping live performance tests")
    return h


def test_backtest_run_30d_under_10s(host):
    """POST /api/backtest/run for 30 days must complete in under 10 seconds on Pi."""
    url = f"http://{host}/api/backtest/run"
    t0 = time.time()
    resp = requests.post(url, json={"symbol": "BTCUSDT", "timeframe": "4H", "days": 30},
                         timeout=30)
    elapsed = time.time() - t0
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert elapsed < 10.0, f"Backtest took {elapsed:.1f}s — expected < 10s"


def test_backtest_run_180d_under_30s(host):
    """POST /api/backtest/run for 180 days must complete in under 30 seconds on Pi."""
    url = f"http://{host}/api/backtest/run"
    t0 = time.time()
    resp = requests.post(url, json={"symbol": "BTCUSDT", "timeframe": "4H", "days": 180},
                         timeout=60)
    elapsed = time.time() - t0
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert elapsed < 30.0, f"Backtest took {elapsed:.1f}s — expected < 30s"


def test_scanner_status_under_200ms(host):
    """GET /api/scanner/status must respond in under 200ms (read-only dict)."""
    url = f"http://{host}/api/scanner/status"
    t0 = time.time()
    resp = requests.get(url, timeout=5)
    elapsed = time.time() - t0
    assert resp.status_code == 200
    assert elapsed < 0.2, f"Scanner status took {elapsed*1000:.0f}ms — expected < 200ms"


def test_dashboard_kpis_under_500ms(host):
    """GET /api/analytics/dashboard must respond in under 500ms."""
    url = f"http://{host}/api/analytics/dashboard"
    t0 = time.time()
    resp = requests.get(url, timeout=10)
    elapsed = time.time() - t0
    assert resp.status_code == 200
    assert elapsed < 0.5, f"Dashboard took {elapsed*1000:.0f}ms — expected < 500ms"


def test_optimizer_starts_immediately(host):
    """GET /api/backtest/optimize must return job_id within 2 seconds (non-blocking)."""
    url = f"http://{host}/api/backtest/optimize?symbol=BTCUSDT&n_trials=5"
    t0 = time.time()
    resp = requests.get(url, timeout=10)
    elapsed = time.time() - t0
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "job_id" in data["data"]
    assert elapsed < 2.0, f"Optimizer start took {elapsed:.1f}s — expected < 2s (non-blocking)"
```

- [ ] **Step 2: Run against Pi to establish baselines**

```bash
venv/bin/python3 -m pytest tests/test_performance_baseline.py -v -s --host=192.168.1.21:8082
```

Record the output. These times become the reference for future optimisation work.

- [ ] **Step 3: Commit**

```bash
git add tests/test_performance_baseline.py
git commit -m "test(perf): add performance baseline tests for Pi execution times"
git push origin main
```

---

## Self-Review

- [x] Async job infrastructure complete (Task 1)
- [x] Route updated — immediate response + polling endpoint (Task 2)
- [x] JS updated to poll every 10s instead of awaiting (Task 2)
- [x] Performance baselines locked in (Task 3)
- [x] No placeholders — all code complete
- [x] Pi stays responsive during long optimizer runs
