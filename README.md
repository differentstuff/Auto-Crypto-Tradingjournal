# Auto-Trader

> **Disclaimer:** Vibe-coded with [Claude Code](https://claude.ai/code). Not reviewed by professional security experts. Use at your own risk.

Self-hosted, 24/7 automated crypto futures trading system. Runs a reaction network of enzymes that sense market conditions, evaluate setups, regulate risk, and learn from every outcome. Designed for a Raspberry Pi 5 or any Linux box.

---

## What It Does

The daemon runs continuous cycles: load strategy config → sense market → evaluate signals → approve or wait → execute trades → record outcomes → learn. Each cycle is driven by **enzymes** — small, condition-activated modules that fire only when their preconditions are met. A shared **substrate** holds all state. The system converges toward **attractors** (goal states like `trade_opened`, `trade_closed`, `learning_updated`).

In practice: you write a strategy in YAML (which symbols, which indicators, what risk limits, what entry/exit rules). The daemon trades it. It tracks which signals actually predicted wins, suppresses the ones that didn't, and rewrites its own rulebook. Over time it adapts to what works.

---

## Why Reaction Network Design

The original codebase used a linear 7-agent pipeline. Every cycle ran every agent in fixed order regardless of whether it was needed. That design has three problems:

1. **Wasted compute.** Running a full sentiment analysis when there's no signal is burning tokens for nothing.
2. **No self-improvement.** Agents produce output and pass it along. Nothing measures whether individual signals are actually predictive.
3. **Fragile flow.** A failure or timeout in agent 3 blocks agents 4–7.

The reaction network fixes all three:

- **Enzymes fire on condition.** No signal? The `Wait` enzyme activates. Nothing else runs. Strong confluence? Sensors, evaluators, and regulators fire in priority order. Compute scales with opportunity.
- **Learning is mandatory.** Every closed trade updates per-signal accuracy (Wilson score intervals). Signals below 55% accuracy get suppressed. Signals below 30% get inverted as contrarian indicators. Weights adjust automatically after enough samples.
- **Regulator enzymes have override authority.** `ApproveTrade` and `ApproveExit` (the RiskManager) always fire first when activatable. No trade executes without regulator clearance. The system cannot bypass its own risk rules.
- **Substrate, not contracts.** All enzymes read/write a single shared state container. No typed contract drift between agents. Any enzyme can observe any substrate field; it only writes to its designated output.
- **Wait is default.** The market owes us nothing. No strong signal = no action. Idle cycles are tracked and feed the learning engine (prevents false high win rates from simply not trading during bad conditions).

---

## Current State

Fully operational. All core modules are built and tested:

| Component | Status |
|-----------|--------|
| Daemon loop (24/7, hot-reload config) | Working |
| Substrate (shared state, ISC verification, DB persistence) | Working |
| 19 enzymes (sensors, evaluators, regulators, transporters, synthases) | Working |
| Indicator registry (RSI, MACD, EMA, ADX, ATR, S/R, OBV, VWAP, etc.) | Working |
| Learning engine (per-signal accuracy, pairwise combinations, trajectory, weight adjuster, rulebook) | Working |
| LLM router (multi-provider: Anthropic, Gemini, OpenRouter, Grok; key rotation on 429/529) | Working |
| Exchange integration (CCXT, paper mode guard, multi-position tracking) | Working |
| Strategy YAML (hot-pluggable, per-strategy UID for isolated learning) | Working |
| Backtester + quality metrics (PBO, Deflated Sharpe, Bootstrap CI) | Working |
| Soft penalties (noise, confluence, trajectory — replace hard-gate ISCs 005/006/007) | Working |
| Learning-adjusted thresholds (penalty ratios auto-tuned from trade outcomes) | Working |
| Setup script, smoke tests, systemd service | Working |

**Not yet implemented** (defined in design docs, not in code):
- On-chain metrics (MVRV, exchange flow) — module toggle exists, code not ported
- HMM regime detection — module toggle exists, code not ported
- Sentiment / Nansen / Grok / liquidation clusters — module toggle exists, code not ported
- Browser UI — not needed for daemon operation, not ported

---

## Architecture

```
DAEMON (24/7 loop)
  │
  ▼
SUBSTRATE (shared state)
  strategy | portfolio | market | analysis | decisions | learning | validity
  │
  ├── SENSOR enzymes      — CollectOHLCV, CollectPreTradeContext, CollectMacroContext
  ├── EVALUATOR enzymes   — ScoreConfluence, ValidateEntryZone, DetectNoise
  ├── REGULATOR enzymes   — ApproveTrade, ApproveExit (RiskManager, override authority)
  ├── TRANSPORTER enzymes — ExecuteTrade, ExecuteExit, SyncPositions, SendTelegramLog
  ├── SYNTHASE enzymes    — UpdateLearning, UpdateRulebook, RecordTradeOutcome
  └── ISOMERASE enzyme    — Wait (default, fires when nothing else can)
```

Each cycle: find activatable enzymes → regulators fire first → highest flux-score enzyme transforms the substrate → verify ISC conditions → apply soft penalties → repeat until attractor reached or no enzyme can fire.

**Hard ISCs (001–004)** are non-negotiable gates: entry threshold, stop-loss set, risk limit, max positions. **Soft penalties** (noise, confluence, trajectory) reduce the effective score instead of blocking trades entirely, allowing the learning engine to collect data from penalized trades and auto-tune penalty ratios over time.

---

## Strategy Configuration

Strategies are YAML files. The daemon picks up changes on every cycle — no restart needed.

```yaml
description: |
  Enter long when momentum indicators start rising before price moves
  at structural support on 4h candles.

strategy:
  name: momentum_rising
  timeframe: "4h"
  max_positions: 3
  cycle_interval_minutes: 15

symbols:
  always_watch: [BTCUSDT, ETHUSDT, SOLUSDT]
  dynamic_filter:
    limit: 15
    criteria: "momentum"

indicators:
  - name: "rsi"
    weight: 0.25
  - name: "macd"
    weight: 0.25
  - name: "ema_stack"
    weight: 0.30
  - name: "adx"
    weight: 0.20

scoring:
  entry_threshold: 6.5
  confluence_min_signals: 3

exit_rules:
  hard_stop:
    placement: "below_support"
    width_atr_multiplier: 1.5
  trailing_stop:
    enabled: true
    activation_profit_pct: 0.5

learning:
  min_trades_before_adjusting: 30
  rulebook_max_rules: 10
  track_idle_cycles: true
```

See `config/strategies/momentum_rising.yaml` for the full template with all options.

---

## Learning Engine

The learning engine is what makes this more than a rule-based bot.

1. **Per-signal accuracy.** After each closed trade, every indicator signal at entry is checked against the outcome. Wilson score intervals provide confidence bounds. Verdicts: valid (≥75%), monitor (55–75%), suppress (45–55%, coin flip), contrarian (≤30%, invert the signal), review (30–45%).
2. **Weight adjustment.** Valid signals get boosted. Suppressed signals get zeroed. Contrarian signals get negative weights — their bullish contribution is subtracted instead of added. All changes are recorded in `weight_history` for auditing.
3. **Pairwise combinations.** RSI + MACD both bullish might be 83% accurate while either alone is 55%. The combination tracker finds statistically significant pairs.
4. **Trajectory analysis.** Were indicators aligning gradually over 12 bars, or snapping together by coincidence? The trajectory module scores `coincidence_risk` before entry.
5. **Rulebook generation.** Auto-generated from findings. Max 10 rules. Sharp, not verbose. Refreshed every retrain cycle.
6. **Idle cycle tracking.** When no trade was made, the system notes why. This prevents false "high win rate" from simply not trading during bad conditions.
7. **Soft penalties with learning feedback.** Noise, low confluence, and trajectory coincidence no longer hard-block trades (former ISC-005/006/007). Instead they apply multiplicative penalties: `effective_score = raw_score × (1 - noise) × (1 - confluence) × (1 - trajectory)`. The learning engine adjusts penalty ratios based on trade outcomes — if penalized trades win more than average, penalties are reduced; if they lose more, penalties increase.

Each strategy has a stable UID. Learning data is keyed to that UID, so two strategies can share the same database without collisions. Clear the UID to reset learning from scratch.

---

## Setup

```bash
bash setup.sh
```

Idempotent. Installs Python 3.13, creates venv, installs dependencies, copies config templates, runs smoke tests. Safe to re-run.

Then edit `.env` with your API keys. Minimum: an OpenRouter key (free-tier models available). Paper mode needs no exchange keys.

```bash
source venv/bin/activate
python main.py --paper --strategy paper_test --cycle-once   # quick test
python main.py --paper --strategy momentum_rising            # run continuously
```

---

## Directory Structure

```
main.py                   Single entrypoint
config/
  default.yaml            All defaults (never hand-edit)
  llm.yaml                LLM routing config
  strategies/             Strategy YAML files (hot-pluggable)
core/
  daemon.py               24/7 loop, config hot-reload
  substrate.py            Shared state container, ISC verification
  enzyme.py               Enzyme base class + registry
  config_loader.py        YAML config merge + validation
  database.py             SQLite WAL (persisted state, learning data)
  exchange.py             CCXT wrapper (paper mode guard)
  scheduler.py            Cycle cadence
enzymes/                  19 enzymes, one file each
indicators/               Indicator computations + registry
learning/                 Accuracy, combinations, trajectory, weight adjuster, rulebook
llm/                      Multi-provider router, key manager, prompt builder
tools/                    Utility scripts
scripts/                  backup_db.sh, migrate_db.py, verify_e2e.sh
tests/                    Unit + integration tests
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Runtime | Python 3.13 |
| State | SQLite WAL |
| Exchange | CCXT (Binance, Bitget, Bybit, OKX) |
| LLM | Anthropic Claude · Google Gemini · OpenRouter · xAI Grok |
| ML | scikit-learn · XGBoost · hmmlearn |
| Alerts | Telegram Bot API |
| Host | Raspberry Pi 5 / systemd |

---

## Design References

- `docs/reaction-design/README.md` — Full architecture plan
- `docs/reaction-design/substrate-schema.yaml` — Substrate state structure
- `docs/reaction-design/enzyme-definitions.yaml` — All enzyme activation conditions
- `docs/reaction-design/strategy-template.yaml` — Strategy config template
- `docs/reaction-design/learning-engine.md` — Learning engine design
- `docs/reaction-design/migration-plan.md` — Migration roadmap from original codebase

---

## License

This project is licensed under [Apache 2.0 with Commons Clause](./LICENSE).

Not free for commercial use: selling, SaaS, or commercial deployment requires permission — see LICENSE file.