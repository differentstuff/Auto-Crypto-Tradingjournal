# Migration Plan

> Phased roadmap from the current codebase to the Reaction Network architecture.

---

## Strategy: Fork, Not Rewrite

We create a new branch `feature/auto-trader` from the current `main`. The current codebase stays untouched on `main`. We build the new system alongside it, porting what works and redesigning what needs change.

**Key principle:** Port the math, redesign the architecture. The indicator calculations, exchange clients, and database layer are solid. The agent pipeline, config system, and learning loop need to be rebuilt.

---

## Phase A: Foundation (Week 1)

### Goal: New directory structure, substrate, enzyme framework, YAML config

### Tasks

| Task | Description | Source to Port |
|------|-------------|---------------|
| Create branch | `git checkout -b feature/auto-trader` | - |
| Create directory structure | Set up `core/`, `enzymes/`, `indicators/`, `learning/`, `llm/`, `tools/`, `config/` | - |
| Build substrate.py | State container class with dot-access, JSON serialization, ISC verification | `agent_types.py` (TypedDict structure as reference) |
| Build enzyme.py | Base class with `can_activate()`, `transform()`, `flux_score()`, class hierarchy | `../ReactionNetworkModel.md` (enzyme model) |
| Build database.py | Port SQLite WAL, add new learning tables | `database.py` (init_db, get_conn, db_conn) |
| Build config loader | YAML config reader with hot-reload, defaults merging | NEW (replaces `constants.py`) |
| Create default.yaml | All current hardcoded constants moved to YAML | `constants.py` (all values) |
| Create exchange.yaml template | API keys structure, endpoint config | `.env.example` |
| Create strategy-template.yaml | Full strategy template with momentum_rising defaults | NEW |
| Build daemon.py | 24/7 loop: load config, run network, persist state, sleep | `app.py` (startup), `monitor_scheduler.py` (loop pattern) |
| Build scheduler.py | Cycle timing, interval management | `monitor_scheduler.py`, `scanner_scheduler.py` |

### What We Get

- A running daemon that loads YAML config and prints "cycle completed" every 15 minutes
- Substrate initialized from config
- Enzyme framework that can register and activate enzymes
- Database with new learning tables created

### No trading yet. This is the skeleton.

---

## Phase B: Sensors and Evaluators (Week 2)

### Goal: Data collection, indicator computation, confluence scoring

### Tasks

| Task | Description | Source to Port |
|------|-------------|---------------|
| Port exchange.py | Unified CCXT wrapper (Bitget + Blofin + Binance) | `ccxt_client.py`, `bitget_client.py`, `blofin_client.py` |
| Build collect_ohlcv.py | Fetch OHLCV, compute enabled indicators only | `agent_data_collector.py`, `chart_indicators.py`, `indicators.py` |
| Port all indicator modules | `momentum.py`, `trend.py`, `volatility.py`, `volume.py`, `structure.py` | `chart_indicators.py` (split by category) |
| Build indicator registry | `registry.py`: name -> function lookup | NEW |
| Port sr_levels | S/R detection from candle data | `chart_sr.py` |
| Port chart_patterns | Pattern detection | `chart_patterns.py` |
| Build collect_pre_trade_context.py | Trajectory analysis over 12 bars | NEW (uses `chart_indicators.py` historical computation) |
| Build score_confluence.py | Confluence scoring with configurable weights | `chart_confluence.py` (rewrite with config-driven weights) |
| Build validate_entry_zone.py | Entry zone + SL/TP computation | `agent_trade_prep.py` (SL/TP logic) |
| Build detect_noise.py | Volume, spread, kill zone, conflicting signals | `scanner_criteria.py` (kill zone, criteria) |
| Port macro context clients | VIX, DXY, F&G, BTC dominance (as optional module) | `market_context.py`, `coingecko_client.py`, `finnhub_client.py` |

### What We Get

- CollectOHLCV fetches data for dynamic symbol list
- Indicators computed per strategy config (4 indicators for momentum, not 12)
- ScoreConfluence produces candidates with configurable weights
- ValidateEntryZone produces entry zones with SL/TP
- DetectNoise flags noisy conditions
- Pre-trade trajectory computed for candidates
- Substrate fully populated with market + analysis data each cycle

### Still no trading. The system scores symbols but does not act.

---

## Phase C: Regulators and Transporters (Week 3)

### Goal: RiskManager, trade execution, position monitoring

### Tasks

