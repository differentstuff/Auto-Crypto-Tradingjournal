# Single-Coin Scan — Design Spec
*Date: 2026-05-14 · Status: Approved*

---

## Overview

Add a symbol input + "Scan Symbol" button to the Setup Scanner that runs the full 3-stage pipeline on a single coin. If a full scan is already running, the request is queued and auto-fires when the current scan completes.

No backend changes. All logic lives in `14-scanner.js`.

---

## Architecture

**Backend:** Already supported. `POST /api/scanner/run` accepts `{"symbols": ["AAVEUSDT"], "force": true}` which calls `force_scan(symbols=["AAVEUSDT"])` — runs the full 3-stage pipeline on one symbol. No new endpoints needed.

**Frontend queue state:**
```javascript
let _pendingSingleScan = null;  // null | string (symbol only — min_score read at fire time)
```

---

## UI

Added to `renderScannerMeta()` in `14-scanner.js`, below the existing scan controls row:

```
[🔍 Scan Now]  [🔄 Re-scan]  [⚙ Criteria]   Min score [6+]
[BTCUSDT    ▾]  [🔍 Scan Symbol]
```

- The symbol input uses `_attachSymbolPicker` (same dropdown autocomplete used for chart explorer and manual trade entry). Input ID: `scan-single-symbol`.
- Button ID: `btn-scan-single`. Label: `🔍 Scan Symbol` when idle; `⏳ Queued…` when a scan is running and symbol is pending.
- Empty symbol input → button is disabled (no action).
- Symbol normalisation: uppercase + append `USDT` if not already ending in `USDT`.

---

## Behaviour

### Case 1 — No scan running
User types symbol, clicks "Scan Symbol":
1. Normalise symbol (uppercase, ensure `USDT` suffix).
2. `POST /api/scanner/run` with `{"symbols": [sym], "force": true, "min_score": current_min}`.
3. Clear the symbol input.
4. Scanner progress bar takes over (existing polling handles display).

### Case 2 — Scan already running
User clicks "Scan Symbol" while scanner is `status === "running"`:
1. Set `_pendingSingleScan = sym` (string only).
2. Show amber queue badge above the scanner table:
   ```
   ⏳ BTCUSDT queued — waiting for current scan to finish  [✕]
   ```
   Badge ID: `scan-queue-badge`. ✕ button calls `_clearPendingSingleScan()`.
3. Disable "Scan Symbol" button, show label `⏳ Queued…`.

### Case 3 — Current scan completes while queued
On every `pollScannerStatus()` call, after state update:
```javascript
if (_pendingSingleScan && state.status !== 'running') {
  _firePendingSingleScan();
}
```
`_firePendingSingleScan()`:
1. Fires `POST /api/scanner/run` with the queued symbol + min_score.
2. Clears `_pendingSingleScan`.
3. Removes queue badge.
4. Restores "Scan Symbol" button label.

### Edge cases
- User changes min_score while a scan is queued → current min_score is used at fire time (not stored at queue time).
- Symbol input cleared by user after queuing → `_pendingSingleScan` still fires (symbol was stored at queue time).
- App reload → queue is lost (in-memory only, acceptable).

---

## Files Changed

| File | Change |
|------|--------|
| `static/js/14-scanner.js` | Add symbol input row to `renderScannerMeta()`; add `_pendingSingleScan` state; add `_firePendingSingleScan()`, `_clearPendingSingleScan()`; hook into existing `pollScannerStatus()` |
| `templates/index.html` | Bump `14-scanner.js` version |

No Python changes. No DB changes. No new API endpoints.

---

## Testing

Manual verification steps:
1. No scan running → type `BTC`, click Scan Symbol → progress bar starts for BTCUSDT, result appears.
2. Start full scan → immediately type `ETH`, click Scan Symbol → badge shows "ETHUSDT queued", full scan completes, ETHUSDT scan auto-fires.
3. Start full scan → queue `ETH` → click ✕ on badge → badge clears, no scan fires after full scan.
4. Type nothing → Scan Symbol button is disabled.
5. Type `btc` (lowercase) → normalises to `BTCUSDT`.
