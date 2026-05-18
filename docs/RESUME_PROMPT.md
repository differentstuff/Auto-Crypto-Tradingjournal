# Session Resume Prompt

*Paste this entire block as your first message in a new Claude Code session to restore full context.*

---

## Context

We are continuing work on a self-hosted crypto futures trading journal.

**Project:** `/Users/fbauer/Documents/ClaudeAIData/Trading-Journal/` (local clone)
**Latest commit:** `54bcca0` — deployed to Pi 2026-05-18
**Version:** v1.6.0 + post-release fixes (no version bump for bug-fix releases)
**Stack:** Python 3.13 / Flask 3.1 / SQLite WAL / Raspberry Pi 5 at 192.168.1.21:8082
**GitHub:** https://github.com/anvilfilbert/Auto-Crypto-Tradingjournal
**Tests:** 467 passing (`python3 -m pytest tests/ -v`)

---

## What is complete (v1.6.0 + post-release)

### v1.6.0 — Intelligence Layer

| Feature | Module | Notes |
|---------|--------|-------|
| Liquidation cluster (11th signal) | `liquidation_levels.py` | ±0.20 conditional, CCXT |
| Order flow delta (12th signal) | `chart_indicators.py` | ±0.15 per-TF, tick-rule |
| On-chain MVRV + flows | `onchain_client.py` | CoinMetrics Community, keyless |
| HMM regime detection | `market_regime.py` | 3-state BTC 4H, migration 38 |
| ML win-probability | `signal_scorer.py` | XGBoost, migration 39, activates at 20+ outcomes |
| Backtest quality | `backtest_quality.py` | PBO + Deflated Sharpe + Bootstrap CI |
| Structured AI prompts | `agent_trade_prep.py` | 6-section ANALYST_INSTRUCTIONS + RISK_INSTRUCTIONS |
| Browser a11y baseline | — | 16/16 tabs, 4/4 100% a11y, 42 aria-label fixes |

### Post-v1.6.0 fixes (2026-05-18)

**Gemini AI fallback** (`ai_client.py`)
- `send()` catches `anthropic.APIError` → calls `gemini_client.send_text()` transparently
- All 10+ AI modules fallback with no per-module changes; logged as `{module}+gemini`
- 5 tests: `tests/test_ai_client_fallback.py`

**Scanner stale-alert guard** (`scanner_scheduler.py`)
- 4-layer price proximity filter in `_enrich_and_filter_setups()`:
  1. No entry_ref → drop
  2. Entry >20% from current price → drop
  3. Directional drift >5% → drop
  4. Price fetch exception → drop (fail-closed)
- 5 tests: `tests/test_scanner_price_filter.py`
- Fixed: KITEUSDT false alert (entry $0.146 when price was $0.2399)

**Scanner timeframe normalization** (`static/js/14-scanner.js` v4.2)
- `_VALID_TF` Set maps `"Multi-TF (1D/4H/1H)"` → valid Bitget granularity before chart URL
- Fixed: LABUSDT "no candle data" error

**Chart legend panel** (`templates/chart.html`)
- `?` button in header → inline collapsible panel explaining all chart abbreviations
- 7 sections: Trade Levels · S/R · Trendlines · Fibonacci · Liquidation · WaveTrend · Volume
- Static HTML, no `innerHTML`; toggles via `panel.classList.toggle('open')`

**Pending orders UX** (`static/js/10-pending.js` v3.5)
- `↗ Pop Out` button overlaid on chart thumbnail → opens `chart.html` popup
- JSON-in-summary guard: `summary.trimStart().startsWith('{')` → extract `entry_reason` or show retry hint
- `ai_limit.py` max_tokens: 768 → 1024

**Blofin live positions fix** (`blofin_client.py`)
- `get_open_positions()` now returns full Bitget-compatible shape
- Was missing: `direction`, `margin_usdt`, `size_usdt`, `mark_price`, `unrealized_pct`, `liquidation_price`, `stop_loss`, `take_profit`, `margin_mode`, `exchange`, `duration_minutes`
- Crashed JS: `p.direction.toLowerCase()` when Blofin positions present; caused NaN in margin KPI
- `static/js/08-live.js` v3.4: defensive `(p.direction||'long').toLowerCase()` guard added

---

## Current JS versions

| File | Version |
|------|---------|
| `08-live.js` | v3.4 |
| `10-pending.js` | v3.5 |
| `14-scanner.js` | v4.2 |
| `chart.html` | (legend panel) |

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

