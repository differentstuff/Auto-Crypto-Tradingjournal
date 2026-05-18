# Trading Journal — Architecture & Data Flow

*v1.6.0 · Updated 2026-05-17*

---

## v1.6.0 additions (2026-05-17)

### New Modules
| Module | Purpose |
|--------|---------|
| `liquidation_levels.py` | CCXT Binance USDM forced-liquidation cluster detection; TTL-cached 15 min |
| `order_flow` (chart_indicators.py) | Tick-rule proxy for per-candle buy/sell aggressor delta + divergence |
| `onchain_client.py` | CoinMetrics Community API — MVRV, exchange net-flow; TTL-cached 1 h |
| `market_regime.py` | GaussianHMM 3-state regime classifier on BTC 4H (trending_up/ranging/trending_down); retrained 4 h |
| `signal_scorer.py` | XGBoost win-probability from historical analyzed_calls; 24 h retrain, activates at 20+ outcomes |
| `backtest_quality.py` | PBO (CSCV), Deflated Sharpe (Bailey et al. 2014), Bootstrap Sharpe CI |

### Confluence Signals (12, up from 9)
| Signal | Weight | Notes |
|--------|--------|-------|
| RSI | grouped momentum ±1.5 | with MACD |
| MACD | grouped momentum ±1.5 | capped with RSI |
| EMA stack | ±1.0 | |
| ADX | ±strength | |
| WaveTrend | grouped oscillator ±1.0 | with MFI |
| MFI | grouped oscillator ±1.0 | capped with WT |
| CVD | ±0.4 | |
| order_flow | ±0.15 per-TF | tick-rule delta, new v1.6.0 |
| volume | ±0.5/−0.25 amplifier | |
| smt_weight | +0.15 | cross-exchange price divergence |
| smt_direction_weight | ±0.15 | 24h directional divergence |
| liquidation_wall | ±0.20 symbol-level | conditional on 3% proximity, new v1.6.0 |

max_per_tf = 5.55 (non-SMT) / 5.85 (SMT) + 0.20 symbol-level conditional

### New DB Columns
- `analyzed_calls.regime_label TEXT` (migration 38) — HMM state label at time of analysis
- `analyzed_calls.ml_win_prob REAL` (migration 39) — ML scorer output

### New Endpoints
- `POST /api/backtest/quality` — PBO, Deflated Sharpe, Bootstrap CI on submitted equity curve

### Prompt Injections Added
- HMM regime: `"Market regime (HMM/BTC): trending_up — confidence 78%"`
- On-chain: `"On-chain BTC: MVRV 2.3 | fair_value | exchange outflow $30M"`
- ML scorer: `"ML win probability: 68% (historical pattern match)"`

### Browser Baseline
- 16/16 tabs: zero JS errors, DOM counts 1590–4740
- 4/4 pages: accessibility 100/100 (42 aria-label fixes across HTML + dynamic JS inputs)
- 3/3 interaction checks: Call Analyzer, Scanner, Chart Explorer
- Test suite: 442 passing

---

## System Overview

```
                        ┌─────────────────────────────────────────────────────────┐
                        │                  RASPBERRY PI 5                          │
                        │                                                           │
  Browser / Mobile      │   ┌──────────┐    ┌─────────────────────────────────┐  │
  ─────────────────────►│   │  Flask   │    │       BACKGROUND THREADS        │  │
  (local network)       │   │  app.py  │    │  ┌──────────┐  ┌─────────────┐  │  │
                        │   │  :8082   │    │  │  Bitget  │  │   Scanner   │  │  │
                        │   └────┬─────┘    │  │  Sync    │  │  Scheduler  │  │  │
                        │        │          │  │  (5 min) │  │  (30 min)   │  │  │
                        │   9 Flask         │  └────┬─────┘  └──────┬──────┘  │  │
                        │   Blueprints      │  ┌─────────────────────────────┐ │  │
                        │        │          │  │   Monitor Scheduler         │ │  │
                        │        │          │  │   (10 min, positions)       │ │  │
                        │        │          │  └──────────────────────────┘  │  │
                        │        │          └───────────────────────────────────┘  │
                        │        ▼                                                   │
                        │   ┌─────────────────────────────────────────────────┐  │
                        │   │              SQLite WAL Database                  │  │
                        │   │  positions · orders · analyzed_calls              │  │
                        │   │  pending_limits · trader_rulebook                 │  │
                        │   │  trade_hindsight · token_usage · settings         │  │
                        │   └─────────────────────────────────────────────────┘  │
                        └─────────────────────────────────────────────────────────┘
```

---

## 7-Agent Pipeline

