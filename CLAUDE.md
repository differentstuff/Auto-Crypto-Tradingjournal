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

## Data Sources (active, wired into 11-worker CollectorResult)
| Client | Data | Key |
|---|---|---|
| ccxt_client.py | Binance/Bybit/OKX prices, multi-exchange L/S ratio | none |
| market_context.py | VIX/DXY (yfinance), BTC mempool, DefiLlama TVL, Fear&Greed, retail vs smart-money L/S divergence | none |
| coinalyze_client.py | Aggregated OI + funding + liq trend + per-exchange funding spread | COINALYZE_API_KEY |
| finnhub_client.py | Economic calendar — FOMC/CPI/NFP macro risk flag | FINNHUB_API_KEY |
| coingecko_client.py | BTC dominance, cap tier, trending coins (keyless public API) | none |
| deribit_client.py | BTC/ETH put/call skew — institutional sentiment proxy | none |
| nansen_client.py | Smart money wallet flows + accumulating/distributing direction | paid |
| grok_client.py | Social/news context per coin (cap-weighted 0-80%) | XAI_API_KEY |

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
- _build_macro_header(): prepended to every Stage 3 scoring prompt
- macro_ctx stored in _state["macro_ctx"] — visible in scanner status API

## Confluence Signals (chart_confluence.py)
9 signals + 2 SMT variants → max_val = 6.50/TF:
RSI, MACD, EMA, ADX, WaveTrend, MFI, CVD, volume,
_smt_weight (cross-exchange price divergence ≥0.5%),
_smt_direction_weight (24h directional divergence vs correlated pair ±0.15)
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
- Always backup before restart: `bash /home/fbauer/trading-journal/scripts/backup_db.sh`
- Backups auto-run via ExecStopPost on every systemctl stop/restart (7-day rolling, in backups/)
- Daily cron backup at 04:00 Pi time
- Restore procedure: stop service → cp backups/trading_journal_YYYYMMDD_HHMMSS.db trading_journal.db → start service

## Calculation Invariants (do not change without updating both sides)
- WaveTrend: n1=10, n2=21, rolling(4) — must match in both chart_indicators.py AND backtest_engine.py
- CVD: Money Flow Multiplier formula v*(2c-l-h)/(h-l) — must match in both chart_indicators.py AND backtest_engine.py
- Sharpe annualization: periods_per_year=2190 for 4H crypto (6 bars/day × 365, 24/7 market)
- SMT weight: +0.15 on divergence (delta >= 0.5%), 0.0 on agreement — signal fires when prices DISAGREE
- SMT direction weight: +0.15 bullish (symbol↑ pair↓), -0.15 bearish (symbol↓ pair↑), threshold ≥1% delta
- Walk-forward split: 70% training / 30% test on real position date range

## New Tools (Analysis tab)
- Optimizer history: GET /api/backtest/optimizer-history — last 5 runs with Sharpe + params
- Walk-forward test: POST /api/backtest/walk-forward — splits real positions 70/30, tests generalization
- Hindsight re-run: POST /api/hindsight/run?n=841 — skips already-scored positions (LEFT JOIN fix)

## JS Frontend
- 17 modules static/js/01-utils.js through 16-settings.js
- Bump ?v=X.X in templates/index.html on every JS change
- notify(msg, type) toast function in 01-utils.js
