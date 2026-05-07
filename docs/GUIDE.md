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
    Flask (app.py)              ← ~50 lines: blueprint registration + startup
    ├── helpers.py              ← Shared _ok(), _err(), _filters_from_args()
    ├── database.py             ← SQLite schema, get_conn(), db_conn() context manager
    │
    ├── routes/                 ← Flask Blueprints — one file per domain
    │   ├── journal.py          ← positions CRUD, symbols, wallet, import
    │   ├── analytics.py        ← dashboard KPIs, deep dive, heatmap, patterns, R:R, market
    │   ├── calls.py            ← call analyzer, saved calls, outcomes, analyst stats
    │   ├── limits.py           ← pending limit orders
    │   ├── live.py             ← live positions, pending orders, live AI analysis
    │   └── sync.py             ← sync trigger, sync status, AI advisor
    │
    ├── importer.py             ← Bitget CSV → SQLite (historical data)
    ├── analytics.py            ← KPI + stats calculations (pure Python)
    ├── ai_advisor.py           ← Full-portfolio Claude analysis
    ├── ai_live_trade.py        ← Per-trade Claude analysis for open positions
    ├── ai_call_analyzer.py     ← Analyst call analysis + pending limit analysis
    ├── ai_trade_grader.py      ← Auto-grade closed trade execution via Claude
    ├── ai_pattern_detector.py  ← Detect statistical patterns in trade history via Claude
    ├── ai_rulebook.py          ← Self-learning personalised rulebook (Claude synthesises rules from trade history)
    ├── market_context.py       ← Fear & Greed, funding rate, long/short ratio (5-min cache)
    ├── chart_context.py        ← OHLCV candles + pandas-ta indicator suite (10-min cache)
    ├── bitget_client.py        ← Authenticated Bitget REST API v2 client
    ├── bitget_sync.py          ← Background sync thread (every 15 min)
    └── trading_journal.db      ← SQLite database (auto-created, excluded from git)

templates/index.html            ← Frontend: HTML structure only (~910 lines)
templates/chart.html            ← Detached chart window (LightweightCharts, S/R boxes, trendlines, liquidation levels)
static/style.css                ← All dark-theme CSS (extracted from index.html)
static/app.js                   ← Legacy entry point (kept for cache compat); JS now split into static/js/
static/js/01-utils.js           ← Globals, openChart, S/R overlay, symbol picker, nav, helpers, makeChart, tooltip engine
static/js/02-dashboard.js       ← Dashboard KPIs, charts, streak
static/js/03-journal.js         ← Journal table, add/edit modals
static/js/04-deep-edge.js       ← Deep Dive, Edge Lab, heatmap, pattern detector, rulebook
static/js/05-advisor.js         ← AI Advisor
static/js/06-import.js          ← CSV import + sync page
static/js/07-calls.js           ← Call Analyzer, saved calls, analyst stats
static/js/08-live.js            ← Live Trades, position cards, correlation warning
static/js/09-analysis.js        ← Prediction accuracy, postmortem, call sizing
static/js/10-pending.js         ← Pending limits, Bitget live orders, bulk operations
static/js/11-sync.js            ← Live Sync page
static/js/12-explorer.js        ← Chart Explorer (inline LightweightCharts)
static/js/13-init.js            ← showPage extension, app startup
data/                           ← CSV files for import
docs/GUIDE.md                   ← This file (technical)
docs/USER_GUIDE.md              ← User-facing manual
docs/RATING_CRITERIA.md         ← All AI scoring/grading criteria documented
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
| Dashboard charts | Chart.js 4.4.0 (CDN) | One script tag, great defaults |
| Candlestick charts | LightweightCharts v4.1.3 (CDN) | TradingView library, interactive OHLCV charts |
| Technical analysis | pandas-ta | Indicator suite computed server-side |
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
| analyst | TEXT | signal source (e.g. "CryptoGuru") |
| is_manual | INTEGER | 1 = hand-entered |
| setup_type | TEXT | Breakout / Pullback / Trend Continuation / Range Fade / Reversal / News-Event / Other |
| call_id | INTEGER | FK → analyzed_calls.id (links trade to analyst call for R:R analysis) |
| execution_grade | TEXT | A / B / C / D — Claude-assigned execution quality grade |
| execution_grade_reason | TEXT | Claude's written explanation for the grade |
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

### `trader_rulebook` — Personalised AI-generated rules

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | auto |
| rule_type | TEXT | `warning`, `strength`, `habit`, `calibration` |
| title | TEXT | ≤7 words |
| rule | TEXT | 1–2 sentences with specific numbers from trade data |
| confidence | TEXT | `high`, `medium`, `low` |
| data_points | INTEGER | number of trades behind this rule |
| generated_at | TEXT | UTC datetime of last generation |

Cleared and fully regenerated on each `POST /api/rulebook/update`. Auto-updated weekly by `bitget_sync.py`.

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

## Route Blueprints

Routes are split across `routes/` — registered in `app.py` at startup. All blueprints share `helpers.py` (`_ok`, `_err`, `_filters_from_args`) and `database.db_conn()`.

