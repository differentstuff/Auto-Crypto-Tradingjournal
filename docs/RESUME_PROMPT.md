# Session Resume Prompt

*Paste this entire block as your first message in a new Claude Code session to restore full context.*

---

## Context

We are continuing work on a self-hosted crypto futures trading journal.

**Project:** local clone — see CLAUDE.md for paths
**Version:** v1.6.0 (latest commit: 6e6f2d0)
**Stack:** Python 3.13 / Flask 3.1 / SQLite WAL / Raspberry Pi 5 at 192.168.1.21:8082
**GitHub:** https://github.com/anvilfilbert/Auto-Crypto-Tradingjournal

---

## What is built (v1.6.0 complete)

### v1.6.0 — Intelligence Layer (11th & 12th signals, on-chain, HMM, ML)

**Liquidation cluster detection (11th confluence signal)**
- `liquidation_levels.py`: CCXT liquidation cluster detection — `_liquidation_weight()` in `chart_confluence.py`
- Signal: symbol-level, +0.20 conditional (short-squeeze / cascade within 3% of current price)
- Constants: `LIQ_TTL = 900` (15-min cache)

**Order flow delta (12th confluence signal)**
- `chart_indicators.py`: `compute_order_flow_delta()` tick-rule proxy added, wired into `compute_all_indicators()`
- Signal: TF-level, ±0.15 per timeframe — `_order_flow_weight()` in `chart_confluence.py`
- Result stored as `result["order_flow"]` in indicator dict

**On-chain data (CoinMetrics Community)**
- `onchain_client.py`: MVRV + exchange net-flow (BTC only, no API key required)
- Constants: `ONCHAIN_TTL = 3600` (1-h cache)
- Injected into prompts via `prompt_builder.py` on-chain block

**HMM market regime**
- `market_regime.py`: 3-state `GaussianHMM` on BTC 4H candles — labels: `trending_up` / `ranging` / `trending_down`
- DB: migration 38 — `ALTER TABLE analyzed_calls ADD COLUMN regime_label TEXT DEFAULT NULL`
- Constants: `REGIME_TTL = 14400` (4-h retrain window)
- Injected into prompts via `prompt_builder.py` HMM regime block

**ML win-probability scorer**
- `signal_scorer.py`: XGBoost model — features: `setup_score / direction / rr_ratio / consensus_score`
- DB: migration 39 — `ALTER TABLE analyzed_calls ADD COLUMN ml_win_prob REAL DEFAULT NULL`
- Constants: `ML_SCORER_TTL = 86400` (24-h retrain), `ML_MIN_SAMPLES = 20` (activates at 20+ labeled outcomes)
- Injected into prompts via `prompt_builder.py` ML block; **silent until 20 labeled outcomes exist**
- Retrain triggered automatically on 24h interval

**Backtest quality metrics**
- `backtest_quality.py`: PBO (Probability of Backtest Overfitting) + Deflated Sharpe Ratio + Bootstrap Sharpe CI
- Route: `POST /api/backtest/quality`

**Agent prompt templates**
- `agent_data_interpreter.py`: `ANALYST_INSTRUCTIONS` (6-section template)
- `agent_risk_mgmt.py`: `RISK_INSTRUCTIONS` (decision table)
- `agent_trade_prep.py` combines both: `system_prompt = ANALYST_INSTRUCTIONS + "\n\n" + RISK_INSTRUCTIONS`

**Accessibility baseline**
- 40 HTML inputs + 2 dynamic JS inputs given `aria-label`
- Browser baseline verified: 16/16 tabs, 4/4 a11y 100% Lighthouse, 3/3 interactions
- Dynamic inputs: `backtestSymbol` (09-analysis.js v3.7), `scan-single-symbol` (14-scanner.js v4.1)

**Chrome DevTools MCP**
- `.mcp.json` + `.claude/settings.json` — `chrome-devtools-mcp` registered via `bash -c` inline wrapper
- Pending: MCP tools not loading in cloud sessions (local Chrome on port 9222 required)

**New dependencies:** `hmmlearn`, `joblib`, `scikit-learn`, `xgboost`

**Test baseline: 442 passing, 9 pre-existing failures (ImportErrors in test_chart_sr), 8 skipped**

---

### v1.5.0 — Optimisation Sprint (Plans A–D)