| Task | Description | Source to Port |
|------|-------------|---------------|
| Build approve_trade.py | RiskManager gate: Kelly sizing, ISC verification, override authority | `agent_risk_mgmt.py`, `risk_analytics.py` |
| Build approve_exit.py | RiskManager exit gate: hard SL/trailing, soft signal reversal | `agent_risk_mgmt.py`, `agent_trade_monitor.py` |
| Build request_exit.py | Any enzyme can request exit, RiskManager decides | `agent_trade_monitor.py`, `entry_watcher.py` |
| Build execute_trade.py | CCXT order placement, SL order, database recording | `bitget_client.py` (order functions), `database.py` (insert) |
| Build execute_exit.py | Close position, cancel orders, record outcome | `ccxt_client.py` |
| Build position sync | Periodic sync of open positions from exchange | `bitget_sync.py`, `blofin_sync.py`, `sync_base.py` |
| Build wait.py | Default enzyme: no action, record idle cycle | NEW |
| Port telegram notify | One-way log push (disabled by default) | `telegram_notify.py` |
| ISC verification system | Automated ISC checking in substrate | NEW (from `../Network_Framework.md`) |
| Trailing stop implementation | Track peak price, move SL when conditions met | NEW (uses ATR and position monitoring) |

### What We Get

- Full trading cycle: collect -> score -> validate -> approve -> execute -> monitor -> exit
- RiskManager has override authority (Regulator class, priority 10)
- ISC conditions enforced before any trade
- Trailing stops activate after profit threshold
- Idle cycles recorded with reasons
- Paper trading mode: can set `exchange.primary: "paper"` to test without real orders

### The system can trade. But it does not learn yet.

---

## Phase D: Learning Engine (Week 4)

### Goal: Self-improvement: accuracy tracking, combination analysis, rulebook generation

### Tasks

| Task | Description | Source to Port |
|------|-------------|---------------|
| Build tracker.py | Per-trade data collection: all signal states at entry | `ai_hindsight.py` (recording pattern) |
| Build analyzer.py | Per-signal accuracy with Wilson score interval | NEW (current system lacks per-signal tracking) |
| Build combination.py | Pairwise signal combination accuracy with chi-squared | NEW |
| Build trajectory.py | Pre-trade trajectory classification and accuracy tracking | NEW |
| Build rulebook.py | Auto-generate rulebook from findings, max 10 rules | `ai_rulebook.py` (generation logic), `ai_hindsight.py` (feedback loop) |
| Build update_rulebook.py | Synthase enzyme that fires when retrain conditions are met | NEW (replaces manual rulebook regeneration) |
| Build record_idle_cycle.py | Track why system chose not to trade, retrospective validation | NEW |
| Build weight adjustment | Adjust indicator weights based on accuracy verdicts | NEW (current weights are hardcoded) |
| Database: create learning tables | trade_learning, signal_accuracy, combination_accuracy, etc. | NEW (see `learning-engine.md` schema) |
| Learning activation thresholds | min 30 trades before adjusting, min 15 per signal | NEW |

### What We Get

- After 30 trades: rulebook auto-generated from real outcomes
- Suppressed signals (below 55%) removed from scoring
- Highlighted signals (above 75%) boosted in scoring
- Combination rules: "RSI+MACD both bullish = 83% (p=0.008)"
- Trajectory rules: "gradual alignment wins 78%, sudden snap loses 67%"
- Idle rules: "during VIX>35, waiting was correct 80% of the time"
- Weight history: auditable record of every weight change

### The system is self-improving. This is the complete loop.

---

## Phase E: LLM Integration and Polish (Week 5)

### Goal: Provider-agnostic LLM routing, prompt builder, optional modules

### Provider-Agnostic Routing Principle

The LLM layer is **provider-agnostic by design**. No enzyme hardcodes a provider name or model string. All routing decisions are driven by config:

```yaml
# config/default.yaml (or strategy YAML override)
llm:
  routing:
    pre_filter:
      provider: "openrouter"
      model: "deepseek/deepseek-v4-0324:free"
    analysis:
      provider: "anthropic"
      model: "claude-sonnet-4-6"
    rulebook:
      provider: "openrouter"
      model: "meta-llama/llama-3.3-70b-instruct:free"
    fallback:
      provider: "openrouter"
      model: "deepseek/deepseek-v4-0324:free"
```

**Supported providers** (all via `llm/router.py`):
- `anthropic` -- Claude models via Anthropic SDK
- `google` -- Gemini models via Google SDK
- `openrouter` -- 200+ models via OpenAI-compatible API (free-tier available)

