# Session Resume Prompt

*Paste this entire block as your first message in a new Claude Code session to restore full context.*

---

## Context

We are continuing work on a self-hosted crypto futures trading journal.

**Project:** `/Users/fbauer/Documents/ClaudeAIData/Trading-Journal/`
**Version:** v1.4.0 (commit `e73de53`, deployed to Pi at 192.168.1.21:8082)
**Stack:** Python 3.13 / Flask 3.1 / SQLite WAL / Raspberry Pi 5
**GitHub:** https://github.com/anvilfilbert/Auto-Crypto-Tradingjournal

---

## What is built (v1.4.0 complete)

### 7-Agent Pipeline

All TypedDicts live in `agent_types.py`. Agents communicate via typed return values only.

```
DataCollector → [DataInterpreter + MarketSentiment (parallel)] → DataReviewer
→ TradePrep (Claude + Gemini) → RiskMgmt → AnalysisResult
                                                    ↓ [position opens]
                                             TradeMonitor (background, every 10 min)
```

### v1.4.0 — CCXT Integration + Backtester + Optimizer + Chart Toggles

**F1 — CCXT replaces Blofin HMAC client**
- `ccxt_client.py` — factory: `get_blofin_exchange()`, `get_binance_exchange()`, `get_binance_price()`, `get_binance_futures_symbols()`
- `blofin_client.py` — internals replaced with CCXT; all 4 public function signatures unchanged (`is_configured`, `test_connection`, `get_account_equity`, `get_open_positions`)
- `blofin_sync.py` unaffected (public API preserved)

**F2 — Binance watchlist expansion**
- `ai_scanner.py` now merges top-100 Binance USDT-M symbols (by 24h volume) into `DEFAULT_WATCHLIST` via `ccxt_client.get_binance_futures_symbols()`
- Falls back gracefully to Bitget-only list if Binance is unreachable at startup

**F3 — SMT Divergence (9th confluence signal)**
- `chart_context.py`: `SMT_SYMBOLS = {"BTCUSDT","ETHUSDT"}`, `_smt_weight(inds, symbol)`
- Returns +0.15 when Bitget and Binance prices agree within 0.5% (confirmation, no divergence)
- `max_val` updated 6.2 → 6.35 per TF
- Module-level `from ccxt_client import get_binance_price` (required for test patching)

**F4 — Embedded Backtester**
- `backtest_metrics.py` — Sharpe, Sortino, max_drawdown, profit_factor (adapted from Freqtrade GPL-3.0)
- `backtest_engine.py` — `BacktestParams/Trade/Result` dataclasses; vectorized `_compute_signals()` (RSI/EMA/WT/ADX/MFI/CVD); walk-forward `_simulate_trades()` (TP1 checked before TP2 — conservative); paginated `_fetch_ohlcv()` (Bitget caps at 200 candles/call, paginates via `endTime` cursor)
- `routes/backtest.py` — `POST /api/backtest/run` (n_trials≤200, days≤365 caps, ValueError guards)
- `09-analysis.js` — backtest card with symbol input, `► Run` button, `_renderBacktestResult()` (DOM/textContent only)

**F5 — Optuna Optimizer**
- `backtest_optimizer.py` — Bayesian search (maximize Sharpe over 7 params); guards against empty-trial crash
- `GET /api/backtest/optimize` — wrapped in try/except; returns `_err()` on failure
- `09-analysis.js` — `⚙ Optimize` button; shows progress message; disables both buttons during run; displays params as chips on completion

**Chart layer toggles**
- `templates/chart.html` (popout) + `static/js/12-explorer.js` (embedded explorer): toggle buttons in header/bar for `Volume · WT · S/R · Trendlines · Fibonacci · Legend`
- Each toggle stores series/priceLine refs and calls `series.applyOptions({visible})` or `pl.applyOptions({color:'rgba(0,0,0,0)'})`
- Explorer indicator panel redesigned: compact single table (label · value · sub) instead of large cards
- `_esc()` helper in chart.html escapes URL-sourced values before innerHTML insertion

**Security fixes (code review)**
- `blofin_client.test_connection()` — no longer leaks raw CCXT exception strings (CWE-209)
- `backtest_engine.py` — `import bitget_client` moved to module top level
- TP1/TP2 priority corrected: TP1 checked before TP2 within single candle (conservative)
- `backtest_optimizer.py` — `study.best_params` guarded against ValueError when no trials complete

### v1.3.0 — SMC/ICT + VMC Cipher Signal Improvements
- **R:R thresholds raised** — score 6=2:1, 7=2.5:1, 8=3:1, 9=3.5:1, 10=4:1
- **Premium/discount zone penalty** — LONG in premium or SHORT in discount → −1 score
- **Draw-on-liquidity TP guidance** — `DRAW_ON_LIQUIDITY_RULES` injected into all AI call stables
- **MFI standalone confluence signal** — `_mfi_weight()` ±0.3, dead-band ±10, max_val 5.9→6.2
- **Kill zone annotation** — setups outside London (07–10 UTC) or NY AM (12–15 UTC) tagged "⚠ Outside kill zone"
- **1H finalist timeframe** — agent pipeline uses `["1H", "4H", "1D"]` for top-N finalists
- **BOS/CHoCH reversal rubric** — CHoCH required for reversal; BOS alone scores ≤ 6
- 74 tests pass (30 new); 3 new test files

