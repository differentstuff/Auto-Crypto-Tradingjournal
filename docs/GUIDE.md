# Crypto Trading Journal — Full Technical Reference

**Deployed on:** Raspberry Pi 5 (8GB), aarch64, Debian Bookworm  
**Built:** May 2026  
**Project path:** `/home/<your-user>/trading-journal/`

---

## What This Is

A full-stack web application for Bitget USDT-M Futures traders:

1. **Imports** Bitget CSV history (positions, orders, transactions)
2. **Syncs live** new closed trades every 15 minutes via Bitget API
3. **Analyzes** every open position with Claude AI on demand
4. **Analyzes** analyst trade calls before entering — with chart image vision, position sizing, scoring
5. **Tracks** pending limit orders as shadow trades (risk + correlation analysis)
6. **Runs forever** as a systemd service on Raspberry Pi 5

---

## Architecture

```
Browser (http://<your-pi-ip>:8082)
         │
         ▼
    Flask (app.py)              ← HTTP server + all API routes
    ├── database.py             ← SQLite schema + connection helpers
    ├── importer.py             ← Bitget CSV → SQLite (historical data)
    ├── analytics.py            ← KPI + stats calculations (pure Python)
    ├── ai_advisor.py           ← Full-portfolio Claude analysis
    ├── ai_live_trade.py        ← Per-trade Claude analysis for open positions
    ├── ai_call_analyzer.py     ← Analyst call analysis + pending limit analysis
    ├── bitget_client.py        ← Authenticated Bitget REST API v2 client
    ├── bitget_sync.py          ← Background sync thread (every 15 min)
    └── trading_journal.db      ← SQLite database (auto-created, excluded from git)

templates/index.html            ← Entire frontend: HTML + CSS + JavaScript (SPA, ~3000 lines)
static/                         ← Static assets (empty, CDN-loaded Chart.js)
data/                           ← CSV files for import
docs/GUIDE.md                   ← This file (technical)
docs/USER_GUIDE.md              ← User-facing manual
trading-journal.service         ← systemd service file
requirements.txt                ← Python dependencies
```

---

## Tech Stack

| Layer | Choice | Reason |
|-------|--------|--------|
| Language | Python 3.13 | Pre-installed on Pi, fast development |
| Web framework | Flask 3.1.3 | Simple, no boilerplate |
| Database | SQLite 3 (WAL mode) | Zero config, single file, built-in |
| Frontend | Pure HTML/CSS/JavaScript | No build step, SPA with page-view switching |
| Charts | Chart.js 4.4.0 (CDN) | One script tag, great defaults |
| AI | Anthropic claude-sonnet-4-6 | Best reasoning/vision model available |
| Exchange API | Bitget REST v2 | Read-only HMAC-SHA256 auth |
| Process manager | systemd | Auto-start on boot, auto-restart on crash |

---

## Database Schema (`database.py`)

### `positions` — Core trade data (one row per closed trade)

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | auto |
| symbol | TEXT | e.g. `BOMEUSDT` |
| base_asset | TEXT | e.g. `BOME` |
| direction | TEXT | `Long` or `Short` |
| margin_mode | TEXT | `Cross` or `Isolated` |
| open_time | TEXT | ISO datetime string |
| close_time | TEXT | ISO datetime string |
| duration_minutes | INTEGER | calculated: close − open |
| entry_price | REAL | avg open price |
| close_price | REAL | avg close price |
| size_contracts | TEXT | raw: `400000BOME` |
| size_usdt | REAL | position value USDT |
| position_pnl | REAL | gross PnL before fees |
| realized_pnl | REAL | net PnL (after fees) |
| opening_fee | REAL | |
| closing_fee | REAL | |
| total_fees | REAL | |
| notes | TEXT | user-editable freetext |
| tags | TEXT | comma-separated |
| is_manual | INTEGER | 1 = hand-entered |
| external_id | TEXT | Bitget positionId (dedup key) |
| created_at | TEXT | |
| updated_at | TEXT | |

### `orders` — Individual order fills (from Bitget order history CSV/API)

Key columns: `order_id` (UNIQUE dedup), `date`, `direction`, `symbol`, `avg_price`, `trading_volume`, `realized_pnl`, `position_id` (FK to positions).

### `wallet_snapshots` — Every account transaction event

Powers the wallet equity curve chart. Key columns: `date`, `symbol`, `type`, `amount`, `fee`, `wallet_balance`, `bill_id` (UNIQUE dedup for API sync).

### `settings` — Key/value store

