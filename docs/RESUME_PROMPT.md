# Session Resume Prompt

*Paste this entire block as your first message in a new Claude Code session to restore full context.*

---

## Context

We are continuing work on a self-hosted crypto futures trading journal.

**Project:** `/Users/fbauer/Documents/ClaudeAIData/Trading-Journal/`
**Version:** v1.5.0 (commit `2f6e029` + docs/version bump pending), Pi active at 192.168.1.21:8082
**Stack:** Python 3.13 / Flask 3.1 / SQLite WAL / Raspberry Pi 5
**GitHub:** https://github.com/anvilfilbert/Auto-Crypto-Tradingjournal

---

## What is built (v1.5.0 complete)

### v1.5.0 — Optimisation Sprint (Plans A–D)

**Plan A — Quick Wins**
- `tests/conftest.py`: `client` fixture with proper Flask stub save/restore on teardown — eliminated 3 pre-existing test ordering failures
- `backtest_engine.py`: confluence denominator named (`_CONFLUENCE_DENOM = 3.55`) via 6 named weight constants
- `static/js/12-explorer.js`: `_esc(tl.anchor1/anchor2)` in legend title attribute (M1 XSS closed)
- **Result: 216/216 tests green**

**Plan B — Async Optimizer + Performance**
- `backtest_optimizer.py`: `_OptJob` dataclass, thread-safe `_jobs` dict, `start_optimizer_job()` (daemon thread, returns UUID), `get_job_status()`
- `routes/backtest.py`: `GET /api/backtest/optimize` returns `{job_id}` immediately; new `GET /api/backtest/optimize/<job_id>` polling endpoint (errors return HTTP 500, not 400)
- `static/js/09-analysis.js`: `loadOptimizer()` polls every 10s via `setInterval`; `_renderOptimizerResult()` extracted
- `tests/test_performance_baseline.py`: 5 Pi endpoint tests (--host flag); baselines: backtest 30d<10s, 180d<30s, scanner status<200ms, dashboard<500ms, optimizer start<2s (all pass)
- **Pi stays responsive during 5-15 min optimizer runs**

**Plan C — Agent Pipeline Cleanup**
- `consensus.py` (new): `compute_consensus()` + `add_gemini_consensus()` extracted from `agent_orchestrator.py`
- `agent_trade_prep.py`: now imports `from consensus import compute_consensus` — circular import with `agent_orchestrator` broken
- `agent_types.py`: `ScannerSetup` TypedDict added; `empty_interpreter()` + `empty_sentiment()` factory helpers
- `tests/test_orchestrator_integration.py`: 3 tests — 5-stage pipeline contract, circular import verification, consensus importability

**Plan D — Architecture Refactoring**
- `chart_context.py`: 774 → 275 lines (−64%) — split into 4 focused modules, all old import paths preserved via re-exports
  - `chart_candles.py` (101 lines): `get_candles`, `get_candles_at_time`, cache
  - `chart_patterns.py` (225 lines): `detect_trendlines`, `detect_all_trendlines`, `detect_fibonacci`
  - `chart_confluence.py` (212 lines): all `_*_weight` functions, `confluence_score`, `SMT_SYMBOLS`
  - `chart_context.py` (275 lines): thin orchestrator (`get_chart_context`, `get_candles_for_chart`, `format_multi_tf_for_prompt`) + re-exports
- `sync_base.py` (new): `_get_setting`, `_set_setting`, `SyncDriver` Protocol — eliminates 20 lines of duplication between `bitget_sync.py` and `blofin_sync.py`

**Test baseline: 238 tests passing, 0 failing (was 100 at start of session)**

### v1.4.0 — CCXT Integration + Backtester + Chart Toggles
- F1: CCXT replaces Blofin HMAC client (`ccxt_client.py`, `blofin_client.py`)
- F2: Binance top-100 watchlist expansion (`ai_scanner.py` lazy init via `_get_default_watchlist()`)
- F3: SMT Divergence 9th confluence signal (`chart_confluence.py`)
- F4: Embedded vectorized backtester (`backtest_engine.py`, `backtest_metrics.py`, `routes/backtest.py`)
- F5: Optuna Bayesian optimizer — now async (`backtest_optimizer.py`)
- Chart layer toggles: Volume · WT · S/R · Trendlines · Fibonacci · Legend (popout + explorer)

### v1.3.0 — SMC/ICT + VMC Cipher Signal Improvements
- R:R thresholds raised, premium/discount zone penalty, MFI standalone signal
- Kill zone annotation, 1H finalist timeframe, BOS/CHoCH reversal rubric

