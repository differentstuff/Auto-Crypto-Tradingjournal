# Auto-Trader: Reaction Network Architecture Plan

> Self-improving, 24/7 automated crypto trading system.
> Trades your account using your strategy, your risk profile, your schedule.
> Learns from every trade. Adapts over time. Strives for lowest error rate.

---

## Core Idea

Replace the current linear 7-agent pipeline with a **Reaction Network** of enzymes (skills). Each enzyme activates only when its conditions are met. The system converges toward **attractors** (goal states). The **RiskManager** is the master regulator with override authority.

**Key principles:**

- **Enzymes, not agents**: Each skill has strict activation conditions, deterministic output, and self-improving instructions
- **Substrate, not contracts**: Single shared state container that all enzymes read/write
- **Attractors, not endpoints**: Goal states with hard-to-vary conditions (ISC)
- **Gradient, not pipeline**: System fires whichever enzyme moves us closest to the attractor
- **Wait is default**: No strong signal = no action. The market owes us nothing.
- **RiskManager is master**: Only regulator can approve/kill trades. All other enzymes request, never execute.
- **Multi-trade support**: The system can hold multiple concurrent positions, limited by `max_positions` in strategy config. Each position is tracked independently with its own exit logic.
- **Learning is mandatory**: Every trade (and every cycle with no trade) feeds the accuracy tracker.
- **Key rotation by design**: Multiple API keys per LLM provider, automatic rotation on overload (429/529). See: `key_manager.py`.

---

## Architecture Overview

```
┌----------------------------------------------------------------┐
│                    DAEMON (24/7 loop)                          │
│  Every cycle: load config, run network, persist state          │
└---------------------------┬------------------------------------┘
                            │
                            ▼
┌----------------------------------------------------------------┐
│                    SUBSTRATE (shared state)                    │
│  strategy | portfolio | market | analysis | decisions |        │
│  learning | validity | pending                                 │
│  See: substrate-schema.yaml                                    │
└---------------------------┬------------------------------------┘
                            │
            ┌---------------┼---------------┐
            ▼               ▼               ▼
     ┌----------┐   ┌----------┐   ┌----------┐
     │ SENSOR   │   │ EVALUATOR│   │REGULATOR │
     │ enzymes  │   │ enzymes  │   │ enzymes  │
     └----------┘   └----------┘   └----------┘
            │               │               │
            └---------------┼---------------┘
                            │
                            ▼
     ┌----------┐   ┌----------┐   ┌----------┐
     │ SYNTHASE │   │TRANSPORT │   │WAIT      │
     │ enzymes  │   │ enzymes  │   │ enzyme   │
     └----------┘   └----------┘   └----------┘

     Enzymes fire based on activation conditions + flux score.
     Regulator enzymes have PRIORITY over all others.
     See: enzyme-definitions.yaml
```

---

## Execution Loop

```python
def run_cycle(substrate, enzymes, attractors, isc_table):
    """
    One cycle of the reaction network.
    Find activatable enzymes, fire the best one, update substrate.
    Repeat until attractor is reached or no enzyme can activate.
    """
    max_steps = 20  # prevent infinite loops

    for step in range(max_steps):
        if at_attractor(substrate, attractors):
            return substrate

        # Find enzymes whose activation conditions are met
        activatable = [e for e in enzymes if e.can_activate(substrate)]

        if not activatable:
            # No enzyme can fire -- log as "idle cycle"
            substrate.decisions.action = "wait"
            substrate.learning.idle_cycles += 1
            return substrate

        # Regulator enzymes always have priority
        regulators = [e for e in activatable if e.class == "Regulator"]
        if regulators:
            best = regulators[0]  # regulators fire in defined order
        else:
            # Calculate flux scores (progress toward attractor)
            scores = [flux_score(e, substrate, attractors) for e in activatable]
            if max(scores) <= 0:
                # No enzyme improves our position -- wait
                substrate.decisions.action = "wait"
                substrate.learning.idle_cycles += 1
                return substrate
            best = activatable[argmax(scores)]

        substrate = best.transform(substrate)

        # Verify ISC entries (hard-to-vary conditions)
        for isc in isc_table:
            if isc.can_verify(substrate):
                isc.verify(substrate)

        substrate.pending = [
            isc.id for isc in isc_table if isc.status == "pending"
        ]

    return substrate
```