| Blueprint | File | Domain |
|-----------|------|--------|
| `journal` | `routes/journal.py` | Positions CRUD, import, symbols, wallet history |
| `analytics` | `routes/analytics.py` | Dashboard KPIs, deep dive, heatmap, patterns, R:R, market data, chart routes |
| `calls` | `routes/calls.py` | Call analyzer, saved calls, outcomes, analyst stats |
| `limits` | `routes/limits.py` | Pending limit orders |
| `live` | `routes/live.py` | Live Bitget positions, pending orders, per-trade AI |
| `sync` | `routes/sync.py` | Sync trigger, sync status, AI advisor, rulebook |

## API Reference

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

### Trader Rulebook

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/rulebook` | Fetch current rules from DB |
| POST | `/api/rulebook/update` | Regenerate rules via Claude + persist to DB |

### Chart

| Method | Path | Description |
|--------|------|-------------|
| GET | `/chart` | Serve `templates/chart.html` (detached chart window) |
| GET | `/api/chart/candles` | OHLCV candles + S/R levels + multi-TF trendlines. Params: `symbol`, `timeframe` (15m/1H/4H/1D), `limit` (default 200). Returns `{candles, levels, trendlines, current_price, symbol, timeframe}` where `trendlines` includes all TFs (1W/1D/4H/1H), each tagged with `timeframe` and `weight` |
| GET | `/api/chart/indicators` | Technical indicator values. Params: `symbol`, `timeframes` (comma-separated, e.g. `4H,1D`). Returns per-timeframe indicator dict |
| GET | `/api/exchange/symbols` | Full Bitget USDT-M Futures symbol list (~200+). Sourced from Bitget `/api/v2/mix/market/tickers?productType=USDT-FUTURES`. 1-hour server-side cache. Returns `{ok: true, symbols: ["BTCUSDT", ...]}` |
| GET | `/api/market/prices` | Current mark prices for a list of symbols. Params: `symbols` (comma-separated). 60-second cache. Returns `{ok: true, data: {"BTCUSDT": 80960.1, ...}}` |

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

## Frontend

Split across four files:
- `templates/index.html` — HTML structure only (~910 lines), no inline CSS
- `templates/chart.html` — standalone detached chart window (self-contained, no shared CSS/JS)
- `static/style.css` — all dark-theme CSS (~195 lines), loaded via `<link>`
- `static/js/` — JavaScript split into 13 topic files (see file listing above). `index.html` loads them all as regular `<script>` tags; all functions remain in global scope so `onclick` attributes work unchanged.

Structure:
```
<link rel="stylesheet" href="/static/style.css">
<body>
  <nav>   Sidebar: 10 navigation items (includes Chart Explorer)
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
    #page-charts       Chart Explorer — symbol input, TF buttons, LightweightCharts canvas, indicator panel
  Modals:
    #trade-modal       Add/edit manual trade
    #notes-modal       Edit trade — analyst, notes, tags
    #outcome-modal     Record call outcome (won/lost/manual)
    #limit-modal       Add/edit pending limit order
    #match-modal       Track Bitget live order as shadow trade (+ link to call)
    #bulk-link-modal   Link multiple selected limits to one analyst call
<script src="/static/js/01-utils.js"> ... <script src="/static/js/13-init.js">
```

**Navigation pattern:**
```javascript
showPage(name) → hide all .page-view → show #page-<name> → mark nav active → call load function
```

Special pages handled by `showPage()` override: `['live', 'trades', 'calls', 'pending', 'charts']`

When `charts` activates: `_initExplorerTfBtns()` is called to populate TF buttons. Symbol input uses the searchable picker (see Symbol Picker section below) — no separate datalist is needed.

**Key JS globals:**
- `currentPage` — active page name
- `livePositionsCache` — last fetched open positions (also used by Chart Explorer for liquidation levels)
- `liveAnalysisCache` — AI results keyed by `"SYMBOL_direction"` (survives auto-refresh)
- `liveOpenPanels` — Set of card indices with open AI panels
- `liveCallMatches` — matched calls keyed by `"SYMBOL_direction"`
- `_deepStatsCache` — cached `/api/analytics/deep` result (fetched once per Call Analyzer load)
- `_lastCallResult` — latest call analysis result (for saving)
- `currentLimitStatus` — active filter tab on Pending Orders page (`waiting`/`triggered`/`cancelled`)
- `selectedLimitIds` — Set of pending limit IDs selected for bulk operations
- `_bitgetOrdersCache` — last fetched live Bitget orders (for match modal pre-fill)
- `_matchOrderData` — current order/limit being tracked in match modal
- `_explorerChart` — current LightweightCharts instance in Chart Explorer (null when no chart drawn)
- `_explorerTf` — active timeframe in Chart Explorer (default `'4H'`)

**Auto-refresh:** Live Trades auto-refreshes every 30s via `liveTradesInterval`. AI analysis results and open panels are preserved across refreshes using the cache globals.

**Dashboard parallel fetch:** `loadDashboard()` fires three requests simultaneously via `Promise.all`: `/api/dashboard/kpis`, `/api/market/context`, `/api/live/positions`. The Open Position Risk KPI card renders from the already-available positions data rather than a second round-trip.

**Live Trades cross-page awareness:** `loadLiveTrades()` fetches `/api/limits?status=waiting` in the same `Promise.all` as positions. `renderPositionCards(positions, waitingLimits)` shows a `⏳ N limit(s)` chip on any card whose symbol has waiting limits; clicking it navigates to Pending Orders.

**Proximity alerts:** After `loadPendingLimits('waiting')` renders cards, it calls `/api/market/prices?symbols=...` for all waiting limit symbols. Each card within 5% of current mark price gets a `📍 X.X% from limit` badge (red <1%, yellow <3%, blue <5%) injected into the `#prox-<id>` span on that card.