Used by `bitget_sync.py` to persist: `last_sync_ms`, `account_equity`, `available_balance`.

### `analyzed_calls` — Saved call analyses

One row per analyst call the user analyzed and chose to save.

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| symbol | TEXT | |
| direction | TEXT | |
| call_text | TEXT | raw analyst call paste |
| entry_price | REAL | extracted from call |
| dca_price | REAL | |
| sl_price | REAL | |
| tp1_price | REAL | |
| tp2_price | REAL | |
| avg_entry | REAL | weighted avg of entry+DCA |
| total_notional | REAL | USDT notional |
| margin_needed | REAL | |
| risk_pct | REAL | |
| risk_amount | REAL | |
| leverage | INTEGER | |
| has_dca | INTEGER | 0/1 |
| has_candle_close_sl | INTEGER | 0/1 |
| setup_score | INTEGER | 1-10 |
| setup_label | TEXT | Poor/Weak/Moderate/Good/Strong/Excellent |
| rr_ratio | TEXT | e.g. `1:2.3` |
| trade_type | TEXT | Breakout/Trend Follow/Reversal etc |
| sl_warning | TEXT | instruction for candle-close SL |
| entry_timing | TEXT | |
| analysis_json | TEXT | full Claude JSON response |
| analyst | TEXT | source (e.g. "CryptoGuru") |
| status | TEXT | `saved` → `matched` → `closed` \| `dismissed` |
| matched_at | TEXT | when linked to a live position |
| outcome | TEXT | `won` / `lost` / `manual` |
| outcome_pnl | REAL | actual PnL when closed |
| hit_tp1 | INTEGER | 0/1 |
| hit_tp2 | INTEGER | 0/1 |
| hit_sl | INTEGER | 0/1 |
| outcome_at | TEXT | when outcome was recorded |
| actual_notional | REAL | actual trade size used |
| created_at | TEXT | |

### `pending_limits` — Shadow trades (limit orders not yet triggered)

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| call_id | INTEGER | FK to analyzed_calls (nullable) |
| symbol | TEXT | |
| direction | TEXT | `Long` or `Short` |
| limit_price | REAL | target entry price |
| size_usdt | REAL | notional at full fill |
| leverage | INTEGER | default 10 |
| sl_price | REAL | |
| tp1_price | REAL | |
| tp2_price | REAL | |
| analyst | TEXT | source of the signal |
| status | TEXT | `waiting` → `triggered` / `cancelled` |
| triggered_at | TEXT | when limit filled |
| analysis_json | TEXT | stored AI analysis blob |
| notes | TEXT | |
| created_at | TEXT | |

### `import_log` — Audit trail of all CSV imports

---

## `importer.py` — CSV Import

Parses Bitget USDT-M export CSVs. Handles two Bitget quirks:

1. **BOM prefix** — files start with invisible UTF-8 byte-order mark → `encoding='utf-8-sig'`
2. **Units in numbers** — values like `19.36USDT` or `400000BOME` → strip trailing letters with regex

Duplicate prevention: checks `(symbol, open_time, close_time)` uniqueness for positions.

File type detection by keyword in filename:
- `position history` → `positions` table
- `order history` → `orders` table
- `order details` → skipped (redundant)
- `transactions` → `wallet_snapshots` table

---

## `bitget_client.py` — Exchange API Client

HMAC-SHA256 authentication:
```
message   = timestamp + "GET" + path + "?" + query_string
signature = base64(hmac_sha256(secret_key, message))
Headers: ACCESS-KEY, ACCESS-SIGN, ACCESS-TIMESTAMP, ACCESS-PASSPHRASE
```

**Pagination rule (critical):** Bitget cursor-based pagination.
- Page 1: send `startTime` + `endTime` (max 90-day window)
- Page 2+: send **only** `endId` — **never** resend the time range (causes error 00001 if range > 90 days)

**Confirmed live API field names:**

*Position history:* `positionId`, `holdSide`, `openAvgPrice`, `closeAvgPrice`, `openTotalPos`, `pnl`, `netProfit`, `openFee`, `closeFee`, `totalFunding`, `marginMode`, `ctime`/`utime` (lowercase)

*Orders:* `orderId`, `priceAvg`, `quoteVolume`, `fee`, `totalProfits`, `side`, `posSide`, `tradeSide`, `orderSource`, `cTime`/`uTime` (uppercase)

*Bills:* `billId`, `symbol`, `amount`, `fee`, `businessType`, `balance`, `cTime`

