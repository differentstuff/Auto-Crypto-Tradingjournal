# Phase 4 UI/UX + Accuracy Accumulation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add stale-data badge on live trades, scanner ETA display, automated retroactive outcome recording for saved calls, and an accuracy progress widget.

**Architecture:** Four independent changes: two pure-frontend (JS only), one pure-backend (Python sync + route), one full-stack (endpoint + JS). Symbol autocomplete was already fully implemented via `_attachSymbolPicker` + `/api/exchange/symbols` — confirmed in codebase, skip.

**Tech Stack:** Python 3.13 / Flask 3.1 / SQLite / vanilla JS / pytest

---

## File Map

| File | Change |
|------|--------|
| `constants.py` | Add `ACCURACY_TARGET = 35` |
| `bitget_sync.py` | Add `_retroactive_close_calls(conn)` + call in `run_sync()` |
| `blofin_sync.py` | Import + call `_retroactive_close_calls()` after sync |
| `routes/calls.py` | Add `GET /api/calls/accuracy-progress` endpoint |
| `static/js/08-live.js` | Add stale-data badge (track `_liveLastRefreshAt`, watcher interval) |
| `static/js/14-scanner.js` | Add ETA text to `_buildProgressBlock()` |
| `static/js/09-analysis.js` | Add `loadAccuracyProgress()` and call from `loadPredictionAccuracy()` |
| `tests/test_retroactive_calls.py` | New test file for `_retroactive_close_calls` |
| `tests/test_accuracy_progress.py` | New test file for `/api/calls/accuracy-progress` |

---

## Task 1: Add ACCURACY_TARGET constant

**Files:**
- Modify: `constants.py`

- [ ] **Step 1: Add constant**

Open `constants.py` and append after the `NANSEN_CACHE_TTL` line:

```python
# ── Accuracy tracking ─────────────────────────────────────────────────────────
ACCURACY_TARGET        = 35     # calls needed for 85% statistical confidence
```

- [ ] **Step 2: Verify import works**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
python -c "from constants import ACCURACY_TARGET; print(ACCURACY_TARGET)"
```
Expected: `35`

- [ ] **Step 3: Commit**

```bash
git add constants.py
git commit -m "feat: add ACCURACY_TARGET = 35 to constants"
```

---

## Task 2: Retroactive outcome recorder — write failing tests

**Files:**
- Create: `tests/test_retroactive_calls.py`

- [ ] **Step 1: Write the test file**

```python
"""Tests for _retroactive_close_calls in bitget_sync."""
import sys, os
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from datetime import datetime, timezone, timedelta


def _insert_saved_call(db, symbol, direction, entry, sl, tp1, tp2=None, hours_ago=3):
    created = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute("""
        INSERT INTO analyzed_calls
          (symbol, direction, entry_price, sl_price, tp1_price, tp2_price,
           status, created_at)
        VALUES (?,?,?,?,?,?,'saved',?)
    """, (symbol, direction, entry, sl, tp1, tp2, created))
    db.commit()
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def _make_candles(rows):
    """rows: list of (timestamp_ms, high, low)"""
    return pd.DataFrame(
        [(ts, 0.0, h, l, 0.0, 0.0, 0.0) for ts, h, l in rows],
        columns=["timestamp", "open", "high", "low", "close", "volume", "quote_volume"]
    )


def test_long_tp1_hit(db, monkeypatch):
    call_id = _insert_saved_call(db, "BTCUSDT", "Long", entry=100, sl=90, tp1=110, tp2=120)
    candles = _make_candles([
        (int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp() * 1000), 115, 95)
    ])
    monkeypatch.setattr("bitget_sync.chart_context.get_candles_at_time", lambda *a, **kw: candles)
    from bitget_sync import _retroactive_close_calls
    assert _retroactive_close_calls(db) == 1
    row = db.execute(
        "SELECT status, outcome, hit_tp1, hit_tp2, hit_sl FROM analyzed_calls WHERE id=?",
        (call_id,)
    ).fetchone()
    assert row == ("closed", "won", 1, 0, 0)


