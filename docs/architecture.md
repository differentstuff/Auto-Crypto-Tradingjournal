# Trading Journal — Architecture & Data Flow

*v1.0 · Updated 2026-05-13*

---

## System Overview

```
                        ┌─────────────────────────────────────────────────────────┐
                        │                  RASPBERRY PI 5                          │
                        │                                                           │
  Browser / Mobile      │   ┌──────────┐    ┌─────────────────────────────────┐  │
  ─────────────────────►│   │  Flask   │    │       BACKGROUND THREADS        │  │
  (local network)       │   │  app.py  │    │  ┌──────────┐  ┌─────────────┐ │  │
                        │   │  :8082   │    │  │  Bitget  │  │  Scanner    │ │  │
                        │   └────┬─────┘    │  │   Sync   │  │ Scheduler   │ │  │
                        │        │          │  │  (5 min) │  │  (30 min)   │ │  │
                        │   9 Flask         │  └────┬─────┘  └──────┬──────┘ │  │
                        │   Blueprints      │       │                │        │  │
                        │        │          └───────┼────────────────┼────────┘  │
                        │        │                  │                │            │
                        │        ▼                  ▼                ▼            │
                        │   ┌─────────────────────────────────────────────────┐  │
                        │   │              SQLite WAL Database                  │  │
                        │   │  positions · orders · analyzed_calls              │  │
                        │   │  pending_limits · trader_rulebook                 │  │
                        │   │  trade_hindsight · token_usage · settings         │  │
                        │   └─────────────────────────────────────────────────┘  │
                        └─────────────────────────────────────────────────────────┘
```

---

## AI Agent Pipeline

```
USER REQUEST (paste call text / click Analyze)
         │
         ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                        MASTER ORCHESTRATOR                                  │
│                     agent_orchestrator.py                                   │
│                                                                              │
│  route_model(task) → Sonnet or Haiku                                        │
│  compute_consensus(claude_score, gemini_score) → consensus dict             │
│  add_gemini_consensus(setups, ctx_map) → enriched setups                    │
└────────────────────────────────────────────────────────────────────────────┘
         │
         │  Dispatches to 4 parallel pre-analysis tracks:
         │
    ┌────┴─────────────────────────────────────────┐
    │            │             │                    │
    ▼            ▼             ▼                    ▼
┌────────┐  ┌────────┐  ┌──────────────┐  ┌───────────────┐
│  ATR   │  │ Market │  │  xAI Grok    │  │    Google     │
│ Check  │  │Context │  │  Social Intel│  │    Gemini     │
│(1H chart)│ │(F&G,   │  │  (X/Twitter │  │  Pre-proof    │
│        │  │ funding│  │   sentiment) │  │  Scoring      │
│trade_  │  │  L/S,  │  │              │  │               │
│utils   │  │  OI)   │  │grok_client.py│  │gemini_client.py│
└────┬───┘  └────┬───┘  └──────┬───────┘  └───────┬───────┘
     │           │             │                    │
     └─────────────────────────┴────────────────────┘
                       │  (all complete in parallel, max = slowest)
                       ▼
         ┌─────────────────────────────┐
         │    prompt_builder.py        │
         │                             │
         │  build_stable_prefix()      │  ← CACHED by Anthropic (ephemeral)
         │    rulebook + calibration   │    changes weekly, same key per call
         │    + pattern strengths      │    40-60% token saving on repeats
         │                             │
         │  build_context()            │  ← NOT cached (changes per call)
         │    backtest insights        │    historical WR by setup/symbol/time
         │    market context           │    live funding rates, F&G
         │    chart context (4H+1D)    │    RSI/MACD/EMA/ADX/WaveTrend/CVD
         │    Nansen smart money       │    on-chain wallet activity
         │    Grok social intel        │    X/Twitter sentiment (MC-weighted)
         │    similar trades           │    recent same-symbol outcomes
         └─────────────────────────────┘
                       │
                       ▼
         ┌─────────────────────────────┐
         │    Claude Sonnet 4.6        │  ← Full analysis + CoT reasoning
         │    ai_call.py               │    max_tokens: 4096
         │                             │    CoT stored as cot_reasoning
         │    Returns:                 │    (fed back into next same-symbol call)
         │    • Setup score 1-10       │
         │    • Entry/SL/TP levels     │
         │    • Risk/Reward ratio      │
         │    • Pattern warnings       │
         │    • Sizing recommendation  │
         └─────────────────────────────┘
                       │
                       ▼
         ┌─────────────────────────────┐
         │  CONSENSUS SCORING          │
         │  agent_orchestrator.py      │
         │                             │
         │  Claude: 7/10               │
         │  Gemini: 6/10               │
         │  Delta: 1 → HIGH confidence │
         │                             │
         │  |Δ|≤1 → ✓ Confirmed        │
         │  |Δ|≤2 → ~ Aligned          │
         │  |Δ|≤3 → ⚠ Divergent        │
         │  |Δ|>3 → ⚡ REVIEW           │
         └─────────────────────────────┘
                       │
                       ▼
              RESULT SAVED TO DB
              analyzed_calls table:
              setup_score, gemini_score,
              consensus_score, consensus_flag,
              cot_reasoning, analysis_json
```

