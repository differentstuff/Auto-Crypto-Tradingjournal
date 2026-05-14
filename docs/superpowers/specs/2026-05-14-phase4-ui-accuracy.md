# Phase 4 UI/UX + Accuracy Accumulation — Design Spec
*Date: 2026-05-14 · Status: Approved*

---

## Overview

Two independent features:

1. **Phase 4 UI/UX** — three targeted improvements to the live trades view and scanner UX
2. **Accuracy accumulation** — retroactive outcome recording for unmatched/saved calls using historical OHLCV data, plus a progress tracker widget

Both features are additive — no existing behaviour changes.

---

## Feature 1 — Phase 4 UI/UX

### 1a. Stale-data badge on live trades

**Problem:** The live trades tab auto-refreshes every 30 s, but if the exchange API is slow or the Pi sleeps, displayed data can be minutes old without any indication.

**Design:**
- In `08-live.js`, record `window._liveLastRefreshAt = Date.now()` on every successful `loadLiveTrades()` completion.
- A `setInterval` every 60 s checks the gap. If `now − _liveLastRefreshAt > 180_000` (3 min), inject a `<div id="live-stale-badge">` above the position cards: `⚠ Data may be stale — last updated X min ago`.
- Badge clears automatically on the next successful refresh.
- Badge styled with `var(--yellow)` text, no new CSS class needed (inline style).

**Scope:** `static/js/08-live.js` only — no backend changes.

---

### 1b. Symbol autocomplete on call analyzer

**Problem:** The call analyzer symbol input is a free-text field; users must remember exact symbol formats.

**Design:**
- On page load in `07-calls.js`, fetch `/api/scanner/watchlist` (already exists, returns `{symbols: [...]}`) and store as `window._symbolList`.
- On `input` event on `#call-symbol`, filter `_symbolList` case-insensitively and render a `<datalist id="symbol-suggestions">` attached to the input via `list="symbol-suggestions"`.
- Native `<datalist>` — zero extra dependencies, keyboard-navigable, degrades gracefully if the fetch fails.
- The input still accepts free text for symbols not in the watchlist.

**Scope:** `static/js/07-calls.js` only — no backend changes.

---

### 1c. Scanner ETA

**Problem:** The scanner progress bar shows stage and percentage but no time estimate, so users don't know whether to wait 30 s or 4 min.

**Design:**
- `ai_scanner._state` already has `started_at` (unix timestamp), `stage` (1/2/3), `stage_progress` (0–100), and `scanned` count.
- In `14-scanner.js`, inside `updateScannerProgress()`, compute:
  ```
  elapsed = Date.now()/1000 − state.started_at
  overall_pct = weighted stage pct  (stage1=40%, stage2=20%, stage3=40%)
  eta_sec = (elapsed / overall_pct) * (1 − overall_pct)   [only when overall_pct > 5%]
  ```
- Render `~Xm Ys remaining` inline next to the existing progress text. Hide when `overall_pct < 5%` or `> 95%`.

**Scope:** `static/js/14-scanner.js` only — no backend changes.

---

## Feature 2 — Accuracy accumulation

### Context

`bitget_sync._auto_close_calls` already records outcomes for **matched** calls (calls linked to a live position that has since closed). The gap: **saved/unmatched calls** — analyses where the user reviewed but did not trade, or scanner signals that were never linked to a position. These calls have `entry_price`, `sl_price`, `tp1_price`, `tp2_price` and `created_at`, so their outcome can be determined retroactively from historical OHLCV data.

### Retroactive outcome recorder

**New function:** `bitget_sync._retroactive_close_calls(conn) → int`

Logic per eligible call:
1. Select calls where `status = 'saved'` and `created_at < datetime('now', '-2 hours')` and all of `sl_price`, `tp1_price`, `entry_price` are NOT NULL.
2. Fetch 1H candles from `created_at` to `now` using `chart_context.get_candles_at_time(symbol, '1H', end_time_ms=now_ms, limit=500)` (reuses existing function, no new API client needed).
3. Filter returned candles to those with `timestamp_ms > created_at_ms`, then walk chronologically. For a Long call:
   - First candle where `low ≤ sl_price` → `hit_sl=1, outcome='lost'`
   - First candle where `high ≥ tp2_price` (if set) → `hit_tp1=1, hit_tp2=1, outcome='won'`
   - First candle where `high ≥ tp1_price` → `hit_tp1=1, outcome='won'`
   - Whichever event comes first wins.
4. For a Short call: mirror the comparisons (`high ≥ sl_price`, `low ≤ tp2_price`, `low ≤ tp1_price`).
5. If no candle resolves in the lookback window → skip (outcome still unknown).
6. Record: `outcome_pnl = NULL` (no actual trade), set `status='closed'`, write `outcome_at`.

**Called from:** `bitget_sync.run_sync()` after `_auto_close_calls()`, using the same `conn`. Blofin sync imports and calls it the same way.

**Rate:** runs on every sync cycle (every 5 min). Safe to call repeatedly — only touches `status='saved'` calls.

**Candle lookback cap:** 500 candles at 1H = ~20 days. Calls older than 20 days with no resolution are left as-is (stale signal, not worth recording).

---

### Accuracy progress widget

**New endpoint:** `GET /api/calls/accuracy-progress`

Returns:
```json
{
  "recorded": 14,
  "target":   35,
  "win_rate": 64.3,
  "remaining": 21,
  "enough_data": false
}
```

Target = 35 calls (statistical minimum for 85% confidence at ±10% margin). Hardcoded constant in `routes/calls.py`.

**Frontend:** Small card in `09-analysis.js` / prediction accuracy section:
```
📊 Accuracy tracking: 14 / 35 calls recorded · 64.3% win rate
[████████░░░░░░░░░░░░] 40%  · 21 more needed for statistical confidence
```
Progress bar uses `var(--accent)`, turns `var(--accent3)` (green) when target reached.

---

## Data contracts (no schema changes needed)

- `_retroactive_close_calls` writes to the same `analyzed_calls` columns already used by `_auto_close_calls` (`outcome`, `outcome_pnl`, `hit_tp1`, `hit_tp2`, `hit_sl`, `outcome_at`, `status`).
- `outcome_pnl` is `NULL` for retroactive records (no actual trade executed) — this is already allowed by the schema.

---

## Testing plan

1. **Stale badge:** manually pause Pi network → wait 3+ min → verify badge appears. Resume → verify it clears.
2. **Autocomplete:** type `BT` in call analyzer symbol field → verify dropdown shows `BTCUSDT`, `BTCDOMUSDT` etc. Type unknown symbol → verify it still submits.
3. **Scanner ETA:** trigger a manual scan → verify ETA appears at >5% progress and hides at >95%.
4. **Retroactive recorder:** insert a test `saved` call with known TP/SL from 3 hours ago → run sync → verify `outcome` is recorded and `status='closed'`.
5. **Progress widget:** verify count matches `SELECT COUNT(*) FROM analyzed_calls WHERE outcome IS NOT NULL`.

---

## Files changed

| File | Change |
|------|--------|
| `static/js/08-live.js` | stale-data badge |
| `static/js/07-calls.js` | symbol autocomplete |
| `static/js/14-scanner.js` | ETA display |
| `bitget_sync.py` | `_retroactive_close_calls()` + call in `run_sync()` |
| `blofin_sync.py` | import + call `_retroactive_close_calls()` |
| `routes/calls.py` | `GET /api/calls/accuracy-progress` |
| `static/js/09-analysis.js` | accuracy progress widget |
| `constants.py` | `ACCURACY_TARGET = 35` |

No DB migrations required. No new dependencies.