def test_long_tp2_hit(db, monkeypatch):
    call_id = _insert_saved_call(db, "BTCUSDT", "Long", entry=100, sl=90, tp1=110, tp2=120)
    candles = _make_candles([
        (int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp() * 1000), 125, 95)
    ])
    monkeypatch.setattr("bitget_sync.chart_context.get_candles_at_time", lambda *a, **kw: candles)
    from bitget_sync import _retroactive_close_calls
    assert _retroactive_close_calls(db) == 1
    row = db.execute(
        "SELECT outcome, hit_tp1, hit_tp2 FROM analyzed_calls WHERE id=?", (call_id,)
    ).fetchone()
    assert row == ("won", 1, 1)


def test_long_sl_hit(db, monkeypatch):
    call_id = _insert_saved_call(db, "BTCUSDT", "Long", entry=100, sl=90, tp1=110)
    candles = _make_candles([
        (int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp() * 1000), 105, 88)
    ])
    monkeypatch.setattr("bitget_sync.chart_context.get_candles_at_time", lambda *a, **kw: candles)
    from bitget_sync import _retroactive_close_calls
    assert _retroactive_close_calls(db) == 1
    row = db.execute(
        "SELECT outcome, hit_sl FROM analyzed_calls WHERE id=?", (call_id,)
    ).fetchone()
    assert row == ("lost", 1)


def test_short_tp1_hit(db, monkeypatch):
    call_id = _insert_saved_call(db, "ETHUSDT", "Short", entry=100, sl=110, tp1=90)
    candles = _make_candles([
        (int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp() * 1000), 105, 88)
    ])
    monkeypatch.setattr("bitget_sync.chart_context.get_candles_at_time", lambda *a, **kw: candles)
    from bitget_sync import _retroactive_close_calls
    assert _retroactive_close_calls(db) == 1
    row = db.execute(
        "SELECT outcome, hit_tp1 FROM analyzed_calls WHERE id=?", (call_id,)
    ).fetchone()
    assert row == ("won", 1)


def test_short_sl_hit(db, monkeypatch):
    call_id = _insert_saved_call(db, "ETHUSDT", "Short", entry=100, sl=110, tp1=90)
    candles = _make_candles([
        (int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp() * 1000), 112, 95)
    ])
    monkeypatch.setattr("bitget_sync.chart_context.get_candles_at_time", lambda *a, **kw: candles)
    from bitget_sync import _retroactive_close_calls
    assert _retroactive_close_calls(db) == 1
    row = db.execute(
        "SELECT outcome, hit_sl FROM analyzed_calls WHERE id=?", (call_id,)
    ).fetchone()
    assert row == ("lost", 1)


def test_no_resolution_when_price_between_sl_and_tp(db, monkeypatch):
    call_id = _insert_saved_call(db, "BTCUSDT", "Long", entry=100, sl=90, tp1=110)
    candles = _make_candles([
        (int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp() * 1000), 108, 95)
    ])
    monkeypatch.setattr("bitget_sync.chart_context.get_candles_at_time", lambda *a, **kw: candles)
    from bitget_sync import _retroactive_close_calls
    assert _retroactive_close_calls(db) == 0
    row = db.execute(
        "SELECT status, outcome FROM analyzed_calls WHERE id=?", (call_id,)
    ).fetchone()
    assert row == ("saved", None)


def test_sl_wins_when_same_candle_touches_both(db, monkeypatch):
    """SL takes priority when a single candle touches both SL and TP1."""
    call_id = _insert_saved_call(db, "BTCUSDT", "Long", entry=100, sl=90, tp1=110)
    candles = _make_candles([
        (int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp() * 1000), 115, 88)
    ])
    monkeypatch.setattr("bitget_sync.chart_context.get_candles_at_time", lambda *a, **kw: candles)
    from bitget_sync import _retroactive_close_calls
    assert _retroactive_close_calls(db) == 1
    row = db.execute("SELECT outcome FROM analyzed_calls WHERE id=?", (call_id,)).fetchone()
    assert row[0] == "lost"


def test_skips_call_too_recent(db, monkeypatch):
    """Calls newer than 2 hours must not be processed."""
    _insert_saved_call(db, "BTCUSDT", "Long", entry=100, sl=90, tp1=110, hours_ago=1)
    candles = _make_candles([
        (int(datetime.now(timezone.utc).timestamp() * 1000), 115, 88)
    ])
    monkeypatch.setattr("bitget_sync.chart_context.get_candles_at_time", lambda *a, **kw: candles)
    from bitget_sync import _retroactive_close_calls
    assert _retroactive_close_calls(db) == 0


