# Crypto Trading Journal — Complete Builder's Guide

**Live URL:** http://192.168.1.21:8082  
**Deployed on:** Raspberry Pi 5 (8GB), aarch64, Debian  
**Built:** May 2026

---

## What This Is

A full-stack web application that:

1. **Imports** 6 months of Bitget USDT-M Futures history from CSV export
2. **Syncs live** new closed trades automatically every 15 minutes via Bitget API
3. **Shows** a 5-module dashboard: KPIs, trade journal, deep analytics, AI advisor, live trades
4. **Analyzes** every open position with Claude AI on demand
5. **Runs forever** as a systemd service, auto-starts on Pi boot

---

## Architecture at a Glance

```
Browser (http://192.168.1.21:8082)
         │
         ▼
    Flask (app.py)          ← HTTP server + all API routes
    ├── database.py         ← SQLite schema + helpers
    ├── importer.py         ← Bitget CSV → SQLite (historical data)
    ├── analytics.py        ← KPI + stats calculations (pure Python)
    ├── ai_advisor.py       ← Full-portfolio Claude analysis
    ├── ai_live_trade.py    ← Per-trade Claude analysis for open positions
    ├── bitget_client.py    ← Authenticated Bitget REST API v2 client
    ├── bitget_sync.py      ← Background sync thread (every 15 min)
    └── trading_journal.db  ← SQLite database (auto-created)

templates/index.html        ← Entire frontend: HTML + CSS + JavaScript (SPA)
data/                       ← CSV files for import
docs/GUIDE.md               ← This file
```

---

## The Tech Stack

| Layer | Choice | Why |
|-------|--------|-----|
| Language | Python 3.13 | Pre-installed on Pi, fast to write |
| Web framework | Flask 3.1 | Simple, no boilerplate, already installed |
| Database | SQLite 3 | Zero config, single file, built into Python |
| Frontend | Plain HTML/CSS/JS | No build step, readable, runs in any browser |
| Charts | Chart.js 4 (CDN) | One `<script>` tag, great defaults |
| AI | Anthropic claude-sonnet-4-6 | Best model for trading analysis |
| Exchange API | Bitget REST v2 | Read-only, HMAC-SHA256 auth |
| Process manager | systemd | Auto-start on boot, auto-restart on crash |

---

## File-by-File Reference

### `database.py` — Schema & Connection

Creates and manages four SQLite tables:

**`positions`** — One row per closed trade. This is the core data.

| Column | Type | Source |
|--------|------|--------|
| id | INTEGER PK | auto |
| symbol | TEXT | e.g. `BOMEUSDT` |
| base_asset | TEXT | e.g. `BOME` |
| direction | TEXT | `Long` or `Short` |
| margin_mode | TEXT | `Cross` or `Isolated` |
| open_time | TEXT | ISO datetime |
| close_time | TEXT | ISO datetime |
| duration_minutes | INTEGER | calculated |
| entry_price | REAL | avg open price |
| close_price | REAL | avg close price |
| size_contracts | TEXT | raw: `400000BOME` |
| size_usdt | REAL | position value in USDT |
| position_pnl | REAL | gross PnL before fees |
| realized_pnl | REAL | net PnL (after fees) |
| opening_fee | REAL | |
| closing_fee | REAL | |
| total_fees | REAL | |
| notes | TEXT | user-editable |
| tags | TEXT | comma-separated |
| is_manual | INTEGER | 1 = hand-entered |
| external_id | TEXT | Bitget positionId (for dedup) |

**`orders`** — Individual order fills from Bitget order history.

**`wallet_snapshots`** — Every account transaction event with resulting balance. Powers the wallet equity curve chart. Has a `bill_id` column added by `bitget_sync.py` for deduplication.

**`settings`** — Key/value store. Holds `last_sync_ms`, `account_equity`, `available_balance`.

**`import_log`** — Audit trail of CSV imports.

Key function: `get_conn()` returns a `sqlite3.Connection` with `row_factory=sqlite3.Row` so rows behave like dicts. WAL mode enabled for safe concurrent reads.

---