**Cost strategy**: High-frequency roles (`pre_filter`, `rulebook`) use OpenRouter free-tier models. Low-frequency, high-stakes roles (`analysis`) use Anthropic Sonnet. Any role can be swapped per-strategy in the strategy YAML without touching code.

**KeyManager** handles key rotation for all providers identically. Provider names in `llm_keys` (exchange.yaml) must match `llm.routing.<role>.provider` values.

### Tasks

| Task | Description | Source to Port |
|------|-------------|---------------|
| Build `llm/anthropic_client.py` | Anthropic SDK client, reads key from KeyManager | `ai_client.py` |
| Build `llm/gemini_client.py` | Gemini SDK client, reads key from KeyManager | `gemini_client.py` |
| Build `llm/openrouter_client.py` | OpenAI-compatible client for OpenRouter, reads key from KeyManager, sends required HTTP-Referer + X-Title headers | `openrouter_client.py` (port to new location) |
| Build `llm/router.py` | Provider-agnostic dispatcher: reads `llm.routing.<role>`, calls correct client, tracks daily budget, falls back to `llm.routing.fallback` on None key | NEW (extends `token_log.py` concept) |
| Build `llm/prompt_builder.py` | Dynamic context allocation with rulebook priority | `prompt_builder.py` (rewrite with new priority order) |
| Wire ValidateEntryZone LLM call | Optional call for complex patterns -- role: `analysis` | `agent_trade_prep.py` (prompt structure) |
| Wire UpdateRulebook LLM call | Optional call for rulebook formatting -- role: `rulebook` | `ai_rulebook.py` |
| Build regime_detector.py (optional) | HMM 3-state, off by default | `market_regime.py` |
| Build sentiment module (optional) | F&G, L/S ratio, contrarian signal, off by default | `market_context.py`, `coinalyze_client.py` |
| Build onchain module (optional) | MVRV, exchange flow, off by default | `onchain_client.py` |
| Build liquidation module (optional) | Cluster walls, off by default | `liquidation_client.py`, `liquidation_levels.py` |
| Build model consensus (optional) | Second-opinion call on any provider, off by default | `consensus.py` |
| End-to-end testing | Run full system in paper mode for 24 hours | NEW |
| Performance baseline | Verify: cycle time < 30s, memory < 500MB | NEW |

### Router Contract

`llm/router.py` exposes a single function that all enzymes call:

```python
def call_llm(role: str, prompt: str, system: str = None) -> str | None:
    """
    Call the LLM configured for the given role.

    Reads llm.routing.<role> from config to determine provider + model.
    Gets key from KeyManager (returns None if all keys in cooldown).
    If key is None: logs idle reason, returns None -- enzyme handles gracefully.
    If provider call fails: tries llm.routing.fallback before giving up.
    Tracks token usage against daily budget (llm.cost_budget_daily_usd).
    """
```

Enzymes never import a specific client directly. They call `router.call_llm(role, prompt)` and handle `None` as "skip LLM this cycle, use rule-based fallback".

---

## Porting Map: Current -> New

### Direct Ports (keep logic, move to new location)

| Current File | New Location | Changes |
|-------------|-------------|---------|
| `database.py` (init_db, get_conn, db_conn) | `core/database.py` | Add new learning tables |
| `ccxt_client.py` | `core/exchange.py` | Merge with bitget/blofin into unified CCXT wrapper |
| `bitget_client.py` | `core/exchange.py` | Specific exchange config in CCXT wrapper |
| `blofin_client.py` | `core/exchange.py` | Specific exchange config in CCXT wrapper |
| `chart_indicators.py` | `indicators/momentum.py`, `trend.py`, `volatility.py`, `volume.py` | Split by category, keep all math |
| `chart_sr.py` | `indicators/structure.py` | Keep S/R detection logic |
| `chart_patterns.py` | `indicators/structure.py` | Keep pattern detection logic |
| `chart_confluence.py` | `enzymes/score_confluence.py` | Rewrite weights as config-driven |
| `chart_candles.py` | `indicators/trend.py` | Candle fetching |
| `ai_client.py` | `llm/anthropic_client.py` | Keep send() + Gemini fallback |
| `gemini_client.py` | `llm/gemini_client.py` | Keep as-is |
| `token_log.py` | `llm/router.py` | Extend with budget tracking |
| `helpers.py` (build_cached_messages) | `llm/prompt_builder.py` | Rewrite priority order |
| `telegram_notify.py` | `enzymes/send_telegram_log.py` | Keep, make optional |
| `market_context.py` | `enzymes/collect_macro_context.py` | Keep data fetching, make optional |
| `coingecko_client.py` | `indicators/sentiment.py` (dominance data) | Keep, make optional |
| `coinalyze_client.py` | `indicators/sentiment.py` (OI, funding) | Keep, make optional |
| `onchain_client.py` | `indicators/onchain.py` | Keep, make optional |
| `market_regime.py` | `indicators/regime.py` | Keep HMM logic, make optional |
| `liquidation_levels.py` | `indicators/liquidation.py` | Keep, make optional |
| `backtest_quality.py` | `tools/backtest_quality.py` | Keep PBO, deflated Sharpe |
| `constants.py` | `config/default.yaml` | All values moved to YAML |