def test_skips_matched_calls(db, monkeypatch):
    """Only touches 'saved' calls — matched calls handled by _auto_close_calls."""
    db.execute("""
        INSERT INTO analyzed_calls
          (symbol, direction, entry_price, sl_price, tp1_price, status, created_at)
        VALUES ('BTCUSDT','Long',100,90,110,'matched', datetime('now', '-3 hours'))
    """)
    db.commit()
    candles = _make_candles([
        (int(datetime.now(timezone.utc).timestamp() * 1000), 115, 88)
    ])
    monkeypatch.setattr("bitget_sync.chart_context.get_candles_at_time", lambda *a, **kw: candles)
    from bitget_sync import _retroactive_close_calls
    assert _retroactive_close_calls(db) == 0


def test_outcome_pnl_is_null(db, monkeypatch):
    """Retroactive records have NULL outcome_pnl — no actual trade."""
    call_id = _insert_saved_call(db, "BTCUSDT", "Long", entry=100, sl=90, tp1=110)
    candles = _make_candles([
        (int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp() * 1000), 115, 95)
    ])
    monkeypatch.setattr("bitget_sync.chart_context.get_candles_at_time", lambda *a, **kw: candles)
    from bitget_sync import _retroactive_close_calls
    _retroactive_close_calls(db)
    row = db.execute("SELECT outcome_pnl FROM analyzed_calls WHERE id=?", (call_id,)).fetchone()
    assert row[0] is None


def test_empty_candles_skipped(db, monkeypatch):
    _insert_saved_call(db, "BTCUSDT", "Long", entry=100, sl=90, tp1=110)
    monkeypatch.setattr(
        "bitget_sync.chart_context.get_candles_at_time", lambda *a, **kw: pd.DataFrame()
    )
    from bitget_sync import _retroactive_close_calls
    assert _retroactive_close_calls(db) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
python -m pytest tests/test_retroactive_calls.py -v 2>&1 | head -30
```
Expected: ImportError or AttributeError — `_retroactive_close_calls` not defined yet.

---

## Task 3: Implement `_retroactive_close_calls`

**Files:**
- Modify: `bitget_sync.py`

- [ ] **Step 1: Add `chart_context` import**

In `bitget_sync.py`, find the imports at the top. After the line `import market_context as _mkt`, add:

```python
import chart_context
```

- [ ] **Step 2: Add `_retroactive_close_calls` after `_auto_close_calls`**

Insert the following function immediately after the `_auto_close_calls` function (after the `return closed` line and its commit, around line 260):

```python
def _retroactive_close_calls(conn) -> int:
    """
    For every 'saved' call older than 2 hours with entry/sl/tp1 prices set,
    fetch 1H candles and check if price hit TP1, TP2, or SL since creation.
    Records outcome retroactively; outcome_pnl is NULL (no actual trade).
    Returns number of calls resolved.
    """
    from datetime import datetime, timezone

    cur   = conn.cursor()
    calls = cur.execute("""
        SELECT id, symbol, direction, sl_price, tp1_price, tp2_price, created_at
        FROM analyzed_calls
        WHERE status      = 'saved'
          AND sl_price    IS NOT NULL
          AND tp1_price   IS NOT NULL
          AND entry_price IS NOT NULL
          AND created_at  < datetime('now', '-2 hours')
    """).fetchall()

    now_ms   = int(time.time() * 1000)
    resolved = 0

    for call in calls:
        call_id, symbol, direction, sl_price, tp1_price, tp2_price, created_at = call

        try:
            df = chart_context.get_candles_at_time(symbol, "1H", now_ms, limit=500)
        except Exception:
            continue

        if df.empty:
            continue

        try:
            created_dt = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            created_ms = int(created_dt.timestamp() * 1000)
        except Exception:
            continue

        df = df[df["timestamp"] > created_ms]
        if df.empty:
            continue

        is_long   = "long" in (direction or "").lower()
        hit_sl    = hit_tp1 = hit_tp2 = 0
        outcome   = None

        for _, row in df.iterrows():
            high = row["high"]
            low  = row["low"]
            if is_long:
                if sl_price and low <= sl_price:
                    hit_sl, outcome = 1, "lost"
                    break
                elif tp2_price and high >= tp2_price:
                    hit_tp1, hit_tp2, outcome = 1, 1, "won"
                    break
                elif tp1_price and high >= tp1_price:
                    hit_tp1, outcome = 1, "won"
                    break
            else:
                if sl_price and high >= sl_price:
                    hit_sl, outcome = 1, "lost"
                    break
                elif tp2_price and low <= tp2_price:
                    hit_tp1, hit_tp2, outcome = 1, 1, "won"
                    break
                elif tp1_price and low <= tp1_price:
                    hit_tp1, outcome = 1, "won"
                    break

        if outcome is None:
            continue

        cur.execute("""
            UPDATE analyzed_calls
            SET status      = 'closed',
                outcome     = ?,
                outcome_pnl = NULL,
                hit_tp1     = ?,
                hit_tp2     = ?,
                hit_sl      = ?,
                outcome_at  = datetime('now')
            WHERE id = ?
        """, (outcome, hit_tp1, hit_tp2, hit_sl, call_id))
        resolved += 1
        print(f"[Sync] Retroactive #{call_id} {symbol} {direction} → {outcome}", flush=True)

    conn.commit()
    return resolved