---

## Attractors (Goal States)

| Attractor | When Reached | ISC Conditions |
|-----------|-------------|----------------|
| `watching` | Default state, no signal | Portfolio loaded, indicators fresh |
| `trade_opened` | New position created | Entry threshold met, SL set, risk limit respected, position recorded |
| `trade_managed` | Position monitored | Exit request evaluated, trailing stop active or exit pending |
| `trade_closed` | Position closed | Exit executed, outcome recorded |
| `learning_updated` | Cycle complete | Accuracy tracker updated, rulebook refreshed if stale |

---

## Enzyme Classes

| Class | Role | Trading Example |
|-------|------|----------------|
| **Sensor** | Extract data from environment | CollectOHLCV, CollectPreTradeContext, CollectMacroContext |
| **Oxidoreductase** | Evaluate, score, rank | ScoreConfluence, ValidateEntryZone, DetectNoise |
| **Synthase** | Build new structures | UpdateRulebook, GenerateReport |
| **Regulator** | Override authority, gate decisions | ApproveTrade, ApproveExit (RiskManager) |
| **Transporter** | Move data, execute actions | ExecuteTrade, ExecuteExit, SendTelegramLog |
| **Isomerase** | Transform, map | Wait (default state transform) |

---

## The Learning Engine

The learning engine is the unique selling point. It tracks:

1. **Per-signal accuracy**: "RSI(14) was right 71% of the time. MACD solo was 50% -- suppress it."
2. **Pairwise combinations**: "RSI+MACD both bullish = 83% win rate (p<0.01). Statistically significant."
3. **Pre-trade trajectory**: Were indicators aligning gradually over 12 bars, or snapping together by coincidence?
4. **Idle cycle tracking**: When no trade was made for N cycles, the system notes why. This prevents false "high win rate" from simply not trading during bad conditions.
5. **Rulebook generation**: Auto-generated from findings. Max 10 rules. Sharp, not verbose.

See: learning-engine.md for full design.

---

## Strategy Configuration

Strategies are YAML files containing:

- **Human description**: Plain-language thesis ("Enter when momentum rises before price at support on 4H")
- **Machine settings**: Timeframes, risk limits, max positions
- **Enabled indicators**: Which enzymes to activate (all indicators exist, only enabled ones compute)
- **ISC conditions**: Hard-to-vary entry/exit rules that cannot be bypassed
- **Module toggles**: Optional features (macro, sentiment, regime, consensus, telegram)

See: strategy-template.yaml for the full template.

### Strategy UID

Each strategy has a **uid** — a stable identity that persists across renames, parameter changes, and reordering. The uid is stored in the strategy YAML:

```yaml
strategy:
  name: momentum_rising
  uid: ""   # Auto-generated on first load. Clear to reset learning data.
```

**How it works:**
1. On first load, if `uid` is empty or missing, `ConfigLoader` generates a UUID4 and writes it back to the YAML file.
2. The uid flows through the system: `ConfigLoader → Substrate.strategy.uid → enzymes → learning modules`.
3. All learning tables (`signal_accuracy`, `combination_accuracy`, `trajectory_accuracy`, `idle_condition_accuracy`, `weight_history`, `rulebook_versions`, `trade_learning`) use `strategy_uid` as a primary key component or column.
4. This means two strategies can share the same database without learning data collisions.

**Resetting learning data:** Set `uid: ""` in the strategy YAML. On next daemon load, a fresh uid is generated and all learning starts from scratch. The old data remains in the DB (keyed to the old uid) but is no longer read.

**Default value:** `"legacy"` — used when uid is not available (backward compatibility, tests, manual DB inserts before migration).

---

## Migration from Current Codebase

The current repo has ~100 root-level files, 7 agents, 12 signals, 4 AI providers, 15 data sources. We keep the math and data infrastructure, redesign the agent layer.

**What we port (keep):**
- All indicator calculations (RSI, MACD, EMA, ADX, WaveTrend, etc.)
- Exchange clients (CCXT, Bitget, Blofin)
- Database layer (SQLite WAL)
- AI client with Gemini fallback
- PBO / backtest quality metrics
- Telegram notify infrastructure