### Redesigned (concept kept, implementation rebuilt)

| Current File | New Location | Changes |
|-------------|-------------|---------|
| `agent_orchestrator.py` | `core/substrate.py` + `core/enzyme.py` | Linear pipeline -> reaction network |
| `agent_types.py` | `core/substrate.py` | TypedDict contracts -> shared substrate |
| `agent_data_collector.py` | `enzymes/collect_ohlcv.py` | Fetch only strategy-enabled indicators |
| `agent_data_interpreter.py` | `enzymes/score_confluence.py` | Interpreter -> confluence scorer |
| `agent_market_sentiment.py` | `enzymes/collect_macro_context.py` (optional) | Sentiment -> optional module |
| `agent_data_reviewer.py` | Merged into `score_confluence.py` | Redundant middleman, quality gate in scoring |
| `agent_trade_prep.py` | `enzymes/validate_entry_zone.py` + `approve_trade.py` | Split: validation + risk approval |
| `agent_risk_mgmt.py` | `enzymes/approve_trade.py` + `approve_exit.py` | RiskManager -> Regulator enzyme |
| `agent_trade_monitor.py` | `enzymes/request_exit.py` + `approve_exit.py` | Monitor -> exit request + approval |
| `ai_hindsight.py` | `learning/tracker.py` + `analyzer.py` + `rulebook.py` | Hindsight -> full learning engine |
| `ai_rulebook.py` | `learning/rulebook.py` + `update_rulebook.py` | Rulebook -> auto-generated from accuracy |
| `signal_scorer.py` | `learning/analyzer.py` | XGBoost -> statistical accuracy tracking |
| `prompt_builder.py` | `llm/prompt_builder.py` | Rewrite: rulebook priority, reduced budget |
| `prompt_fragments.py` | `strategy-template.yaml` (description field) | Hardcoded prompts -> config-driven |
| `scanner_stages.py` | `enzymes/score_confluence.py` + `detect_noise.py` | 3-stage scanner -> enzyme network |
| `scanner_scheduler.py` | `core/daemon.py` + `core/scheduler.py` | Scheduler -> daemon loop |
| `scanner_watchlist.py` | `strategy-template.yaml` (symbols section) | Hardcoded watchlist -> config-driven |
| `scanner_criteria.py` | `enzymes/detect_noise.py` | Kill zone + criteria -> noise detection |
| `scanner_prompts.py` | `strategy-template.yaml` (description field) | Hardcoded prompts -> config-driven |
| `app.py` | `main.py` | Flask app -> daemon entrypoint |

### Removed (not ported, functionality dropped or optional)

| Current File | Reason |
|-------------|--------|
| `nansen_client.py` | Paid service, no technical edge. Optional in future. |
| `grok_client.py` | Social sentiment = noise for technical. Optional as sentiment module. |
| `ai_scanner.py` | Replaced by enzyme network (no single scanner file) |
| `ai_advisor.py` | Portfolio coaching -- not needed for automated 24/7 system |
| `ai_call.py` | Manual call analysis -- not needed for automated system |
| `ai_live_trade.py` | Merged into monitor enzymes |
| `ai_limit.py` | Merged into risk manager enzymes |
| `ai_pattern_detector.py` | Merged into validate_entry_zone (optional LLM) |
| `ai_trade_grader.py` | Merged into learning analyzer |
| `backtest_engine.py` | → `tools/backtest/backtest_engine.py` (nice-to-have addon, not core) |
| `backtest_metrics.py` | → `tools/backtest/backtest_metrics.py` |
| `backtest_optimizer.py` | → `tools/backtest/backtest_optimizer.py` |
| `backtest_quality.py` | → `tools/backtest/backtest_quality.py` |
| `analytics.py` | Dashboard analytics -- replaced by learning engine + log reports |
| `data_sources.py` | Merged into indicator modules |
| `importer.py` | CSV import -- replaced by exchange sync |
| `monitor_scheduler.py` | Merged into daemon loop |
| `entry_watcher.py` | Merged into request_exit.py |
| `sync_base.py` | Merged into exchange.py |
| `bitget_sync.py` | Merged into position sync in daemon |
| `blofin_sync.py` | Same |
| `consensus.py` | Optional module (model_consensus), not core |
| `static/*`, `templates/*` | Web UI -- optional, not needed for daemon |
| `routes/*` | Flask routes -- replaced by daemon + logs |
| `scripts/browser_*` | Browser testing -- irrelevant for daemon |