*Open positions:* `symbol`, `holdSide`, `openPriceAvg`, `markPrice`, `unrealizedPL`, `marginSize`, `total`, `leverage`, `takeProfit`, `stopLoss`, `liquidationPrice`, `breakEvenPrice`, `achievedProfits`, `totalFee`, `cTime`

**Credentials (stored in bitget_client.py, overridable via env vars):**
- API Key: `bg_99c0e8528bf9d7c168c36e75466e1cbd` (read-only)
- `BITGET_PASSPHRASE` env var or hardcoded fallback

---

## `bitget_sync.py` — Background Sync

Daemon thread, runs inside Flask, syncs every 15 minutes.

**First-run logic:** Uses timestamp of most recent DB position as sync start point → no redundant 85-day backfill.

**Chunked time ranges:** All requests split into ≤89-day windows (Bitget rejects >90-day windows).

**Deduplication:** `external_id` for positions, `order_id` for orders, `bill_id` for bills.

**Thread safety:** `threading.Lock()` prevents concurrent syncs.

**Status dict** (module-level `_sync_status`):
```python
{
  "running": bool,
  "last_run": "2026-05-05 14:27 UTC",
  "last_result": {"positions": 2, "orders": 5, "bills": 8},
  "last_error": None,
  "next_run": "2026-05-05 16:42:49"
}
```

---

## `analytics.py` — KPI Calculations

Pure Python over SQLite. Two public functions:

**`get_dashboard_kpis(filters, conn)`** — returns dict with:
- `total_trades`, `win_trades`, `loss_trades`, `win_rate`
- `total_pnl`, `total_fees`, `net_pnl`
- `best_trade`, `worst_trade`, `avg_win`, `avg_loss`
- `profit_factor` = Σ(wins) / abs(Σ(losses))
- `max_drawdown` = largest peak-to-trough on cumulative PnL curve
- `pnl_curve` = `[{date, cumulative_pnl}]` sorted ascending
- `wallet_curve` = downsampled to ≤200 points
- `top_symbols` = top 5 by realized PnL
- `recent_trades` = last 10 closed positions
- `current_month_pnl` = this calendar month's PnL
- `current_win_streak`, `current_loss_streak`

**`get_deep_stats(filters, conn)`** — returns breakdowns by:
- Symbol (all, sorted by PnL)
- Month (calendar month)
- Weekday (Mon–Sun, `strftime('%w')`)
- Hour of day (0–23 UTC, based on open_time)
- Direction (Long vs Short)
- Duration buckets (`< 1h`, `1-4h`, `4-24h`, `1-7 days`, `> 7 days`)
- Streaks (max + current win/loss streaks)
- Fee analysis (total, avg, % of gross PnL)
- Worst 5 symbols

**Filter building:** `_build_where(filters)` returns parameterized `(where_clause, params)` — no SQL injection possible.

---

## `ai_advisor.py` — Portfolio AI Analysis

Sends full 6-month stats (~23k tokens) to Claude, returns structured trading assessment.

**Prompt:** Serializes `get_dashboard_kpis()` + `get_deep_stats()` to JSON. Instructs JSON-only response (no markdown). Strips code fences from response before parsing.

**Response schema:**
```json
{
  "overall_status": "paragraph",
  "score": {"value": 3, "label": "Developing"},
  "strengths": [{"title": "...", "detail": "..."}],
  "weaknesses": [{"title": "...", "detail": "..."}],
  "recommendations": [{"priority": "High", "title": "...", "action": "...", "expected_impact": "..."}],
  "symbol_insights": [{"symbol": "...", "insight": "..."}],
  "risk_management": "paragraph",
  "mindset_note": "sentence"
}
```

---

## `ai_live_trade.py` — Per-Trade AI Analysis

Analyzes a single **open** position with context: live position data + last 30 closed trades on that symbol.

**Response schema:**
```json
{
  "risk_rating": {"value": 8, "label": "Critical"},
  "action": "Close Now",
  "action_reason": "one sentence",
  "tp_recommendation": {"price": "0.038", "rationale": "..."},
  "sl_recommendation": {"price": "0.030", "rationale": "..."},
  "key_risks": ["...", "..."],
  "historical_context": "your past 12 BOME trades: 72% WR",
  "time_urgency": "Immediate",
  "summary": "2-3 sentence assessment"
}
```

---

## `ai_call_analyzer.py` — Call Analyzer + Pending Limit Analysis

The most complex AI module. Two public functions.

### `analyze_call(call_text, account_equity, image_b64, image_type, market_regime)`