**What we redesign:**
- 7-agent pipeline -> Reaction Network of enzymes
- Hardcoded constants -> YAML config per strategy
- TypedDict contracts -> Shared substrate
- 12-signal confluence -> Configurable indicator whitelist with learned weights
- Hindsight scoring -> Full learning engine with per-signal accuracy

**What we make optional (keep code, disable by default):**
- Nansen, Grok, on-chain, Coinalyze (sentiment modules)
- HMM regime detection
- Gemini consensus (model consensus module)
- Telegram interaction (kept for logs, disabled)
- Browser UI (not needed for daemon operation)

See: migration-plan.md for the phased roadmap.

---

## Directory Structure

```
auto-trader/
  main.py                         # Single entrypoint: daemon loop
  config/
    default.yaml                  # All defaults (never hand-edited)
    strategies/
      momentum_rising.yaml        # Primary strategy
      breakout.yaml               # Future strategy
    exchange.yaml                 # API keys, endpoints
  core/
    daemon.py                     # 24/7 loop, config hot-reload
    substrate.py                  # State container class
    enzyme.py                     # Enzyme base class + activation logic
    database.py                   # SQLite WAL (ported)
    exchange.py                   # CCXT wrapper (ported)
    scheduler.py                  # Cycle cadence, timing
  enzymes/                        # Each enzyme = one file
    collect_ohlcv.py
    collect_pre_trade_context.py
    collect_macro_context.py
    score_confluence.py
    validate_entry_zone.py
    detect_noise.py
    approve_trade.py              # RiskManager (Regulator)
    approve_exit.py               # RiskManager (Regulator)
    request_exit.py               # Any enzyme can call this
    execute_trade.py
    execute_exit.py
    update_rulebook.py            # Hindsight (Synthase)
    record_trade_outcome.py       # Record trades to learning DB (Synthase)
    send_telegram_log.py
    wait.py                       # Default enzyme
  indicators/                     # All indicators, loaded but activated per strategy
    momentum.py                   # rsi, macd, adx, wavetrend
    trend.py                      # ema, sma, supertrend
    volatility.py                 # atr, bollinger, keltner
    volume.py                     # obv, cvd, vwap
    structure.py                  # sr_levels, pivots, fib
    sentiment.py                  # fear_greed, grok, ls_ratio (off by default)
    onchain.py                    # mvrv, exchange_flow (off by default)
    regime.py                     # hmm_3state (off by default)
    liquidation.py                # cluster_walls (off by default)
    registry.py                   # name -> function lookup
  learning/
    analyzer.py                   # Per-signal accuracy
    combination.py                # Pairwise signal combinations
    trajectory.py                 # Pre-trade indicator trajectory analysis
    rulebook.py                   # Auto-generated rules
    weight_adjuster.py            # Adjust indicator weights from accuracy verdicts
  llm/
    key_manager.py                # API key rotation (multi-key per provider, auto-switch on overload)
    router.py                     # Cost-aware model selection (uses KeyManager)
    anthropic_client.py           # Ported from ai_client.py
    gemini_client.py              # Ported
    prompt_builder.py             # Dynamic budget with rulebook priority
  tools/
    signal_calculator.py          # Pre-compute on schedule
    regime_detector.py            # Optional HMM
    backtest_quality.py           # PBO, deflated Sharpe (ported)
  tests/
```

---

## Communication with the System

The user communicates through **text files** (YAML config). No web UI needed for 24/7 operation.

- **Strategy files**: `config/strategies/*.yaml` -- dictate what to trade, how, when
- **Exchange config**: `config/exchange.yaml` -- API keys, endpoints
- **Logs**: Written to database + optional Telegram channel
- **Learning reports**: Auto-generated in `data/reports/` after every retrain cycle

The daemon picks up config changes on every cycle. No restart needed to adjust strategy, risk limits, or indicator selection.

---

## References

- `substrate-schema.yaml` -- Full substrate state structure
- `enzyme-definitions.yaml` -- All enzyme activation conditions and outputs
- `strategy-template.yaml` -- Strategy config template with all options
- `learning-engine.md` -- Learning engine design (accuracy, combinations, rulebook)
- `migration-plan.md` -- Phased migration roadmap from current codebase
- `key_manager.py` -- API key rotation manager (multi-key per provider, auto-switch on overload)
- `../Network_Framework.md` -- PAI + Reaction Network synthesis (inspiration)
- `../ReactionNetworkModel.md` -- Enzyme model (inspiration)
