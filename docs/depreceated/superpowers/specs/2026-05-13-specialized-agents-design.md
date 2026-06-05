# Specialized Agent Infrastructure — Design Spec

*v1.0 · 2026-05-13 · **IMPLEMENTED** in commits d01741c–6e70a9d*

---

## Overview

Refactor the existing AI pipeline into seven specialized agents, each with a typed input/output contract. The external API (all Flask routes) stays identical. The goal is: clear ownership per agent, independently testable units, and two new capabilities — a dedicated RiskManagement agent with Kelly criterion sizing, and a proactive TradeMonitor background thread.

**Approach:** One file per agent (Approach A). Agent contracts use `TypedDict`. Communication via return values only (no shared mutable state, no DB-persisted pipeline). Existing routes, `scanner_scheduler.py`, and all data-source clients (`chart_context.py`, `nansen_client.py`, `grok_client.py`, etc.) are untouched.

---

## Pipeline Topology

```
Call text / scanner symbol / live position
              │
              ▼
   ┌──────────────────────┐
   │   DataCollector      │  agent_data_collector.py
   │   OHLCV · funding    │  → CollectorResult
   │   OI · F&G · FRED    │
   │   Nansen · Grok      │
   └────────┬─────────────┘
            │
      ┌─────┴──────────────────┐  (parallel)
      ▼                        ▼
┌─────────────────┐   ┌─────────────────────────┐
│ DataInterpreter │   │  MarketSentimentAnalyzer │
│ RSI·MACD·EMA    │   │  macro bias · funding    │
│ S/R · WaveTrend │   │  L/S ratio · Grok social │
│ confluence score│   │  → SentimentResult       │
│ → InterpreterResult│ └─────────────────────────┘
└────────┬────────┘
         │
         ▼
┌─────────────────────────┐
│  DataReviewer           │
│  + KPI Generator        │  agent_data_reviewer.py
│  signal quality score   │  → ReviewerResult
│  backtest WR/streak     │
│  trading KPIs from DB   │
└────────┬────────────────┘
         │
         ▼
┌─────────────────────────┐
│   TradePreparation      │  agent_trade_prep.py
│   main Claude call      │  → TradePrepResult
│   assembles all above   │
│   + Gemini consensus    │
└────────┬────────────────┘
         │
         ▼
┌─────────────────────────┐
│   RiskManagement        │  agent_risk_mgmt.py
│   sizing · correlation  │  → RiskResult
│   ATR SL · Kelly crit.  │
└────────┬────────────────┘
         │
         ▼
    AnalysisResult → saved to analyzed_calls
         │
   [position opens]
         │
         ▼
┌─────────────────────────┐
│   TradeMonitor          │  agent_trade_monitor.py
│   background thread     │  → MonitorResult
│   Collector→Interp      │
│   →Sentiment→Haiku      │
│   Telegram + UI badge   │
└─────────────────────────┘
```

---

## Agent Contracts

### 1. DataCollector — `agent_data_collector.py`

**Purpose:** Single entry point for all external data. Runs all fetches in parallel. Uses existing TTL caches — no extra latency.

```python
class CollectorInput(TypedDict):
    symbol: str
    direction: str          # "Long" | "Short"
    timeframes: list[str]   # e.g. ["4H", "1D"]

class CollectorResult(TypedDict):
    symbol: str
    candles: dict           # {"4H": pd.DataFrame, "1D": pd.DataFrame}
    funding_rate: dict      # {rate, annualized, bias}
    open_interest: dict     # {value, change_pct}
    fear_greed: dict        # {value, classification}
    fred_macro: dict        # {fed_rate, cpi, m2, treasury_yield}
    long_short: dict        # {long_pct, short_pct, ratio} from get_long_short_ratio()
    nansen: dict            # {signal, label, smart_money_bias}
    grok: dict              # {text, weight} — empty dict if large-cap or unconfigured
    fetched_at: float       # unix timestamp
```

**Wraps:** `chart_context.get_candles()`, `market_context.get_funding_rate()`, `market_context.get_open_interest()`, `market_context.get_long_short_ratio()`, `market_context.get_fear_greed()`, `market_context.get_fred_macro()`, `nansen_client.get_smart_money_signal()`, `grok_client.get_coin_context()`.

**Failure:** If any individual source fails, its key returns an empty dict. DataCollector itself does not raise unless candle fetch fails (candles are required for all downstream agents).

---

### 2. DataInterpreter — `agent_data_interpreter.py`

**Purpose:** Transforms raw candles into structured technical signals. Pure function — no AI, no DB, no network calls.