### Optional Modules (kept, disabled by default)

| Module | Config Toggle | Current File |
|--------|-------------|-------------|
| Macro context | `modules.macro_context: true` | `market_context.py`, `finnhub_client.py` |
| Regime detection | `modules.regime_detection: true` | `market_regime.py` |
| Sentiment (contrarian) | `modules.sentiment: true` | `coinalyze_client.py`, market F&G |
| On-chain | `modules.onchain: true` | `onchain_client.py` |
| Liquidation walls | `modules.liquidation: true` | `liquidation_levels.py` |
| Model consensus | `modules.model_consensus: true` | `consensus.py`, `gemini_client.py` |
| Telegram logs | `modules.telegram_logs: true` | `telegram_notify.py` |
| Telegram interaction | `modules.telegram_interaction: true` | `scripts/hermes-telegram-bot.py` |

---

## Testing Strategy

### Per Phase

| Phase | Test Focus |
|-------|-----------|
| A | Substrate creation, config loading, daemon startup, ISC verification |
| B | Indicator computation matches current results (port validation) |
| C | RiskManager gates, order execution, SL/trailing, ISC enforcement |
| D | Accuracy tracking, Wilson score, chi-squared, rulebook generation |
| E | LLM routing, budget tracking, optional module activation |

### Port Validation Tests

For every indicator we port, we write a test that:
1. Uses the same input data as the current implementation
2. Verifies the new implementation produces the same output
3. Marks the port as validated

This prevents subtle bugs from the refactor.

### Test Count Estimate

| Category | Tests |
|----------|-------|
| Substrate + enzyme framework | 20 |
| Indicator ports (match current) | 30 |
| Enzyme activation conditions | 15 |
| RiskManager gates | 10 |
| Learning engine | 20 |
| Integration (full cycle) | 5 |
| **Total** | **~100** |

---

## Deployment

### Target: Raspberry Pi 5 or any Linux server

```bash
# Clone and switch to new branch
git clone https://github.com/anvilfilbert/Auto-Crypto-Tradingjournal.git
cd Auto-Crypto-Tradingjournal
git checkout feature/auto-trader

# Install dependencies
pip3 install -r requirements.txt

# Configure
cp config/exchange.yaml.template config/exchange.yaml
# Edit exchange.yaml with your API keys

# Create your strategy
cp docs/reaction-design/strategy-template.yaml config/strategies/my_strategy.yaml
# Edit my_strategy.yaml with your settings

# Run in paper mode first
python3 main.py --paper

# When confident, switch to live mode
python3 main.py

# Or as systemd service
sudo cp trading-journal.service /etc/systemd/system/auto-trader.service
sudo systemctl enable --now auto-trader
```

### Paper Trading Mode

Set `exchange.primary: "paper"` in strategy config. The system runs all enzymes, makes all decisions, but writes orders to a log file instead of the exchange. This validates the full loop without risking capital.

---

## Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| Port bugs: indicator math differs | Port validation tests comparing old vs new output |
| RiskManager bypass: ISC conditions not enforced | ISC verification is automatic in substrate, cannot be skipped |
| Learning engine draws false conclusions | Wilson score interval prevents conclusions from small samples |
| Exchange API changes break execution | CCXT wrapper handles exchange abstraction; fallback to paper mode |
| Config typo causes wrong behavior | YAML validation on load; invalid config = system stays in "wait" mode |
| LLM cost overruns | Daily budget hard cap; Haiku fallback when budget low |
| System runs unattended for weeks | Telegram logs (optional) + database reports; no human intervention needed |

---