**Price extraction:** Regex patterns for `entry at $X`, `dca: $X`, `sl under $X`, `@$X` etc. Falls back to highest/lowest price in text for entry/SL.

**Position sizing formula:**
```
base_risk_pct = 2.0% (DCA) or 1.0% (no DCA)
risk_multiplier = 0.25 (account ≤-20% from peak) | 0.5 (≤-10%) | 1.0 (normal)
risk_pct = base_risk_pct × risk_multiplier
risk_amount = equity × risk_pct / 100
stop_dist = (avg_entry − sl) / avg_entry
notional = risk_amount / stop_dist
margin = notional / leverage
```

**Pattern context injected into prompt:**
- Worst 3 weekdays by PnL
- Worst 3 hours (UTC) by PnL
- Direction performance (Long vs Short)
- Win/loss hold duration ratio
- Overall win rate + avg win/loss

**Scoring rules embedded in prompt:**
- R:R < 1:1.5 → cap score at 6/10 max
- Bear regime + Long direction → deduct 1-2 points
- Account in drawdown → "7/10 call becomes 5-6/10"
- Pattern violations pre-computed and injected as explicit checklist

**Response schema** (Claude returns pure JSON):
```json
{
  "symbol": "XYZUSDT",
  "direction": "Long",
  "trade_type": "Breakout",
  "has_dca": true,
  "has_candle_close_sl": false,
  "setup_quality": {"score": 7, "label": "Good"},
  "chart_analysis": "...",
  "risk_reward": {"ratio": "1:2.3", "entry": 0.0485, "sl": 0.041, "tp1": 0.057, "tp2": 0.068},
  "pattern_flags": ["Friday trade (your worst day)", "..."],
  "bitget_settings": {
    "symbol": "XYZUSDT", "direction": "Long / Buy",
    "margin_mode": "Cross", "leverage": "10x",
    "order_1": {"type": "Market", "notional_usdt": 1800, "note": "60% of position"},
    "order_2": {"type": "Limit", "price": "0.042", "notional_usdt": 1200, "note": "DCA"},
    "stop_loss": {"price": "0.041", "type": "Price SL", "bitget_instruction": "..."},
    "take_profit_1": {"price": "0.057", "note": "Resistance zone"},
    "take_profit_2": {"price": "0.068", "note": "Major resistance"}
  },
  "entry_timing": "...",
  "optimizations": ["...", "..."],
  "risks": ["...", "..."],
  "historical_context": "...",
  "sl_warning": "...",
  "summary": "..."
}
```

### `analyze_pending_limit(limit, account_equity, open_positions, other_limits)`

Assesses a pending limit order before it fills. Calculates: stop distance %, risk if SL hit (USDT + % of account), R:R to TP1, total pending exposure vs equity. Sends to Claude with open positions + other pending limits as correlation context.

**Response schema:**
```json
{
  "verdict": "Keep | Adjust | Cancel",
  "confidence": "High | Medium | Low",
  "setup_score": 7,
  "sizing_ok": true,
  "limit_price_assessment": "...",
  "sl_assessment": "...",
  "tp_assessment": "...",
  "correlation_risk": "...",
  "total_exposure_warning": "...",
  "adjustments": ["..."],
  "risks": ["..."],
  "summary": "..."
}
```

---

## `app.py` — Complete API Reference

All routes return: `{"ok": true, "data": {...}}` or `{"ok": false, "error": "..."}`.

### Core Data

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Serve SPA (index.html) |
| GET | `/api/dashboard/kpis` | Dashboard KPIs + chart arrays |
| GET | `/api/analytics/deep` | Deep-dive stats object |
| GET | `/api/symbols` | Distinct symbol list |
| GET | `/api/wallet/history` | Wallet balance curve (downsampled) |

### Positions (Trade Journal)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/positions` | Paginated list with filters: symbol, direction, date_from, date_to, search, pnl_side |
| POST | `/api/positions` | Create manual trade |
| GET | `/api/positions/<id>` | Single position |
| PUT | `/api/positions/<id>` | Edit notes, tags, analyst, prices |
| DELETE | `/api/positions/<id>` | Delete |

### Import / Sync

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/import` | Upload CSV or ZIP |
| GET | `/api/import/status` | Import log |
| POST | `/api/sync` | Trigger immediate Bitget sync |
| GET | `/api/sync/status` | Sync state + account equity |

### Live Positions

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/live/positions` | Real-time open positions from Bitget |
| POST | `/api/live/analyze` | Per-trade Claude analysis |