```

- [ ] **Step 3: Call `_retroactive_close_calls` in `run_sync`**

In `bitget_sync.py`, find the `run_sync` function. After the existing `n_closed = _auto_close_calls(conn)` line, add:

```python
        n_retro  = _retroactive_close_calls(conn)
```

Then update the return dict's `calls_closed` value:

```python
            "calls_closed": n_closed + n_retro,
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
python -m pytest tests/test_retroactive_calls.py -v
```
Expected: all 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add bitget_sync.py tests/test_retroactive_calls.py
git commit -m "feat: retroactive outcome recorder for saved calls via OHLCV candle check"
```

---

## Task 4: Wire retroactive recorder into blofin_sync

**Files:**
- Modify: `blofin_sync.py`

- [ ] **Step 1: Find and update the import + call in blofin_sync**

In `blofin_sync.py` around lines 135–140, find:
```python
from bitget_sync import _auto_close_calls
calls_closed = _auto_close_calls(conn, exchange="blofin")
```

Replace with:
```python
from bitget_sync import _auto_close_calls, _retroactive_close_calls
calls_closed  = _auto_close_calls(conn, exchange="blofin")
calls_closed += _retroactive_close_calls(conn)
```

- [ ] **Step 2: Verify no import errors**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
python -c "import blofin_sync; print('ok')"
```
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add blofin_sync.py
git commit -m "feat: wire _retroactive_close_calls into blofin_sync"
```

---

## Task 5: `/api/calls/accuracy-progress` endpoint — write failing test

**Files:**
- Create: `tests/test_accuracy_progress.py`

- [ ] **Step 1: Write test file**

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


def _client(db, monkeypatch):
    import flask
    monkeypatch.setattr("database.DB_PATH", db.execute("PRAGMA database_list").fetchone()[2])
    import importlib, routes.calls as rc
    importlib.reload(rc)
    app = flask.Flask(__name__)
    app.register_blueprint(rc.bp)
    return app.test_client()


def test_empty(db, monkeypatch):
    c = _client(db, monkeypatch)
    data = c.get("/api/calls/accuracy-progress").get_json()
    assert data["ok"] is True
    assert data["data"]["recorded"]    == 0
    assert data["data"]["target"]      == 35
    assert data["data"]["win_rate"]    == 0.0
    assert data["data"]["remaining"]   == 35
    assert data["data"]["enough_data"] is False


def test_partial(db, monkeypatch):
    for _ in range(10):
        _insert_outcome(db, "won")
    for _ in range(4):
        _insert_outcome(db, "lost")
    c = _client(db, monkeypatch)
    data = c.get("/api/calls/accuracy-progress").get_json()
    assert data["data"]["recorded"]  == 14
    assert data["data"]["win_rate"]  == round(10 / 14 * 100, 1)
    assert data["data"]["remaining"] == 21
    assert data["data"]["enough_data"] is False