**Chart Explorer title overlay:** `drawExplorerChart()` injects a `.explorer-chart-title` `<div>` absolutely positioned top-left inside `#explorer-chart-wrap`, showing the symbol name and active timeframe pill. Rebuilt on every draw; removed on chart destroy.

**Mouseover tooltip engine (`01-utils.js`):** A floating `#tip` div is created once at startup and appended to `<body>`. Three document-level event listeners handle show/hide/position: `mouseover` shows the tip for the nearest ancestor with `data-tip="..."`, `mouseout` hides it, `mousemove` repositions it cursor+14px with viewport-edge clamping (flips side when the tip would overflow). CSS: `#tip { position:fixed; z-index:9999; opacity:0; transition:opacity .15s }` + `#tip.visible { opacity:1 }`. Any element can opt in by adding `data-tip="explanation text"`.

KPI tooltips:
- **Dashboard KPIs** (`02-dashboard.js`): all 11 KPI cards (including the dynamic Open Position Risk card) have `data-tip` attributes explaining the metric, how to interpret good/bad values, and any formula notes.
- **Live Trades KPIs** (`08-live.js`): all 5 KPI cards in `renderLiveKpis()` have `data-tip` including the Open Position Risk card.
- **Chart Explorer indicators** (`12-explorer.js`): `card(label, value, sub, color, tip)` helper accepts an optional 5th arg; all 9 indicator cards (RSI, MACD, EMA, Bollinger, ADX, Stoch RSI, ATR, Volume, Key S/R) have tooltip text explaining signal thresholds and how to act on the value.

### Chart Window (`openChart()`)

```javascript
function openChart(symbol, tf = '4H') {
  // read liquidation prices from livePositionsCache for this symbol
  const liqs = (livePositionsCache || [])
    .filter(p => p.symbol === symbol && p.liquidation_price)
    .map(p => ({ price: parseFloat(p.liquidation_price), label: p.direction }));
  let url = `/chart?symbol=${encodeURIComponent(symbol)}&timeframe=${tf}`;
  if (liqs.length) url += '&liqs=' + encodeURIComponent(JSON.stringify(liqs));
  window.open(url, `chart_${symbol}`, 'width=1060,height=680,resizable=yes,...');
}
```

Named windows (`chart_${symbol}`) reuse the same browser window on repeat calls, preventing clutter.

### Canvas Overlay (`_startSrOverlay()`)

Shared helper used by both Chart Explorer and `chart.html`:

```javascript
function _startSrOverlay(wrap, series, levels, liquidations) {
  // Creates an absolutely-positioned <canvas> over the LightweightCharts container
  // RAF loop: redraws on every frame → stays in sync with pan/zoom
  // Auto-stops when canvas is removed from DOM (document.contains check)
  // Draws: grey boxes for S/R (opacity = min(0.07+(touches-1)*0.035, 0.42))
  //        yellow dashed lines for liquidation levels with bold labels
  // Right ~65px left uncovered to keep price-scale labels readable
}
```

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

### Symbol Picker (`_attachSymbolPicker(inputId)`)

Searchable dropdown attached to every coin-name input: `#explorer-symbol` (Chart Explorer), `#m-symbol` (Add Trade modal), `#lm-symbol` (Log Manual Trade modal).

**Two variants** are needed because `.modal { overflow-y:auto }` clips absolutely-positioned children:

| Context | CSS class | Positioning | Parent |
|---------|-----------|-------------|--------|
| Non-modal inputs | `.sym-drop-abs` | `position:absolute; top:calc(100%+3px)` | `.sym-wrap` wrapper (`position:relative`) |
| Modal inputs | `.sym-drop-fixed` | `position:fixed` with `getBoundingClientRect()` coords | `document.body` |

Detection: `const inModal = !!inp.closest('.modal')` — picks the variant automatically.

**Data sources (two-tier):**
1. `symbolList` (journal symbols, available immediately) — used as fallback on first render
2. `_exchangeSymbols` (full Bitget list, ~200+) — loaded async via `_loadExchangeSymbols()` → `GET /api/exchange/symbols` at startup; open dropdowns refresh automatically when it arrives