`max_per_tf = 5.55` (non-SMT) / `5.85` (SMT) + `0.20` symbol-level conditional  
VIX multiplier: score × 0.80 when VIX > 30

---

## Key Files

```
constants.py              VERSION="1.6.0", model names, all TTL constants
app.py                    Flask entry, 10 blueprints, starts sync + scanner threads
database.py               SQLite schema + migrations 1–39
ai_client.py              Anthropic wrapper + Gemini fallback on APIError
gemini_client.py          send_json() (consensus) + send_text() (fallback)
blofin_client.py          CCXT-based; get_open_positions() now Bitget-compatible shape
scanner_scheduler.py      30-min daemon + 4-layer price proximity guard
scanner_stages.py         Stage 1/2/3 pipeline, HTF→LTF (1D/4H/1H)
agent_trade_prep.py       Main Claude call; ANALYST_INSTRUCTIONS + RISK_INSTRUCTIONS
liquidation_levels.py     CCXT liquidation cluster detection
onchain_client.py         CoinMetrics Community MVRV + exchange net-flow (no key)
market_regime.py          3-state GaussianHMM BTC 4H
signal_scorer.py          XGBoost win-probability; silent until 20+ outcomes
backtest_quality.py       PBO + Deflated Sharpe + Bootstrap CI
chart_confluence.py       12 signals, SMT_SYMBOLS, _liquidation_weight, _order_flow_weight
chart_context.py          Thin orchestrator (275 lines)
prompt_builder.py         on-chain block + HMM regime block + ML win-prob block
templates/chart.html      Pop-out chart; ? button → legend panel
static/js/08-live.js      Live Trades; v3.4 — Blofin direction guard
static/js/10-pending.js   Pending Orders; v3.5 — pop-out button, JSON-in-summary fix
static/js/14-scanner.js   Setup Scanner; v4.2 — timeframe normalization
tests/test_ai_client_fallback.py    5 tests for Gemini fallback
tests/test_scanner_price_filter.py  5 tests for price proximity guard
```

---

## Known Gotchas

- **Mac has NO local database** — never rsync `*.db` to Pi; Pi backup system in `backups/`
- **Always** `sudo systemctl restart trading-journal` — never `nohup python app.py`
- `signal_scorer` is **silent** until 20+ labeled outcomes exist in `analyzed_calls`
- `chart_context.py` re-exports all old names via imports from split modules — old callers unaffected
- Optimizer is async: `GET /api/backtest/optimize` returns `job_id`; poll `GET /api/backtest/optimize/<job_id>`
- Pi uses system Python3 + `--break-system-packages` for ccxt/optuna/pytest/hmmlearn/xgboost
- Blofin `get_open_positions()` uses CCXT `fetch_positions()` — shape now fully normalized to Bitget format

---

## Deployment

```bash
git push
# Then SSH expect deploy (credentials in local Claude memory only):
expect -c "
  spawn ssh -o StrictHostKeyChecking=no fbauer@192.168.1.21
  expect 'password:'; send 'laZHn0rd\r'
  expect '$'
  send 'cd /home/fbauer/trading-journal && git fetch origin && git reset --hard origin/main && sudo systemctl restart trading-journal\r'
  expect '$'; send 'exit\r'; expect eof
"
```

**Pi path:** `/home/fbauer/trading-journal`  
**Last backup:** `backups/trading_journal_20260518_182503.db` (4.9MB)

---

## Next Priorities

1. **Re-run hindsight** — `POST /api/hindsight/run?n=200` to score more outcomes and reach ML_MIN_SAMPLES (20) to activate `signal_scorer`
2. **Accuracy accumulation** — rebuild `analyzed_calls` organically from live trade analysis
3. **PDF docs** — regenerate `architecture_detailed.pdf` and factsheet when ready for external sharing

---

## API Keys (in .env — never commit)

- `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `GROK_API_KEY`
- `BITGET_*`, `BLOFIN_*`, `NANSEN_*`, `COINALYZE_API_KEY`, `FINNHUB_API_KEY`
- `TELEGRAM_BOT_TOKEN`, `FRED_API_KEY`

---

## Versioning Policy

- v1.4.0 = CCXT + backtester + Optuna + chart toggles
- v1.5.0 = Optimisation: async, security, architecture refactoring
- v1.6.0 = Intelligence layer: liquidation clusters, order flow, on-chain, HMM regime, ML scorer
- Post-release = continuous bug-fix commits, no version bump
- v2.0 = Major new capability (new exchange, auth layer, new data tier)

---

*Claude Code memory files contain full detail. Read `CLAUDE.md` for deployment rules and code invariants.*