### `importer.py` — CSV Import

Parses all four Bitget USDT-M export CSVs:

| File pattern | Table | Rows (6 months) |
|---|---|---|
| `position history` | positions | 808 |
| `order history` | orders | 7,008 |
| `order details` | (skipped, redundant) | — |
| `transactions` | wallet_snapshots | 10,000 |

**Two Bitget quirks handled:**

1. **BOM prefix** — Files start with invisible UTF-8 byte-order mark. Fixed by `encoding='utf-8-sig'`.
2. **Units in numbers** — Values like `19.3581879USDT` and `235.75USDT`. Fixed by `_clean_float()`:
   ```python
   val = re.sub(r'[A-Za-z]+$', '', val).strip()  # strip trailing letters
   float(val)
   ```

**Duplicate prevention:** position import checks `(symbol, open_time, close_time)` uniqueness before inserting.

**CLI usage:**
```bash
python3 importer.py data/
```

---

### `bitget_client.py` — Exchange API Client

Handles all communication with Bitget REST API v2.

**Authentication (HMAC-SHA256):**
```
message  = timestamp + "GET" + path + "?" + query_string
signature = base64( hmac_sha256(secret_key, message) )

Headers:
  ACCESS-KEY: <api_key>
  ACCESS-SIGN: <signature>
  ACCESS-TIMESTAMP: <unix_ms>
  ACCESS-PASSPHRASE: <passphrase>
```

**Credentials:**
- API Key: `REDACTED_API_KEY` (read-only)
- Passphrase: stored in `bitget_client.py`, overridable via `BITGET_PASSPHRASE` env var

**Pagination design:** Bitget uses cursor-based pagination. Critical rule discovered through testing:

- **Page 1:** send `startTime` + `endTime` (max 90-day window per request)
- **Page 2+:** send **only** `endId` — do NOT resend time range. Resending causes Bitget to recompute the interval from startTime to the cursor, which can exceed 90 days and triggers error `00001`.
- **Safeguard:** each fetched row's timestamp is checked against `start_ms`; once a row is older than the window, pagination stops. This prevents paginating through the entire account history.

**Public methods:**

| Method | Endpoint | Returns |
|--------|----------|---------|
| `get_account_equity()` | `/api/v2/mix/account/accounts` | Current balance dict |
| `get_position_history(start_ms, end_ms)` | `/api/v2/mix/position/history-position` | Closed positions list |
| `get_order_history(start_ms, end_ms)` | `/api/v2/mix/order/orders-history` | Orders list |
| `get_account_bills(start_ms, end_ms)` | `/api/v2/mix/account/bill` | Bills list |
| `get_open_positions()` | `/api/v2/mix/position/all-position` | Live open positions |

**Confirmed field names from live API:**

*Position history:* `positionId`, `holdSide`, `openAvgPrice`, `closeAvgPrice`, `openTotalPos`, `pnl`, `netProfit`, `openFee`, `closeFee`, `totalFunding`, `marginMode`, `ctime`/`utime` (**lowercase** — unlike orders which use uppercase)

*Orders:* `orderId`, `priceAvg`, `quoteVolume`, `fee`, `totalProfits`, `side`, `posSide`, `tradeSide`, `orderSource`, `cTime`/`uTime` (uppercase)

*Bills:* `billId`, `symbol`, `amount`, `fee`, `businessType`, `balance`, `cTime`

*Open positions:* `symbol`, `holdSide`, `openPriceAvg`, `markPrice`, `unrealizedPL`, `marginSize`, `total`, `leverage`, `takeProfit`, `stopLoss`, `liquidationPrice`, `breakEvenPrice`, `achievedProfits`, `totalFee`, `cTime`

---

### `bitget_sync.py` — Background Sync Engine

Runs as a daemon thread inside Flask. Syncs Bitget → SQLite every 15 minutes.

**First-run logic:** Uses the timestamp of the most recent position already in the database as the sync start point. This means:
- CSV import covers the full history
- API sync only fetches trades *after* the latest CSV trade
- No redundant 85-day backfill on first start