### v1.2.0 — Phase 4 UI/UX + Accuracy Accumulation
- **Retroactive outcome recorder** — `_retroactive_close_calls()` in bitget+blofin sync
- **Accuracy progress widget** — `/api/calls/accuracy-progress`, `ACCURACY_TARGET = 35`
- **Stale-data badge** — amber badge after 3 min without live trades refresh
- **Scanner ETA** — `~Xm remaining` shown during active scan

---

## Key Files

```
agent_types.py            All TypedDicts (CollectorResult, TradePrepResult, etc.)
agent_orchestrator.py     compute_consensus() + 3 pipeline runners
ai_scanner.py             3-stage + agent pipeline; Bitget+Binance watchlist merge
prompt_fragments.py       SCORING_SCALE, LEVEL_PROXIMITY_RULES, DRAW_ON_LIQUIDITY_RULES
prompt_builder.py         Stable prefix (cached) + dynamic context; BOS/CHoCH rubric
chart_context.py          confluence_score() — 9 signals: RSI/MACD/EMA/ADX/WT/MFI/CVD/vol/SMT
constants.py              MODEL, FAST_MODEL, GEMINI_*, VERSION="1.4.0", ACCURACY_TARGET=35
database.py               Migrations 1–31
bitget_sync.py            _retroactive_close_calls() + _auto_close_calls()
blofin_sync.py            Both close-calls functions wired in
blofin_client.py          CCXT-backed (public API unchanged); test_connection() safe error messages
ccxt_client.py            Factory: get_blofin_exchange, get_binance_exchange, get_binance_price, get_binance_futures_symbols
backtest_metrics.py       Sharpe, Sortino, max_drawdown, profit_factor (Freqtrade-adapted, GPL-3.0)
backtest_engine.py        BacktestParams/Trade/Result + vectorized signals + walk-forward simulation
backtest_optimizer.py     Optuna Bayesian optimizer (maximize Sharpe, 7 params)
routes/backtest.py        POST /api/backtest/run + GET /api/backtest/optimize
routes/calls.py           /api/calls/accuracy-progress endpoint
static/js/09-analysis.js  loadAccuracyProgress(), loadBacktest(), loadOptimizer() — v3.1
static/js/12-explorer.js  Chart Explorer with layer toggles + compact indicator panel — v3.0
templates/chart.html      Popout chart with layer toggles (Volume/WT/S&R/TL/Fib/Legend)
templates/index.html      Main SPA — 17 JS modules loaded
scripts/self_test.py      Smoke runner — --agents flag tests full pipeline
```

### Known gotchas
- Pi must run via `sudo systemctl restart trading-journal` — NOT `nohup python app.py` (service loads .env via EnvironmentFile=)
- Bitget candles API caps at 200/call — `_fetch_ohlcv` in backtest_engine.py paginates via `endTime` cursor
- `plt.switch_backend("Agg")` called inside `draw()` — must be after imports, not at module level
- `analyzed_calls.symbol` and `.direction` are NOT NULL — always explicitly set
- Pi needs `pip install ccxt optuna --break-system-packages` (system Python, PEP 668)
- Layer toggles in chart.html use `pl.__col` to store original price line color (for restoring when re-shown)
- Telegram alerts paused until user explicitly re-enables

---

## Deployment
- Pi IP: 192.168.1.21, credentials in memory `feedback_pi_ssh.md`
- After any push: `git fetch origin && git reset --hard origin/main && sudo systemctl restart trading-journal`
- **NEVER use `nohup python app.py`** — systemd service loads `.env` via `EnvironmentFile=`; running directly leaves all API keys empty
- Service: `trading-journal.service` (systemd), `ExecStart=/usr/bin/python3 app.py`

---

## Next Work (priority order)

1. **Hyblock Capital integration** — liquidation levels as 10th confluence signal (deferred: need account/credentials)
2. **F7 FreqAI** — deferred; review before implement
3. **Async optimizer** — `GET /api/backtest/optimize` currently blocks Flask worker for 5-15 min (C3 from code review); needs background thread + job poll
4. **Import-time Binance fetch** — `ai_scanner.py` calls `get_binance_futures_symbols()` at module load (I4); needs lazy init
5. **Accuracy accumulation** — retroactive recorder auto-populates; target 35 for statistical confidence
6. **Telegram alerts** — paused until user explicitly re-enables

---

## API Keys (in .env — never commit)
- `ANTHROPIC_API_KEY` — Claude
- `GEMINI_API_KEY` — Google Gemini
- `GROK_API_KEY` — xAI Grok
- `BITGET_*` — Bitget exchange (HMAC-SHA256)
- `BLOFIN_*` — Blofin exchange (via CCXT)
- `NANSEN_*` — Nansen smart money
- `TELEGRAM_BOT_TOKEN` — alerts (paused)
- `FRED_API_KEY` — macro data (free)

---

## Versioning Policy
- v1.0.1 = Core journal + consensus + backtest loop
- v1.1.0 = 7-agent pipeline + TradeMonitor + charts + Kelly
- v1.2.0 = Phase 4 UI/UX + retroactive outcome recorder
- v1.3.0 = SMC/ICT signal improvements (MFI, kill zones, R:R, premium/discount, CHoCH)
- v1.4.0 = CCXT + Binance watchlist + SMT Divergence + embedded backtester + Optuna + chart toggles
- v2.0 = Major new capability (new exchange, auth layer, new data tier)

---

*Memory files at `/Users/fbauer/.claude/projects/-Users-fbauer/memory/` contain full detail.*