def test_target_reached(db, monkeypatch):
    for _ in range(35):
        _insert_outcome(db, "won")
    c = _client(db, monkeypatch)
    data = c.get("/api/calls/accuracy-progress").get_json()
    assert data["data"]["enough_data"] is True
    assert data["data"]["remaining"]   == 0
    assert data["data"]["recorded"]    == 35
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
python -m pytest tests/test_accuracy_progress.py -v 2>&1 | head -20
```
Expected: 404 or routing error — endpoint not yet defined.

---

## Task 6: Implement `/api/calls/accuracy-progress`

**Files:**
- Modify: `routes/calls.py`

- [ ] **Step 1: Add endpoint after `api_calls_prediction_accuracy`**

In `routes/calls.py`, find `api_calls_prediction_accuracy` (around line 295). Add immediately after its closing return:

```python
@bp.route("/api/calls/accuracy-progress")
def api_calls_accuracy_progress():
    from constants import ACCURACY_TARGET
    with db_conn() as conn:
        row = conn.execute("""
            SELECT
                COUNT(*)                                            AS recorded,
                SUM(CASE WHEN outcome = 'won' THEN 1 ELSE 0 END)  AS wins
            FROM analyzed_calls
            WHERE outcome IS NOT NULL
        """).fetchone()
    recorded = row[0] or 0
    wins     = row[1] or 0
    win_rate = round(wins / recorded * 100, 1) if recorded else 0.0
    return _ok({
        "recorded":    recorded,
        "target":      ACCURACY_TARGET,
        "win_rate":    win_rate,
        "remaining":   max(0, ACCURACY_TARGET - recorded),
        "enough_data": recorded >= ACCURACY_TARGET,
    })
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
python -m pytest tests/test_accuracy_progress.py -v
```
Expected: all 3 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add routes/calls.py tests/test_accuracy_progress.py
git commit -m "feat: add /api/calls/accuracy-progress endpoint"
```

---

## Task 7: Accuracy progress widget in `09-analysis.js`

**Files:**
- Modify: `static/js/09-analysis.js`

All DOM manipulation uses `createElement`/`textContent` — no `innerHTML` with server data.

- [ ] **Step 1: Add `loadAccuracyProgress` before `loadPredictionAccuracy`**

Insert this function at the top of `09-analysis.js`, before the `loadPredictionAccuracy` function:

```javascript
async function loadAccuracyProgress() {
  const res = await api('/api/calls/accuracy-progress');
  if (!res.ok) return;
  const d = res.data;

  const sec = document.getElementById('prediction-accuracy-section');
  if (sec) sec.style.display = '';

  let card = document.getElementById('accuracy-progress-card');
  if (!card) {
    card = document.createElement('div');
    card.id = 'accuracy-progress-card';
    card.style.cssText = 'margin-bottom:16px;padding:14px 18px;background:var(--bg2);border:1px solid var(--border);border-radius:10px';
    const content = document.getElementById('prediction-accuracy-content');
    if (content) content.parentElement.insertBefore(card, content);
    else return;
  }

  card.textContent = '';

  const header = document.createElement('div');
  header.style.cssText = 'display:flex;align-items:center;justify-content:space-between;margin-bottom:10px';

  const title = document.createElement('span');
  title.style.cssText = 'font-size:.85rem;font-weight:600';
  title.textContent = '\u{1F4CA} Accuracy Tracking';

  const rateColor = d.win_rate >= 55 ? 'var(--accent3)' : d.win_rate >= 40 ? 'var(--yellow)' : 'var(--red)';
  const meta = document.createElement('span');
  meta.style.cssText = 'font-size:.8rem;color:var(--muted)';
  meta.textContent = d.recorded + ' / ' + d.target + ' calls recorded · ';
  const rateSpan = document.createElement('span');
  rateSpan.style.color = rateColor;
  rateSpan.textContent = d.win_rate + '% win rate';
  meta.appendChild(rateSpan);

  header.appendChild(title);
  header.appendChild(meta);

  const barWrap = document.createElement('div');
  barWrap.style.cssText = 'background:var(--bg);border-radius:6px;height:8px;overflow:hidden;margin-bottom:8px';
  const fill = document.createElement('div');
  const pct  = Math.min(100, Math.round(d.recorded / d.target * 100));
  fill.style.cssText = 'height:100%;border-radius:6px;transition:width .4s;width:' + pct + '%;background:' +
    (d.enough_data ? 'var(--accent3)' : 'var(--accent)');
  barWrap.appendChild(fill);

  const note = document.createElement('div');
  note.style.cssText = 'font-size:.75rem;color:var(--muted)';
  note.textContent = d.enough_data
    ? '✅ Statistical target reached — accuracy data is reliable'
    : d.remaining + ' more outcome-recorded calls needed for statistical confidence';

  card.appendChild(header);
  card.appendChild(barWrap);
  card.appendChild(note);
}
```