```
Call text / scanner symbol / live position
              │
              ▼
   ┌──────────────────────┐
   │   DataCollector      │  agent_data_collector.py
   │   OHLCV · funding    │  → CollectorResult
   │   OI · F&G · FRED    │  (parallel fetches, TTL caches)
   │   Nansen · Grok      │
   └────────┬─────────────┘
            │
      ┌─────┴──────────────────┐  (parallel)
      ▼                        ▼
┌─────────────────┐   ┌─────────────────────────┐
│ DataInterpreter │   │  MarketSentimentAnalyzer │
│ RSI·MACD·EMA    │   │  macro bias · funding    │
│ S/R · WaveTrend │   │  L/S ratio · Grok social │
│ confluence score│   │  contra_signal flag      │
│ → InterpreterResult  → SentimentResult         │
└────────┬────────┘   └─────────────────────────┘
         │
         ▼
┌─────────────────────────┐
│  DataReviewer           │
│  + KPI Generator        │  agent_data_reviewer.py
│  signal quality 0-10    │  → ReviewerResult
│  backtest WR/streak     │
│  trading KPIs from DB   │
└────────┬────────────────┘
         │
         ▼
┌─────────────────────────┐
│   TradePreparation      │  agent_trade_prep.py
│   (main Claude call)    │  → TradePrepResult
│   assembles all above   │  chart_png_b64 generated here
│   + Gemini consensus    │
└────────┬────────────────┘
         │
         ▼
┌─────────────────────────┐
│   RiskManagement        │  agent_risk_mgmt.py
│   sizing · correlation  │  → RiskResult
│   ATR SL · Kelly crit.  │  (pure math — no AI call)
└────────┬────────────────┘
         │
         ▼
    AnalysisResult → saved to analyzed_calls
    (risk_verdict_json, chart_png_b64, monitor_alert columns)
         │
   [position opens]
         │
         ▼
┌─────────────────────────┐
│   TradeMonitor          │  agent_trade_monitor.py
│   background thread     │  → MonitorResult
│   Collector→Interp      │  polls every 10 min
│   →Sentiment→Haiku      │  fires Telegram + UI badge
│   on risk_rating ≥ 7    │  on risk_rating ≥ 7 or action ≠ Hold
└─────────────────────────┘
```

---

## Agent Contract Types

All TypedDicts live in `agent_types.py` — single source of truth.

| Type | Description |
|------|------------|
| `CollectorResult` | Raw data: candles, funding_rate, open_interest, long_short, fear_greed, fred_macro, nansen, grok |
| `InterpreterResult` | Signals: by_timeframe, sr_levels, confluence_score, trend_direction, momentum_bias |
| `SentimentResult` | Macro: macro_bias, sentiment_score, funding_bias, crowd_position, contra_signal |
| `ReviewerResult` | Quality: signal_quality, warnings, backtest_context, kpis, rubric |
| `TradePrepResult` | Trade: setup_score, entry/sl/tp prices, key_conditions, cot_reasoning, consensus, chart_png_b64 |
| `RiskResult` | Risk: approved, position_size_usdt, margin_usdt, kelly_fraction, atr_sl_valid |
| `MonitorResult` | Monitor: action, risk_rating, alert_level, tp/sl recommendations |
| `AnalysisResult` | Flat merge of all agent outputs for DB persistence |

---

## Orchestrator Pipeline Functions

`agent_orchestrator.py` wires agents together:

```python
run_call_analysis(call_text, symbol, direction, equity, setup_type, positions, conn)
    → AnalysisResult   # 5-stage pipeline for call analysis

run_scanner_prep(symbol, direction, collected, interpreted, reviewed, sentiment, conn)
    → TradePrepResult  # stage 3b entry for scanner (per finalist)

run_monitor(position, original_prep)
    → MonitorResult    # lightweight chain for background monitor thread
```

---

## Consensus Scoring

```
|Claude - Gemini| ≤ 1 → ✓ Confirmed   (HIGH confidence, avg score)
|Claude - Gemini| ≤ 2 → ~ Aligned     (MED confidence, avg score)
|Claude - Gemini| ≤ 3 → ⚠ Divergent   (LOW confidence, Claude 60% weight)
|Claude - Gemini| > 3 → ⚡ REVIEW      (very_low, keep Claude score)
```

---

## Model Routing Table

```
Task                     Model      Tokens(out)  Rationale
─────────────────────────────────────────────────────────────────────────────
call_analyzer (TradePrep) Sonnet    4096         Complex structured JSON + CoT
scanner_batch (TradePrep) Sonnet    4096/symbol  Per-finalist via agent pipeline
advisor                  Sonnet     4096         Portfolio-level strategy
rulebook                 Sonnet     2048         Synthesis of full history
limit_analyzer           Sonnet     768          Risk decision (keep accuracy)
pattern_detector         Sonnet     1200         Cross-pattern reasoning

scanner_quick            Haiku      120          Score + 1 sentence (fast pre-filter)
hindsight                Haiku      512          Retroactive classification task
live_trade/monitor       Haiku      768          Quick action rec (latency critical)
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
         ▼ Stage 3b — Agent pipeline per finalist (replaces old batch call)
         │  DataCollector → DataInterpreter+MarketSentiment → DataReviewer
         │  → TradePrep (Claude call) + chart generation
         │  Returns: entry_zone, sl_price, tp1, tp2, rr_ratio, key_conditions
         │
         ▼ Stage 3c — Gemini consensus (top-5 only, parallel)
         │  Independent indicator-only score
         │  Consensus confidence flag added to each setup
         │
         ▼ Telegram alert with annotated chart (if any setup ≥ 6/10)
         │  + Save to analyzed_calls (analyst='scanner')
         │  + Auto-link to matching open positions via check-matches
         │
         ▼ Results cached 30 min
```