**Plan A — Quick Wins**
- `tests/conftest.py`: `client` fixture with proper Flask stub save/restore on teardown
- `backtest_engine.py`: confluence denominator named `_CONFLUENCE_DENOM = 3.55` with 6 weight constants
- `static/js/12-explorer.js`: `_esc(tl.anchor1/anchor2)` in legend title (XSS closed)

**Plan B — Async Optimizer**
- `backtest_optimizer.py`: `_OptJob` dataclass, thread-safe `_jobs` dict, async jobs with UUID
- `routes/backtest.py`: `GET /api/backtest/optimize` returns `{job_id}`; poll `GET /api/backtest/optimize/<job_id>`
- `static/js/09-analysis.js`: polls every 10s via `setInterval`

**Plan C — Agent Pipeline Cleanup**
- `consensus.py`: `compute_consensus()` + `add_gemini_consensus()` extracted — circular import broken
- `agent_types.py`: `ScannerSetup` TypedDict + `empty_interpreter()` / `empty_sentiment()` factories

**Plan D — Architecture Refactoring**
- `chart_context.py` split 774 → 275 lines into 4 focused modules (re-exports preserved):
  - `chart_candles.py`: `get_candles`, `get_candles_at_time`, cache
  - `chart_patterns.py`: `detect_trendlines`, `detect_all_trendlines`, `detect_fibonacci`
  - `chart_confluence.py`: all `_*_weight` functions, `confluence_score`, `SMT_SYMBOLS`
  - `chart_context.py`: thin orchestrator + re-exports (275 lines)
- `sync_base.py`: `_get_setting`, `_set_setting`, `SyncDriver` Protocol

---

### v1.4.0 — CCXT + Backtester + Chart Toggles
- CCXT replaces Blofin HMAC client (`ccxt_client.py`)
- Binance top-100 watchlist (`ai_scanner.py` lazy init)
- SMT Divergence — 9th confluence signal
- Vectorized backtester (`backtest_engine.py`, `backtest_metrics.py`)
- Optuna Bayesian optimizer (async)
- Chart layer toggles: Volume · WT · S/R · Trendlines · Fibonacci · Legend

---

## Confluence Signals (12 total)

| # | Signal | Scope | Weight |
|---|--------|-------|--------|
| 1 | RSI | TF-level | ±varies |
| 2 | MACD | TF-level | grouped ±1.5 cap |
| 3 | EMA | TF-level | ±varies |
| 4 | ADX | TF-level | ±varies |
| 5 | WaveTrend | TF-level | ±varies |
| 6 | MFI | TF-level | grouped ±1.0 cap |
| 7 | CVD | TF-level | ±varies |
| 8 | order_flow | TF-level | ±0.15 |
| 9 | volume | TF-level | ±varies |
| 10 | smt_weight | TF-level | +0.15 on divergence |
| 11 | smt_direction_weight | TF-level | ±0.15 |
| 12 | liquidation_wall | Symbol-level | +0.20 conditional |

**Caps:** `max_per_tf = 5.55` (non-SMT) / `5.85` (SMT) + `0.20` symbol-level (conditional)
**VIX multiplier:** score × 0.80 when VIX > 30 (5-min cached)
**SMT_SYMBOLS:** BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT

---

## Key Files