**Chunked time ranges:** All requests are split into ≤89-day windows using `_chunked_sync()`. This is necessary because Bitget's API rejects requests where `endTime - startTime > 90 days`.

**Deduplication:** Uses `external_id` (= Bitget `positionId`) for positions, `order_id` for orders, `bill_id` for bills. All stored in the DB so re-runs are safe.

**Thread safety:** `threading.Lock()` prevents two syncs running simultaneously. The `/api/sync` endpoint returns `{"error": "Sync already running"}` if a background sync is in progress.

**Background thread:**
```python
def start_background_sync():
    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
```
Called once from `app.py __main__`. Daemon thread means it auto-stops when Flask stops.

**Sync status** stored in module-level `_sync_status` dict:
```python
{
  "running": bool,
  "last_run": "2026-05-05 14:27:50 UTC",
  "last_result": {"positions": 2, "orders": 5, "bills": 8, "equity": {...}},
  "last_error": None,
  "next_run": "2026-05-05 16:42:49"
}
```

---

### `analytics.py` — KPI Calculations

Pure Python calculations over the SQLite data. No external libraries.

**`get_dashboard_kpis(filters)`** returns:

| Field | Formula |
|-------|---------|
| win_rate | `win_trades / total_trades × 100` |
| profit_factor | `Σ(winning trades) / abs(Σ(losing trades))` |
| max_drawdown | Largest peak-to-trough drop on cumulative PnL curve |
| pnl_curve | `[{date, cumulative_pnl}]` array for line chart |
| wallet_curve | Downsampled to ≤200 points for performance |
| top_symbols | Top 5 by total realized PnL |

**`get_deep_stats(filters)`** returns breakdowns by:
- Symbol (all 194 symbols, sorted by PnL)
- Month (calendar month aggregation)
- Weekday (Monday–Sunday)
- Hour of day (0–23 UTC, based on open_time)
- Direction (Long vs Short)
- Duration bucket (`< 1h`, `1-4h`, `4-24h`, `1-7 days`, `> 7 days`)
- Streaks (longest consecutive win/loss run)
- Fee analysis (total, avg, % of gross PnL)

**Filters:** All functions accept `{symbol, direction, date_from, date_to}`. Built into safe parameterized SQL with `?` placeholders — no SQL injection possible.

---

### `ai_advisor.py` — Portfolio AI Analysis

Sends the trader's full 6-month statistics to Claude claude-sonnet-4-6 and returns a structured trading assessment.

**Prompt design:** Serializes `get_dashboard_kpis()` + `get_deep_stats()` to JSON (~23,000 tokens input). Instructs Claude to return pure JSON with a specific schema. Strips markdown code fences from the response before parsing (Claude sometimes wraps output in ` ```json ``` ` even when told not to).

**Response schema:**
```json
{
  "overall_status": "paragraph",
  "score": {"value": 3, "label": "Developing"},
  "strengths": [{"title": "...", "detail": "..."}],
  "weaknesses": [{"title": "...", "detail": "..."}],
  "recommendations": [{"priority": "High", "title": "...", "action": "...", "expected_impact": "..."}],
  "symbol_insights": [{"symbol": "BTCUSDT", "insight": "..."}],
  "risk_management": "paragraph",
  "mindset_note": "sentence"
}
```

**Cost:** ~$0.02 per analysis (23k input + ~2k output tokens at Sonnet pricing).

---

### `ai_live_trade.py` — Per-Trade AI Analysis

Analyzes a single **open** position and gives specific trade management advice.

**Context provided to Claude:**
1. The live position data (entry, mark price, unrealized PnL, TP/SL, leverage, duration)
2. The trader's historical closed-trade stats for that symbol (last 30 trades: win rate, avg win/loss, total PnL, avg hold time)

**Response schema:**
```json
{
  "risk_rating": {"value": 8, "label": "Critical"},
  "action": "Close Now",
  "action_reason": "one sentence",
  "tp_recommendation": {"price": "0.038", "rationale": "..."},
  "sl_recommendation": {"price": "0.030", "rationale": "..."},
  "key_risks": ["risk 1", "risk 2", "risk 3"],
  "historical_context": "your past 12 BOME trades had 72% WR",
  "time_urgency": "Immediate",
  "summary": "2-3 sentence assessment"
}
```

