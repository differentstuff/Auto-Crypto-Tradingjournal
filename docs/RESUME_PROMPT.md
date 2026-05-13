# Session Resume Prompt

*Paste this entire block as your first message in a new Claude Code session to restore full context.*

---

## Context

We are continuing work on a self-hosted crypto futures trading journal.

**Project:** `/Users/fbauer/Documents/ClaudeAIData/Trading-Journal/`
**Version:** v1.1.0 (commit `080b408`, deployed to Pi at 192.168.1.21:8082)
**Stack:** Python 3.13 / Flask 3.1 / SQLite WAL / Raspberry Pi 5
**GitHub:** https://github.com/anvilfilbert/Auto-Crypto-Tradingjournal

---

## What is built (v1.1.0 complete)

### 7-Agent Pipeline (NEW in v1.1.0)

All TypedDicts live in `agent_types.py`. Agents communicate via typed return values only.

```
DataCollector → [DataInterpreter + MarketSentiment (parallel)] → DataReviewer
→ TradePrep (Claude + Gemini) → RiskMgmt → AnalysisResult
                                                    ↓ [position opens]
                                             TradeMonitor (background, every 10 min)
```

**New files:**
- `agent_types.py` — all TypedDict contracts
- `agent_data_collector.py` — parallel fetch (OHLCV, funding, OI, F&G, FRED, Nansen, Grok)
- `agent_data_interpreter.py` — pure indicator transforms
- `agent_market_sentiment.py` — pure sentiment: contra_signal, crowd_position, funding_bias
- `agent_data_reviewer.py` — signal quality gate (0-10) + KPIs from DB
- `agent_risk_mgmt.py` — pure math: sizing + Kelly criterion (0.05–0.25)
- `agent_chart_draw.py` — mplfinance annotated PNG (entry/SL/TP + criteria)
- `agent_trade_prep.py` — main Claude + Gemini call, assembles all agents
- `agent_trade_monitor.py` — Haiku chain, fires Telegram + UI badge on risk_rating ≥ 7
- `monitor_scheduler.py` — background thread (10 min, polls positions)

**Modified files (same external API):**
- `agent_orchestrator.py` — gained `run_call_analysis()`, `run_scanner_prep()`, `run_monitor()`
- `ai_call.py` — delegates to `run_call_analysis()`
- `ai_scanner.py` — Stage 3b calls `run_scanner_prep()` per finalist
- `ai_live_trade.py` — delegates to `run_monitor()`
- `telegram_notify.py` — gained `send_photo()` + chart attachment to scanner alerts

**DB migrations 29-31:** `analyzed_calls` has `risk_verdict_json`, `monitor_alert`, `chart_png_b64`

### Consensus Logic (from v1.0.1)
```
|Claude - Gemini| ≤ 1 → ✓ Confirmed
|Claude - Gemini| ≤ 2 → ~ Aligned
|Claude - Gemini| ≤ 3 → ⚠ Divergent
|Claude - Gemini| > 3 → ⚡ REVIEW (skip trade)
```

### Prompt Caching (from v1.0.1)
- `build_stable_prefix()` → rulebook + calibration → `cache_control: ephemeral`
- `build_context()` → backtest + market + chart + Nansen + Grok → not cached
- Expected savings: 40–60% on repeated calls

---

## Key Files

```
agent_types.py            All TypedDicts (CollectorResult, TradePrepResult, etc.)
agent_orchestrator.py     compute_consensus() + 3 pipeline runners
ai_call.py                analyze_call() → delegates to orchestrator
ai_scanner.py             3-stage + agent pipeline Stage 3b
monitor_scheduler.py      Background position monitor
constants.py              MODEL, FAST_MODEL, GEMINI_*, VERSION="1.1.0", MONITOR_*
database.py               Migrations 1–31
routes/calls.py           api_calls_save() reads _sizing, risk_reward, bitget_settings from result
scripts/self_test.py      54 tests + --agents pipeline smoke test
scripts/generate_architecture_pdf.py  reportlab PDF generator
docs/architecture.md      ASCII flow maps
docs/architecture_detailed.pdf  10-section PDF
```

---

## Deployment
- Pi IP: 192.168.1.21, credentials in memory `feedback_pi_ssh.md`
- After any push: SSH via expect + password, `git reset --hard origin/main`, restart service
- Service runs via `python app.py` (not gunicorn)

---

## Next Work (priority order)

1. **Accuracy accumulation** — need ~15–20 outcome-recorded calls for 85% target (currently ~5)
2. **Hyblock Capital integration** — liquidation levels as 8th confluence signal. Spec: `docs/superpowers/specs/2026-05-09-architecture-review-and-optimisation.md` §9
3. **Phase 4 UI/UX** — stale-data badge on live trades, symbol autocomplete, scanner ETA display
4. **Architecture PDF regeneration** — `python3 scripts/generate_architecture_pdf.py` to update `docs/architecture_detailed.pdf` with v1.1.0 agent pipeline

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
- v1.0.x = bug fixes and minor additions (continuous)
- v1.1 = 7-agent pipeline + TradeMonitor + charts + Kelly
- v2.0 = major new capability (new exchange, auth layer, new data tier)

---

*Memory files at `/Users/fbauer/.claude/projects/-Users-fbauer/memory/` contain full detail.*
