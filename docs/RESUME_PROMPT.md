# Session Resume Prompt

*Paste this entire block as your first message in a new Claude Code session to restore full context.*

---

## Context

We are continuing work on a self-hosted crypto futures trading journal.

**Project:** `/Users/fbauer/Documents/ClaudeAIData/Trading-Journal/`
**Version:** v1.0.1 (commit `9b07267`, deployed to Pi at 192.168.1.21:8082)
**Stack:** Python 3.13 / Flask 3.1 / SQLite WAL / Raspberry Pi 5
**GitHub:** https://github.com/anvilfilbert/Auto-Crypto-Tradingjournal

---

## What is built (v1.0.1 complete)

### AI Agent Pipeline
- **Master orchestrator:** `agent_orchestrator.py` — routes models, computes Claude vs Gemini consensus
- **Call Analyzer:** `ai_call.py` — Claude Sonnet 4.6, parallel Gemini pre-proof + Grok social intel, CoT stored + reused
- **Scanner:** `ai_scanner.py` — 3-stage: confluence filter → quality gate → Haiku quick-score → Sonnet batch → Gemini consensus top-5
- **Advisor:** `ai_advisor.py` — portfolio coaching, cached stable prefix
- **Hindsight / Trade Grader / Live Trade / Limit Analyzer:** Haiku (fast classification)
- **Gemini:** `gemini_client.py` — independent pre-proof scoring (Gemini 2.0 Flash)
- **Grok:** `grok_client.py` — xAI social intelligence, MC-weighted (micro-cap 80%, small 40%, mid 15%, large 0%)

### Consensus Logic
```
|Claude - Gemini| ≤ 1 → ✓ Confirmed
|Claude - Gemini| ≤ 2 → ~ Aligned
|Claude - Gemini| ≤ 3 → ⚠ Divergent
|Claude - Gemini| > 3 → ⚡ REVIEW (skip trade)
```

### Prompt Caching
- `build_stable_prefix()` → rulebook + calibration → `cache_control: ephemeral` (changes weekly)
- `build_context()` → backtest insights + market + chart + Nansen + Grok → not cached
- Expected savings: 40–60% on repeated calls

### Backtest Accuracy Loop
- `analytics.get_backtest_context()` injects historical WR by symbol/setup/weekday/hour into every prompt
- `scripts/backtest_consensus.py` measures H1/H2/H3 accuracy (need ~15–20 more outcomes for 85% target)

### Auto-Linking
- Scanner alerts saved to `analyzed_calls` (analyst='scanner') by `_persist_setups()` in `scanner_scheduler.py`
- `check-matches` auto-confirms scanner + closed calls against open positions

### Key Files
```
constants.py          — MODEL, FAST_MODEL, GEMINI_*, VERSION, CONSENSUS_*_DELTA
agent_orchestrator.py — compute_consensus(), route_model(), add_gemini_consensus()
gemini_client.py      — score_call(), score_setup()
grok_client.py        — get_coin_context(), grok_weight()
prompt_builder.py     — build_stable_prefix() + build_context()
helpers.py            — build_cached_messages() — places cache_control on stable block
analytics.py          — get_backtest_context()
ai_call.py            — full pipeline with parallel Gemini+Grok
ai_scanner.py         — 3-stage + Gemini consensus
routes/calls.py       — api_calls_linkable() includes status='closed'
scanner_scheduler.py  — _persist_setups() → analyzed_calls
database.py           — migrations 1–28; analyzed_calls has gemini_score, consensus_score, consensus_flag
docs/architecture.md  — ASCII flow maps
docs/architecture_detailed.pdf — 10-section PDF (beginners + experts)
scripts/self_test.py  — 54-test smoke runner (--host, --write, --ai)
scripts/backtest_consensus.py — accuracy measurement
```

### DB: migrations applied through #28
`analyzed_calls` table has 3 new columns: `gemini_score INTEGER`, `consensus_score REAL`, `consensus_flag TEXT`

---

## Deployment
- Pi IP: 192.168.1.21 — credentials in memory file `feedback_pi_ssh.md`
- After any push: SSH with expect + password, git reset --hard origin/main, restart service
- Service: `trading-journal` (systemd, always auto-restarts)

---

## Next Work (pick up here)

1. **Accuracy accumulation** — only 5 outcome-recorded calls exist; need ~15–20 more for 85% target. Run new calls through the analyzer and record outcomes.
2. **Hyblock Capital integration** — liquidation levels as 8th confluence signal. Spec: `docs/superpowers/specs/2026-05-09-architecture-review-and-optimisation.md` §9. Register at hyblockcapital.com, get OAuth2 creds, build `hyblock_client.py`.
3. **Phase 4 UI/UX** — stale-data badge on live trades, symbol autocomplete in call analyzer, scanner ETA display.

---

## API Keys (in .env — never commit)
- `ANTHROPIC_API_KEY` — Claude
- `GEMINI_API_KEY` — Google Gemini
- `GROK_API_KEY` — xAI Grok
- `BITGET_*` — Bitget exchange
- `BLOFIN_*` — Blofin exchange
- `NANSEN_*` — Nansen smart money
- `TELEGRAM_BOT_TOKEN` — alerts

---

## Versioning Policy
- Bump only on significant feature milestones (not bug fixes)
- v1.x = feature additions; v2.0 = major new capability (e.g. Hyblock integration + Phase 4 complete)
- Current: v1.0.1

---

*Memory files at `/Users/fbauer/.claude/projects/-Users-fbauer/memory/` contain full detail. Read `project_trading_journal.md` for the complete module map.*