### AI Advisor

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/ai/analyze` | Full portfolio Claude analysis |

### Call Analyzer

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/calls/analyze` | Analyze a call (text + optional image) |
| POST | `/api/calls/save` | Save analysis to DB |
| GET | `/api/calls/saved` | List all saved calls |
| PATCH | `/api/calls/<id>` | Update editable fields: `analyst`, `notes` |
| GET | `/api/calls/check-matches` | Match saved calls against open positions |
| POST | `/api/calls/<id>/confirm-match` | Mark call as matched to live position |
| POST | `/api/calls/<id>/dismiss` | Dismiss a match |
| POST | `/api/calls/<id>/close` | Mark matched call as closed |
| DELETE | `/api/calls/<id>` | Delete call |
| POST | `/api/calls/<id>/record-outcome` | Record won/lost/manual + actual PnL, TP/SL hit |
| GET | `/api/calls/<id>/postmortem` | Rule-based loss postmortem |
| GET | `/api/calls/analyst-stats` | Per-analyst performance table |
| GET | `/api/calls/prediction-accuracy` | Score band → actual win rate |

### Pending Limit Orders

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/limits` | List limits by `?status=waiting\|triggered\|cancelled` |
| POST | `/api/limits` | Create pending limit order |
| PATCH | `/api/limits/<id>` | Update status, prices, notes, call_id |
| DELETE | `/api/limits/<id>` | Delete |
| GET | `/api/limits/risk-summary` | Total notional if all waiting limits fill |
| POST | `/api/limits/bulk-update` | Bulk update multiple limits: `{ids:[...], sl_price?, tp1_price?, tp2_price?, call_id?, status?}` |
| POST | `/api/limits/<id>/analyze` | Run Claude analysis on a pending limit |

### Live Bitget Feed

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/live/positions` | Real-time open positions from Bitget |
| GET | `/api/live/pending-orders` | Unfilled limit orders from Bitget + `tracked_ids` set |
| POST | `/api/live/analyze` | Per-trade Claude analysis |

---

## Frontend (`templates/index.html`)

Single-file SPA, ~3000 lines. Structure:
```
<style>   Dark theme CSS (CSS variables throughout)
<body>
  <nav>   Sidebar: 9 navigation items
  sync-bar    Live sync status + Sync Now button
  <main>
    #page-dashboard    KPI cards + 4 charts + target tracker + streak
    #page-journal      Filter bar + paginated table + notes/trade modals
    #page-deep         6 analytics charts + symbol/fee tables
    #page-ai           Portfolio AI advisor
    #page-calls        Call Analyzer + saved calls + analyst stats
    #page-trades       Live open positions + per-trade AI
    #page-import       Drag-drop CSV upload
    #page-live         Bitget sync status + account details
    #page-pending      Pending limit orders + risk summary
  Modals:
    #trade-modal       Add/edit manual trade
    #notes-modal       Edit trade — analyst, notes, tags
    #outcome-modal     Record call outcome (won/lost/manual)
    #limit-modal       Add/edit pending limit order
    #match-modal       Track Bitget live order as shadow trade (+ link to call)
    #bulk-link-modal   Link multiple selected limits to one analyst call
<script>  All JavaScript
```

**Navigation pattern:**
```javascript
showPage(name) → hide all .page-view → show #page-<name> → mark nav active → call load function
```

Special pages handled by `showPage()` override: `['live', 'trades', 'calls', 'pending']`

**Key JS globals:**
- `currentPage` — active page name
- `livePositionsCache` — last fetched open positions
- `liveAnalysisCache` — AI results keyed by `"SYMBOL_direction"` (survives auto-refresh)
- `liveOpenPanels` — Set of card indices with open AI panels
- `liveCallMatches` — matched calls keyed by `"SYMBOL_direction"`
- `_deepStatsCache` — cached `/api/analytics/deep` result (fetched once per Call Analyzer load)
- `_lastCallResult` — latest call analysis result (for saving)
- `currentLimitStatus` — active filter tab on Pending Orders page (`waiting`/`triggered`/`cancelled`)
- `selectedLimitIds` — Set of pending limit IDs selected for bulk operations
- `_bitgetOrdersCache` — last fetched live Bitget orders (for match modal pre-fill)
- `_matchOrderData` — current order/limit being tracked in match modal

**Auto-refresh:** Live Trades auto-refreshes every 30s via `liveTradesInterval`. AI analysis results and open panels are preserved across refreshes using the cache globals.

