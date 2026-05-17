# Trading Journal — Claude Code Context

## Project Overview
Self-hosted crypto futures trading journal. Flask 3.1 / Python 3.13 / SQLite WAL.
Runs as a systemd service on a Raspberry Pi 5 (<Pi-IP>). Accessible from any browser on the local network.

## Deployment
- **Pi SSH:** `<user>@<Pi-IP>` (use expect — no BatchMode; credentials in local memory only)
- **Service:** `sudo systemctl restart trading-journal`
- **Pi path:** `/home/<user>/trading-journal`
- **Dev path:** local clone of this repo
- **Port:** 8082

## Database
- **Mode:** SQLite WAL — safe for concurrent reads during sync
- **Migrations:** database.py::init_db() — ALL must be idempotent
- **Tables:** positions, orders, wallet_snapshots, analyzed_calls, pending_limits, trader_rulebook, trade_hindsight, settings, import_log, token_usage, schema_version

## Import Graph (safe edit order)
constants.py, prompt_fragments.py, trade_history.py, chart_sr.py, chart_indicators.py — no internal deps, edit freely
token_log.py — token telemetry only; imported by ai_client via helpers re-export
helpers.py, database.py — imported by everything, edit carefully
sync_base.py — SyncDriver protocol, SyncState class, auto_close_calls, retroactive_close_calls
ai_client.py — imports constants + helpers (log_token_usage re-exported from token_log)
chart_candles.py, chart_patterns.py, chart_confluence.py — split from chart_context
chart_context.py — thin facade over chart_candles + chart_patterns + chart_confluence
prompt_builder.py — imports chart_context, ai_rulebook, ai_pattern_detector, nansen_client
agent_types.py — TypedDicts + empty_interpreter/empty_sentiment/empty_reviewer factories
ai_*.py — import ai_client + prompt_builder + trade_history
scanner_watchlist.py — symbol lists; scanner_criteria.py — CRITERIA_DEFAULTS + kill-zone; scanner_prompts.py — prompt builders; scanner_stages.py — Stage 1/2
ai_scanner.py — thin: _state, scan thread, Stage 3, public API (imports scanner_* modules)
routes/*.py — import helpers + ai_* modules

## AI Pipeline
- Sonnet (claude-sonnet-4-6): call analyzer, advisor, scanner, rulebook, pattern detector, grader
- Haiku (claude-haiku-4-5-20251001): scanner quick-score, hindsight, live trade check, limit analysis
- Token logging: log_token_usage(module, model, in, out, cached) — import from helpers or token_log
- Prompt caching: build_cached_messages() — ephemeral cache on context blocks >= 4096 chars
- Error fallbacks: use empty_interpreter/empty_sentiment/empty_reviewer from agent_types (not private _empty_* functions)
- Data pipeline: agent_data_collector → 15 parallel workers → CollectorResult → prompt_builder → Claude
- Adding a new data source: add fetch_X() to data_sources.py + field to CollectorResult in agent_types.py

## Data Sources (active, wired into 12-worker CollectorResult)
| Layer | Client | Data | Key |
|---|---|---|---|
| 1 — Global Macro | market_context.py | VIX/DXY (yfinance) | none |
| 1 — Global Macro | market_context.py | ES1! — S&P 500 Futures price + 24h change (yfinance ES=F) | none |
| 1 — Global Macro | market_context.py | Fear & Greed (alternative.me) | none |
| 1 — Global Macro | finnhub_client.py | Economic calendar — FOMC/CPI/NFP macro risk flag | FINNHUB_API_KEY |
| 1 — Global Macro | coingecko_client.py | BTC.D, ETH.D, USDT.D, OTHERS.D, TOTAL2, TOTAL3 (market_cap_percentage) | none |
| 1 — Global Macro | coingecko_client.py | MEME.C, STABLE.C, STABLE.C.D — /coins/categories | none |
| 2 — Market Structure | deribit_client.py | BTC/ETH put/call skew — institutional sentiment proxy | none |
| 2 — Market Structure | market_context.py | BTC mempool congestion (blockchain.com) | none |
| 2 — Market Structure | coingecko_client.py | Trending coins (top-10, last 24h) | none |
| 3 — Symbol-Level | ccxt_client.py + market_context.py | Multi-exchange L/S ratio + retail vs smart-money divergence | none |
| 3 — Symbol-Level | coinalyze_client.py | Aggregated OI + funding + liq trend + per-exchange funding spread | COINALYZE_API_KEY |
| 3 — Symbol-Level | liquidation_client.py | Historical liquidations per day (longs_usd/shorts_usd); CSV cache in data/liquidations/{symbol}/ | COINALYZE_API_KEY |
| 3 — Symbol-Level | coingecko_client.py | Cap rank, cap tier, 24h volume | none |
| 3 — Symbol-Level | market_context.py | DefiLlama TVL (DeFi tokens only) | none |
| 3 — Symbol-Level | chart_context.py via ccxt | OHLCV candles (Binance Futures) | none |
| 4 — Trade Intelligence | nansen_client.py | Smart money wallet flows + accumulating/distributing direction | paid |
| 4 — Trade Intelligence | grok_client.py | Social/news context per coin (cap-weighted 0-80%) | XAI_API_KEY |

See Tools → Data Sources page in the UI for the full interactive reference.

## Prompt budget order (prompt_builder.py)
1. Backtest context — most relevant to setup
2. Market context string — pre-fetched by caller
3. All data source blocks (Coinalyze, Fear&Greed, macro regime, L/S divergence, etc.)
4. Rulebook — protected until remaining < 100 chars (was 500)
5. Calibration — protected until remaining < 100 chars
6. Chart context — protected until remaining < 100 chars
7. Grok social — protected until remaining < 150 chars

## Scanner macro layer (scanner_stages.py)
- _get_scan_macro_context() called ONCE per scan: VIX, F&G, Finnhub events, BTC dominance
- _apply_macro_cap(): VIX > 35 → cap 6.0, VIX 25-35 → cap 7.5, macro event in 24h → cap 7.0
- _build_macro_header(): prepended to every Stage 3 scoring prompt — shows VIX | ES1! | F&G | BTC.D | USDT.D | STABLE.D | MEME cap
- macro_ctx stored in _state["macro_ctx"] — visible in scanner status API
- get_macro_regime() now includes ES=F (S&P 500 futures via yfinance): returns es, es_change_pct alongside vix and dxy
- get_global_market() extended: btc_dominance_pct, eth_dominance_pct, usdt_dominance_pct (USDT.D), others_dominance_pct (OTHERS.D), total2_usd (TOTAL2), total3_usd (TOTAL3)
- get_category_caps() in coingecko_client.py: calls /coins/categories → meme_cap_usd (MEME.C), stable_cap_usd, stable_dominance_pct (STABLE.C.D)

## Confluence Signals (chart_confluence.py)
12 signals total: 11 TF-level + 1 symbol-level → max_per_tf = 5.55 (non-SMT) / 5.85 (SMT):
TF-level: RSI, MACD (grouped momentum cap ±1.5), EMA, ADX,
          WaveTrend, MFI (grouped oscillator cap ±1.0), CVD, order_flow, volume,
          _smt_weight (cross-exchange price divergence ≥0.5%),
          _smt_direction_weight (24h directional divergence vs correlated pair ±0.15)
Symbol-level: liquidation wall +0.20 (conditional — short-squeeze/cascade within 3%)
VIX multiplier: score × 0.80 when VIX > 30 (5-min cached)
SMT_SYMBOLS = {BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT}
SMT_PAIRS = {BTC↔ETH, SOL→ETH, BNB→BTC, XRP→BTC}

## Testing
- Framework: pytest
- Tests in tests/ directory
- Run: python3 -m pytest tests/ -v
- Fixtures: tests/conftest.py — db (in-memory SQLite), sample_positions

## API Rules
- All routes return {"ok": true/false, "data": ...} via _ok() / _err()
- Never expose exception messages in API responses (CWE-209)
- Never change existing endpoint URLs or response shapes
- Use _safe_float(val) in routes/calls.py for parsing price fields from request JSON
- Validate status fields against VALID_STATUSES allowlist before DB writes (see routes/limits.py)

## Deployment (IMPORTANT)
- **Never rsync *.db files to Pi** — production DB lives on Pi only, local has none
- rsync exclude flags: --exclude="*.db" --exclude="*.db-wal" --exclude="*.db-shm" --exclude=".agents"
- Always backup before restart: `bash ~/trading-journal/scripts/backup_db.sh`
- Backups auto-run via ExecStopPost on every systemctl stop/restart (7-day rolling, in backups/)
- Daily cron backup at 04:00 Pi time
- Restore procedure: stop service → cp backups/trading_journal_YYYYMMDD_HHMMSS.db trading_journal.db → start service

## Calculation Invariants (do not change without updating both sides)
- WaveTrend: n1=10, n2=21, rolling(4) — must match in both chart_indicators.py AND backtest_engine.py
- CVD: Money Flow Multiplier formula v*(2c-l-h)/(h-l) — must match in both chart_indicators.py AND backtest_engine.py
- Sharpe annualization: periods_per_year=2190 for 4H crypto (6 bars/day × 365, 24/7 market)
- SMT weight: +0.15 on divergence (delta >= 0.5%), 0.0 on agreement — signal fires when prices DISAGREE
- SMT direction weight: +0.15 bullish (symbol↑ pair↓), -0.15 bearish (symbol↓ pair↑), threshold ≥1% delta
- Walk-forward split: 70% training / 30% test; end_offset_days prevents data leakage (training ends at now-test_days)
- Sharpe (dashboard): sample variance (N-1 denominator), daily returns, annualize × sqrt(365)
- Calmar (dashboard): max_dd_pct tracked as % of running peak at each step (NOT final all-time peak)
- Wallet snapshot filter: wallet_balance > 1 USDT — excludes dust/zero entries that corrupt return series

## New Tools (Analysis tab)
- Optimizer history: GET /api/backtest/optimizer-history — last 5 runs with Sharpe + params
- Walk-forward test: POST /api/backtest/walk-forward — splits real positions 70/30, tests generalization
- Walk-forward poll: GET /api/backtest/walk-forward/<job_id> — dedicated poll endpoint (not /optimize/)
- Hindsight re-run: POST /api/hindsight/run?n=200 — skips already-scored positions (LEFT JOIN fix), max 200

## Market & Analytics API (routes/market.py, routes/analytics.py)
- GET /api/price/<symbol> — live price via Binance, Bitget fallback
- GET /api/coin/summary/<symbol> — price + 4H/1H indicators (RSI, EMA, WaveTrend, ADX, ATR) + Nansen + Coinalyze + BTC regime + F&G + liquidations_14d
- GET /api/market/dominances — full dashboard: BTC.D, ETH.D, USDT.D, OTHERS.D, TOTAL2, TOTAL3, MEME.C, STABLE.C, STABLE.C.D, ES1! price+change
- GET /api/chart/annotated/<symbol> — on-demand annotated chart PNG (base64); params: direction, entry, entry_high, sl, tp1, tp2, tf
- GET /api/liquidations/<symbol>?days=30 — Coinalyze historical liquidation data (longs_usd, shorts_usd per day); available=false when plan doesn't include it
- POST /api/scanner/cancel — cancel running scan; sets _cancel_event in ai_scanner.py; status becomes "cancelled"

## Data Sources page
- Tools → Data Sources in left nav — lists all 14 sources grouped by macro→micro layer
- Shows: provider, auth requirement, inputs, data returned, pipeline usage

## JS Frontend
- 17 modules static/js/01-utils.js through 16-settings.js
- Bump ?v=X.X in templates/index.html on every JS change
- notify(msg, type) toast function in 01-utils.js

## Scanner Stage 3 — HTF→LTF Breakdown
- `enrich_finalists_1h()` in scanner_stages.py: fetches 1H candles via `compute_indicators()` + `format_for_prompt()` — adds S/R levels + prompt_text to ctx["1H"] for all 30 finalists before Stage 3
- Stage 3 prompts (_build_prompt, _build_batch_prompt, _quick_score) include 1H data and explicit instruction: **1D bias → 4H confirmation → 1H entry/SL → 4H/1D TP**
- Scored output reports `"timeframe": "Multi-TF (1D/4H/1H)"`
- Rationale: closed 4H bars can be up to 4h stale; 1H cuts max staleness to 1h for entry/SL precision

## Chart System

### Static Annotated Chart (agent_chart_draw.py)
Generated as base64 PNG for Telegram alerts + pending limit cards.

```python
agent_chart_draw.draw(
    candles, symbol, direction,
    entry,          # entry zone low (or single entry)
    sl, tp1, tp2,
    criteria=[],    # key_conditions list → top-right text box
    n_candles=60,
    entry_high=None,   # entry zone high → shaded blue band
    sr_levels=[],      # list of {type, price, touches, strength}
)
```

**Visual elements:**
- `▲ LONG` / `▼ SHORT` colored badge (top-left, green/red)
- Entry zone: shaded blue band between entry and entry_high
- SL = red dashed, TP1 = bright green `#26D96B`, TP2 = cyan `#4FC3F7`
- Price labels on right edge of every level line
- S&R zones: green (support) / red (resistance), alpha scales with touches
- Confluence zones: adjacent levels within 0.3% merged into wider band (`_merge_sr()`)
- At-level highlight: zones within 0.5% of current price get brighter fill + border + ⚡ label
- ATR-based zone width: `ATR × 0.15` half-width for singleton zones (F)
- `scanner_scheduler._enrich_and_filter_setups()` passes `entry_zone.low/high`, `key_conditions`, `detect_support_resistance(candles)` as `sr_levels`

### Live Chart Popup (templates/chart.html — LightweightCharts)
- Direction badge chip: `▲ LONG` (green) / `▼ SHORT` (red) shown first in legend
- Trade levels: Entry (blue, solid), SL (red, dashed), TP1 (green, solid), TP2 (cyan, solid) — all with axis labels
- S&R canvas overlay: green support / red resistance (was uniform grey)
- `_mergeSrLevels()`: JS confluence merge (within 0.3%), `_computeAtr()`: ATR from candle array for zone width
- `_startOverlay(wrap, series, mergedLvls, htf_levels, liquidations, atr)`: uses ATR × 0.15 for singleton zone half-width
- At-level zones: 2px border rect + brighter fill + `⚡AT LEVEL` chip in legend
- Weekly S&R stays gold, unchanged

## Pending Limit Orders (routes/limits.py + static/js/10-pending.js)

### AI Verdict Display (bug fixed)
- `analysis_json` from `ai_limit.py` uses fields: `recommendation`, `setup_quality.score`, `risk_assessment`
- Old code read `verdict`/`setup_score`/`confidence` → showed "undefined". Fixed in 10-pending.js v3.0+
- Color: Keep=green, Cancel=red, Adjust*=yellow

### Bitget Preset SL/TP Sync
- `bitget_client.get_pending_orders()` now parses `presetStopLossPrice` → `preset_sl`, `presetTakeProfitPrice` → `preset_tp`
- `GET /api/live/pending-orders` backfills `sl_price`/`tp1_price` on journal limit rows where NULL, matched by `bitget_order_id`

### Scanner Chart in Limit Card
- `GET /api/limits` JOINs `analyzed_calls.analysis_json` for rows with a `call_id` and extracts `chart_png_b64`
- Pending limit card renders the chart as inline `<img>` below the AI verdict

### Delete Route
- `DELETE /api/limits/<id>` now has try/except → returns JSON error instead of HTML 500

## Live Trade Analysis (agent_trade_monitor.py)

### Direction-Aware Prompt (bug fixed)
Position dict from `bitget_client.get_open_positions()` uses `direction` / `entry_price` / `mark_price` — the prompt was reading `side` / `openPrice` / `markPrice` (all wrong, all defaulted). Fixed field names.

For Short positions, the prompt now injects a `DIRECTION CONTEXT — SHORT` block:
- Bearish momentum = **favorable** (price moving toward TP)
- SL must be **above** entry (not below)
- TP is **below** entry
- Hard rule: "never swap SL/TP placement for Short positions"

## Hermes Agent (interactive Telegram assistant)
Separate from the scanner alert bot. Two-bot setup:
- **Alert bot** (`TELEGRAM_BOT_TOKEN` in journal .env): one-way push from `telegram_notify.py`
- **Hermes bot** (`~/.hermes/.env`): two-way interactive chat for querying the journal

Hermes runs as a user systemd service (`hermes-gateway.service`) on the Pi, configured to query the journal API at `http://localhost:8082`. SOUL.md documents all key endpoints + response style. MEMORY.md seeds trader profile.

Service commands:
```bash
hermes gateway status
hermes gateway start / stop
journalctl --user -u hermes-gateway -f
```