**Analysis rules embedded in the prompt:**
- If `unrealized_pct < -30%` → seriously recommend Close Now or Partial Close
- If `stop_loss` is empty AND `unrealized_pct < -5%` → set SL immediately
- Reference actual numbers: entry, mark price, PnL%, TP/SL

**Cost:** ~$0.003 per analysis (~670 input + ~540 output tokens).

---

### `app.py` — Flask Web Server

Maps URLs to Python functions. All responses use the same envelope:
```json
{"ok": true,  "data": {...}}   // success
{"ok": false, "error": "..."}  // failure
```

**Complete API reference:**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Serve SPA (index.html) |
| GET | `/api/dashboard/kpis` | Dashboard KPI data + chart arrays |
| GET | `/api/positions` | Paginated position list with filters |
| POST | `/api/positions` | Create manual trade entry |
| GET | `/api/positions/<id>` | Single position detail |
| PUT | `/api/positions/<id>` | Edit notes, tags, or trade fields |
| DELETE | `/api/positions/<id>` | Delete a position |
| GET | `/api/analytics/deep` | Full deep-dive stats object |
| POST | `/api/ai/analyze` | Portfolio AI analysis (Claude) |
| GET | `/api/symbols` | List of distinct symbols |
| GET | `/api/wallet/history` | Wallet balance curve (downsampled) |
| POST | `/api/import` | Upload CSV or ZIP for import |
| GET | `/api/import/status` | Import log |
| POST | `/api/sync` | Trigger immediate Bitget API sync |
| GET | `/api/sync/status` | Sync state + account equity |
| GET | `/api/live/positions` | Real-time open positions from Bitget |
| POST | `/api/live/analyze` | Per-trade AI analysis for open position |

**Startup sequence:**
1. `init_db()` — create tables if not exist
2. If DB empty + CSVs in `data/` → auto-import
3. `bitget_sync.start_background_sync()` — start the 15-minute sync loop
4. `app.run()` — start Flask on port 8082

---

### `templates/index.html` — The Frontend (SPA)

One file, ~1,400 lines. Structured as:
```
<style>   All CSS (dark theme with CSS variables)
<body>
  <nav>   Sidebar with 7 nav items
  <div>
    sync-bar            Live sync status + Sync Now button
    <main>
      #page-dashboard   KPI cards + 4 charts + recent trades
      #page-journal     Filter bar + paginated table + modals
      #page-deep        6 analytics charts + stats tables
      #page-ai          Portfolio AI advisor
      #page-trades      Live open positions + per-trade AI
      #page-import      Drag-drop CSV upload
      #page-live        Sync status + account details
  #trade-modal   Add manual trade form
  #notes-modal   Edit notes/tags form
<script>  All JavaScript (~800 lines)
```

**SPA navigation pattern:**
```javascript
function showPage(name) {
  // hide all .page-view elements
  // show #page-<name>
  // mark #nav-<name> as active
  // call the load function for that page
}
```

**Live Trades module state management:**
- `livePositionsCache` — last fetched positions array
- `liveAnalysisCache` — AI results keyed by `SYMBOL_direction` string
- `liveOpenPanels` — Set of card indices with open AI panels

When auto-refresh fires (every 30 seconds), `renderPositionCards()` re-renders the HTML but then immediately re-injects any cached AI analyses and re-opens previously open panels. This is why clicking AI Analysis and then waiting for auto-refresh doesn't lose your results.

**Dark color scheme (CSS variables):**
```css
--bg:      #0f1117   page background
--bg2:     #1a1d2e   card background
--bg3:     #22263a   secondary surface
--accent:  #6c63ff   purple — primary action
--accent2: #4fc3f7   blue — neutral highlight
--accent3: #26d96b   green — profit
--red:     #ef5350   loss / danger
--yellow:  #ffb300   warning / medium risk
```

---

## The 7 Modules