---

## Model Routing Table

```
Task                     Model      Tokens(out)  Rationale
─────────────────────────────────────────────────────────────────────────────
call_analyzer            Sonnet     4096         Complex structured JSON + CoT
scanner_batch            Sonnet     1200×N       Multi-symbol reasoning
advisor                  Sonnet     4096         Portfolio-level strategy
rulebook                 Sonnet     2048         Synthesis of full history
limit_analyzer           Sonnet     768          Risk decision (keep accuracy)
pattern_detector         Sonnet     1200         Cross-pattern reasoning

scanner_quick            Haiku      120          Score + 1 sentence (fast pre-filter)
hindsight                Haiku      512          Retroactive classification task
live_trade               Haiku      768          Quick action rec (latency critical)
trade_grader             Haiku      350          A/B/C/D rubric classification

Gemini 2.0 Flash         [parallel] 200          Independent pre-proof score only
Grok 3 Fast              [parallel] 130          Social/news brief (MC-weighted)
```

---

## Scanner Pipeline (every 30 min)

```
DEFAULT_WATCHLIST (100 symbols)
         │
         ▼ Stage 1 — Confluence filter (parallel, no AI, no cost)
         │  chart_indicators: RSI·MACD·EMA·ADX·WaveTrend·CVD per 4H+1D
         │  ✗ Drop if < 2 signals aligned in one direction
         │
         ▼ Stage 2 — Technical quality gate (no AI, instant)
         │  ✗ Drop: overextended RSI, missing S/R, flat ADX, high funding
         │
         ▼ Stage 3a — Haiku quick-score (cheap pre-filter)
         │  Compact indicator prompt → score 0-10 + one-sentence rationale
         │  ✗ Drop if score < threshold (default 6, self-calibrated)
         │
         ▼ Stage 3b — Sonnet batch scoring (top-N finalists, ONE call)
         │  Full context per finalist: indicators + Nansen + history
         │  Returns: entry_zone, sl_price, tp1, tp2, rr_ratio, urgency
         │
         ▼ Stage 3c — Gemini consensus (top-5 only, parallel)
         │  Independent indicator-only score
         │  Consensus confidence flag added to each setup
         │
         ▼ Telegram alert (if any setup ≥ 6/10)
         │  + Save to analyzed_calls (analyst='scanner')
         │  + Auto-link to matching open positions via check-matches
         │
         ▼ Results cached 30 min
```

---

## Prompt Caching Architecture

