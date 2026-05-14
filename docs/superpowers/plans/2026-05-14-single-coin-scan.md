# Single-Coin Scan — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a symbol input + "Scan Symbol" button to the scanner that runs the full pipeline on one coin, queueing automatically if a scan is in progress.

**Architecture:** Pure frontend change to `14-scanner.js`. `_pendingSingleScan` (string|null) holds a queued symbol. `renderScannerMeta()` gets a second row for the input; `_startScanPoller()` fires the queue on completion. No backend changes — `POST /api/scanner/run` already accepts `symbols: ["BTCUSDT"]`.

**Tech Stack:** Vanilla JS, existing `_attachSymbolPicker` autocomplete, existing `api()` helper.

---

## File Map

| File | Change |
|------|--------|
| `static/js/14-scanner.js` | Add global state, helper functions, symbol row in `renderScannerMeta`, queue check in `_startScanPoller` |
| `templates/index.html` | Bump `14-scanner.js` version from `3.1` to `3.2` |

---

## Task 1: Add global state + helper functions

**Files:**
- Modify: `static/js/14-scanner.js` (top of file, near existing `let _scanSetups`, `let _scanPollInterval`)

- [ ] **Step 1: Find the existing global declarations at the top of `14-scanner.js`**

Look for this block near the top (lines 5–10):
```javascript
let _scanSetups        = [];
let _scanLastState     = null;
let _scanPollInterval  = null;
```

- [ ] **Step 2: Add `_pendingSingleScan` immediately after those declarations**

```javascript
let _pendingSingleScan = null;   // string (symbol) when a single-coin scan is queued
```

- [ ] **Step 3: Add helper functions at the end of `14-scanner.js`, before `_loadScannerWatchlist`**

Find the line `async function _loadScannerWatchlist() {` (last function in the file) and insert BEFORE it:

```javascript
// ── Single-coin scan helpers ──────────────────────────────────────────────────

async function _doSingleScan(sym) {
  const minScore = parseInt(document.getElementById('scan-min-score')?.value || '6');
  const criteria = _readCriteriaFromCheckboxes();
  const res = await api('/api/scanner/run', 'POST',
    { force: true, symbols: [sym], min_score: minScore, criteria });
  if (!res.ok) return;
  renderScannerPage(res.data);
  if (res.data.status === 'running') _startScanPoller();
}

function _firePendingSingleScan() {
  if (!_pendingSingleScan) return;
  const sym = _pendingSingleScan;
  _pendingSingleScan = null;
  _doSingleScan(sym);
}

function _clearPendingSingleScan() {
  _pendingSingleScan = null;
  if (_scanLastState) renderScannerMeta(_scanLastState);
}

function _startSingleScan() {
  const inp = document.getElementById('scan-single-symbol');
  if (!inp) return;
  let sym = inp.value.trim().toUpperCase();
  if (!sym) return;
  if (!sym.endsWith('USDT')) sym += 'USDT';
  const state = _scanLastState || {};
  if (state.status === 'running') {
    _pendingSingleScan = sym;
    inp.value = '';
    renderScannerMeta(state);
    return;
  }
  inp.value = '';
  _doSingleScan(sym);
}
```

- [ ] **Step 4: Verify no syntax errors**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
node --input-type=module < static/js/14-scanner.js 2>&1 | head -5
```
Expected: error about `api` not defined (module context) — that's fine, means no syntax errors. If you see a SyntaxError instead, fix the syntax.

- [ ] **Step 5: Commit**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
git add static/js/14-scanner.js
git commit -m "feat: single-coin scan — global state and helper functions"
```

---

## Task 2: Add symbol input row to `renderScannerMeta`

**Files:**
- Modify: `static/js/14-scanner.js` — `renderScannerMeta()` function

- [ ] **Step 1: Find the insertion point in `renderScannerMeta`**

The function ends with:
```javascript
  const sub = document.createElement('div');
  sub.style.cssText = 'font-size:.78rem;color:var(--muted);margin-top:6px';
  sub.textContent = (window._scannerWatchlistCount||100) + ' symbols · scores ' + activeMins + '–10 · results cached 30 min · click a row for details';
  el.appendChild(sub);
}
```