```python
class InterpreterInput(TypedDict):
    collected: CollectorResult

class InterpreterResult(TypedDict):
    symbol: str
    by_timeframe: dict      # {"4H": {rsi, macd, ema, adx, wavetrend, bb, cvd, stochrsi}, "1D": {...}}
    sr_levels: list[dict]   # [{price, type, strength, touches, recency_score}]
    confluence_score: dict  # {score, direction_bias, signals_aligned, signals_total}
    trend_direction: str    # "bullish" | "bearish" | "neutral"
    momentum_bias: str      # "strong" | "moderate" | "weak" | "conflicted"
    prompt_text: str        # compact ~300-char summary for injection into prompts
```

**Calls:** `chart_indicators.compute_all_indicators()`, `chart_sr.detect_support_resistance()`, `chart_context.confluence_score()` — all on candles from `CollectorResult`.

**Failure:** Returns neutral defaults with empty `sr_levels` and `confluence_score.score = 0`. Never raises.

---

### 3. MarketSentimentAnalyzer — `agent_market_sentiment.py`

**Purpose:** Interprets already-fetched market data into a structured macro sentiment verdict. Pure — reads only from `CollectorResult`.

```python
class SentimentInput(TypedDict):
    symbol: str
    direction: str
    collected: CollectorResult

class SentimentResult(TypedDict):
    macro_bias: str          # "bullish" | "neutral" | "bearish"
    sentiment_score: float   # 0–10 (10 = strongly supports trade direction)
    funding_bias: str        # "longs_paying" | "shorts_paying" | "neutral"
    crowd_position: str      # "majority_long" | "majority_short" | "balanced"
    contra_signal: bool      # True when crowd heavily positioned against trade direction
    key_factors: list[str]   # ["F&G 82 — Extreme Greed", "Funding +0.08%/8h — longs paying"]
    grok_summary: str        # Grok text or "" if large-cap / unconfigured
    prompt_text: str         # compact summary for injection into TradePrep prompt
```

**Logic:** Scores F&G (extreme values penalise trend trades, reward contrarian), funding rate direction and magnitude, L/S ratio vs trade direction, Grok summary text. `contra_signal=True` when crowd_position opposes direction by >65%.

**Failure:** Returns neutral defaults (`macro_bias="neutral"`, `sentiment_score=5`). Never raises.

---

### 4. DataReviewer + KPI Generator — `agent_data_reviewer.py`

**Purpose:** Quality-gates the technical picture before an expensive Claude call. Generates trading KPIs and backtest context from DB history.

```python
class ReviewerInput(TypedDict):
    interpreted: InterpreterResult
    symbol: str
    direction: str
    setup_type: str          # "breakout" | "reversal" | "continuation" | "range"

class ReviewerResult(TypedDict):
    signal_quality: float    # 0–10 — how clean/reliable the technical picture is
    warnings: list[str]      # ["ADX 18 — no clear trend", "Only 1 S/R touch — weak level"]
    backtest_context: str    # compact string from analytics.get_backtest_context()
    kpis: dict               # {win_rate, avg_win, avg_loss, profit_factor, streak, total_trades}
    symbol_history: dict     # from trade_history.get_symbol_summary()
    rubric: str              # setup-type scoring rubric from prompt_builder.get_setup_rubric()
```

**Signal quality scoring rules:**
- Start at 10. Deduct for: confluence_score < 3 (-2), ADX < 20 on trend setup (-1.5), single S/R touch (-1), RSI in no-signal zone 40–60 (-0.5), conflicted momentum_bias (-1), missing volume data (-0.5).
- Cap at 10, floor at 0.

**Hits DB:** `analytics.get_backtest_context()`, `analytics.get_dashboard_kpis()`, `trade_history.get_symbol_summary()`. Read-only.

**Failure:** Returns `signal_quality=5`, empty warnings, empty backtest_context. Never raises.

---

### 5. TradePreparation — `agent_trade_prep.py`

**Purpose:** Assembles all upstream agent outputs into a single prompt and makes the main Claude Sonnet call. Runs Gemini in parallel for consensus.

```python
class TradePrepInput(TypedDict):
    collected: CollectorResult
    interpreted: InterpreterResult
    reviewed: ReviewerResult
    sentiment: SentimentResult
    call_text: str
    account_equity: float
    setup_type: str

class TradePrepResult(TypedDict):
    setup_score: int             # 1–10
    direction: str
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: float
    rr_ratio: float
    key_conditions: list[str]
    pattern_warnings: list[str]
    sizing_hint: str
    cot_reasoning: str           # stored in analyzed_calls for next same-symbol call
    gemini_score: int
    consensus: dict              # from agent_orchestrator.compute_consensus()
    raw_json: dict               # full Claude response
    _model: str
    _cached_tokens: int
```

**Prompt assembly order (same priority as current `prompt_builder.build_context()`):**
1. `build_stable_prefix()` — rulebook + calibration (cached block)
2. `reviewed.backtest_context` — historical WR/streaks
3. `sentiment.prompt_text` — macro bias + contra signal flag
4. `interpreted.prompt_text` — chart indicators per timeframe
5. `reviewed.rubric` — setup-type scoring rubric
6. `call_text` — the actual trade call