**Color scheme:**
```css
--bg:      #0f1117   page background
--bg2:     #1a1d2e   card background  
--bg3:     #22263a   secondary surface
--accent:  #6c63ff   purple (primary action)
--accent2: #4fc3f7   blue (neutral highlight)
--accent3: #26d96b   green (profit)
--red:     #ef5350   loss / danger
--yellow:  #ffb300   warning / medium risk
```

---

## Deployment

### systemd Service

File on Pi: `/etc/systemd/system/trading-journal.service`
Source: `trading-journal.service` in project root

```ini
[Unit]
Description=Crypto Trading Journal
After=network.target

[Service]
Type=simple
# Replace <your-user> with your Linux username
User=<your-user>
WorkingDirectory=/home/<your-user>/trading-journal
EnvironmentFile=/home/<your-user>/trading-journal/.env
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 app.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Credentials are loaded via `EnvironmentFile=` from `.env` (gitignored). Copy `.env.example` to `.env` and fill in values before starting the service.

### Passwordless sudo (optional but recommended)

Allows remote `sudo systemctl restart` without a password prompt. Run once on the Pi:

```bash
echo '<your-user> ALL=(ALL) NOPASSWD: ALL' | sudo tee /etc/sudoers.d/99-<your-user>-nopasswd
sudo chmod 440 /etc/sudoers.d/99-<your-user>-nopasswd
```

After this, `sudo systemctl restart trading-journal` works from non-interactive SSH sessions.

### Git identity (important for contributors)

Set git identity before making any commits, otherwise git falls back to the OS username:

```bash
git config --global user.name "your-github-username"
git config --global user.email "your-github-username@users.noreply.github.com"
```

This persists across all repos on the machine. Without it, every commit leaks the OS username even if a repo-level config was previously set (e.g. after running `git filter-repo`, which reinitialises the repo and clears local config).

### GitHub repository security settings

Configured on the public repo (`anvilfilbert/Auto-Crypto-Tradingjournal`):

| Setting | Value |
|---------|-------|
| Merge strategy | Squash merge only (merge commits + rebase disabled) |
| Delete branch on merge | Enabled |
| Branch protection | CodeQL must pass before any merge to `main` |
| Dependabot | Weekly `pip` dependency updates via `.github/dependabot.yml` |
| Secret scanning | Enabled — alerts on any committed credentials |
| Wiki / Projects | Disabled |

**Secret scanning incident (resolved):** An old Anthropic API key was found in an early commit blob. The key was already revoked by Anthropic before discovery. Git history had been scrubbed with `git filter-repo`. Alert closed as `revoked`.

**Rule:** never commit credentials. All keys live in `.env` (gitignored, mode 600), loaded via systemd `EnvironmentFile=`.

### Python Dependencies (`requirements.txt`)

```
flask>=3.1.3
anthropic>=0.100.0
```

Bumped May 2026 via Dependabot PRs #5 and #6. Both packages installed on Pi and service verified running after update.

---

## Restore From Scratch

See `docs/USER_GUIDE.md` for usage documentation.

### Complete restore procedure (new Pi or new machine):

**1. Install Python dependencies**
```bash
pip3 install flask anthropic requests
```

**2. Clone from GitHub**
```bash
git clone https://github.com/anvilfilbert/Auto-Crypto-Tradingjournal.git trading-journal
cd trading-journal
```

**3. Configure credentials**
```bash
cp .env.example .env
# Edit .env — fill in BITGET_API_KEY, BITGET_SECRET_KEY, BITGET_PASSPHRASE, ANTHROPIC_API_KEY
```

**4. Initialize DB**
```bash
python3 database.py          # creates trading_journal.db with all tables
```

**5. Test the app manually first**
```bash
python3 app.py
# Open http://<host>:8082 in browser — confirm it loads
# Ctrl+C to stop
```

**6. Install systemd service**
```bash
sudo cp trading-journal.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable trading-journal
sudo systemctl start trading-journal
systemctl status trading-journal
```

**7. Verify**
```bash
curl -s http://localhost:8082/api/dashboard/kpis | python3 -m json.tool | head -20
curl -s http://localhost:8082/api/sync/status | python3 -m json.tool
```

### Common Operations

```bash
# Logs
journalctl -u trading-journal -f

# Restart after code update
sudo systemctl restart trading-journal

# Deploy code update from a remote machine
rsync -avz -e ssh \
  /path/to/local/trading-journal/ \
  <your-user>@<your-pi-ip>:/home/<your-user>/trading-journal/ \
  --exclude='*.db' --exclude='*.pyc'