---

## Monitor Scheduler (every 10 min)

```
App start → wait 2 min → first pass → every 10 min → repeat

Per position that passes filter (unrealized_pct < -5% OR duration > 240 min):
  DataCollector (TTL caches — minimal network cost)
      → DataInterpreter (pure)
      → MarketSentimentAnalyzer (pure)
      → Haiku verdict (768 tokens)

On risk_rating ≥ 7 or action ≠ "Hold":
  → Telegram alert
  → UPDATE analyzed_calls SET monitor_alert=1 (UI badge)
```

---

## Prompt Caching Architecture

```
STABLE BLOCK (cache_control: ephemeral) ← cached across calls
  build_stable_prefix(): rulebook + calibration + pattern strengths
  Changes: at most weekly

DYNAMIC BLOCK (no cache) ← changes every call
  DataReviewer: backtest context + KPIs
  MarketSentiment: macro bias + contra signal
  DataInterpreter: chart indicators per timeframe
  Rubric: setup-type scoring rules
  Signal quality score + warnings

USER PROMPT (never cached)
  call text + account equity + setup type

Expected savings: 40-60% on repeated calls
```

---

## Backtest → Accuracy Feedback Loop

```
Every trade outcome recorded → DB: positions.realized_pnl
         │
         ▼ DataReviewer.get_backtest_context(conn, symbol, direction, setup_type)
         │
  ┌──────────────────────────────────────────────┐
  │  BACKTEST INSIGHTS (injected into TradePrep) │
  │  • Recent form: 72% WR last 20 · streak WWLWW│
  │  • Breakout setups: 100% WR (6 trades) +$7   │
  │  • BTCUSDT Long: 75% WR (12 trades) +$12.50  │
  │  • ⚠ Wednesday: caution (57% WR, -$355)      │
  └──────────────────────────────────────────────┘
         │
         ▼ TradePrep uses this BEFORE scoring the new trade
         │
         ▼ New call scored → outcome recorded → next call gets updated insights
```

---

## DB Schema (analyzed_calls key columns)

| Column | Added | Purpose |
|--------|-------|---------|
| gemini_score | mig 26 | Gemini pre-proof score |
| consensus_score | mig 27 | Claude+Gemini average |
| consensus_flag | mig 28 | ✓/~/⚠/⚡ label |
| risk_verdict_json | mig 29 | Full RiskResult JSON |
| monitor_alert | mig 30 | 1 = monitor fired alert |
| chart_png_b64 | mig 31 | Annotated trade chart |

---

## Data Sources

| Source | What it provides | Cache TTL |
|--------|-----------------|-----------|
| Bitget REST v2 | OHLCV candles, positions, funding rate | 10 min (candles) |
| Anthropic API | Claude Sonnet/Haiku | n/a |
| Google Gemini Flash | Pre-proof consensus scoring | 30 min |
| xAI Grok | X/Twitter sentiment, news (MC-weighted) | 30 min |
| Nansen.ai | On-chain smart money signals | 30 min |
| CoinGecko | Market cap lookup for Grok weight | 24 h |
| alternative.me | Fear & Greed Index | 5 min |
| Binance futures | Open Interest (public) | 5 min |
| Bybit/OKX | Multi-exchange funding rates | 5 min |
| FRED (St. Louis Fed) | Fed rate, treasury yield, CPI, M2 | 6 h |
| ForexFactory mirror | High-impact USD economic events | 1 h |

---

## Token Budget per Operation

| Operation | Stable (cached) | Dynamic (not cached) | Output | Providers |
|-----------|----------------|---------------------|--------|-----------|
| Call analysis | ~1,200 tokens | ~2,800 tokens | 1,200 | Sonnet + Gemini + Grok |
| Scanner quick-score | 800/symbol | 400/symbol | 30 | Haiku |
| Scanner per-finalist | ~1,200 | ~3,500 | 1,200 | Sonnet + Gemini (top-5) |
| Monitor check | — | 800 | 300 | Haiku |
| Hindsight score | — | 800 | 200 | Haiku |
| Trade grade | — | 700 | 100 | Haiku |
| Advisor | ~1,200 | ~2,800 | 1,500 | Sonnet |
| Rulebook regen | — | 3,000 | 800 | Sonnet |