**Model:** Claude Sonnet (`MODEL`). Gemini Flash (`GEMINI_FAST_MODEL`) in parallel thread.

**Failure:** Raises on Claude API error (caller catches). Gemini failure degrades gracefully — consensus skipped, `gemini_score=0`.

---

### 6. RiskManagement — `agent_risk_mgmt.py`

**Purpose:** Pure-math risk gate. No AI call. Validates sizing, correlation, SL quality, and Kelly criterion before a trade is presented to the user.

```python
class RiskInput(TypedDict):
    trade_prep: TradePrepResult
    account_equity: float
    open_positions: list[dict]

class RiskResult(TypedDict):
    approved: bool               # False = hard block (max_risk_hit or invalid SL)
    position_size_usdt: float
    margin_usdt: float
    risk_pct: float
    atr_sl_valid: bool           # SL is outside 1H ATR noise range
    correlation_warning: str     # "" or human-readable warning
    max_risk_hit: bool           # True if trade would breach drawdown limit
    kelly_fraction: float        # Kelly criterion fraction, capped at 0.25
    warnings: list[str]
    sizing_breakdown: dict       # full calculation detail
```

**Logic:**
- `_calc_sizing()` moves here from `ai_call.py` (same formula, same result)
- Kelly criterion: `f = (win_rate * avg_win_r - (1-win_rate)) / avg_win_r` where `avg_win_r = avg_win / abs(avg_loss)`, capped at 0.25, floored at 0.05
- `max_risk_hit`: True if `(open_position_count >= 5 AND direction concentration >= 4 same-side)`
- `atr_sl_valid`: calls `trade_utils.atr_sl_warning()` — same check as today
- `approved = not max_risk_hit and atr_sl_valid`

**Failure:** Never raises. Returns `approved=False` with warnings on any calculation error.

---

### 7. TradeMonitor — `agent_trade_monitor.py`

**Purpose:** Proactive monitoring of open positions via a background thread. Recommend-only — fires Telegram alerts and sets a UI badge. No autonomous trade execution.

```python
class MonitorInput(TypedDict):
    position: dict               # live position dict from bitget_client
    original_prep: dict | None   # TradePrepResult stored when trade was opened (nullable)
    interpreted: InterpreterResult
    sentiment: SentimentResult

class MonitorResult(TypedDict):
    action: str                  # "Hold" | "Adjust SL" | "Partial Close" | "Close Now"
    action_reason: str
    risk_rating: int             # 1–10
    alert_level: str             # "info" | "warning" | "critical"
    tp_recommendation: dict      # {price, rationale}
    sl_recommendation: dict      # {price, rationale}
    key_risks: list[str]
    summary: str
    _symbol: str
    _checked_at: float
```

**Monitor chain per position:**
```
DataCollector (fresh, uses TTL caches)
    → DataInterpreter (pure)
    → MarketSentimentAnalyzer (pure)
    → Haiku prompt (action verdict, 768 tokens max)
```

**Thread schedule:** `App start → wait 2 min → first pass → every MONITOR_INTERVAL (default 600s / 10 min) → repeat`

**Polling filter:** Only process positions where `unrealized_pct < -5%` OR `duration_minutes > 240`. Skips healthy short-duration positions to avoid unnecessary Haiku calls.

**Alert trigger:** `risk_rating >= 7` OR `action != "Hold"` → Telegram message + `UPDATE analyzed_calls SET monitor_alert=1 WHERE symbol=? AND status='matched'`

**Failure:** Per-position errors are caught and logged. Thread never stops.

---

## Orchestrator Pipeline Functions

Three new functions added to `agent_orchestrator.py`. Existing `compute_consensus()`, `route_model()`, `add_gemini_consensus()` are unchanged.

```python
def run_call_analysis(call_text, symbol, direction, account_equity,
                      setup_type, open_positions, conn) -> AnalysisResult

def run_scanner_prep(symbol, direction, collected, interpreted,
                     reviewed, sentiment, conn) -> TradePrepResult

def run_monitor(position, original_prep) -> MonitorResult
```

`AnalysisResult` is a flat TypedDict merging all agent outputs for persistence to `analyzed_calls`:

```python
class AnalysisResult(TypedDict):
    # from TradePrepResult
    setup_score: int
    direction: str
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: float
    rr_ratio: float
    key_conditions: list[str]
    pattern_warnings: list[str]
    cot_reasoning: str
    gemini_score: int
    consensus: dict
    raw_json: dict
    # from RiskResult
    risk_approved: bool
    risk_verdict_json: str       # JSON-serialised RiskResult — stored in analyzed_calls
    position_size_usdt: float
    margin_usdt: float
    kelly_fraction: float
    # from SentimentResult
    macro_bias: str
    contra_signal: bool
    sentiment_score: float
    # from ReviewerResult
    signal_quality: float
    reviewer_warnings: list[str]
    # pipeline metadata
    error: str                   # "" on success
    degraded: bool               # True if any non-blocking agent failed
```

