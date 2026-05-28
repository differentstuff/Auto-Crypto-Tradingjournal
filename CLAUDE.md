# Auto-Trader v2 — Claude Code Context

## Project Overview

**Autonomous, self-improving, 24/7 crypto futures trading daemon** based on the Reaction Network architecture. Not a web app. Not a Telegram bot. A daemon that runs continuously, trades your account using your strategy, learns from every trade, and adapts over time.

Runs as a systemd service on a Raspberry Pi 5. Single entrypoint: `python3 main.py`.

**Core principle:** Enzymes, not agents. Substrate, not contracts. Attractors, not endpoints. The system fires whichever enzyme moves the substrate closest to an attractor — no stochastic tool selection, no LLM-driven orchestration.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     DAEMON (24/7 loop)                       │
│  Every cycle: hot-reload config → reset substrate →          │
│  run network → persist state → sleep                         │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                     SUBSTRATE (shared state)                 │
│  strategy | portfolio | market | analysis | decisions |     │
│  learning | validity | pending                               │
│  See: docs/reaction-design/substrate-schema.yaml             │
└──────────────────────────┬──────────────────────────────────┘
                           │
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
    ┌──────────┐   ┌──────────────┐   ┌──────────┐
    │ SENSOR   │   │OXIDOREDUCTASE│   │REGULATOR │
    │ enzymes  │   │  enzymes      │   │ enzymes  │
    └──────────┘   └──────────────┘   └──────────┘
           │               │               │
           └───────────────┼───────────────┘
                           ▼
    ┌──────────┐   ┌──────────────┐   ┌──────────┐
    │SYNTHASE  │   │TRANSPORTER   │   │WAIT      │
    │ enzymes  │   │  enzymes     │   │ enzyme   │
    └──────────┘   └──────────────┘   └──────────┘
```

**Enzyme classes:**

| Class | Role | Current Enzymes |
|-------|------|-----------------|
| **Sensor** | Extract data from environment | CollectOHLCV, CollectPreTradeContext, CollectMacroContext, RequestExit |
| **Oxidoreductase** | Evaluate, score, rank | ScoreConfluence, ValidateEntryZone, DetectNoise |
| **Regulator** | Override authority, gate decisions | ApproveTrade, ApproveExit |
| **Transporter** | Execute on exchange, send notifications | ExecuteTrade, ExecuteExit, SyncPositions, SendTelegramLog, UpdateMarkPrices |
| **Synthase** | Build new knowledge | UpdateLearning, UpdateRulebook, RecordTradeOutcome |
| **Isomerase** | Default state transform | Wait |

**Attractors (goal states):** `watching`, `trade_opened`, `trade_managed`, `trade_closed`, `learning_updated`

**ISC (Ideal State Criteria):** Config-driven hard-to-vary conditions that MUST pass before any trade. No ISC bypass possible. See `substrate.py DEFAULT_ISCS`.

---

## Directory Structure

```
auto-trader/
  main.py                         # Single entrypoint: daemon loop
  config/
    default.yaml                  # All defaults (never hand-edit)
    llm.yaml                      # LLM routing, parameters, prompts
    strategies/
      _template.yaml              # Full template with all keys
      momentum_rising.yaml        # Primary strategy
      paper_learning_test.yaml    # Paper mode test strategy
      paper_test.yaml             # Paper trading strategy
  core/
    daemon.py                     # 24/7 loop, config hot-reload, attractor logic
    substrate.py                  # Shared state container, ISC verification
    enzyme.py                     # Enzyme base class, activation conditions, registry
    database.py                   # SQLite WAL, all tables, migrations
    config_loader.py              # YAML config merge (default < strategy < exchange)
    exchange.py                   # CCXT wrapper (Bitget primary, Binance/Bybit fallback)
    scheduler.py                  # Cycle timing with jitter
  enzymes/                        # Each enzyme = one file
    collect_ohlcv.py              # Sensor: fetch OHLCV, compute indicators
    collect_pre_trade_context.py  # Sensor: trajectory analysis, coincidence risk
    collect_macro_context.py      # Sensor: VIX, DXY, BTC dominance (optional)
    score_confluence.py           # Oxidoreductase: weighted confluence scoring
    validate_entry_zone.py       # Oxidoreductase: S/R entry zones, R:R validation
    detect_noise.py               # Oxidoreductase: noise detection, kill zones
    approve_trade.py              # Regulator: RiskManager approval gate
    approve_exit.py               # Regulator: RiskManager exit approval
    request_exit.py               # Sensor: exit request from signal reversal
    execute_trade.py              # Transporter: place order on exchange
    execute_exit.py               # Transporter: close position on exchange
    sync_positions.py             # Transporter: sync open positions with exchange
    send_telegram_log.py          # Transporter: one-way push notifications
    update_mark_prices.py         # Transporter: update mark prices for open positions
    update_learning.py            # Synthase: per-signal accuracy tracking
    update_rulebook.py            # Synthase: auto-generated rulebook from accuracy data
    record_trade_outcome.py       # Synthase: record trade outcome in learning DB
    wait.py                       # Isomerase: default resting state
  indicators/                     # Pure computation, no API calls, no side effects
    momentum.py                   # rsi, macd, adx, wavetrend
    momentum_quality.py           # slope × R² ranking (dynamic symbol filter)
    trend.py                      # ema, sma, supertrend
    volatility.py                 # atr, bollinger, keltner
    volume.py                     # obv, cvd, vwap
    structure.py                  # sr_levels, pivots, fib
    registry.py                   # name → function lookup
  learning/
    analyzer.py                   # Per-signal accuracy with Wilson CI
    combination.py                # Pairwise signal combination significance
    trajectory.py                 # Pre-trade trajectory pattern classification
    rulebook.py                   # Auto-generated rules (max 10)
    weight_adjuster.py            # Adjust indicator weights from accuracy verdicts
  llm/
    key_manager.py                # API key rotation (multi-key per provider, auto-switch on 429/529)
    router.py                     # Cost-aware model selection
    anthropic_client.py           # Sonnet/Haiku for analysis
    gemini_client.py              # Fallback provider
    openrouter_client.py          # Optional provider
    prompt_builder.py              # Dynamic budget with rulebook priority
    response_parser.py             # Parse LLM responses
  tools/
    backtest/                     # Backtesting engine (PBO, deflated Sharpe)
  data/
    trading_journal.db            # SQLite WAL database
  tests/                          # Pytest suite
  scripts/
    verify_learning.py            # Learning verification script
    self_test.py                  # End-to-end self-test