### 1. Dashboard
Overview of the full 6-month trading history.
- **10 KPI cards:** Total Realized PnL, Total Fees, Win Rate, Profit Factor, Best Trade, Worst Trade, Avg Win, Avg Loss, Max Drawdown, Total Trades
- **Cumulative PnL curve** — line chart, all 808 closed trades
- **Wallet Balance History** — equity curve from transaction data
- **Top 5 Symbols by PnL** — bar chart
- **Win vs Loss** — doughnut chart
- **Recent 10 Trades** — table

### 2. Journal
Full trade management interface.
- **Filters:** Symbol, Direction (Long/Short), Result (Win/Loss), Date range, Free text search
- **Paginated table** — 50 trades/page, sortable columns
- **Click a row** → edit notes and tags inline
- **+ Add Trade** — manual entry form (symbol, direction, entry/exit price, size, PnL, fees, notes, tags)
- **Delete** — per-trade delete with confirmation

### 3. Deep Dive
Advanced pattern analysis.
- **P&L by Symbol** — horizontal bar chart, all 194 symbols
- **Monthly P&L** — bar chart per calendar month
- **P&L by Day of Week** — Mon–Sun breakdown
- **P&L by Open Hour (UTC)** — 0–23 hour heatmap-style chart
- **Long vs Short** — doughnut comparison
- **Trade Duration Breakdown** — `< 1h`, `1-4h`, `4-24h`, `1-7 days`, `> 7 days`
- **Key Stats pills:** win streaks, fee analysis
- **Full Symbol Table** — every symbol: trades, win rate, total/avg/best/worst PnL, fees
- **Worst Symbols** — bottom 5 loss leaders

### 4. AI Advisor
Full-portfolio Claude analysis.
- **"Analyze My Trading" button** → sends all stats to Claude, returns in ~15 seconds
- **Score card** (1–10 with color)
- **Strengths** — what the trader does well (with specific numbers)
- **Areas to Improve** — concrete weaknesses
- **Action Plan** — prioritized recommendations (High/Medium/Low) with specific actions and expected impact
- **Symbol Insights** — per-symbol observations
- **Risk Management** section
- **Mindset note**

### 5. Live Trades ⚡
Real-time open positions dashboard.
- **Auto-refreshes every 30 seconds** from Bitget API
- **4 summary KPIs:** Open Positions, Total Unrealized P&L, Margin In Use, Account Equity
- **Position cards** — one per open trade, sorted by worst loss first
  - Visual alerts: **NO SL** badge (red), **CRITICAL** badge with pulse animation (unrealized < -30%)
  - Shows: Symbol, Direction + Leverage badge, Size (USDT), Entry Price, Mark Price, Unrealized P&L + %, TP/SL prices, Duration open
  - Click card header → expands to show Break Even price, Liquidation price, Margin, Fees, Achieved profits
- **🤖 AI Analysis button** per trade:
  - Sends live position + 30-trade historical stats for that symbol to Claude
  - Returns in ~5 seconds
  - Shows: Risk Rating (1–10), Time Urgency chip, Action recommendation, TP/SL suggestions with rationale, Key Risks, Historical Context
  - Results cached — survive the 30-second auto-refresh (panel stays open, results preserved)
  - Button becomes **🔄 Re-analyze** after first analysis

### 6. Import Data
CSV upload interface.
- Drag-and-drop or click to upload
- Accepts: individual `.csv` files or `.zip` archive
- Auto-detects file type by filename keyword (`position history`, `order history`, `transactions`)
- Shows import history log

### 7. Live Sync 🔴
Sync status and connection details.
- Account Equity, Available Balance, Last Sync time, Next Sync time
- **Sync Now button** — triggers immediate sync
- Last sync result (new positions/orders/bills)
- Connection details (API key, interval, mode, permissions)

---

## Deployment

### Systemd Service

File: `/etc/systemd/system/trading-journal.service`

```ini
[Unit]
Description=Crypto Trading Journal
After=network.target

[Service]
Type=simple
User=fbauer
WorkingDirectory=/home/fbauer/trading-journal
Environment=PORT=8082
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 app.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### Common Operations

```bash
# Check status
systemctl status trading-journal