ssh <your-user>@<your-pi-ip> sudo systemctl restart trading-journal

# Wipe and reimport DB
ssh <your-user>@<your-pi-ip> "cd /home/<your-user>/trading-journal && \
  rm trading_journal.db && \
  python3 importer.py data/ && \
  sudo systemctl restart trading-journal"

# Add new Bitget export via API
curl -X POST http://<your-pi-ip>:8082/api/import \
  -F "file=@/path/to/export.zip"
```

---

## Round 3 UI Features (added May 2026)

### 1. Analyst Inline Edit (Call Analyzer page)

Each saved call row shows a small ✏ button next to the analyst badge.

**Flow:**
1. Click ✏ → `editCallAnalyst(callId, currentAnalyst)` replaces the tag with an `<input>` pre-filled with the current name
2. User types new name → Enter or "Save" → `saveCallAnalyst(callId)` → `PATCH /api/calls/<id>` → `loadSavedCalls()`
3. Escape or ✕ → `loadSavedCalls()` (resets to original)

**Backend:** `PATCH /api/calls/<id>` accepts `{analyst, notes}` — any subset.

### 2. Bulk Selection (Pending Orders page)

On the **Waiting** tab, each pending limit card shows a checkbox in the top-left corner.

**Selection state:** `selectedLimitIds` (JavaScript `Set<number>`) — cleared when switching tabs or after any bulk action.

**Bulk action bar:** Appears sticky at the bottom of the page when `selectedLimitIds.size > 0`.

| Button | Action | Endpoint |
|--------|--------|----------|
| Set SL | `prompt()` for price → applies to all selected | `POST /api/limits/bulk-update` `{ids, sl_price}` |
| Set TP1 | Same for tp1_price | same |
| Set TP2 | Same for tp2_price | same |
| 🔗 Link to Call | Opens `#bulk-link-modal` → pick call → apply call_id | `POST /api/limits/bulk-update` `{ids, call_id}` |
| ✕ Cancel All | Confirm → set all to cancelled | `POST /api/limits/bulk-update` `{ids, status:'cancelled'}` |
| ✕ Clear | Deselects all (no API call) | — |

**Helper functions:** `toggleLimitSelection(id, checked)`, `updateBulkBar()`, `clearBulkSelection()`, `bulkSetField(field, label)`, `bulkCancel()`, `bulkLinkCall()`, `selectBulkCall(callId)`, `confirmBulkLink()`, `closeBulkLinkModal()`.

### 3. Bulk Link to Analyst Call

**`#bulk-link-modal`:** Shows count of selected limits + scrollable call picker (same style as match modal). Picking a call highlights it with an accent border. "Link Selected" button calls `confirmBulkLink()` → bulk-update → clears selection.

**Backend:** `POST /api/limits/bulk-update` with `{ids: [...], call_id: N}` — sets `call_id` on all specified rows in one `UPDATE ... WHERE id IN (...)` statement.

### 4. Analyst Field on Journal Rows (added May 2026)

### 5. Recent Trades totals footer (added May 2026)

The recent trades table on the Dashboard now has a `<tfoot>` row summing realized P&L and fees across the displayed trades (last 10).

**Code:** `templates/index.html`, `loadDashboard()` — computed after building tbody, written to `#recent-tfoot`.

### 6. Open Position Risk — SL-based calculation (added May 2026)

The **Open Position Risk** KPI on the Dashboard now shows true dollar risk to stop-loss, not margin locked.

**Formula:**
- Long: `risk = (entry_price − stop_loss) / entry_price × size_usdt`
- Short: `risk = (stop_loss − entry_price) / entry_price × size_usdt`
- No SL set: falls back to `margin_usdt` (collateral at risk)

Sub-label shows `· SL-based` or `· no SL` to indicate which mode is active.

**Code:** `templates/index.html`, `loadDashboard()` → async `/api/live/positions` call.

The `positions` table has an `analyst` column (migrated automatically at startup if missing). The journal table now shows an **Analyst** column (`📡 Name` or `—`).

**Flow:** Click any journal row → "Edit Trade" modal → Analyst input at top → Save → `PUT /api/positions/<id>` with `{analyst: "..."}`. Works on all trades including historical ones.

**Backend:** `PUT /api/positions/<id>` editable fields: `notes, tags, analyst, entry_price, close_price, size_usdt, realized_pnl, total_fees, open_time, close_time, direction`.