```
constants.py              VERSION="1.6.0", LIQ_TTL=900, ONCHAIN_TTL=3600,
                           REGIME_TTL=14400, ML_SCORER_TTL=86400, ML_MIN_SAMPLES=20
app.py                    Flask entry, 10 blueprints (incl. backtest)
database.py               SQLite schema + migrations 1–39
helpers.py                _ok, _err, log_token_usage, strip_fence, build_cached_messages
consensus.py              compute_consensus(), add_gemini_consensus()
agent_types.py            TypedDicts + ScannerSetup + empty_interpreter/empty_sentiment factories
agent_orchestrator.py     Pipeline runners (imports from consensus.py)
agent_trade_prep.py       Main Claude call; system_prompt = ANALYST_INSTRUCTIONS + RISK_INSTRUCTIONS
liquidation_levels.py     CCXT liquidation cluster detection (11th signal)
onchain_client.py         CoinMetrics Community MVRV + exchange net-flow (no key)
market_regime.py          3-state GaussianHMM on BTC 4H — trending_up/ranging/trending_down
signal_scorer.py          XGBoost win-probability; activates at ML_MIN_SAMPLES (20) outcomes
backtest_quality.py       PBO + Deflated Sharpe + Bootstrap CI; POST /api/backtest/quality
ai_scanner.py             3-stage scanner; _get_default_watchlist() lazy Binance fetch
ccxt_client.py            Factory: get_blofin_exchange, get_binance_exchange, get_binance_price
sync_base.py              _get_setting, _set_setting, SyncDriver Protocol
bitget_sync.py            Position/order/bill sync
blofin_sync.py            Blofin sync
backtest_engine.py        Vectorized backtester; _CONFLUENCE_DENOM named constant
backtest_metrics.py       Sharpe, Sortino, max_drawdown, profit_factor
backtest_optimizer.py     Optuna async jobs: start_optimizer_job(), get_job_status()
chart_candles.py          get_candles(), get_candles_at_time(), cache [extracted]
chart_patterns.py         detect_trendlines(), detect_all_trendlines(), detect_fibonacci() [extracted]
chart_confluence.py       confluence_score(), _liquidation_weight(), _order_flow_weight(),
                           all _*_weight functions, SMT_SYMBOLS [12 signals]
chart_context.py          Thin orchestrator: get_chart_context(), get_candles_for_chart() [275 lines]
chart_indicators.py       compute_all_indicators() + compute_order_flow_delta() [12th signal]
prompt_builder.py         On-chain block, HMM regime block, ML win-prob block
routes/backtest.py        POST /api/backtest/run, /quality; GET /api/backtest/optimize (async + poll)
static/js/09-analysis.js  Backtest card + polling optimizer (v3.7)
static/js/12-explorer.js  Chart Explorer with layer toggles (v3.1)
static/js/14-scanner.js   Scanner UI (v4.1)
templates/chart.html      Popout chart with layer toggles
tests/conftest.py         db, sample_positions, client fixtures
scripts/browser_test_sequence.json  Phase 1–4 browser test definitions
scripts/browser_test_report.html    Latest browser baseline (16/16 tabs clean)
.mcp.json                 chrome-devtools-mcp registered (bash -c wrapper)
.claude/settings.json     chrome-devtools-mcp permissions
```

---

## Known Gotchas

- **Mac has NO local database** — never rsync `*.db` files to Pi; Pi backup system in `backups/`
- **Always** `sudo systemctl restart trading-journal` — never `nohup python app.py`
- `signal_scorer` is **silent** until 20+ labeled outcomes exist in `analyzed_calls`
- chrome-devtools MCP requires local session + Chrome on port 9222 — does not work in cloud sessions
- `chart_context.py` re-exports all old names via imports from split modules — old callers unaffected
- `blofin_sync.py` still imports `_auto_close_calls` / `_retroactive_close_calls` from `bitget_sync` — full `sync_base` migration deferred
- Optimizer now async: `GET /api/backtest/optimize` returns `job_id`; poll `/<job_id>`
- Pi uses system Python3 + `--break-system-packages` for ccxt/optuna/pytest/hmmlearn/xgboost

---

## Deployment

```bash
git push
# Then deploy via SSH expect (credentials in local memory only)
# Pi runs: git pull && sudo systemctl restart trading-journal
# Backup auto-runs on every systemctl stop/restart (ExecStopPost)
```

**Pi path:** `/home/<user>/trading-journal` — credentials in local Claude memory only (not in repo)

---

## Next Priorities

1. **Re-run hindsight** — `POST /api/hindsight/run?n=200` to score more outcomes and reach ML_MIN_SAMPLES (20) to activate `signal_scorer`
2. **Accuracy accumulation** — rebuild `analyzed_calls` organically from live trade analysis
3. **MCP chrome-devtools fix** — tools not loading in cloud session; local path resolution issue in `.mcp.json`

---

## API Keys (in .env — never commit)

- `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `GROK_API_KEY`
- `BITGET_*`, `BLOFIN_*`, `NANSEN_*`, `COINALYZE_API_KEY`, `FINNHUB_API_KEY`
- `TELEGRAM_BOT_TOKEN`, `FRED_API_KEY`

---

## Versioning Policy

- v1.4.0 = CCXT + backtester + Optuna + chart toggles
- v1.5.0 = Optimisation: async, security, 238 tests, architecture refactoring
- v1.6.0 = Intelligence layer: liquidation clusters, order flow, on-chain, HMM regime, ML scorer, 442 tests
- v2.0 = Major new capability (new exchange, auth layer, new data tier)

---

*Claude Code memory files contain full detail.*