**Live filtering:** `drop._render(q)` filters the merged list, highlights matches with `_hlMatch(str, q)` (wraps matched chars in `<b>`), and renders up to 50 results.

**Cache busting:** script tags use `?v=2.2` query strings (e.g. `<script src="/static/js/01-utils.js?v=2.2">`) — bump the version across all 13 `<script>` tags in `index.html` when deploying JS changes so browsers don't serve stale code.

---

## `chart_context.py` — Candle Data, S/R & Trendlines

Fetches OHLCV candles from Bitget (no extra API key — reuses existing Bitget auth) and computes a full technical analysis suite server-side via `pandas-ta`. Results are cached in memory for 10 minutes per `(symbol, timeframe)`.

### `detect_support_resistance(df, n_swing=5, tolerance_pct=0.003, max_levels=8)`

Swing-pivot S/R detection:
1. Identifies local highs (high > all neighbours within `n_swing` candles) and local lows
2. Clusters pivots within `tolerance_pct` (0.3%) of each other
3. Counts touches per cluster, sorts by touch count descending, keeps top `max_levels`
4. Returns `[{price, type ('support'|'resistance'), strength, touches}, ...]`

### `detect_trendlines(df, n_swing=5, max_lines=4, now_time_sec=None)`

Ascending support / descending resistance line detection for a single timeframe:
1. Finds the same swing pivots as S/R detection
2. For each pair of pivots of the same type, computes the implied slope (candle-index based, for validation)
3. Validates: no candle between the two anchors may violate the line by more than 0.5%
4. Extends each valid line to `now_time_sec` using real-time slope (price/second) so the endpoint displays correctly on any viewing TF
5. Deduplicates by anchor price (keeps best by touch count), returns up to 2 uptrend + 2 downtrend lines
6. Returns `[{type, p1_time, p1_price, p2_time, p2_price, touches, anchor1, anchor2}, ...]` — timestamps in Unix seconds

### `detect_all_trendlines(symbol)`

Runs trendline detection across four timeframes and returns them all in one sorted list:

```python
_TF_TL_CONFIG = [
    {"tf": "1W", "limit": 104, "weight": 4, "n_swing": 3},
    {"tf": "1D", "limit": 200, "weight": 3, "n_swing": 4},
    {"tf": "4H", "limit": 200, "weight": 2, "n_swing": 5},
    {"tf": "1H", "limit": 200, "weight": 1, "n_swing": 5},
]
```

Each trendline dict is tagged with `timeframe` (e.g. `"1W"`) and `weight` (4=1W → 1=1H). The combined list is sorted by `weight` descending (weekly first). `get_candles_for_chart()` calls this instead of the single-TF `detect_trendlines()`.

**Visual weight system** in `chart.html` and chart explorer:

| Weight | TF | Opacity | Line width |
|--------|----|---------|-----------|
| 4 | 1W | 0.90 | 2.5px |
| 3 | 1D | 0.70 | 2px |
| 2 | 4H | 0.50 | 1.5px |
| 1 | 1H | 0.30 | 1px |

Rendering order: lower-weight lines first (drawn behind), higher-weight last (drawn in front). Weekly/daily structure is never obscured by lower-TF noise.

### `get_candles_for_chart(symbol, tf, limit=200)`

Returns the combined payload for the chart route:
```python
{
  "candles":       [{time, open, high, low, close}, ...],  # Unix seconds, floats
  "levels":        [{price, type, strength, touches}, ...],
  "trendlines":    [{type, p1_time, p1_price, p2_time, p2_price, touches, anchor1, anchor2,
                     timeframe, weight}, ...],  # all TFs, sorted weight desc
  "current_price": float,
  "symbol":        str,
  "timeframe":     str,
}
```

### `compute_indicators(symbol, timeframe)`

Per-timeframe indicator dict (cached 10 min):

| Key | Description |
|-----|-------------|
| `rsi` | RSI(14) |
| `macd_signal` | MACD signal line |
| `ema_20`, `ema_50`, `ema_200` | Exponential moving averages |
| `ema_stack` | `"bullish"` / `"bearish"` / `"mixed"` (20 > 50 > 200 vs reverse) |
| `bb_pct` | Bollinger Band percentile position (0 = lower band, 1 = upper) |
| `stoch_rsi_k`, `stoch_rsi_d` | Stochastic RSI |
| `adx` | Average Directional Index(14) |
| `adx_direction` | `"+DI"` / `"-DI"` (trend direction) |
| `atr_pct` | ATR(14) as % of current price |
| `volume_ratio` | Last volume / 20-period average volume |
| `last_candles` | Last 3 candle descriptions (`"bullish"` / `"bearish"` / `"doji"`) |
| `support_resistance` | S/R levels list (same as `detect_support_resistance`) |
| `trendlines` | Trendline list (same as `detect_trendlines`) |

### `format_for_prompt(symbol, timeframes=['4H','1D'])`