- [ ] **Step 2: Call `loadAccuracyProgress` at the top of `loadPredictionAccuracy`**

Find `async function loadPredictionAccuracy()` in `09-analysis.js`. Add `loadAccuracyProgress();` as its first line:

```javascript
async function loadPredictionAccuracy() {
  loadAccuracyProgress();
  const res = await api('/api/calls/prediction-accuracy');
  // ... rest unchanged
```

- [ ] **Step 3: Manual verification**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
python app.py &
```
Open browser → Calls tab → verify progress bar card appears above the score-band table. Kill server: `pkill -f "python app.py"`

- [ ] **Step 4: Commit**

```bash
git add static/js/09-analysis.js
git commit -m "feat: accuracy progress widget on calls tab"
```

---

## Task 8: Stale-data badge on live trades

**Files:**
- Modify: `static/js/08-live.js`

- [ ] **Step 1: Track refresh timestamp**

In `08-live.js`, find the end of the `try` block inside `loadLiveTrades`. The last statement in that block is:
```javascript
document.getElementById('trades-refresh-label').textContent =
  'Live · ' + new Date().toLocaleTimeString();
```

Add these two lines immediately after it (still inside the `try` block):
```javascript
window._liveLastRefreshAt = Date.now();
const _staleBadge = document.getElementById('live-stale-badge');
if (_staleBadge) _staleBadge.remove();
```

- [ ] **Step 2: Add staleness watcher at bottom of file**

After all existing functions in `08-live.js`, append:

```javascript
// ── Stale-data badge ──────────────────────────────────────────────────────────
(function _startStalenessWatcher() {
  setInterval(() => {
    if (typeof currentPage !== 'undefined' && currentPage !== 'trades') return;
    if (!window._liveLastRefreshAt) return;
    const age = Date.now() - window._liveLastRefreshAt;
    if (age < 180_000) return;
    if (document.getElementById('live-stale-badge')) return;
    const mins = Math.floor(age / 60_000);
    const badge = document.createElement('div');
    badge.id = 'live-stale-badge';
    badge.style.cssText = [
      'padding:8px 14px',
      'margin-bottom:12px',
      'background:rgba(255,179,0,.1)',
      'border:1px solid rgba(255,179,0,.3)',
      'border-radius:8px',
      'font-size:.82rem',
      'color:var(--yellow)',
    ].join(';');
    badge.textContent = '⚠ Data may be stale — last updated ' + mins + 'm ago';
    const container = document.getElementById('trades-container');
    if (container) container.insertBefore(badge, container.firstChild);
  }, 60_000);
})();
```

- [ ] **Step 3: Manual verification**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
python app.py &
```
Open browser → Live Trades tab. In the browser console, run:
```javascript
window._liveLastRefreshAt = Date.now() - 200000;
```
Within 60 s the amber badge should appear above the position cards. Run `loadLiveTrades()` in console — badge should disappear. Kill server: `pkill -f "python app.py"`

- [ ] **Step 4: Commit**

```bash
git add static/js/08-live.js
git commit -m "feat: stale-data badge on live trades after 3 min without refresh"
```

---

## Task 9: Scanner ETA in `14-scanner.js`

**Files:**
- Modify: `static/js/14-scanner.js`

- [ ] **Step 1: Add ETA block to `_buildProgressBlock`**

In `14-scanner.js`, find `_buildProgressBlock(state)`. The function ends with:
```javascript
  // Detail line for stages 2+
  if (stage >= 2 && detail) {
    const det = document.createElement('div');
    det.className = 'prog-detail';
    det.textContent = detail;
    wrap.appendChild(det);
  }

  return wrap;
}
```

Replace the `return wrap;` line with:

```javascript
  // ETA estimation (shown only between 5% and 95% overall progress)
  if (state.started_at && state.status === 'running') {
    const elapsed    = Date.now() / 1000 - state.started_at;
    const overallPct = stage === 1 ? pct * 0.40
                     : stage === 2 ? 40 + pct * 0.20
                     : stage === 3 ? 60 + pct * 0.40
                     : 0;
    const frac = overallPct / 100;
    if (frac > 0.05 && frac < 0.95 && elapsed > 5) {
      const totalEst  = elapsed / frac;
      const remaining = Math.max(0, Math.round(totalEst - elapsed));
      const etaText   = remaining < 60
        ? '~' + remaining + 's remaining'
        : '~' + Math.ceil(remaining / 60) + 'm remaining';
      const eta = document.createElement('div');
      eta.style.cssText = 'font-size:.75rem;color:var(--muted);margin-top:4px';
      eta.textContent = etaText;
      wrap.appendChild(eta);
    }
  }

  return wrap;
}
```

- [ ] **Step 2: Manual verification**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
python app.py &
```
Navigate to Scanner tab → click "Scan Now" → once the bar reaches ~5% progress, an ETA line (`~Xm remaining` or `~Xs remaining`) should appear below the bar. Kill server: `pkill -f "python app.py"`

- [ ] **Step 3: Commit**

```bash
git add static/js/14-scanner.js
git commit -m "feat: scanner ETA display during active scan"
```

---

## Task 10: Full test suite + version bump + deploy

**Files:**
- Modify: `constants.py` (version)

- [ ] **Step 1: Run all tests**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
python -m pytest tests/ -v --tb=short 2>&1 | tail -30
```
Expected: all existing tests plus 13 new tests pass. Zero failures.

- [ ] **Step 2: Bump version in `constants.py`**

Change:
```python
VERSION = "1.1.0"
```
to:
```python
VERSION = "1.2.0"
```

- [ ] **Step 3: Commit version bump**

```bash
git add constants.py
git commit -m "chore: bump version to 1.2.0"
```

- [ ] **Step 4: Push to GitHub**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
git push origin main
```

- [ ] **Step 5: Deploy to Pi via SSH**

```bash
expect -c "
  spawn ssh fbauer@192.168.1.21
  expect \"password:\"
  send \"raspberry\r\"
  expect \"\\\$\"
  send \"cd /home/fbauer/trading-journal && git pull && pkill -f 'python app.py'; sleep 1; nohup python app.py > /tmp/app.log 2>&1 &\r\"
  expect \"\\\$\"
  send \"exit\r\"
  expect eof
"
```

- [ ] **Step 6: Smoke-test on Pi**

```bash
curl -s http://192.168.1.21:8082/api/calls/accuracy-progress | python3 -m json.tool
```
Expected output contains `"ok": true` and `"target": 35`.

```bash
curl -s http://192.168.1.21:8082/api/version | python3 -m json.tool
```
Expected: version `1.2.0`.

- [ ] **Step 7: Update project memory**

Update `project_trading_journal.md` in memory to reflect v1.2.0, new features, and that Hyblock is deferred.

---

## Self-review

**Spec coverage:**
- [x] Stale-data badge → Task 8
- [x] Symbol autocomplete → already done (`_attachSymbolPicker` confirmed in codebase)
- [x] Scanner ETA → Task 9
- [x] Retroactive outcome recorder → Tasks 2–4 (Option A: hook into sync)
- [x] Accuracy progress widget → Tasks 5–7
- [x] `ACCURACY_TARGET = 35` → Task 1
- [x] blofin_sync wired → Task 4
- [x] Tests → Tasks 2 (10 tests), 5 (3 tests)
- [x] Deploy → Task 10

**Type/name consistency:**
- `_retroactive_close_calls(conn)` — defined Task 3, imported Task 4, monkeypatched in tests Task 2 ✓
- `loadAccuracyProgress()` — defined and called Task 7 ✓
- `#accuracy-progress-card`, `#live-stale-badge` — created dynamically, no template edits ✓
- `ACCURACY_TARGET` — defined Task 1, imported Task 6 ✓
- `chart_context.get_candles_at_time` — existing function, correct signature `(symbol, timeframe, end_time_ms, limit)` ✓