---

## Key Files

```
constants.py              MODEL, FAST_MODEL, GEMINI_*, VERSION="1.5.0", ACCURACY_TARGET=35
app.py                    Flask entry, 10 blueprints (added backtest)
database.py               SQLite schema + migrations 1–31
helpers.py                _ok, _err, log_token_usage, strip_fence, build_cached_messages
consensus.py              compute_consensus(), add_gemini_consensus() [extracted from orchestrator]
agent_types.py            All TypedDicts + ScannerSetup + empty_interpreter/empty_sentiment factories
agent_orchestrator.py     Pipeline runners (imports from consensus.py)
agent_trade_prep.py       Main Claude call (imports from consensus.py — no circular dep)
ai_scanner.py             3-stage scanner; _get_default_watchlist() lazy Binance fetch
ccxt_client.py            Factory: get_blofin_exchange, get_binance_exchange, get_binance_price
blofin_client.py          CCXT-backed (public API unchanged)
sync_base.py              _get_setting, _set_setting, SyncDriver Protocol
bitget_sync.py            Position/order/bill sync (imports from sync_base)
blofin_sync.py            Blofin sync (imports from sync_base)
backtest_engine.py        Vectorized backtester; _CONFLUENCE_DENOM named constant
backtest_metrics.py       Sharpe, Sortino, max_drawdown, profit_factor
backtest_optimizer.py     Optuna async jobs: start_optimizer_job(), get_job_status()
chart_candles.py          get_candles(), get_candles_at_time(), cache [extracted]
chart_patterns.py         detect_trendlines(), detect_all_trendlines(), detect_fibonacci() [extracted]
chart_confluence.py       confluence_score(), all _*_weight functions, SMT_SYMBOLS [extracted]
chart_context.py          Thin orchestrator: get_chart_context(), get_candles_for_chart() [275 lines]
routes/backtest.py        POST /api/backtest/run, GET /api/backtest/optimize (async + polling)
static/js/09-analysis.js  Backtest card + polling optimizer (v3.2)
static/js/12-explorer.js  Chart Explorer with layer toggles + compact indicator panel (v3.1)
templates/chart.html      Popout chart with layer toggles
tests/conftest.py         db, sample_positions, client fixtures (client restores Flask stub on teardown)
tests/test_performance_baseline.py  Live Pi perf tests (--host flag required)
```

### Known gotchas
- Pi uses system Python3 + `--break-system-packages` for ccxt/optuna/pytest
- `sudo systemctl restart trading-journal` ALWAYS — never `nohup python app.py`
- Optimizer now async — `GET /api/backtest/optimize` returns job_id; poll `/<job_id>`
- `_evict_old_jobs()` must be called with `_jobs_lock` held (documented in code)
- `chart_context.py` re-exports all old names via `from chart_candles/patterns/confluence import ...`
- `blofin_sync.py` still has `from bitget_sync import _auto_close_calls, _retroactive_close_calls` — full sync_base migration deferred

---

## Deployment
- Pi IP: 192.168.1.21, credentials in memory `feedback_pi_ssh.md`
- After push: `git pull && sudo systemctl restart trading-journal`
- **NEVER use `nohup python app.py`**

---

## Next Work (priority order)

1. **LLM provider unification** — Anthropic/Gemini/Grok behind single `LLMProvider` interface (v1.6, L effort)
2. **Migrate `_auto_close_calls` + `_retroactive_close_calls` into `sync_base.py`** — shrinks `bitget_sync` to pure driver
3. **`init_db()` try/finally** — conn.close() on exception (was C2 in code review, deferred — needs re-indenting 300 lines)
4. **Hyblock Capital** — liquidation levels as 10th confluence signal (needs credentials)
5. **Accuracy accumulation** — target 35 outcome-recorded calls
6. **Telegram alerts** — paused until user explicitly re-enables
7. **FreqAI (F7)** — deferred; review before implement

---

## API Keys (in .env — never commit)
- `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `GROK_API_KEY`
- `BITGET_*`, `BLOFIN_*`, `NANSEN_*`, `TELEGRAM_BOT_TOKEN`, `FRED_API_KEY`

---

## Versioning Policy
- v1.4.0 = CCXT + backtester + Optuna + chart toggles
- v1.5.0 = Optimisation: async, security, 238 tests, architecture refactoring
- v2.0 = Major new capability (new exchange, auth layer, new data tier)

---

*Memory files at `/Users/fbauer/.claude/projects/-Users-fbauer/memory/` contain full detail.*