```

---

## How to Run

```bash
# Paper mode (default, safe):
python3 main.py --paper --strategy momentum_rising

# Single cycle (for testing):
python3 main.py --cycle-once --paper

# Live mode (requires API keys in config/exchange.yaml):
python3 main.py --strategy momentum_rising

# Verbose logging:
python3 main.py --log-level DEBUG --paper
```

Systemd service: `auto-trader.service` — `sudo systemctl restart auto-trader`

---

## Config System

**Merge order (later overrides earlier):**
```
default.yaml < strategies/<name>.yaml < exchange.yaml
```

**Critical rule: NO hardcoded defaults in Python code.** All config values must be in `default.yaml` or the strategy YAML. `Substrate.cfg()` raises `ValueError` if a key is missing — this catches config errors immediately at startup, not silently at runtime.

**Strategy UID:** Each strategy has a `uid` field (auto-generated UUID4 on first load). All learning data is keyed by `strategy_uid`. Clear the uid to reset learning data.

**Hot-reload:** The daemon reloads config on every cycle. No restart needed to adjust strategy, risk limits, or indicator selection.

---

## Substrate

The single shared state container. All enzymes read from and write to this object. No enzyme talks to another enzyme directly — all communication goes through substrate fields.

**Sections:**
- `strategy` — name, uid, timeframe, max_positions (from config, persisted)
- `portfolio` — equity, open_positions, risk limits (persisted across restarts)
- `market` — indicators, pre_trade_context, macro (NOT persisted, sensors repopulate)
- `analysis` — candidates, entry_zones, noise_flag, signal_states (NOT persisted)
- `decisions` — action, trade_approved, exit_request, exit_approved (cleared each cycle)
- `learning` — signal_accuracy, combination_accuracy, rulebook, adjusted_weights (persisted)
- `validity` — ISC conditions (config-driven, verified each cycle)

**Shallow-copy safety:** The daemon passes a `shallow_copy()` to each enzyme. If an enzyme raises, the original substrate is unchanged. Enzymes must NOT mutate nested values in-place — they must create new values and reassign entire fields.

**Persistence:** `to_persistent_dict()` serializes durable state (strategy, portfolio, learning, validity). Market and analysis are NOT persisted — they're stale on restart and sensors repopulate them.

---

## Enzyme Execution Loop

```python
for step in range(max_cycle_steps):  # default 20
    if at_attractor(substrate): break

    activatable = [e for e in enzymes if e.can_activate(substrate)]
    if not activatable: fire_wait("no enzyme can activate"); break

    # Regulators always have priority (priority=10)
    best = highest_flux_score(activatable)

    substrate = best.transform(substrate.shallow_copy())
    substrate.verify_iscs()  # check all ISC conditions