## Milestones

| Milestone | Phase | Success Criteria |
|-----------|-------|-----------------|
| Skeleton runs | A | Daemon starts, loads config, completes empty cycles |
| Data flows | B | CollectOHLCV produces indicators, ScoreConfluence produces candidates |
| First paper trade | C | RiskManager approves, ExecuteTrade writes to log (paper mode) |
| Learning activates | D | After 30 paper trades, rulebook auto-generated |
| LLM integrated | E | Optional Sonnet validation works, budget tracked |
| Live trading starts | E+ | Paper mode validated for 7+ days, switch to live |
| Self-improving confirmed | E+2 | After 50 live trades, accuracy metrics show meaningful patterns |
| Root is clean | F | Only `main.py` + config/docs/requirements in root |

---

## Phase F: Root Cleanup (after Phase E)

### Goal: Clean root directory — only entrypoint, config, docs, and requirements remain

Once the reaction network is fully operational (Phases A–E complete), the old system files are deleted or moved. The old Flask web UI, agent pipeline, and scanner files have no place in the new architecture.

### Target root state

After Phase F, only these files remain in root:

```
main.py                    ← daemon entrypoint
requirements.txt
README.md
CLAUDE.md
SECURITY.md
LICENSE
.env.example
.gitignore
trading-journal.service
```

All logic lives in subdirectories: `core/`, `enzymes/`, `indicators/`, `learning/`, `llm/`, `tools/`, `config/`, `docs/`, `tests_new/`.

### Tasks

| Task | Action | Notes |
|------|--------|-------|
| Move backtest files | `backtest_engine.py`, `backtest_metrics.py`, `backtest_optimizer.py`, `backtest_quality.py` → `tools/backtest/` | Nice-to-have addon; update all internal imports |
| Delete agent pipeline | `agent_*.py` (8 files) | All ported to `enzymes/` in Phase B/C |
| Delete AI layer | `ai_*.py` (9 files) | All ported to `llm/` or `learning/` in Phase D/E |
| Delete scanner files | `scanner_*.py` (5 files) | All ported to `enzymes/` + `config/` in Phase B |
| Delete chart files | `chart_*.py` (6 files) | All ported to `indicators/` in Phase B |
| Delete exchange clients | `bitget_client.py`, `blofin_client.py`, `ccxt_client.py`, `deribit_client.py` | Ported to `core/exchange.py` in Phase B |
| Delete sync files | `bitget_sync.py`, `blofin_sync.py`, `sync_base.py` | Ported to daemon position sync in Phase C |
| Delete data clients | `coinalyze_client.py`, `coingecko_client.py`, `finnhub_client.py`, `nansen_client.py`, `onchain_client.py`, `liquidation_client.py` | Ported to optional `indicators/` modules in Phase E |
| Delete old LLM clients | `ai_client.py`, `cerebras_client.py`, `gemini_client.py`, `grok_client.py`, `groq_client.py`, `openai_compat_client.py`, `openrouter_client.py` | Ported to `llm/` in Phase E |
| Delete old infra files | `analytics.py`, `database.py`, `constants.py`, `helpers.py`, `data_sources.py`, `importer.py`, `indicators.py`, `signal_scorer.py` | Replaced by new architecture |
| Delete scheduler files | `monitor_scheduler.py`, `scanner_scheduler.py`, `entry_watcher.py` | Merged into `core/daemon.py` + `core/scheduler.py` |
| Delete misc files | `consensus.py`, `market_context.py`, `market_regime.py`, `liquidation_levels.py`, `risk_analytics.py`, `trade_history.py`, `trade_utils.py`, `token_log.py`, `prompt_builder.py`, `prompt_fragments.py` | Ported or superseded |
| Delete Flask web UI | `app.py`, `routes/`, `static/`, `templates/` | Web UI retired; daemon + logs replace it |
| Delete old tests | `tests/` directory | Replaced by `tests_new/` |
| Delete scripts | `scripts/browser_*`, `scripts/compare_*`, `scripts/generate_*` | Dev tools; irrelevant for daemon |
| Final import audit | Run `python -m pytest tests_new/` | All tests must pass after cleanup |

### What We Get

- Root contains only what a new developer needs to understand the entry point
- No dead code, no legacy files, no confusion about which system is active
- `tools/backtest/` available as an optional CLI tool for manual backtesting
- `tests_new/` is the single source of truth for tests
- Clean git history: one commit per phase, Phase F is "The Great Cleanup"