- [ ] **Step 2: Insert the single-coin row and queue badge BEFORE the closing `}` of `renderScannerMeta`**

Replace the closing `}` of `renderScannerMeta` with:

```javascript
  // ── Single-coin scan row ──────────────────────────────────────────────────
  const singleRow = document.createElement('div');
  singleRow.style.cssText = 'display:flex;align-items:center;gap:8px;margin-top:10px';

  const symInput = document.createElement('input');
  symInput.type = 'text';
  symInput.id = 'scan-single-symbol';
  symInput.placeholder = 'Symbol e.g. BTC';
  symInput.style.cssText = [
    'padding:5px 10px', 'font-size:.82rem', 'background:var(--bg2)',
    'border:1px solid var(--border)', 'border-radius:6px', 'color:var(--text)',
    'width:130px', 'text-transform:uppercase',
  ].join(';');
  symInput.addEventListener('input', () => {
    symInput.value = symInput.value.toUpperCase();
    const b = document.getElementById('btn-scan-single');
    if (b) b.disabled = !symInput.value.trim() || !!_pendingSingleScan;
  });
  symInput.addEventListener('keydown', e => {
    if (e.key === 'Enter') document.getElementById('btn-scan-single')?.click();
  });

  const singleBtn = document.createElement('button');
  singleBtn.className = 'btn btn-secondary btn-sm';
  singleBtn.id = 'btn-scan-single';
  singleBtn.disabled = true;
  singleBtn.textContent = _pendingSingleScan ? '⏳ Queued…' : '🔍 Scan Symbol';
  singleBtn.onclick = _startSingleScan;

  singleRow.appendChild(symInput);
  singleRow.appendChild(singleBtn);
  el.appendChild(singleRow);

  // Attach symbol autocomplete after the input is in the DOM
  if (typeof _attachSymbolPicker === 'function') _attachSymbolPicker('scan-single-symbol');

  // Queue badge — shown when a single-coin scan is waiting
  if (_pendingSingleScan) {
    const badge = document.createElement('div');
    badge.id = 'scan-queue-badge';
    badge.style.cssText = [
      'display:flex', 'align-items:center', 'gap:8px',
      'padding:6px 12px', 'margin-top:6px',
      'background:rgba(255,179,0,.1)', 'border:1px solid rgba(255,179,0,.3)',
      'border-radius:8px', 'font-size:.8rem', 'color:var(--yellow)',
    ].join(';');
    const msg = document.createElement('span');
    msg.textContent = '⏳ ' + _pendingSingleScan + ' queued — waiting for current scan to finish';
    const xBtn = document.createElement('button');
    xBtn.textContent = '✕';
    xBtn.style.cssText = 'background:none;border:none;color:var(--yellow);cursor:pointer;font-size:.9rem;padding:0 0 0 4px;line-height:1';
    xBtn.onclick = _clearPendingSingleScan;
    badge.appendChild(msg);
    badge.appendChild(xBtn);
    el.appendChild(badge);
  }
}
```

- [ ] **Step 3: Verify syntax**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
node --input-type=module < static/js/14-scanner.js 2>&1 | head -5
```
Expected: same reference errors as Task 1 — no SyntaxError.

- [ ] **Step 4: Commit**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
git add static/js/14-scanner.js
git commit -m "feat: single-coin scan — symbol input row and queue badge in renderScannerMeta"
```

---

## Task 3: Hook queue into scan poller + version bump

**Files:**
- Modify: `static/js/14-scanner.js` — `_startScanPoller()`
- Modify: `templates/index.html` — version bump

- [ ] **Step 1: Find `_startScanPoller` in `14-scanner.js`**

Current code:
```javascript
function _startScanPoller() {
  _stopScanPoller();
  _scanPollInterval = setInterval(async () => {
    const res = await api('/api/scanner/status');
    if (!res.ok) return;
    renderScannerPage(res.data);
    if (res.data.status !== 'running') _stopScanPoller();
  }, 2000);
}
```

- [ ] **Step 2: Add `_firePendingSingleScan()` call when scan completes**