```

**Loop guard:** If the same enzyme fires 3+ times consecutively, it's stuck. Break with Wait.

**Each enzyme fires at most once per cycle** (tracked by `fired_this_cycle` set).

---

## Learning Engine

The unique selling point. Tracks:

1. **Per-signal accuracy** — "RSI(14) was right 71% of the time. MACD solo was 50% — suppress it."
2. **Pairwise combinations** — "RSI+MACD both bullish = 83% win rate (p<0.01). Statistically significant."
3. **Pre-trade trajectory** — Were indicators aligning gradually or snapping together by coincidence?
4. **Idle cycle tracking** — When no trade was made, WHY? Prevents false "high win rate" from cherry-picking.
5. **Weight adjustment** — Signals with ≥75% accuracy get boosted (+20%). Signals with ≤30% accuracy get NEGATIVE weights (contrarian). Coin-flip signals (45-55%) get suppressed (weight=0).
6. **Rulebook generation** — Max 10 rules, auto-generated from findings, injected into prompts.

**Verdict classification:**
- `valid` (≥75%): highlight, boost weight
- `monitor` (55-75%): keep, no change
- `suppress` (45-55%): coin flip, weight=0
- `contrarian` (≤30%): anti-signal, NEGATIVE weight
- `review` (30-45%): borderline, reduce weight 10%
- `insufficient_data`: keep original weight

---

## ISC (Ideal State Criteria)

Config-driven hard-to-vary conditions. Cannot be bypassed. All must pass before any trade.

| ISC | Criterion | Operator |
|-----|-----------|----------|
| ISC-001 | Entry threshold met before trade | `any_score_gte` |
| ISC-002 | Stop loss always set | `sl_set_or_no_trade` |
| ISC-003 | Position size within risk limit | `size_within_risk` |
| ISC-004 | Max concurrent positions not exceeded | `count_lt` |
| ISC-005 | No trade when noise_flag is true | `false_or_action_wait` |
| ISC-006 | Minimum confluence signals aligned | `all_field_gte` |
| ISC-007 | Pre-trade trajectory not sudden coincidence | `none_field_eq` |

New ISC conditions can be added in strategy YAML without touching Python code.

---

## Database

**SQLite WAL** — safe for concurrent reads during sync. All migrations are idempotent.

**Key tables:**
- `positions`, `orders`, `wallet_snapshots` — legacy trade journal
- `trade_learning` — per-trade signal recording, trajectory, outcome
- `signal_accuracy` — per-indicator accuracy with Wilson CI, verdicts
- `combination_accuracy` — pairwise signal combinations with p-values
- `trajectory_accuracy` — trajectory pattern classification
- `weight_history` — every weight change with justification
- `rulebook_versions` — auto-generated rulebook history
- `substrate_state` — persistent substrate snapshots (pruned to max_rows)
- `cycle_log` — every cycle: action, enzymes fired, ISC results, duration

---

## Exchange Support

- **Primary:** Bitget (USDT-M perpetuals)
- **Data source:** Bitget (public OHLCV, no auth needed)
- **Fallback:** Binance, Bybit (public endpoints)
- Paper mode: all enzymes run, no real orders placed

---

## LLM Integration

- **Primary:** Anthropic Claude (Sonnet for analysis, Haiku for quick tasks)
- **Fallback:** Google Gemini (automatic on 429/529 from Anthropic)
- **Optional:** OpenRouter, Grok
- Key rotation: multiple keys per provider, auto-switch on overload (429/529)
- All LLM calls are OPTIONAL — enzymes fall back to rule-based logic if no keys configured

---

## Testing

```bash
python3 -m pytest tests/ -v
```

**Test structure:**
- `conftest.py` — `make_full_config()` helper for complete substrate configs
- `test_substrate.py` — substrate creation, ISC verification, serialization
- `test_enzyme.py` — enzyme base class, activation conditions, registry
- `test_config_loader.py` — YAML merge, hot-reload
- `test_database.py` — DB init, migrations, substrate persistence
- `test_daemon.py` — daemon loop, attractor detection, cycle execution
- `test_momentum_quality.py` — momentum_quality indicator (slope × R²)
- `test_phase_b.py` through `test_phase_e.py` — enzyme integration tests
- `test_learning_config.py` — learning engine with config

**Key test pattern:** `Substrate(config=make_full_config())` — always use `make_full_config()` from conftest. Never create a Substrate with partial config (it will raise ValueError on missing keys).

---

## Deployment

- **Pi SSH:** `<user>@<Pi-IP>` (use expect — no BatchMode)
- **Service:** `sudo systemctl restart auto-trader`
- **Pi path:** `/home/<user>/auto-trader`
- **Dev path:** local clone of this repo
- **Log file:** `logs/auto-trader.log` (10MB rotating, 5 backups)
- **Database:** `data/trading_journal.db` — never rsync to Pi, production DB lives on Pi only

---

## Calculation Invariants (do not change without updating both sides)

- **momentum_quality**: `slope × R²` on log-price series via OLS. R² < `min_r_squared` → filtered (no score). Adaptive lookback: high R² → shorter window.
- **Kelly criterion**: `kelly_fraction = (win_rate × avg_win_ratio - loss_rate) / avg_win_ratio`. Half-Kelly applied. Hard cap at `max_size_pct_of_equity`.
- **Confluence score**: weighted sum of enabled, non-suppressed indicators. Suppressed (weight=0) excluded. Contrarian (negative weight) subtracts. Normalized to 0-10 scale.
- **Signal accuracy**: Wilson score confidence interval for binomial proportion. Verdict at 95% CI.
- **Combination significance**: Chi-squared test, p < 0.05 = statistically significant.
- **Position sizing**: `size_usdt = equity × risk_per_trade_pct / 100`. Capped by `max_size_pct_of_equity`. Correlation check reduces size for same-direction positions.

---

## System Goals

**The completed Auto-Trader is:**

1. **Autonomous** — runs 24/7 without human intervention. Trades, learns, adapts on its own.
2. **Self-improving** — every trade feeds back into signal accuracy, combination significance, trajectory classification, and weight adjustment. The system gets better at its own strategy over time.
3. **Dynamically scoped** — not limited to a static watchlist. The system discovers the best symbols from the exchange in real time, filtered by volume, OI, and momentum quality. `always_watch` always overrides inclusion; `never_trade` always overrides exclusion (applied last).
4. **Validated** — every weight change is paper-forward tested against the previous weights before going live. Profit factor decides: new weights must earn their place. The challenger runs the same daemon with the same data, strictly separated actions, full `challenger_log` DB table for traceability.
5. **Risk-calibrated** — position sizing accounts for volatility via ATR caps. No single position exceeds its ATR-based limit, regardless of what Kelly or risk-per-trade suggests.

---

## What This System Is NOT

- ❌ NOT a Flask web app — there is no HTTP server
- ❌ NOT a Telegram bot — SendTelegramLog is one-way push only
- ❌ NOT a browser-based UI — no HTML, no JS frontend
- ❌ NOT a multi-agent pipeline — enzymes fire based on activation conditions and flux scores
- ❌ NOT stochastic — the daemon selects enzymes deterministically (highest flux score wins)

---

## What NOT to Do

- **Never add hardcoded defaults in Python code.** All config values come from YAML. `Substrate.cfg()` raises ValueError on missing keys.
- **Never mutate substrate nested values in-place.** Create new values and reassign entire fields. The shallow-copy safety depends on this.
- **Never bypass ISC conditions.** They are hard-to-vary constraints, not suggestions.
- **Never store exchange credentials or LLM keys on the substrate.** The substrate gets a secrets-free config slice. Enzymes that need credentials get them from ConfigLoader directly.
- **Never restart the daemon to change config.** Hot-reload on every cycle. Change YAML, save, done.
- **Never create a Substrate with partial config in tests.** Always use `make_full_config()` from conftest.
- **Never let an enzyme fire twice in one cycle.** The `fired_this_cycle` set prevents this.