**Migration guard** in `app.py` startup:
```python
if "analyst" not in cols:
    conn.execute("ALTER TABLE positions ADD COLUMN analyst TEXT DEFAULT ''")
```

### 7. Security fixes (May 2026) — CodeQL alerts resolved

Six CodeQL alerts were found and resolved:

| # | Rule | File | Fix |
|---|------|------|-----|
| 1 & 2 | `py/stack-trace-exposure` | `bitget_sync.py`, `app.py` | `str(e)` in sync error result replaced with generic string; all `_err(str(e), 500)` calls replaced with `"Internal server error"` |
| 3 & 4 | `py/path-injection` | `app.py` | Uploaded filename sanitized with `werkzeug.utils.secure_filename()` before `os.path.join()` |
| 5 & 6 | `py/sql-injection` | `analytics.py` | Dismissed as false positive — `_build_where()` only interpolates hardcoded SQL fragments into the query string; user values go into bound `?` params only. Added allowlist validation (symbol `[A-Z0-9]+`, direction `Long`/`Short`, dates `YYYY-MM-DD`) for extra safety |

---

## Quick Reference

| Item | Value |
|------|-------|
| Live URL | http://`<your-pi-ip>`:8082 |
| GitHub | https://github.com/anvilfilbert/Auto-Crypto-Tradingjournal |
| Project dir | /home/`<your-user>`/trading-journal/ |
| Credentials file | `.env` in project root (gitignored, mode 600) |
| Database file | trading_journal.db (excluded from git, DO NOT wipe without backup) |
| Service name | trading-journal |
| Port | 8082 (configurable via PORT in .env) |
| Sync interval | 5 minutes automatic + manual Sync Now |
| AI model | claude-sonnet-4-6 |
| Exchange | Bitget USDT-M Futures |
| Anthropic key | `ANTHROPIC_API_KEY` in `.env` |
| Bitget keys | `BITGET_API_KEY`, `BITGET_SECRET_KEY`, `BITGET_PASSPHRASE` in `.env` |

---

## Data Flow

```
Bitget Exchange
  ├── CSV export (historical, one-time)
  │       └──► importer.py ──► SQLite positions/orders/wallet_snapshots
  │
  └── REST API (ongoing, every 15 min)
          └──► bitget_client.py
                  ├──► bitget_sync.py ──► SQLite (new positions, orders, bills)
                  └──► /api/live/positions ──► Live Trades (real-time, not cached in DB)

SQLite DB
  ├── positions ──► analytics.py ──► Dashboard / Journal / Deep Dive
  ├── wallet_snapshots ──► equity curve + drawdown calculation
  ├── analyzed_calls ──► Call Analyzer / Analyst Stats / Prediction Accuracy
  ├── pending_limits ──► Pending Orders / Risk Summary
  └── settings ──► sync state, account balance

Claude API (claude-sonnet-4-6)
  ├── ai_advisor.py ──► Portfolio analysis (~$0.02/call)
  ├── ai_live_trade.py ──► Per-trade analysis (~$0.003/call)
  └── ai_call_analyzer.py
        ├── analyze_call() ──► Call analysis with vision (~$0.02/call)
        └── analyze_pending_limit() ──► Limit order analysis (~$0.005/call)
```

---

## Adding New Features

### New DB column (safe migration pattern)
```python
# In init_db(), after CREATE TABLE:
try:
    cur.execute("ALTER TABLE my_table ADD COLUMN new_col TEXT DEFAULT ''")
except sqlite3.OperationalError:
    pass  # column already exists
```

### New API endpoint
```python
@app.route("/api/my-endpoint")
def api_my_endpoint():
    conn = get_conn()
    data = [dict(r) for r in conn.execute("SELECT ... FROM positions").fetchall()]
    conn.close()
    return _ok(data)
```

### New KPI on Dashboard
1. Calculate in `analytics.py → get_dashboard_kpis()`, add to returned dict
2. Add to `kpis` array in `loadDashboard()` JS function in `index.html`

### New Chart
1. Add `<canvas id="myChart">` in a `.chart-card` in the target page
2. Call `makeChart('myChart', 'bar', {labels:[...], datasets:[...]})` in the load function

### Push new trade via API (automation)
```python
requests.post("http://<your-pi-ip>:8082/api/positions", json={
    "symbol": "BTCUSDT", "direction": "Long",
    "open_time": "2026-06-01 10:00:00", "close_time": "2026-06-01 14:00:00",
    "entry_price": 105000, "close_price": 107500,
    "size_usdt": 500, "realized_pnl": 11.90, "total_fees": -0.48,
})
```