Formats indicator data as a compact text block for injection into Claude prompts. Includes:
- Nearest S/R levels with distance % from current price
- Active trendlines (direction, anchor range)
- Key indicator values (RSI, EMA stack, ADX, BB position)

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
| CodeQL workflow | `.github/workflows/codeql.yml` — runs on push, PR, and weekly (Monday 06:00 UTC). **Default Setup must stay disabled** — enabling it causes SARIF upload conflict with the custom workflow |
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

# Deploy code update from a remote machine (preferred — preserves subdirectory structure)
rsync -avz -e ssh \
  /path/to/local/trading-journal/ \
  <your-user>@<your-pi-ip>:/home/<your-user>/trading-journal/ \
  --exclude='*.db' --exclude='*.pyc' --exclude='__pycache__'
ssh <your-user>@<your-pi-ip> sudo systemctl restart trading-journal

# WARNING: when using scp for individual files, always specify the full destination path
# including subdirectory — e.g. static/app.js and templates/index.html live in subdirs.
# Wrong:  scp static/app.js pi:~/trading-journal/          ← lands in wrong place
# Right:  scp static/app.js pi:~/trading-journal/static/app.js

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

The **Open Position Risk** KPI appears on both the **Dashboard** and the **Live Trades** KPI strip.

**Formula:**
- Long: `risk = (entry_price − stop_loss) / entry_price × size_usdt`
- Short: `risk = (stop_loss − entry_price) / entry_price × size_usdt`
- No SL set: falls back to `margin_usdt` (collateral at risk)

Sub-label shows `· SL-based` or `· no SL` to indicate which mode is active, plus `% of equity`.

**Code:**
- Dashboard: `loadDashboard()` in `static/js/02-dashboard.js` — positions fetched in the opening `Promise.all`, risk card appended to `#kpi-grid` after the main 10 KPI cards
- Live Trades: `renderLiveKpis(positions, eq)` in `static/js/08-live.js` — risk computed inline from already-fetched positions data, rendered as the 5th card in `#trades-kpi-grid`

The `positions` table has an `analyst` column (migrated automatically at startup if missing). The journal table now shows an **Analyst** column (`📡 Name` or `—`).

**Flow:** Click any journal row → "Edit Trade" modal → Analyst input at top → Save → `PUT /api/positions/<id>` with `{analyst: "..."}`. Works on all trades including historical ones.

**Backend:** `PUT /api/positions/<id>` editable fields: `notes, tags, analyst, entry_price, close_price, size_usdt, realized_pnl, total_fees, open_time, close_time, direction`.

**Migration:** `analyst` column added via `_pos_new_cols` in `database.py → init_db()` (safe `ALTER TABLE … ADD COLUMN` with try/except, same as all other column migrations).

### 7. Security fixes (May 2026) — CodeQL alerts resolved

Six CodeQL alerts were found and resolved:

| # | Rule | File | Fix |
|---|------|------|-----|
| 1 & 2 | `py/stack-trace-exposure` | `bitget_sync.py`, `app.py` | `str(e)` in sync error result replaced with generic string; all `_err(str(e), 500)` calls replaced with `"Internal server error"` |
| 3 & 4 | `py/path-injection` | `app.py` | Uploaded filename sanitized with `werkzeug.utils.secure_filename()` before `os.path.join()` |
| 5 & 6 | `py/sql-injection` | `analytics.py` | Dismissed as false positive — `_build_where()` only interpolates hardcoded SQL fragments into the query string; user values go into bound `?` params only. Added allowlist validation (symbol `[A-Z0-9]+`, direction `Long`/`Short`, dates `YYYY-MM-DD`) for extra safety |

### 8. Incomplete string escaping fix (v1.4.1) — CWE-116

After JS was extracted to `static/app.js`, CodeQL flagged alert #7:

| # | Rule | File | Fix |
|---|------|------|-----|
| 7 | `js/incomplete-string-escaping` | `static/app.js:983` | Analyst name interpolated into an `onclick` attribute escaped `'` but not `\`. Fixed by escaping backslashes first: `.replace(/\\/g,"\\\\").replace(/'/g,"\\'")` |

### 9. Trading precision features (v1.5)

Three features to improve trade analysis and self-coaching.

#### 9a. AI Execution Grading (`ai_trade_grader.py`)

**What it does:** Grades a closed trade A/B/C/D based on execution quality — not just P&L outcome.

**Grades:**
- **A** — Excellent: entry near/better than planned, disciplined exit, strong realized R:R
- **B** — Good: minor flaw only (small slippage, slightly early profitable exit)
- **C** — Average: one clear flaw (chased entry, moved SL, cut winner very early)
- **D** — Poor: multiple/severe flaws (no SL, reckless size, avoidable full loss)

**Flow:** Click **⚡ Grade** button on any journal row → `POST /api/positions/<id>/grade` → `ai_trade_grader.grade_trade(id)` → Claude prompt with trade + linked call data → grade + reason stored in `positions.execution_grade` / `positions.execution_grade_reason` → badge shown inline.

**With linked call:** Entry slippage, realized R:R vs planned R:R, and recorded outcome are all included in the Claude prompt for a richer, more accurate grade.

**Without linked call:** Claude grades from P&L, duration, setup type, and notes alone.

**Backend:** `ai_trade_grader.py` — `grade_trade(position_id, conn)` → `_ask_claude(pos, call)`. Returns `{"grade": "A|B|C|D", "reason": "..."}`.

**Deep Dive:** Execution Grade Analysis table — win rate and avg P&L per grade (appears once trades have been graded).

#### 9b. Setup Type Tagging

**What it does:** Labels each trade with a setup category for pattern analysis.

**Options:** Breakout · Pullback · Trend Continuation · Range Fade · Reversal · News/Event · Other

**Flow:** Click any journal row → **Setup Type** dropdown → Save → stored in `positions.setup_type`.

**Deep Dive:** P&L by Setup Type — two charts (total P&L bar, win rate bar) and a breakdown table. Only trades with a setup_type set are included.

**Backend:** `setup_type` added to `PUT /api/positions/<id>` editable fields. `get_deep_stats()` returns `by_setup` list.

#### 9c. Planned vs Realized R:R (`analytics.get_rr_analysis`)

**What it does:** Compares the R:R planned in an analyst call against what was actually achieved in the trade.

**Formula:**
```
realized_R:R = (close_price − planned_entry) / abs(planned_entry − planned_sl)   [Long]
realized_R:R = (planned_entry − close_price) / abs(planned_entry − planned_sl)   [Short]
```

**Flow:** Open any journal row → enter the **Call ID** (from Call Analyzer) → Save → `positions.call_id` is set → `GET /api/analytics/rr` joins positions to analyzed_calls → Deep Dive R:R table shows planned vs realized.

**Backend:** `analytics.get_rr_analysis(conn)` — JOINs positions + analyzed_calls on `call_id`. Returns up to 100 most recent linked trades.

**Deep Dive:** Planned vs Realized R:R table — symbol, direction, setup, grade, planned R:R, realized R:R (green ≥ 1R, red < 1R), outcome, P&L.

### 10. v1.5.5 — Edge Lab & UX polish

#### 10a. Deep Dive split into two nav pages

| Page | Nav ID | Content |
|------|--------|---------|
| Deep Dive | `page-deep` | 6 breakdown charts, key stats pills, symbol table, worst symbols table |
| Edge Lab | `page-edge` | Setup type charts/table, execution grade table, AI pattern detector, R:R table |

`loadEdge()` handles the Edge Lab page — fetches `/api/analytics/deep` for `by_setup`/`by_grade` and `/api/analytics/rr` independently of `loadDeep()`.

#### 10b. Analyst Leaderboard (Edge Score)

`GET /api/calls/analyst-stats` now returns additional computed fields per analyst:

| Field | Formula |
|-------|---------|
| `call_win_rate` | call outcomes won / (won + sl_hits) × 100 |
| `tp1_hit_rate` | tp1_hits / total_analyzed × 100 |
| `conv_rate` | entered / total_analyzed × 100 |
| `edge_score` | `win_rate × 0.5 + call_win_rate × 0.3 + tp1_hit_rate × 0.2` (requires ≥ 3 trades) |

Sorted by edge_score descending. Rows color-coded: green ≥ 65, red < 45.

#### 10c. Correlation Detector (sector-aware)

`renderCorrelationWarning()` in `static/js/08-live.js` groups open positions by sector:

`Bitcoin` · `ETH/L2` · `SOL/L1` · `Meme` · `DeFi` · `AI/Infra`

Two severity tiers: 🟡 yellow (2 positions same sector + direction), 🔴 red (3+). Background color changes with severity.

#### 10d. AI Pattern Detector (`ai_pattern_detector.py`)

`POST /api/analytics/patterns` — collects stats by setup, weekday, session (Asia/London/NY/Off-hours), direction, duration, grade. Minimum 20 total trades, minimum 5 per category. Claude returns up to 6 findings as `{type, title, finding, recommendation, confidence}`.

Session buckets (UTC): Asia 00-08 · London 08-13 · NY/Overlap 13-21 · Late/Off-hours 21-24.

#### 10e. Setup Type filter in Journal

`GET /api/positions` now accepts `setup` query param:
- `setup=untagged` → `(setup_type IS NULL OR setup_type = '')`
- `setup=Breakout` (or any named type) → `setup_type = ?` (allowlist validated)

Filter dropdown added to journal filter bar between Result and From date. Reset button clears it.

#### 10f. Setup Type in Add Trade modal

`POST /api/positions` now accepts `setup_type` in request body and stores it at creation time. Dropdown added to the Add Trade modal above Notes.

### 12. v1.7 — Trading Tools & Heatmap

#### 12a. Position Sizing Calculator

Located in the Call Analyzer input panel. No backend needed — purely frontend.

**Inputs:** Entry price · Stop Loss · Risk % (persisted to `localStorage`)
**Auto-population:** Account equity loaded from `/api/sync/status` on page load. Entry and SL auto-filled from parsed call after every `analyzeCall()` run.

**Formula:**
```
risk_amount  = equity × risk% / 100
risk_dist    = |entry − sl| / entry
size_usdt    = risk_amount / risk_dist
leverage     = size_usdt / equity
```

Leverage color: green ≤7x · yellow ≤15x · red >15x.

**Code:** `calcSizing()` in `static/js/07-calls.js`. `_szEquity` global holds current equity. `renderCallResult()` auto-fills inputs after analysis.

#### 12b. Economic Calendar

**Source:** `https://nfs.faireconomy.media/ff_calendar_thisweek.json` — ForexFactory community mirror, no auth, 1-hour cache.

**Filter:** High-impact USD events only. Events for today and tomorrow (UTC) are returned.

**API:** `GET /api/market/calendar` → list of `{title, time, forecast, previous, when}`

**Frontend:** Yellow warning banner on Live Positions page (`#eco-warning`), shown non-blocking after positions load. Hidden when no events.

**Code:** `get_economic_calendar()` in `market_context.py`.

#### 12c. Trade Heatmap (Hour × Day)

**Analytics:** `get_heatmap_data(conn)` in `analytics.py` — groups all positions by `(strftime('%w', close_time), strftime('%H', open_time))`.

**API:** `GET /api/analytics/heatmap` → list of `{weekday, hour, trade_count, total_pnl, win_rate}`

**Frontend:** `renderHeatmap(rows)` in `static/js/04-deep-edge.js` — builds an HTML table (7 cols × 24 rows). Cells require ≥3 trades. Opacity scales with trade count (more trades = more opaque). Hover shows count + WR + P&L.

Color key: green ≥65% WR · blue 50–64% · yellow 40–49% · red <40%

**Location:** Deep Dive page, after Worst Symbols table.

#### 12d. BTC Dominance

Added to `get_market_context()` and `format_for_prompt()`.

**Source:** `https://api.coingecko.com/api/v3/global` — CoinGecko free API, no auth, 15-min cache.

**Returns:** `{btc_dominance: 58.58, change_24h: 0.14, ok: true}`

**Frontend:** Market Pulse strip on Dashboard. Rising dominance = red (bad for altcoin longs). Falling = green.

**Claude context:** BTC dominance + 24h change included in `format_for_prompt()` output → available in AI Advisor and live position analysis.

### 11. v1.6 — Live Market Context (`market_context.py`)

Three real-time data sources injected into every Claude analysis.

#### Sources

| Source | Endpoint | Auth | Cache |
|--------|----------|------|-------|
| Fear & Greed Index | `https://api.alternative.me/fng/?limit=1` | None | 5 min |
| Bitget funding rate | `/api/v2/mix/market/current-fund-rate` | Bitget (existing) | 5 min per symbol |
| Bitget long/short ratio | `/api/v2/mix/market/account-long-short` | Bitget (existing) | 5 min per symbol |

Cache is an in-process dict `{key: (timestamp, data)}` — resets on service restart.

#### API

`GET /api/market/context?symbols=BTCUSDT,SOLUSDT`

Returns:
```json
{
  "fear_greed": {"value": 46, "classification": "Fear", "ok": true},
  "symbols": {
    "BTCUSDT": {
      "funding":    {"rate_pct": -0.0008, "direction": "shorts paying", "high": false, "ok": true},
      "long_short": {"long_pct": 48.7, "short_pct": 51.3, "bias": "balanced", "ok": true}
    }
  }
}
```

#### How Claude uses it

| AI module | Context injected |
|-----------|-----------------|
| `ai_live_trade.py` | Funding rate + L/S ratio for the specific symbol being analyzed |
| `ai_trade_grader.py` | Fear & Greed Index value at grading time |
| `ai_advisor.py` | F&G + BTC funding rate for full portfolio analysis |

`market_context.format_for_prompt(ctx)` converts the dict to a concise multi-line string appended to each Claude prompt.

#### Frontend

- **Dashboard** — Market Pulse strip above KPI grid: F&G badge + BTC funding + BTC L/S. Loads non-blocking after KPIs.
- **Live Positions** — Two chips per card in the badge row:
  - `F +0.0012%` (yellow = longs paying, green = shorts paying, ⚠ if ≥ 0.05%)
  - `L/S 68/32` (yellow if either side > 65% = crowded trade)
  - Market context fetched after positions render and triggers a re-render.

---

## `chart_context.py` — Technical Indicators

Fetches OHLCV candles from Bitget (`/api/v2/mix/market/candles`) and computes a full indicator suite using `pandas-ta`. Results are cached per `(symbol, timeframe)` for 10 minutes.

#### Indicators computed

| Indicator | Parameters | Signal thresholds |
|-----------|-----------|------------------|
| RSI | 14 | >70 overbought · <30 oversold |
| MACD | 12/26/9 | bullish/bearish crossover detected |
| EMA | 20, 50, 200 | stack alignment (bullish/bearish/mixed) |
| Bollinger Bands | 20, 2σ | price percentile position (0–100) |
| Stochastic RSI | K=14, D=3 | K>80 overbought · K<20 oversold |
| ADX | 14 | >25 strong trend · 20-25 trending · <20 ranging |
| ATR | 14 | expressed as % of price (SL sizing hint) |
| Volume | 20-period avg | >1.5× high · <0.7× low |
| Candle pattern | last 3 | bullish/bearish/doji + body % |

#### API

`GET /api/chart/indicators?symbol=BTCUSDT&timeframes=4H,1D`

Returns per-timeframe indicator dict plus `prompt_text` — a pre-formatted text block ready for Claude.

#### How Claude uses it

Both `ai_live_trade.py` and `ai_call_analyzer.py` automatically call `chart_context.format_multi_tf_for_prompt(symbol, ["4H", "1D"])` and append the result to every prompt. Claude uses indicators to:
- Judge momentum alignment with the trade direction
- Identify overbought/oversold conditions at entry
- Cross-reference call setups against current technicals
- Contextualise SL recommendations using ATR

See `docs/RATING_CRITERIA.md` for the full documented thresholds used per indicator.

---

## `ai_rulebook.py` — Self-Learning Trader Rulebook

Analyses the full trade history in SQLite and asks Claude to synthesise 5–10 personalised rules. Rules are stored in `trader_rulebook` and injected as context into every subsequent AI prompt.

#### Rule types

| Type | Colour | Meaning |
|------|--------|---------|
| `warning` | Red | A losing pattern the trader must stop |
| `strength` | Green | A winning pattern to exploit more |
| `habit` | Yellow | An execution discipline note (positive or negative) |
| `calibration` | Blue | How accurate the AI score assignments have been |

#### Functions

| Function | Description |
|----------|-------------|
| `update_rulebook(conn)` | Collect stats → ask Claude → clear + insert `trader_rulebook` → return DB result |
| `get_rulebook(conn)` | Return `{rules, count, updated_at}` from DB |
| `get_rulebook_for_prompt(conn)` | Short text block (warnings first) for injection into Claude prompts |
| `get_calibration_data(conn)` | Group `analyzed_calls` by score tier, compute TP1/SL rates |
| `get_calibration_for_prompt(conn)` | Formatted calibration text for prompt injection |
| `get_similar_trades(symbol, setup, direction, conn)` | Last 3 closed trades matching symbol+setup+direction (falls back to symbol-only if <3 matches) |
| `get_similar_trades_for_prompt(...)` | Formatted similar-trades block with W/L summary |

#### Prompt injection (all AI modules)

Every call to `ai_live_trade.analyze_position()`, `ai_call_analyzer.analyze_call()`, and `ai_advisor.analyze()` automatically injects:
1. **Rulebook** — personalised rules with specific P&L numbers
2. **Calibration** — historical score accuracy so Claude can recalibrate confidence
3. **Similar trades** — recent context for the exact symbol + setup + direction

#### Auto-update schedule

`bitget_sync.py` calls `_maybe_update_rulebook()` after each background sync. If `rulebook_updated_at` is older than `RULEBOOK_INTERVAL_DAYS` (7), a full regeneration runs automatically.

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
  ├── trader_rulebook ──► ai_rulebook.py ──► injected into ALL AI prompts
  └── settings ──► sync state, account balance, rulebook_updated_at

Bitget Candles API (unauthenticated market data)
  └── chart_context.py ──► pandas-ta indicators ──► ai_live_trade + ai_call_analyzer
        └── 10-min cache per (symbol, timeframe)

ai_rulebook.py (SQLite trade history → Claude → trader_rulebook)
  └── rules + calibration + similar trades ──► injected into all 3 AI modules below

Claude API (claude-sonnet-4-6)
  ├── ai_advisor.py ──► Portfolio analysis (~$0.02/call)
  ├── ai_live_trade.py ──► Per-trade analysis (~$0.003/call)
  ├── ai_call_analyzer.py
  │     ├── analyze_call() ──► Call analysis with vision (~$0.02/call)
  │     └── analyze_pending_limit() ──► Limit order analysis (~$0.005/call)
  └── ai_rulebook.py ──► Rulebook generation (~$0.01/run, weekly)
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

Add the route to the appropriate blueprint in `routes/`. Use `db_conn()` so the connection always closes even on exception:

```python
# Example: routes/analytics.py
from database import db_conn
from helpers import _ok, _err

@bp.route("/api/my-endpoint")
def api_my_endpoint():
    with db_conn() as conn:
        data = [dict(r) for r in conn.execute("SELECT ... FROM positions").fetchall()]
    return _ok(data)
```

`db_conn()` is a `contextlib.contextmanager` in `database.py` — opens with `get_conn()`, guarantees `conn.close()` on exit. Commits must still be explicit (`conn.commit()`) inside the `with` block.

Register the blueprint in `app.py` if you create a new file:
```python
from routes.mymodule import bp as mymodule_bp
app.register_blueprint(mymodule_bp)
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