---

## Migration — Existing Files

| File | Change |
|------|--------|
| `agent_orchestrator.py` | +3 pipeline functions (~60 lines). All existing functions unchanged. |
| `ai_call.py` | `analyze_call()` delegates to `run_call_analysis()`. `_calc_sizing()` delegates to `agent_risk_mgmt.run()`. External signature identical — `routes/calls.py` unchanged. |
| `ai_scanner.py` | Stage 3b (`_batch_score()`) calls `run_scanner_prep()` per finalist. Stages 1, 2, 3a unchanged. |
| `ai_live_trade.py` | `analyze_position()` delegates to `run_monitor()`. External signature identical. |
| `prompt_builder.py` | `build_context()` deprecated (agents replace it). `build_stable_prefix()` stays. |
| `database.py` | Migration 29: `ALTER TABLE analyzed_calls ADD COLUMN risk_verdict_json TEXT`. Migration 30: `ADD COLUMN monitor_alert INTEGER DEFAULT 0`. |
| `app.py` | Start monitor background thread (~10 lines). |
| `constants.py` | Add `MONITOR_INTERVAL = 600`, `MONITOR_THRESHOLD_PCT = -5.0`, `MONITOR_THRESHOLD_DURATION = 240`. |

**Zero changes to:** `routes/*.py`, `scanner_scheduler.py`, `chart_context.py`, `chart_indicators.py`, `chart_sr.py`, `market_context.py`, `nansen_client.py`, `grok_client.py`, `gemini_client.py`, `analytics.py`, `trade_history.py`, `helpers.py`, `prompt_fragments.py`, `trade_utils.py`.

---

## New Files

| File | Lines (est.) | Purpose |
|------|-------------|---------|
| `agent_data_collector.py` | ~130 | Parallel fetch of all data sources |
| `agent_data_interpreter.py` | ~110 | Pure indicator transforms |
| `agent_data_reviewer.py` | ~120 | DB reads + signal quality gate |
| `agent_market_sentiment.py` | ~90 | Pure sentiment interpretation |
| `agent_trade_prep.py` | ~160 | Main Claude + Gemini call |
| `agent_risk_mgmt.py` | ~130 | Pure math sizing + Kelly |
| `agent_trade_monitor.py` | ~150 | Haiku chain + alert firing |

---

## Error Handling

- **Blocking agents** (DataCollector candle fetch, TradePrep Claude call): raise on failure — pipeline short-circuits, `AnalysisResult(error=..., degraded=True)` returned to caller.
- **Non-blocking agents** (MarketSentiment, DataReviewer, RiskMgmt, Grok within DataCollector): degrade to safe defaults and continue. Same pattern as existing `market_context.get_market_str()` fallback.
- **TradeMonitor thread**: per-position errors caught and logged. Thread continues.

---

## Testing

**Unit tests (new):** One test file per agent in `tests/`. Each agent tested in isolation using mocked inputs — no live API calls needed.

```
tests/test_agent_data_interpreter.py   # pure function — feed CSV candles
tests/test_agent_risk_mgmt.py          # pure math — test sizing + Kelly + blocks
tests/test_agent_market_sentiment.py   # pure function — feed mock CollectorResult
tests/test_agent_data_reviewer.py      # uses in-memory SQLite from conftest.py
tests/test_agent_trade_monitor.py      # mock position + interpreted + sentiment
```

**Integration:** `scripts/self_test.py --agents` flag runs the full pipeline against the live Pi with a sample call text. Verifies `AnalysisResult` has no missing keys and `risk_verdict_json` is persisted to `analyzed_calls`.

---

## Build Order

Agents must be built in dependency order:

```
1. agent_data_collector.py          (no deps on other agents)
2. agent_data_interpreter.py        (depends on CollectorResult)
3. agent_market_sentiment.py        (depends on CollectorResult)
4. agent_data_reviewer.py           (depends on InterpreterResult + DB)
5. agent_risk_mgmt.py               (pure math — no agent deps)
6. agent_trade_prep.py              (depends on all above)
7. agent_trade_monitor.py           (depends on 1+2+3, calls Haiku)
8. agent_orchestrator.py additions  (wires all agents)
9. ai_call.py migration             (delegates to orchestrator)
10. ai_scanner.py migration         (Stage 3b delegates)
11. ai_live_trade.py migration      (delegates to monitor)
12. database.py migrations 29-30
13. app.py monitor thread
14. tests + self_test.py --agents flag
```