```
BEFORE (broken — cache never hit):
  ┌────────────────────────────────────────────────────┐
  │  Single block with cache_control: {ephemeral}      │
  │  Content: rulebook + calibration + MARKET DATA     │
  │                          ↑ changes every 5 min     │
  └────────────────────────────────────────────────────┘
  Result: cache key changes on every call → $0 cached tokens

AFTER (fixed — stable/dynamic split):
  ┌────────────────────────────────────────────────────┐
  │  Block 1 — cache_control: {ephemeral}             │  ← CACHED
  │  build_stable_prefix(): rulebook + calibration    │    identical across
  │  + scoring rubrics + pattern strengths            │    multiple calls
  └────────────────────────────────────────────────────┘
  ┌────────────────────────────────────────────────────┐
  │  Block 2 — no cache_control                        │  ← NOT cached
  │  build_context(): backtest insights + market       │    changes per call
  │  + chart context + Nansen + Grok + similar trades  │
  └────────────────────────────────────────────────────┘
  ┌────────────────────────────────────────────────────┐
  │  Block 3 — prompt (call text, sizing, etc.)        │  ← never cached
  └────────────────────────────────────────────────────┘
  Expected savings: 40-60% on call_analyzer + scanner_batch
```

---

## Backtest → Accuracy Feedback Loop

```
Every trade outcome recorded
         │
         ▼
         DB: positions.realized_pnl + setup_type + close_time
         │
         │  get_backtest_context(conn, symbol, direction, setup_type)
         ▼
  ┌──────────────────────────────────────────────┐
  │  BACKTEST INSIGHTS (injected into prompt)    │
  │                                              │
  │  • Recent form: 72% WR last 20 · streak WWLWW│
  │  • Breakout setups: 100% WR (6 trades) +$7   │
  │  • BTCUSDT Long: 75% WR (12 trades) +$12.50  │
  │  • ⚠ Wednesday: caution (57% WR, -$355 total)│
  │  • ⚠ 21:00 UTC: weak hour (70% WR, -$1831)  │
  └──────────────────────────────────────────────┘
         │
         ▼
  Claude uses this BEFORE scoring the new trade
  → avoids repeating historically bad patterns
  → amplifies historically strong setups
         │
         ▼
  New call scored → outcome recorded → next call gets updated insights
  (continuous learning loop, no retraining needed)
```

---

## Accuracy Measurement

```
scripts/backtest_consensus.py --host <host>:8082

H1: Claude-only accuracy   (score ≥ N → WR target 85%)
H2: Consensus accuracy     (|Δ|≤1 confirmed → higher WR than H1)
H3: Divergence avoidance   (|Δ|>2 review flag → these should fail most often)

Current status: 5 outcome-recorded calls (need ≥ 20 for statistical confidence)
Run: python3 scripts/backtest_consensus.py --host <pi-ip>:8082 --live
```

---

## Data Sources

| Source | What it provides | Cache TTL |
|--------|-----------------|-----------|
| Bitget REST v2 | OHLCV candles, open positions, funding rate, mark prices | 10 min (candles) |
| Anthropic API | Claude Sonnet/Haiku — analysis, scoring, coaching | n/a |
| xAI Grok | X/Twitter sentiment, recent news (MC-weighted) | 30 min |
| Google Gemini | Independent pre-proof scoring, setup ranking | 30 min |
| Nansen.ai | On-chain smart money signals | 30 min |
| CoinGecko | Market cap lookup for Grok weight calculation | 24 h |
| alternative.me | Fear & Greed Index | 5 min |
| FRED (St. Louis Fed) | Fed rate, treasury yield, CPI, M2 | 12 h |
| Economic calendar | High-impact USD events | 4 h |

---

## Token Budget per Operation

| Operation | Stable (cached) | Dynamic (not cached) | Output | Providers |
|-----------|----------------|---------------------|--------|-----------|
| Call analysis | ~1,200 tokens | ~2,800 tokens | 1,200 | Sonnet + Gemini + Grok |
| Scanner quick-score | 800/symbol | 400/symbol | 30 | Haiku |
| Scanner batch (top-12) | ~2,000 | ~3,500 | 14,400 | Sonnet + Gemini (top-5) |
| Live trade check | — | 600 | 300 | Haiku |
| Hindsight score | — | 800 | 200 | Haiku |
| Trade grade | — | 700 | 100 | Haiku |
| Advisor | ~1,200 | ~2,800 | 1,500 | Sonnet |
| Rulebook regen | — | 3,000 | 800 | Sonnet |