Replace with:
```javascript
function _startScanPoller() {
  _stopScanPoller();
  _scanPollInterval = setInterval(async () => {
    const res = await api('/api/scanner/status');
    if (!res.ok) return;
    renderScannerPage(res.data);
    if (res.data.status !== 'running') {
      _stopScanPoller();
      _firePendingSingleScan();
    }
  }, 2000);
}
```

- [ ] **Step 3: Bump version in `templates/index.html`**

Find:
```html
<script src="/static/js/14-scanner.js?v=3.1"></script>
```
Replace with:
```html
<script src="/static/js/14-scanner.js?v=3.2"></script>
```

- [ ] **Step 4: Verify syntax**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
node --input-type=module < static/js/14-scanner.js 2>&1 | head -5
```
Expected: no SyntaxError.

- [ ] **Step 5: Commit**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
git add static/js/14-scanner.js templates/index.html
git commit -m "feat: single-coin scan — fire queued scan on completion, bump v3.2"
```

---

## Task 4: Deploy and verify

- [ ] **Step 1: Push to GitHub**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
git push origin main
```

- [ ] **Step 2: Deploy to Pi**

```bash
expect -c "
  set timeout 60
  spawn ssh -o StrictHostKeyChecking=no fbauer@192.168.1.21
  expect \"password:\"
  send \"laZHn0rd\r\"
  expect \"\\\$\"
  send \"cd /home/fbauer/trading-journal && git fetch origin && git reset --hard origin/main && sudo systemctl restart trading-journal && sleep 3 && sudo systemctl is-active trading-journal\r\"
  expect \"\\\$\"
  send \"exit\r\"
  expect eof
"
```
Expected: `active`

- [ ] **Step 3: Smoke-test**

```bash
curl -s http://192.168.1.21:8082/ -o /dev/null -w "%{http_code}"
```
Expected: `200`

- [ ] **Step 4: Manual verification checklist**

Open `http://192.168.1.21:8082` → Scanner tab:

1. Symbol input appears below the Scan Now / Criteria row ✓
2. Input has autocomplete dropdown when typing ✓
3. "Scan Symbol" button disabled until symbol typed ✓
4. Type `btc` → input shows `BTC` (auto-uppercase) ✓
5. Click "Scan Symbol" with no full scan running → progress bar starts for `BTCUSDT` ✓
6. Start a full scan → type `ETH` → click "Scan Symbol" → amber badge shows "⏳ ETHUSDT queued…" ✓
7. Full scan completes → ETHUSDT scan auto-fires without user action ✓
8. Queue a symbol → click ✕ on badge → badge clears, no scan fires ✓

---

## Self-Review

**Spec coverage:**
- [x] Symbol input with autocomplete → Task 2 (`_attachSymbolPicker('scan-single-symbol')`)
- [x] Button disabled when empty → Task 2 (`singleBtn.disabled = true`, enabled on input event)
- [x] Symbol normalisation (uppercase + USDT) → Task 1 `_startSingleScan()` 
- [x] Fires immediately when idle → Task 1 `_doSingleScan(sym)` called directly
- [x] Queues when running → Task 1 `_pendingSingleScan = sym`, badge renders in Task 2
- [x] Auto-fires on completion → Task 3 `_firePendingSingleScan()` in `_startScanPoller`
- [x] Cancel queue via ✕ → Task 2 badge xBtn calls `_clearPendingSingleScan()`
- [x] Version bump → Task 3

**Type consistency:**
- `_pendingSingleScan`: string | null — defined Task 1, read in Task 2 (badge), cleared in Task 1 helpers ✓
- `_firePendingSingleScan()` — defined Task 1, called Task 3 ✓
- `_clearPendingSingleScan()` — defined Task 1, called Task 2 (xBtn) ✓
- `_doSingleScan(sym)` — defined Task 1, called by `_startSingleScan` + `_firePendingSingleScan` ✓
- `scan-single-symbol` — set in Task 2, read by `_startSingleScan` in Task 1 ✓
- `btn-scan-single` — set in Task 2, referenced in input event + keydown in Task 2 ✓
