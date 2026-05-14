# Session Resume Prompt

*Paste this entire block as your first message in a new Claude Code session to restore full context.*

---

## Context

We are continuing work on a self-hosted crypto futures trading journal.

**Project:** `/Users/fbauer/Documents/ClaudeAIData/Trading-Journal/`
**Version:** v1.3.0 (commit `65e05dc`, deployed to Pi at 192.168.1.21:8082)
**Stack:** Python 3.13 / Flask 3.1 / SQLite WAL / Raspberry Pi 5
**GitHub:** https://github.com/anvilfilbert/Auto-Crypto-Tradingjournal

---

## What is built (v1.3.0 complete)

### 7-Agent Pipeline

All TypedDicts live in `agent_types.py`. Agents communicate via typed return values only.

```
DataCollector → [DataInterpreter + MarketSentiment (parallel)] → DataReviewer
→ TradePrep (Claude + Gemini) → RiskMgmt → AnalysisResult
                                                    ↓ [position opens]
                                             TradeMonitor (background, every 10 min)
```

### v1.3.0 — SMC/ICT + VMC Cipher Signal Improvements
- **R:R thresholds raised** — score 6=2:1, 7=2.5:1, 8=3:1, 9=3.5:1, 10=4:1 (was 1.5/2/2.5/3/4)
- **Premium/discount zone penalty** — LONG in premium or SHORT in discount zone → −1 score
- **Draw-on-liquidity TP guidance** — `DRAW_ON_LIQUIDITY_RULES` injected into all AI call stables
- **MFI standalone confluence signal** — `_mfi_weight()` ±0.3, dead-band ±10, max_val 5.9→6.2 per TF
- **Kill zone annotation** — setups outside London (07–10 UTC) or NY AM (12–15 UTC) tagged "⚠ Outside kill zone"
- **1H finalist timeframe** — agent pipeline uses `["1H", "4H", "1D"]` for top-N finalists
- **BOS/CHoCH reversal rubric** — reversal setups require CHoCH; BOS alone scores ≤ 6
- 74 tests pass (30 new); 3 new test files

### v1.2.0 — Phase 4 UI/UX + Accuracy Accumulation
- **Retroactive outcome recorder** — `_retroactive_close_calls()` in bitget+blofin sync; checks 1H OHLCV for saved calls >2h old
- **Accuracy progress widget** — `/api/calls/accuracy-progress`, `ACCURACY_TARGET = 35`
- **Stale-data badge** — amber badge after 3 min without live trades refresh
- **Scanner ETA** — `~Xm remaining` shown during active scan
- Symbol autocomplete confirmed via `_attachSymbolPicker` + `/api/exchange/symbols`

### v1.1.0 — 7-Agent Pipeline
- agent_types.py, agent_data_collector, agent_data_interpreter, agent_market_sentiment
- agent_data_reviewer, agent_risk_mgmt, agent_chart_draw, agent_trade_prep, agent_trade_monitor
- monitor_scheduler.py — background thread (10 min, polls positions)
- DB migrations 29-31: risk_verdict_json, monitor_alert, chart_png_b64

---

## Key Files

```
agent_types.py            All TypedDicts (CollectorResult, TradePrepResult, etc.)
agent_orchestrator.py     compute_consensus() + 3 pipeline runners
agent_chart_draw.py       Pure matplotlib chart — plt.switch_backend("Agg") inside draw()
ai_call.py                analyze_call() → delegates to orchestrator
ai_scanner.py             3-stage + agent pipeline Stage 3b; kill zone annotation
prompt_fragments.py       SCORING_SCALE, LEVEL_PROXIMITY_RULES, MARKET_CONTEXT_RULES, DRAW_ON_LIQUIDITY_RULES
prompt_builder.py         Stable prefix (cached) + dynamic context; LOQ rules + BOS/CHoCH rubric
chart_context.py          confluence_score() — 7 signals: RSI/MACD/EMA/ADX/WT/MFI/CVD + vol
constants.py              MODEL, FAST_MODEL, GEMINI_*, VERSION="1.3.0", ACCURACY_TARGET=35
database.py               Migrations 1–31
bitget_sync.py            _auto_close_calls() + _retroactive_close_calls()
blofin_sync.py            Both close-calls functions wired in
routes/calls.py           /api/calls/accuracy-progress endpoint
static/js/01-utils.js     openChart() — popup blocker detection
static/js/08-live.js      _startStalenessWatcher — stale-data badge
static/js/09-analysis.js  loadAccuracyProgress() — accuracy widget
static/js/14-scanner.js   ETA in _buildProgressBlock()
scripts/self_test.py      74 tests + --agents pipeline smoke test
```

### Known gotchas
- Pi must run via `sudo systemctl restart trading-journal` — NOT `nohup python app.py` (service loads .env)
- `plt.switch_backend("Agg")` called inside `draw()` — must be after imports, not at module level
- Claude returns `"tp1"` key (not `"tp1_price"`) — `ai_call.py` reads `result.get("tp1")` as fallback
- `analyzed_calls.symbol` and `.direction` are NOT NULL — always explicitly set

---

## Deployment
- Pi IP: 192.168.1.21, credentials in memory `feedback_pi_ssh.md`
- After any push: `git fetch origin && git reset --hard origin/main && sudo systemctl restart trading-journal`
- **NEVER use `nohup python app.py`** — systemd service loads `.env` via `EnvironmentFile=`; running directly leaves all API keys empty
- Service: `trading-journal.service` (systemd), `ExecStart=/usr/bin/python3 app.py`

---

## Next Work (priority order)

1. **Hyblock Capital integration** — liquidation levels as 8th confluence signal (deferred: need account/credentials). Spec: `docs/superpowers/specs/2026-05-09-architecture-review-and-optimisation.md` §9
2. **Accuracy accumulation** — retroactive recorder auto-populates; Pi at 17/35 at last check; target 35 for statistical confidence

---

## API Keys (in .env — never commit)
- `ANTHROPIC_API_KEY` — Claude
- `GEMINI_API_KEY` — Google Gemini
- `GROK_API_KEY` — xAI Grok
- `BITGET_*` — Bitget exchange
- `BLOFIN_*` — Blofin exchange
- `NANSEN_*` — Nansen smart money
- `TELEGRAM_BOT_TOKEN` — alerts
- `FRED_API_KEY` — macro data (free)

---

## Versioning Policy
- v1.0.1 = Core journal + consensus + backtest loop
- v1.1.0 = 7-agent pipeline + TradeMonitor + charts + Kelly
- v1.2.0 = Phase 4 UI/UX + retroactive outcome recorder
- v1.3.0 = SMC/ICT signal improvements (MFI, kill zones, R:R, premium/discount, CHoCH)
- v2.0 = Major new capability (new exchange, auth layer, new data tier)

---

*Memory files at `/Users/fbauer/.claude/projects/-Users-fbauer/memory/` contain full detail.*