# View live logs
journalctl -u trading-journal -f

# Restart after code changes
sudo systemctl restart trading-journal

# Stop / start
sudo systemctl stop trading-journal
sudo systemctl start trading-journal
```

### Updating the App

```bash
# On your Mac: edit files, then sync to Pi
rsync -avz -e "ssh -i ~/.ssh/id_ed25519" \
  /Users/fbauer/Documents/ClaudeAIData/trading-journal/ \
  fbauer@192.168.1.21:/home/fbauer/trading-journal/ \
  --exclude='*.db' --exclude='*.pyc'

# On the Pi: restart
sudo systemctl restart trading-journal
```

### Re-importing CSV Data

```bash
ssh fbauer@192.168.1.21
cd /home/fbauer/trading-journal
rm trading_journal.db           # wipe existing
python3 importer.py data/       # reimport from CSVs
sudo systemctl restart trading-journal
```

### Adding a New Bitget Export

1. Go to Import tab in the UI
2. Drag-drop the ZIP file or individual CSVs
3. Duplicate detection handles overlapping records automatically

---

## Data Flow Diagram

```
Bitget Exchange
      │
      ├── CSV export (historical, one-time)
      │       └──► importer.py ──► SQLite positions table
      │
      └── REST API (ongoing, every 15 min)
              └──► bitget_client.py
                      ├──► bitget_sync.py ──► SQLite (new positions/orders/bills)
                      └──► /api/live/positions ──► Live Trades module (real-time, not cached)

SQLite DB
  ├── positions ──► analytics.py ──► Dashboard / Journal / Deep Dive
  ├── wallet_snapshots ──► equity curve charts
  └── settings ──► sync state, account balance

Claude API (claude-sonnet-4-6)
  ├── ai_advisor.py ──► Portfolio analysis (full stats)
  └── ai_live_trade.py ──► Per-trade analysis (live position + symbol history)
```

---

## Adding New Features

### New KPI on Dashboard
1. Calculate value in `analytics.py → get_dashboard_kpis()`, add to returned dict
2. Add entry to the `kpis` array in `loadDashboard()` in `index.html`

### New Chart on Deep Dive
1. Add `<canvas id="myChart">` in a `.chart-card` div in `#page-deep`
2. Call `makeChart('myChart', 'bar', {labels:[...], datasets:[...]})` in `loadDeep()`

### New Filter on Journal
1. Add HTML input/select to the filters bar
2. Read value in `journalLoad()`, add to `params` URLSearchParams
3. Add WHERE clause in `app.py → api_positions_list()`

### New REST Endpoint
```python
@app.route("/api/my-endpoint")
def api_my_endpoint():
    conn = get_conn()
    data = conn.execute("SELECT ... FROM positions").fetchall()
    conn.close()
    return _ok([dict(r) for r in data])
```

### Connect Bitget Automation (future)
The REST API is already automation-ready. To push new closed trades automatically:
```python
# POST new trade via API
requests.post("http://192.168.1.21:8082/api/positions", json={
    "symbol": "BTCUSDT",
    "direction": "Long",
    "open_time": "2026-06-01 10:00:00",
    "close_time": "2026-06-01 14:00:00",
    "entry_price": 105000,
    "close_price": 107500,
    "size_usdt": 500,
    "realized_pnl": 11.90,
    "total_fees": -0.48,
})
```

---

## Quick Reference

| Item | Value |
|------|-------|
| Live URL | http://192.168.1.21:8082 |
| Pi address | 192.168.1.21 |
| Pi user | fbauer |
| Pi project dir | /home/fbauer/trading-journal/ |
| Mac backup dir | /Users/fbauer/Documents/ClaudeAIData/trading-journal/ |
| Database | trading_journal.db (excluded from git) |
| Service name | trading-journal |
| Port | 8082 |
| Sync interval | 15 minutes |
| AI model | claude-sonnet-4-6 |
| Exchange | Bitget USDT-M Futures |
| Positions in DB | 808 (as of May 2026 export) |
| Symbols traded | 194 unique pairs |
