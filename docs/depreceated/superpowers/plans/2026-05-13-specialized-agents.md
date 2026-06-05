# Specialized Agent Infrastructure Implementation Plan

> **STATUS: FULLY IMPLEMENTED** — commits d01741c through 6e70a9d (2026-05-13). All 15 tasks complete, post-implementation bug fixes applied. See spec for final architecture.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the existing AI pipeline into 7 specialized agents with typed contracts, add a proactive TradeMonitor background thread, and generate annotated trade charts for UI + Telegram alerts.

**Architecture:** One file per agent in the project root, following existing module patterns. All TypedDicts live in a single `agent_types.py`. `agent_orchestrator.py` gains 3 pipeline runner functions that wire the agents together. Existing Flask routes, `scanner_scheduler.py`, and all data-source clients are untouched.

**Tech Stack:** Python 3.13, TypedDict (stdlib), ThreadPoolExecutor (stdlib), matplotlib + mplfinance (new dep), SQLite (existing), Anthropic SDK (existing), pytest (existing).

**Spec:** `docs/superpowers/specs/2026-05-13-specialized-agents-design.md`

---

## File Structure

**New files:**
```
agent_types.py                  All TypedDicts — shared contracts
agent_data_collector.py         Parallel fetch of all data sources (~130 lines)
agent_data_interpreter.py       Pure indicator transforms (~110 lines)
agent_market_sentiment.py       Pure sentiment interpretation (~90 lines)
agent_data_reviewer.py          DB reads + signal quality gate (~120 lines)
agent_risk_mgmt.py              Pure math sizing + Kelly criterion (~130 lines)
agent_trade_prep.py             Main Claude + Gemini call (~160 lines)
agent_trade_monitor.py          Haiku chain + alert firing (~150 lines)
agent_chart_draw.py             Annotated trade chart generator (~140 lines)
tests/test_agent_interpreter.py
tests/test_agent_sentiment.py
tests/test_agent_risk_mgmt.py
tests/test_agent_reviewer.py
tests/test_agent_chart_draw.py
```

**Modified files:**
```
agent_orchestrator.py           +3 pipeline functions, existing unchanged
ai_call.py                      analyze_call() delegates to run_call_analysis()
ai_scanner.py                   Stage 3b calls run_scanner_prep()
ai_live_trade.py                analyze_position() delegates to run_monitor()
telegram_notify.py              send_setup_alert() can attach chart image
database.py                     Migrations 29-30
constants.py                    MONITOR_INTERVAL, MONITOR_THRESHOLD_PCT, MONITOR_THRESHOLD_DURATION
app.py                          Start monitor thread
requirements.txt                Add mplfinance
```

---

## Task 0: agent_types.py — all TypedDicts

**Files:**
- Create: `agent_types.py`

- [ ] **Create `agent_types.py` with all contracts**

```python
"""
agent_types.py — TypedDict contracts for all specialized agents.

Single source of truth for all input/output shapes. Import from here
rather than from individual agent files to avoid circular imports.
"""
from __future__ import annotations
from typing import TypedDict


class CollectorInput(TypedDict):
    symbol: str
    direction: str       # "Long" | "Short"
    timeframes: list     # e.g. ["4H", "1D"]


class CollectorResult(TypedDict):
    symbol: str
    candles: dict        # {"4H": pd.DataFrame, "1D": pd.DataFrame}
    funding_rate: dict   # {rate, rate_pct, direction, high, ok}
    open_interest: dict  # {oi_coins, oi_usd_m, change_24h_pct, trend, ok}
    long_short: dict     # {long_pct, short_pct, bias, ok}
    fear_greed: dict     # {value, classification, ok}
    fred_macro: dict     # {fed_rate, cpi, m2_b, t10y, ok}
    nansen: dict         # {signal, label, smart_money_bias} or {}
    grok: dict           # {text, weight} or {}
    fetched_at: float    # unix timestamp


class InterpreterInput(TypedDict):
    collected: CollectorResult


class InterpreterResult(TypedDict):
    symbol: str
    by_timeframe: dict   # {tf: indicators_dict} — raw output of compute_all_indicators()
    sr_levels: list      # [{price, type, strength, touches, recency_score}]
    confluence_score: dict  # {score, max, bullish, bearish, label, details}
    trend_direction: str    # "bullish" | "bearish" | "neutral"
    momentum_bias: str      # "strong" | "moderate" | "weak" | "conflicted"
    prompt_text: str        # compact ~400-char summary


class SentimentInput(TypedDict):
    symbol: str
    direction: str
    collected: CollectorResult


class SentimentResult(TypedDict):
    macro_bias: str         # "bullish" | "neutral" | "bearish"
    sentiment_score: float  # 0–10
    funding_bias: str       # "longs_paying" | "shorts_paying" | "neutral"
    crowd_position: str     # "majority_long" | "majority_short" | "balanced"
    contra_signal: bool     # True when crowd opposes trade direction by >65%
    key_factors: list       # ["F&G 82 — Extreme Greed", ...]
    grok_summary: str       # Grok text or ""
    prompt_text: str        # compact summary for injection


class ReviewerInput(TypedDict):
    interpreted: InterpreterResult
    symbol: str
    direction: str
    setup_type: str          # "breakout" | "reversal" | "continuation" | "range" | ""


class ReviewerResult(TypedDict):
    signal_quality: float    # 0–10
    warnings: list           # ["ADX 18 — no clear trend", ...]
    backtest_context: str    # from analytics.get_backtest_context()
    kpis: dict               # {win_rate_pct, avg_win, avg_loss, profit_factor, streak}
    symbol_history: dict     # from trade_history.get_symbol_summary()
    rubric: str              # setup-type scoring rubric


class TradePrepInput(TypedDict):
    collected: CollectorResult
    interpreted: InterpreterResult
    reviewed: ReviewerResult
    sentiment: SentimentResult
    call_text: str
    account_equity: float
    setup_type: str


class TradePrepResult(TypedDict):
    setup_score: int
    direction: str
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: float
    rr_ratio: float
    key_conditions: list
    pattern_warnings: list
    sizing_hint: str
    cot_reasoning: str
    gemini_score: int
    consensus: dict
    raw_json: dict
    chart_png_b64: str       # base64 PNG of annotated chart, "" if not generated
    _model: str
    _cached_tokens: int


class RiskInput(TypedDict):
    trade_prep: TradePrepResult
    account_equity: float
    open_positions: list


class RiskResult(TypedDict):
    approved: bool
    position_size_usdt: float
    margin_usdt: float
    risk_pct: float
    atr_sl_valid: bool
    correlation_warning: str
    max_risk_hit: bool
    kelly_fraction: float
    warnings: list
    sizing_breakdown: dict


class MonitorInput(TypedDict):
    position: dict               # live position from bitget_client
    original_prep: dict          # TradePrepResult or {} if not available
    interpreted: InterpreterResult
    sentiment: SentimentResult


class MonitorResult(TypedDict):
    action: str                  # "Hold" | "Adjust SL" | "Partial Close" | "Close Now"
    action_reason: str
    risk_rating: int             # 1–10
    alert_level: str             # "info" | "warning" | "critical"
    tp_recommendation: dict      # {price, rationale}
    sl_recommendation: dict      # {price, rationale}
    key_risks: list
    summary: str
    _symbol: str
    _checked_at: float


class AnalysisResult(TypedDict):
    # from TradePrepResult
    setup_score: int
    direction: str
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: float
    rr_ratio: float
    key_conditions: list
    pattern_warnings: list
    cot_reasoning: str
    gemini_score: int
    consensus: dict
    raw_json: dict
    chart_png_b64: str
    # from RiskResult
    risk_approved: bool
    risk_verdict_json: str
    position_size_usdt: float
    margin_usdt: float
    kelly_fraction: float
    # from SentimentResult
    macro_bias: str
    contra_signal: bool
    sentiment_score: float
    # from ReviewerResult
    signal_quality: float
    reviewer_warnings: list
    # pipeline metadata
    error: str
    degraded: bool
```

- [ ] **Commit**

```bash
git add agent_types.py
git commit -m "feat: add agent_types.py — all TypedDict contracts for specialized agents"
```

---

## Task 1: DataCollector

**Files:**
- Create: `agent_data_collector.py`

- [ ] **Create `agent_data_collector.py`**

```python
"""
agent_data_collector.py — DataCollector agent.

Single entry point for all external data. Runs all non-candle fetches
in parallel via ThreadPoolExecutor. Candle fetch is sequential and
blocking — if it fails the pipeline cannot continue.

All non-candle sources degrade gracefully to {} on failure.
"""
import time
from concurrent.futures import ThreadPoolExecutor

import chart_context
import market_context
import nansen_client
import grok_client

from agent_types import CollectorInput, CollectorResult


def run(inp: CollectorInput) -> CollectorResult:
    symbol    = inp["symbol"]
    direction = inp["direction"]
    tfs       = inp["timeframes"]

    # Candles are blocking — raises on failure (downstream agents require them)
    candles = {tf: chart_context.get_candles(symbol, tf) for tf in tfs}

    def _safe(fn):
        try:
            return fn()
        except Exception:
            return {}

    with ThreadPoolExecutor(max_workers=7) as ex:
        f_funding = ex.submit(_safe, lambda: market_context.get_funding_rate(symbol))
        f_oi      = ex.submit(_safe, lambda: market_context.get_open_interest(symbol))
        f_ls      = ex.submit(_safe, lambda: market_context.get_long_short_ratio(symbol))
        f_fg      = ex.submit(_safe, market_context.get_fear_greed)
        f_fred    = ex.submit(_safe, market_context.get_fred_macro)
        f_nansen  = ex.submit(_safe, lambda: nansen_client.get_smart_money_signal(symbol))
        f_grok    = ex.submit(_safe, lambda: _grok(symbol, direction))

    return CollectorResult(
        symbol        = symbol,
        candles       = candles,
        funding_rate  = f_funding.result(),
        open_interest = f_oi.result(),
        long_short    = f_ls.result(),
        fear_greed    = f_fg.result(),
        fred_macro    = f_fred.result(),
        nansen        = f_nansen.result(),
        grok          = f_grok.result(),
        fetched_at    = time.time(),
    )


def _grok(symbol: str, direction: str) -> dict:
    text, weight = grok_client.get_coin_context(symbol, direction)
    if not text:
        return {}
    return {"text": text, "weight": weight}
```

- [ ] **Commit**

```bash
git add agent_data_collector.py
git commit -m "feat: add agent_data_collector — parallel fetch of all data sources"
```

---

## Task 2: DataInterpreter

**Files:**
- Create: `agent_data_interpreter.py`
- Create: `tests/test_agent_interpreter.py`

- [ ] **Write failing test**

```python
# tests/test_agent_interpreter.py
import pandas as pd
import numpy as np
import pytest
from agent_types import CollectorResult, InterpreterResult
import agent_data_interpreter as interp


def _mock_candles(n=100) -> pd.DataFrame:
    """Generate synthetic OHLCV data with a mild uptrend."""
    idx = pd.date_range("2026-01-01", periods=n, freq="4h")
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    df = pd.DataFrame({
        "open":   close - 0.2,
        "high":   close + 0.5,
        "low":    close - 0.5,
        "close":  close,
        "volume": np.random.randint(1000, 5000, n).astype(float),
    }, index=idx)
    return df


def _mock_collected(symbol="BTCUSDT") -> CollectorResult:
    df = _mock_candles()
    return CollectorResult(
        symbol="BTCUSDT", candles={"4H": df, "1D": df},
        funding_rate={}, open_interest={}, long_short={},
        fear_greed={}, fred_macro={}, nansen={}, grok={},
        fetched_at=0.0,
    )


def test_interpreter_returns_correct_shape():
    result = interp.run({"collected": _mock_collected()})
    assert isinstance(result, dict)
    assert result["symbol"] == "BTCUSDT"
    assert "by_timeframe" in result
    assert "sr_levels" in result
    assert "confluence_score" in result
    assert result["trend_direction"] in ("bullish", "bearish", "neutral")
    assert result["momentum_bias"] in ("strong", "moderate", "weak", "conflicted")
    assert isinstance(result["prompt_text"], str)
    assert len(result["prompt_text"]) <= 500


def test_interpreter_handles_empty_candles():
    collected = _mock_collected()
    collected["candles"] = {"4H": pd.DataFrame(), "1D": pd.DataFrame()}
    result = interp.run({"collected": collected})
    assert result["trend_direction"] == "neutral"
    assert result["sr_levels"] == []
    assert result["momentum_bias"] == "conflicted"
```

- [ ] **Run to confirm failure**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
python3 -m pytest tests/test_agent_interpreter.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'agent_data_interpreter'`

- [ ] **Create `agent_data_interpreter.py`**

```python
"""
agent_data_interpreter.py — DataInterpreter agent.

Pure function — no AI, no DB, no network. Transforms raw candles from
CollectorResult into structured technical signals for downstream agents.
"""
import chart_indicators
import chart_sr
import chart_context as cc

from agent_types import InterpreterInput, InterpreterResult


def run(inp: InterpreterInput) -> InterpreterResult:
    collected = inp["collected"]
    symbol    = collected["symbol"]
    candles   = collected["candles"]

    by_tf = {}
    for tf, df in candles.items():
        if df is None or df.empty:
            by_tf[tf] = {}
            continue
        try:
            by_tf[tf] = chart_indicators.compute_all_indicators(df)
        except Exception:
            by_tf[tf] = {}

    # S/R from primary timeframe (prefer 4H)
    primary_df = candles.get("4H") or next(
        (df for df in candles.values() if df is not None and not df.empty), None
    )
    sr_levels = []
    if primary_df is not None and not primary_df.empty:
        try:
            sr_levels = chart_sr.detect_support_resistance(primary_df)
        except Exception:
            pass

    # confluence_score expects ctx in format {tf: {"indicators": {...}, "ok": True}}
    conf_ctx = {tf: {"indicators": data, "ok": bool(data)} for tf, data in by_tf.items()}
    conf = {}
    try:
        conf = cc.confluence_score(symbol, list(candles.keys()), ctx=conf_ctx)
    except Exception:
        pass

    return InterpreterResult(
        symbol           = symbol,
        by_timeframe     = by_tf,
        sr_levels        = sr_levels,
        confluence_score = conf,
        trend_direction  = _trend(by_tf),
        momentum_bias    = _momentum(conf),
        prompt_text      = _prompt_text(symbol, by_tf, conf, sr_levels),
    )


def _trend(by_tf: dict) -> str:
    bullish = bearish = 0
    for data in by_tf.values():
        ema = data.get("ema", {})
        bias = str(ema.get("bias", "") or ema.get("trend", "")).lower()
        if "bullish" in bias:
            bullish += 1
        elif "bearish" in bias:
            bearish += 1
    if bullish > bearish:
        return "bullish"
    if bearish > bullish:
        return "bearish"
    return "neutral"


def _momentum(conf: dict) -> str:
    label = conf.get("label", "").lower()
    if "strong" in label:
        return "strong"
    if label in ("bullish", "bearish"):
        return "moderate"
    if "neutral" in label:
        return "weak"
    return "conflicted"


def _prompt_text(symbol: str, by_tf: dict, conf: dict, sr: list) -> str:
    parts = [f"[{symbol}]"]
    for tf, data in by_tf.items():
        if not data:
            continue
        rsi_v  = data.get("rsi",  {}).get("value", "?")
        ema_b  = data.get("ema",  {}).get("bias") or data.get("ema", {}).get("trend", "?")
        adx_v  = data.get("adx",  {}).get("value", "?")
        macd_s = data.get("macd", {}).get("signal", "?")
        parts.append(f"{tf}: RSI {rsi_v} | EMA {ema_b} | ADX {adx_v} | MACD {macd_s}")
    if conf:
        parts.append(f"Confluence {conf.get('label','?')} ({conf.get('score',0):.1f}/{conf.get('max',0):.1f})")
    if sr:
        near = sr[:3]
        sr_str = " ".join(f"{s.get('type','?')}@{s.get('price','?')}" for s in near)
        parts.append(f"S/R: {sr_str}")
    return " | ".join(parts)[:500]
```

- [ ] **Run tests — expect pass**

```bash
python3 -m pytest tests/test_agent_interpreter.py -v
```

- [ ] **Commit**

```bash
git add agent_data_interpreter.py tests/test_agent_interpreter.py
git commit -m "feat: add agent_data_interpreter — pure indicator transforms"
```

---

## Task 3: MarketSentimentAnalyzer

**Files:**
- Create: `agent_market_sentiment.py`
- Create: `tests/test_agent_sentiment.py`

- [ ] **Write failing test**

```python
# tests/test_agent_sentiment.py
import pytest
from agent_types import CollectorResult, SentimentResult
import agent_market_sentiment as sent


def _collected(fg=55, long_pct=70, funding_rate=0.0005) -> CollectorResult:
    return CollectorResult(
        symbol="XRPUSDT", candles={}, fetched_at=0.0,
        open_interest={}, fred_macro={}, nansen={},
        grok={"text": "Bullish momentum building", "weight": 0.4},
        fear_greed={"value": fg, "classification": "Greed", "ok": True},
        long_short={"long_pct": long_pct, "short_pct": 100 - long_pct,
                    "bias": "crowded long" if long_pct > 65 else "balanced", "ok": True},
        funding_rate={"rate": funding_rate, "rate_pct": funding_rate * 100,
                      "direction": "longs paying" if funding_rate > 0 else "shorts paying",
                      "high": abs(funding_rate) >= 0.0005, "ok": True},
    )


def test_contra_signal_when_crowd_opposes_long():
    # 70% longs + high funding → short squeeze risk for new Long entry
    result = sent.run({"symbol": "XRPUSDT", "direction": "Long", "collected": _collected()})
    assert result["contra_signal"] is True
    assert result["crowd_position"] == "majority_long"
    assert result["funding_bias"] == "longs_paying"
    assert len(result["key_factors"]) >= 1


def test_no_contra_signal_for_short_when_crowd_long():
    # Crowd is long → Short is contrarian (good) → no contra_signal
    result = sent.run({"symbol": "XRPUSDT", "direction": "Short", "collected": _collected()})
    assert result["contra_signal"] is False


def test_neutral_defaults_on_empty_data():
    empty = CollectorResult(
        symbol="XYZUSDT", candles={}, fetched_at=0.0,
        funding_rate={}, open_interest={}, long_short={},
        fear_greed={}, fred_macro={}, nansen={}, grok={},
    )
    result = sent.run({"symbol": "XYZUSDT", "direction": "Long", "collected": empty})
    assert result["macro_bias"] == "neutral"
    assert result["sentiment_score"] == 5.0
    assert result["contra_signal"] is False
```

- [ ] **Run to confirm failure**

```bash
python3 -m pytest tests/test_agent_sentiment.py -v 2>&1 | head -20
```

- [ ] **Create `agent_market_sentiment.py`**

```python
"""
agent_market_sentiment.py — MarketSentimentAnalyzer agent.

Pure function — reads only from CollectorResult. No AI, no DB, no network.
Interprets funding, L/S ratio, Fear & Greed, and Grok into a structured
sentiment verdict with a contra_signal flag.
"""
from agent_types import SentimentInput, SentimentResult


def run(inp: SentimentInput) -> SentimentResult:
    direction = inp["direction"]
    c         = inp["collected"]

    fg     = c.get("fear_greed",   {})
    fr     = c.get("funding_rate", {})
    ls     = c.get("long_short",   {})
    grok   = c.get("grok",         {})

    score       = 5.0
    key_factors = []

    # ── Fear & Greed ────────────────────────────────────────────────────────
    fg_val = fg.get("value")
    fg_cls = fg.get("classification", "")
    if fg_val is not None:
        key_factors.append(f"F&G {fg_val} — {fg_cls}")
        if fg_val <= 25:   # Extreme Fear — contrarian bullish
            score += 1.5 if direction == "Long" else -1.0
        elif fg_val <= 45:  # Fear
            score += 0.5 if direction == "Long" else -0.3
        elif fg_val >= 75:  # Extreme Greed — contrarian bearish
            score -= 1.5 if direction == "Long" else -1.0
        elif fg_val >= 55:  # Greed
            score -= 0.3 if direction == "Long" else 0.5

    # ── Funding rate ────────────────────────────────────────────────────────
    fr_rate = fr.get("rate")
    funding_bias = "neutral"
    if fr_rate is not None:
        fr_pct = fr.get("rate_pct", fr_rate * 100)
        if fr_rate > 0:
            funding_bias = "longs_paying"
            key_factors.append(f"Funding +{fr_pct:.4f}% — longs paying")
            if direction == "Long":
                score -= 0.5 if not fr.get("high") else 1.5
        elif fr_rate < 0:
            funding_bias = "shorts_paying"
            key_factors.append(f"Funding {fr_pct:.4f}% — shorts paying")
            if direction == "Short":
                score -= 0.5 if not fr.get("high") else 1.5

    # ── Long/Short ratio ────────────────────────────────────────────────────
    long_pct      = ls.get("long_pct", 50)
    crowd_pos     = "balanced"
    contra_signal = False

    if ls.get("ok"):
        if long_pct > 65:
            crowd_pos = "majority_long"
            key_factors.append(f"L/S ratio: {long_pct}% long — crowded")
            if direction == "Long":
                contra_signal = True
                score -= 1.0
            else:
                score += 0.5   # contrarian short
        elif long_pct < 35:
            crowd_pos = "majority_short"
            key_factors.append(f"L/S ratio: {long_pct}% long — crowded short")
            if direction == "Short":
                contra_signal = True
                score -= 1.0
            else:
                score += 0.5   # contrarian long

    # ── Grok social ─────────────────────────────────────────────────────────
    grok_text = grok.get("text", "")
    if grok_text:
        weight = grok.get("weight", 0.4)
        lower  = grok_text.lower()
        if any(w in lower for w in ("bearish", "dump", "fud", "sell", "⚠")):
            score -= 1.0 * weight if direction == "Long" else -0.5 * weight
        elif any(w in lower for w in ("bullish", "pump", "buy", "accumul")):
            score += 0.5 * weight if direction == "Long" else -0.5 * weight

    score = round(max(0.0, min(10.0, score)), 1)
    bias  = "bullish" if score > 6 else "bearish" if score < 4 else "neutral"

    lines = [f"Sentiment {score}/10 ({bias})"]
    if key_factors:
        lines.append("Factors: " + " | ".join(key_factors))
    if grok_text:
        lines.append(f"Social: {grok_text[:120]}")
    prompt_text = "\n".join(lines)

    return SentimentResult(
        macro_bias      = bias,
        sentiment_score = score,
        funding_bias    = funding_bias,
        crowd_position  = crowd_pos,
        contra_signal   = contra_signal,
        key_factors     = key_factors,
        grok_summary    = grok_text,
        prompt_text     = prompt_text,
    )
```

- [ ] **Run tests — expect pass**

```bash
python3 -m pytest tests/test_agent_sentiment.py -v
```

- [ ] **Commit**

```bash
git add agent_market_sentiment.py tests/test_agent_sentiment.py
git commit -m "feat: add agent_market_sentiment — pure sentiment interpretation"
```

---

## Task 4: DataReviewer

**Files:**
- Create: `agent_data_reviewer.py`
- Create: `tests/test_agent_reviewer.py`

- [ ] **Write failing test**

```python
# tests/test_agent_reviewer.py
import pytest
from agent_types import InterpreterResult, ReviewerResult
import agent_data_reviewer as rev


def _mock_interpreted(conf_score=6.0, adx=25.0, rsi=65.0, sr_touches=3) -> InterpreterResult:
    return InterpreterResult(
        symbol="BTCUSDT",
        by_timeframe={"4H": {
            "adx":  {"value": adx},
            "rsi":  {"value": rsi},
            "ema":  {"bias": "Bullish"},
            "macd": {"signal": "bullish"},
        }},
        sr_levels=[{"price": 95000, "type": "support", "strength": 0.8,
                    "touches": sr_touches, "recency_score": 0.9}],
        confluence_score={"score": conf_score, "max": 11.8, "label": "Bullish",
                          "bullish": 3.2, "bearish": 0.8, "details": []},
        trend_direction="bullish",
        momentum_bias="moderate",
        prompt_text="[BTCUSDT] 4H: RSI 65",
    )


def test_reviewer_returns_correct_shape(db):
    inp = {"interpreted": _mock_interpreted(), "symbol": "BTCUSDT",
           "direction": "Long", "setup_type": "continuation"}
    result = rev.run(inp, db)
    assert isinstance(result["signal_quality"], float)
    assert 0.0 <= result["signal_quality"] <= 10.0
    assert isinstance(result["warnings"], list)
    assert isinstance(result["backtest_context"], str)
    assert "kpis" in result
    assert "symbol_history" in result
    assert "rubric" in result


def test_low_confluence_reduces_quality(db):
    inp = {"interpreted": _mock_interpreted(conf_score=1.5), "symbol": "BTCUSDT",
           "direction": "Long", "setup_type": "continuation"}
    result = rev.run(inp, db)
    assert result["signal_quality"] <= 8.0
    assert any("confluence" in w.lower() for w in result["warnings"])


def test_low_adx_warns_on_trend_setup(db):
    inp = {"interpreted": _mock_interpreted(adx=15.0), "symbol": "BTCUSDT",
           "direction": "Long", "setup_type": "breakout"}
    result = rev.run(inp, db)
    assert any("adx" in w.lower() for w in result["warnings"])
```

Note: `db` fixture is defined in `tests/conftest.py` (in-memory SQLite, already exists).

- [ ] **Run to confirm failure**

```bash
python3 -m pytest tests/test_agent_reviewer.py -v 2>&1 | head -20
```

- [ ] **Create `agent_data_reviewer.py`**

```python
"""
agent_data_reviewer.py — DataReviewer + KPI Generator agent.

Reads from DB to generate backtest context and trading KPIs. Quality-
gates the technical picture before an expensive Claude call. Never raises.
"""
import json
from analytics import get_backtest_context, get_dashboard_kpis
from trade_history import get_symbol_summary
from prompt_builder import get_setup_rubric
from agent_types import ReviewerInput, ReviewerResult


def run(inp: ReviewerInput, conn) -> ReviewerResult:
    interpreted = inp["interpreted"]
    symbol      = inp["symbol"]
    direction   = inp["direction"]
    setup_type  = inp["setup_type"]

    backtest = ""
    kpis     = {}
    history  = {}
    try:
        backtest = get_backtest_context(conn, symbol, direction, setup_type)
    except Exception:
        pass

    try:
        raw_kpis = get_dashboard_kpis(filters={"symbol": symbol}, conn=conn)
        kpis = {
            "win_rate_pct":  raw_kpis.get("win_rate", 0),
            "avg_win":       raw_kpis.get("avg_win", 0),
            "avg_loss":      raw_kpis.get("avg_loss", 0),
            "profit_factor": raw_kpis.get("profit_factor", 0),
            "total_trades":  raw_kpis.get("total_trades", 0),
        }
    except Exception:
        pass

    try:
        history = get_symbol_summary(symbol, conn)
    except Exception:
        pass

    rubric = get_setup_rubric(setup_type)
    quality, warnings = _signal_quality(interpreted, setup_type)

    return ReviewerResult(
        signal_quality   = quality,
        warnings         = warnings,
        backtest_context = backtest,
        kpis             = kpis,
        symbol_history   = history,
        rubric           = rubric,
    )


def _signal_quality(interpreted: dict, setup_type: str) -> tuple:
    score    = 10.0
    warnings = []

    conf       = interpreted.get("confluence_score", {})
    conf_score = conf.get("score", 0)
    if conf_score < 3:
        score -= 2.0
        warnings.append(f"Confluence {conf_score:.1f} — weak multi-signal alignment")

    # ADX gate for trend-dependent setups
    if setup_type in ("breakout", "continuation"):
        for tf, data in interpreted.get("by_timeframe", {}).items():
            adx_val = data.get("adx", {}).get("value")
            if adx_val is not None:
                try:
                    if float(adx_val) < 20:
                        score -= 1.5
                        warnings.append(f"ADX {adx_val} ({tf}) — no clear trend for {setup_type}")
                except (TypeError, ValueError):
                    pass
                break

    # S/R touch count
    sr = interpreted.get("sr_levels", [])
    if sr and all(s.get("touches", 2) < 2 for s in sr[:3]):
        score -= 1.0
        warnings.append("Only 1 S/R touch on nearest levels — weak level")

    # RSI neutral zone
    for tf, data in interpreted.get("by_timeframe", {}).items():
        rsi_val = data.get("rsi", {}).get("value")
        if rsi_val is not None:
            try:
                rsi = float(rsi_val)
                if 40 <= rsi <= 60:
                    score -= 0.5
                    warnings.append(f"RSI {rsi:.0f} ({tf}) — neutral zone")
            except (TypeError, ValueError):
                pass
        break

    if interpreted.get("momentum_bias") == "conflicted":
        score -= 1.0
        warnings.append("Conflicted momentum — indicators disagree on direction")

    return round(max(0.0, min(10.0, score)), 1), warnings
```

- [ ] **Run tests — expect pass**

```bash
python3 -m pytest tests/test_agent_reviewer.py -v
```

- [ ] **Commit**

```bash
git add agent_data_reviewer.py tests/test_agent_reviewer.py
git commit -m "feat: add agent_data_reviewer — signal quality gate + KPI generator"
```

---

## Task 5: RiskManagement

**Files:**
- Create: `agent_risk_mgmt.py`
- Create: `tests/test_agent_risk_mgmt.py`

- [ ] **Write failing test**

```python
# tests/test_agent_risk_mgmt.py
import pytest
from agent_types import TradePrepResult, RiskInput, RiskResult
import agent_risk_mgmt as rm


def _prep(setup_score=7, entry=1.50, sl=1.42, tp1=1.65, direction="Long") -> TradePrepResult:
    return TradePrepResult(
        setup_score=setup_score, direction=direction,
        entry_price=entry, sl_price=sl, tp1_price=tp1, tp2_price=tp1 * 1.05,
        rr_ratio=round(abs(tp1 - entry) / abs(entry - sl), 2),
        key_conditions=[], pattern_warnings=[], sizing_hint="",
        cot_reasoning="", gemini_score=7, consensus={}, raw_json={},
        chart_png_b64="", _model="", _cached_tokens=0,
    )


def test_basic_sizing():
    result = rm.run({"trade_prep": _prep(), "account_equity": 500.0,
                     "open_positions": []}, conn=None)
    assert isinstance(result["position_size_usdt"], float)
    assert result["position_size_usdt"] > 0
    assert isinstance(result["margin_usdt"], float)
    assert 0 < result["risk_pct"] <= 2.0


def test_blocks_on_sl_wrong_side():
    # Long with SL above entry — invalid
    result = rm.run({"trade_prep": _prep(entry=1.50, sl=1.55), "account_equity": 500.0,
                     "open_positions": []}, conn=None)
    assert result["approved"] is False
    assert any("stop loss" in w.lower() for w in result["warnings"])


def test_kelly_is_capped_at_025():
    result = rm.run({"trade_prep": _prep(), "account_equity": 500.0,
                     "open_positions": []}, conn=None)
    assert 0.05 <= result["kelly_fraction"] <= 0.25


def test_correlation_warning_on_concentration():
    positions = [{"side": "long"} for _ in range(4)]
    result = rm.run({"trade_prep": _prep(direction="Long"), "account_equity": 500.0,
                     "open_positions": positions}, conn=None)
    assert result["correlation_warning"] != ""
```

- [ ] **Run to confirm failure**

```bash
python3 -m pytest tests/test_agent_risk_mgmt.py -v 2>&1 | head -20
```

- [ ] **Create `agent_risk_mgmt.py`**

```python
"""
agent_risk_mgmt.py — RiskManagement agent.

Pure math — no AI call. Validates position sizing, SL quality, Kelly
criterion, and directional concentration. _calc_sizing() migrated here
from ai_call.py.
"""
import json
import trade_utils
from agent_types import RiskInput, RiskResult

LEVERAGE      = 10
MAX_RISK_PCT  = 2.0   # max % of equity risked per trade
MAX_SAME_SIDE = 4     # block if ≥ this many open positions on same side


def run(inp: RiskInput, conn=None) -> RiskResult:
    prep           = inp["trade_prep"]
    account_equity = inp["account_equity"]
    open_positions = inp["open_positions"]

    entry     = prep["entry_price"]
    sl        = prep["sl_price"]
    direction = prep["direction"]
    is_long   = direction.lower() == "long"
    warnings  = []

    # Validate SL side
    if entry and sl:
        if is_long and sl >= entry:
            return _blocked(["Long stop loss must be below entry price"])
        if not is_long and sl <= entry:
            return _blocked(["Short stop loss must be above entry price"])

    sizing = _calc_sizing(account_equity, entry, sl, direction=direction)
    if "error" in sizing:
        return _blocked([sizing["error"]])

    # ATR SL quality check
    atr_warn = ""
    atr_valid = True
    if entry and sl:
        try:
            atr_warn = trade_utils.atr_sl_warning(prep.get("_symbol", ""), entry, sl)
            atr_valid = not bool(atr_warn)
        except Exception:
            pass
    if atr_warn:
        warnings.append(atr_warn)

    # Directional concentration
    corr_warn = _correlation_check(direction, open_positions)
    if corr_warn:
        warnings.append(corr_warn)

    max_risk = _max_risk_check(direction, open_positions)
    if max_risk:
        warnings.append(f"Already {MAX_SAME_SIDE}+ {direction} positions — high directional risk")

    # Kelly criterion from kpis (if available via reviewer — passed as conn here for future use)
    kelly = _kelly(prep)

    approved = atr_valid and not max_risk

    return RiskResult(
        approved             = approved,
        position_size_usdt   = sizing.get("notional", 0.0),
        margin_usdt          = sizing.get("margin", 0.0),
        risk_pct             = sizing.get("risk_pct", 1.0),
        atr_sl_valid         = atr_valid,
        correlation_warning  = corr_warn,
        max_risk_hit         = max_risk,
        kelly_fraction       = kelly,
        warnings             = warnings,
        sizing_breakdown     = sizing,
    )


def _calc_sizing(account_equity: float, entry: float, sl: float,
                 dca_price: float = None, dca_pct: int = 40,
                 leverage: int = LEVERAGE, direction: str = "Long") -> dict:
    """Position sizing based on fixed risk % of equity. Migrated from ai_call.py."""
    is_long  = direction.lower() == "long"
    has_dca  = dca_price is not None
    risk_pct = 2.0 if has_dca else 1.0
    risk_amt = round(account_equity * risk_pct / 100, 2)

    if has_dca:
        e1_pct    = 100 - dca_pct
        avg_entry = (entry * e1_pct + dca_price * dca_pct) / 100
    else:
        avg_entry = entry

    if is_long and avg_entry <= sl:
        return {"error": "Long stop loss must be below entry price"}
    if not is_long and avg_entry >= sl:
        return {"error": "Short stop loss must be above entry price"}

    stop_dist = abs(avg_entry - sl) / avg_entry
    if stop_dist == 0:
        return {"error": "Entry and stop loss are the same price"}

    notional = round(risk_amt / stop_dist, 0)
    margin   = round(notional / leverage, 2)

    return {
        "account_equity": round(account_equity, 2),
        "risk_pct":       risk_pct,
        "risk_amt":       risk_amt,
        "avg_entry":      avg_entry,
        "stop_dist_pct":  round(stop_dist * 100, 3),
        "notional":       notional,
        "margin":         margin,
        "leverage":       leverage,
    }


def _kelly(prep: dict) -> float:
    """Kelly criterion from consensus score as proxy for edge. Capped 0.05–0.25."""
    score = prep.get("setup_score", 5)
    # Map score 1-10 to win_rate proxy 0.35–0.75
    win_rate = 0.35 + (score / 10) * 0.40
    avg_win_r = 2.0  # assume 2:1 R:R as conservative baseline
    f = (win_rate * avg_win_r - (1 - win_rate)) / avg_win_r
    return round(max(0.05, min(0.25, f)), 3)


def _correlation_check(direction: str, positions: list) -> str:
    side = "long" if direction.lower() == "long" else "short"
    count = sum(1 for p in positions if str(p.get("side", "")).lower() == side)
    if count >= 3:
        return f"Already {count} {direction} positions open — directional concentration risk"
    return ""


def _max_risk_check(direction: str, positions: list) -> bool:
    side  = "long" if direction.lower() == "long" else "short"
    count = sum(1 for p in positions if str(p.get("side", "")).lower() == side)
    return count >= MAX_SAME_SIDE


def _blocked(warnings: list) -> RiskResult:
    return RiskResult(
        approved=False, position_size_usdt=0.0, margin_usdt=0.0,
        risk_pct=0.0, atr_sl_valid=False, correlation_warning="",
        max_risk_hit=False, kelly_fraction=0.05,
        warnings=warnings, sizing_breakdown={},
    )
```

- [ ] **Run tests — expect pass**

```bash
python3 -m pytest tests/test_agent_risk_mgmt.py -v
```

- [ ] **Commit**

```bash
git add agent_risk_mgmt.py tests/test_agent_risk_mgmt.py
git commit -m "feat: add agent_risk_mgmt — pure math sizing + Kelly criterion"
```

---

## Task 6: ChartDraw agent

**Files:**
- Create: `agent_chart_draw.py`
- Create: `tests/test_agent_chart_draw.py`
- Modify: `requirements.txt`

- [ ] **Add mplfinance to requirements.txt**

Open `requirements.txt` and add:
```
mplfinance>=0.12.10b0
```

- [ ] **Install locally**

```bash
pip3 install mplfinance --break-system-packages
```

- [ ] **Write failing test**

```python
# tests/test_agent_chart_draw.py
import pandas as pd
import numpy as np
import base64
import pytest
import agent_chart_draw as cd


def _candles(n=60) -> pd.DataFrame:
    idx   = pd.date_range("2026-01-01", periods=n, freq="4h")
    close = 1.50 + np.cumsum(np.random.randn(n) * 0.005)
    return pd.DataFrame({
        "open":   close - 0.002,
        "high":   close + 0.010,
        "low":    close - 0.010,
        "close":  close,
        "volume": np.random.randint(100, 1000, n).astype(float),
    }, index=idx)


def test_chart_returns_base64_png():
    df = _candles()
    result = cd.draw(
        candles=df,
        symbol="XRPUSDT",
        direction="Long",
        entry=1.52,
        sl=1.46,
        tp1=1.61,
        tp2=1.72,
        criteria=["RSI 72 — overbought | EMA bullish", "Confluence 7.2/10 — Strong Bullish"],
    )
    assert isinstance(result, str)
    assert len(result) > 100
    # Must be valid base64 PNG
    decoded = base64.b64decode(result)
    assert decoded[:8] == b'\x89PNG\r\n\x1a\n'


def test_chart_returns_empty_string_on_error():
    # Empty DataFrame — should not raise
    result = cd.draw(
        candles=pd.DataFrame(),
        symbol="XRPUSDT", direction="Long",
        entry=1.52, sl=1.46, tp1=1.61, tp2=1.72,
        criteria=[],
    )
    assert result == ""
```

- [ ] **Run to confirm failure**

```bash
python3 -m pytest tests/test_agent_chart_draw.py -v 2>&1 | head -20
```

- [ ] **Create `agent_chart_draw.py`**

```python
"""
agent_chart_draw.py — Annotated trade chart generator.

Generates a candlestick chart (4H candles, last 60 periods) with:
  - Entry price (blue dashed line)
  - Stop loss (red dashed line)
  - TP1 and TP2 (green dashed lines)
  - Key criteria as text annotations in the top-left
  - RSI subplot below the price chart

Returns base64-encoded PNG string. Returns "" on any failure.
"""
import base64
import io
from typing import Optional

import pandas as pd


def draw(
    candles: pd.DataFrame,
    symbol: str,
    direction: str,
    entry: float,
    sl: float,
    tp1: float,
    tp2: float,
    criteria: list[str],
    n_candles: int = 60,
) -> str:
    """
    Returns base64-encoded PNG or "" on failure.
    criteria: list of short strings explaining why the trade was taken.
    """
    try:
        import mplfinance as mpf
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
    except ImportError:
        return ""

    if candles is None or candles.empty:
        return ""

    try:
        df = candles.tail(n_candles).copy()
        df.index = pd.DatetimeIndex(df.index)

        # Horizontal level lines
        hlines = [entry, sl, tp1, tp2]
        hcolors = ["#4A90D9", "#E05555", "#55A85A", "#55A85A"]
        hwidths = [1.5, 1.5, 1.2, 1.2]
        hstyles = ["--", "--", "--", ":"]

        addplots = []
        for price, color, lw, ls in zip(hlines, hcolors, hwidths, hstyles):
            series = pd.Series(price, index=df.index)
            addplots.append(mpf.make_addplot(series, color=color, width=lw, linestyle=ls))

        title_dir = "▲ LONG" if direction.lower() == "long" else "▼ SHORT"
        title = f"{symbol}  {title_dir}  Entry {entry:.4f}  SL {sl:.4f}  TP1 {tp1:.4f}  TP2 {tp2:.4f}"

        mc = mpf.make_marketcolors(up="#26a69a", down="#ef5350",
                                   wick="inherit", edge="inherit",
                                   volume={"up": "#26a69a44", "down": "#ef535044"})
        style = mpf.make_mpf_style(marketcolors=mc, base_mpf_style="nightclouds",
                                   gridstyle=":", gridcolor="#2a2a2a",
                                   facecolor="#1a1a2e", edgecolor="#2a2a2e",
                                   figcolor="#1a1a2e", y_on_right=False)

        fig, axes = mpf.plot(
            df, type="candle", style=style,
            addplot=addplots,
            volume=True,
            title=title,
            returnfig=True,
            figsize=(14, 8),
            tight_layout=True,
        )

        ax = axes[0]

        # Legend for levels
        patches = [
            mpatches.Patch(color="#4A90D9", label=f"Entry {entry:.4f}"),
            mpatches.Patch(color="#E05555", label=f"SL {sl:.4f}"),
            mpatches.Patch(color="#55A85A", label=f"TP1 {tp1:.4f}"),
            mpatches.Patch(color="#55A85A", alpha=0.5, label=f"TP2 {tp2:.4f}"),
        ]
        ax.legend(handles=patches, loc="upper left", fontsize=8,
                  facecolor="#1a1a2e", edgecolor="#444", labelcolor="white")

        # Criteria annotations in top-right corner
        if criteria:
            crit_text = "\n".join(f"• {c}" for c in criteria[:5])
            ax.text(0.99, 0.99, crit_text, transform=ax.transAxes,
                    fontsize=7, verticalalignment="top", horizontalalignment="right",
                    color="#cccccc", bbox=dict(boxstyle="round,pad=0.3",
                                               facecolor="#1a1a2e", edgecolor="#444", alpha=0.85))

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=120, bbox_inches="tight",
                    facecolor="#1a1a2e", edgecolor="none")
        plt.close(fig)
        buf.seek(0)
        return base64.b64encode(buf.read()).decode("utf-8")

    except Exception:
        return ""
```

- [ ] **Run tests — expect pass**

```bash
python3 -m pytest tests/test_agent_chart_draw.py -v
```

- [ ] **Commit**

```bash
git add agent_chart_draw.py tests/test_agent_chart_draw.py requirements.txt
git commit -m "feat: add agent_chart_draw — annotated trade chart PNG (entry/SL/TP + criteria)"
```

---

## Task 7: TradePreparation

**Files:**
- Create: `agent_trade_prep.py`

- [ ] **Create `agent_trade_prep.py`**

```python
"""
agent_trade_prep.py — TradePreparation agent.

Owns the main Claude Sonnet call. Assembles prompt from all upstream
agent outputs + stable_prefix for caching. Runs Gemini in parallel.
Generates annotated chart after Claude responds.
"""
import json
from concurrent.futures import ThreadPoolExecutor

from constants import MODEL
from ai_client import send as ai_send
from helpers import strip_fence, build_cached_messages
import prompt_builder
import gemini_client
import agent_orchestrator
import agent_chart_draw

from agent_types import TradePrepInput, TradePrepResult


def run(inp: TradePrepInput, conn) -> TradePrepResult:
    collected  = inp["collected"]
    interpreted = inp["interpreted"]
    reviewed   = inp["reviewed"]
    sentiment  = inp["sentiment"]
    call_text  = inp["call_text"]
    setup_type = inp["setup_type"]
    equity     = inp["account_equity"]
    symbol     = collected["symbol"]
    direction  = _infer_direction(call_text) or "Long"

    stable = prompt_builder.build_stable_prefix(conn)

    # Dynamic context assembled from agent outputs
    parts = []
    if reviewed["backtest_context"]:
        parts.append(reviewed["backtest_context"])
    if sentiment["prompt_text"]:
        parts.append(sentiment["prompt_text"])
    if interpreted["prompt_text"]:
        parts.append(interpreted["prompt_text"])
    if reviewed["rubric"]:
        parts.append(f"SETUP RUBRIC ({setup_type}):\n{reviewed['rubric']}")
    sq = reviewed["signal_quality"]
    sq_note = f"SIGNAL QUALITY: {sq:.1f}/10"
    if reviewed["warnings"]:
        sq_note += " — " + "; ".join(reviewed["warnings"])
    parts.append(sq_note)
    dynamic_ctx = "\n\n".join(parts)

    prompt = _build_prompt(call_text, equity, setup_type)

    gemini_result = {}
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_claude = ex.submit(
            ai_send, "call_analyzer", MODEL,
            build_cached_messages(dynamic_ctx, prompt, stable_prefix=stable),
            4096,
        )
        if gemini_client.is_configured() and call_text:
            f_gemini = ex.submit(gemini_client.score_call, call_text, symbol, direction)
        else:
            f_gemini = None

    raw_text, cached = f_claude.result()
    if f_gemini:
        try:
            gemini_result = f_gemini.result() or {}
        except Exception:
            pass

    raw = strip_fence(raw_text.strip())
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}

    claude_score = int(data.get("setup_score", 0))
    gemini_score = int(gemini_result.get("score", 0))
    consensus = (
        agent_orchestrator.compute_consensus(claude_score, gemini_score)
        if gemini_score else {}
    )

    direction_out = data.get("direction", direction)
    entry  = float(data.get("entry_price", 0) or 0)
    sl     = float(data.get("sl_price",    0) or 0)
    tp1    = float(data.get("tp1",         0) or 0)
    tp2    = float(data.get("tp2",         0) or 0)

    # Generate annotated chart
    criteria = [
        f"Score {claude_score}/10 · {sentiment.get('macro_bias','?').title()} macro",
        interpreted.get("prompt_text", "")[:100],
    ]
    criteria += (data.get("key_conditions") or [])[:3]
    candles_4h = collected["candles"].get("4H")
    chart_b64 = agent_chart_draw.draw(
        candles=candles_4h,
        symbol=symbol, direction=direction_out,
        entry=entry, sl=sl, tp1=tp1, tp2=tp2,
        criteria=[c for c in criteria if c],
    ) if entry and sl and tp1 else ""

    return TradePrepResult(
        setup_score      = claude_score,
        direction        = direction_out,
        entry_price      = entry,
        sl_price         = sl,
        tp1_price        = tp1,
        tp2_price        = tp2,
        rr_ratio         = float(data.get("rr_ratio", 0) or 0),
        key_conditions   = data.get("key_conditions", []),
        pattern_warnings = data.get("pattern_warnings", []),
        sizing_hint      = data.get("sizing_hint", ""),
        cot_reasoning    = data.get("cot_reasoning", ""),
        gemini_score     = gemini_score,
        consensus        = consensus,
        raw_json         = data,
        chart_png_b64    = chart_b64,
        _model           = MODEL,
        _cached_tokens   = cached if isinstance(cached, int) else 0,
    )


def _build_prompt(call_text: str, equity: float, setup_type: str) -> str:
    return f"""You are a professional crypto futures trading analyst. Analyze the trade call below.

ACCOUNT EQUITY: ${equity:.2f}
SETUP TYPE: {setup_type or "unspecified"}

TRADE CALL:
{call_text}

Respond with ONLY valid JSON (no markdown, no code fences):
{{"setup_score":1,"direction":"Long","entry_price":0.0,"sl_price":0.0,"tp1":0.0,"tp2":0.0,"rr_ratio":0.0,"key_conditions":["condition 1"],"pattern_warnings":[],"sizing_hint":"one sentence","cot_reasoning":"2-3 sentence chain of thought"}}

Rules:
- setup_score: 1-4=avoid, 5-6=monitor, 7-8=good, 9-10=strong conviction
- Long: sl_price MUST be below entry_price. Short: sl_price MUST be above entry_price.
- tp1 = conservative target (1.5:1 R:R min), tp2 = full target (2.5:1+ R:R)
- Reference the context provided above (backtest WR, sentiment, indicators)
- cot_reasoning: state the 2-3 strongest reasons for your score"""


def _infer_direction(text: str) -> str:
    lower = (text or "").lower()
    if any(w in lower for w in ("short", "sell", "bear")):
        return "Short"
    return "Long"
```

- [ ] **Commit**

```bash
git add agent_trade_prep.py
git commit -m "feat: add agent_trade_prep — main Claude+Gemini call with chart generation"
```

---

## Task 8: TradeMonitor

**Files:**
- Create: `agent_trade_monitor.py`
- Create: `tests/test_agent_trade_monitor.py`

- [ ] **Write failing test**

```python
# tests/test_agent_trade_monitor.py
import pytest
from unittest.mock import patch, MagicMock
from agent_types import MonitorInput, MonitorResult, InterpreterResult, SentimentResult
import agent_trade_monitor as mon


def _interp() -> InterpreterResult:
    return InterpreterResult(
        symbol="BTCUSDT", by_timeframe={}, sr_levels=[],
        confluence_score={}, trend_direction="bearish",
        momentum_bias="strong", prompt_text="bearish momentum",
    )


def _sent() -> SentimentResult:
    return SentimentResult(
        macro_bias="bearish", sentiment_score=3.0, funding_bias="longs_paying",
        crowd_position="majority_long", contra_signal=True,
        key_factors=["F&G 80 — Extreme Greed"],
        grok_summary="", prompt_text="Bearish macro",
    )


def _position(symbol="BTCUSDT", unrealized_pct=-35.0, duration_min=300):
    return {
        "symbol": symbol, "side": "long",
        "unrealized_pct": unrealized_pct,
        "duration_minutes": duration_min,
        "markPrice": "95000",
        "openPrice": "102000",
        "unrealizedPL": "-700",
        "leverage": "10",
    }


@patch("agent_trade_monitor._call_haiku")
def test_critical_loss_returns_close_recommendation(mock_haiku):
    mock_haiku.return_value = MonitorResult(
        action="Close Now", action_reason="Position down -35%, bearish confluence",
        risk_rating=9, alert_level="critical",
        tp_recommendation={"price": "0", "rationale": ""},
        sl_recommendation={"price": "101000", "rationale": "Above entry"},
        key_risks=["High funding cost", "Bearish divergence"],
        summary="Close to prevent further loss",
        _symbol="BTCUSDT", _checked_at=0.0,
    )
    result = mon.run({"position": _position(), "original_prep": {},
                      "interpreted": _interp(), "sentiment": _sent()})
    assert result["risk_rating"] >= 7
    assert result["action"] in ("Close Now", "Partial Close")
    assert result["alert_level"] == "critical"
```

- [ ] **Run to confirm failure**

```bash
python3 -m pytest tests/test_agent_trade_monitor.py -v 2>&1 | head -20
```

- [ ] **Create `agent_trade_monitor.py`**

```python
"""
agent_trade_monitor.py — TradeMonitor agent.

Called by the background monitor thread (monitor_scheduler.py) for each
open position that passes the polling filter. Runs a lightweight chain:
  InterpreterResult + SentimentResult → Haiku verdict.

Returns MonitorResult with action recommendation. Does NOT execute trades.
On risk_rating >= 7 or action != "Hold", callers fire Telegram + set
monitor_alert=1 in analyzed_calls.
"""
import json
import time

from constants import FAST_MODEL
from ai_client import send as ai_send
from helpers import strip_fence
from agent_types import MonitorInput, MonitorResult


def run(inp: MonitorInput) -> MonitorResult:
    return _call_haiku(inp)


def _call_haiku(inp: MonitorInput) -> MonitorResult:
    position   = inp["position"]
    orig_prep  = inp.get("original_prep") or {}
    interpreted = inp["interpreted"]
    sentiment  = inp["sentiment"]
    symbol     = position.get("symbol", "")

    prompt = _build_prompt(position, orig_prep, interpreted, sentiment)

    raw_text, _ = ai_send(
        "live_trade", FAST_MODEL,
        [{"role": "user", "content": prompt}],
        max_tokens=768,
    )
    raw = strip_fence(raw_text.strip())

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}

    risk_rating = int(data.get("risk_rating", {}).get("value", 5)
                      if isinstance(data.get("risk_rating"), dict)
                      else data.get("risk_rating", 5))

    alert_level = "critical" if risk_rating >= 8 else "warning" if risk_rating >= 6 else "info"

    return MonitorResult(
        action           = data.get("action", "Hold"),
        action_reason    = data.get("action_reason", ""),
        risk_rating      = risk_rating,
        alert_level      = alert_level,
        tp_recommendation = data.get("tp_recommendation", {}),
        sl_recommendation = data.get("sl_recommendation", {}),
        key_risks        = data.get("key_risks", []),
        summary          = data.get("summary", ""),
        _symbol          = symbol,
        _checked_at      = time.time(),
    )


def _build_prompt(position: dict, orig_prep: dict,
                  interpreted: dict, sentiment: dict) -> str:
    symbol   = position.get("symbol", "")
    unrl_pct = position.get("unrealized_pct", 0)
    unrl_pl  = position.get("unrealizedPL", "?")
    entry    = position.get("openPrice", "?")
    mark     = position.get("markPrice", "?")
    dur      = position.get("duration_minutes", 0)
    side     = position.get("side", "long").title()
    lev      = position.get("leverage", "10")
    sl       = position.get("stop_loss", "") or orig_prep.get("sl_price", "not set")
    tp       = position.get("take_profit", "") or orig_prep.get("tp1_price", "not set")

    sent_txt  = sentiment.get("prompt_text", "")
    interp_txt = interpreted.get("prompt_text", "")

    return f"""You are a crypto futures risk manager. Assess this OPEN position and give a specific, actionable verdict.

POSITION: {symbol} {side} {lev}x
Entry: {entry} | Mark: {mark} | Unrealized: {unrl_pct:.1f}% (${unrl_pl})
Duration: {dur:.0f} min | SL: {sl} | TP: {tp}

CURRENT TECHNICALS:
{interp_txt}

MARKET SENTIMENT:
{sent_txt}

Respond with ONLY valid JSON (no markdown):
{{"risk_rating":{{"value":1,"label":"Low|Medium|High|Critical"}},"action":"Hold|Adjust SL|Partial Close|Close Now","action_reason":"one sentence WHY","tp_recommendation":{{"price":"0","rationale":"one sentence"}},"sl_recommendation":{{"price":"0","rationale":"one sentence"}},"key_risks":["risk 1","risk 2"],"summary":"2 sentence assessment"}}

Rules:
- unrealized_pct < -30% → seriously consider Close Now or Partial Close
- SL is "not set" AND unrealized_pct < -5% → recommend setting one (risk_rating >= 6)
- Contra signal (crowd against position) → raise risk_rating by 1
- Reference actual numbers in your reasoning"""
```

- [ ] **Run tests — expect pass**

```bash
python3 -m pytest tests/test_agent_trade_monitor.py -v
```

- [ ] **Commit**

```bash
git add agent_trade_monitor.py tests/test_agent_trade_monitor.py
git commit -m "feat: add agent_trade_monitor — Haiku-powered position risk assessment"
```

---

## Task 9: Orchestrator pipeline functions

**Files:**
- Modify: `agent_orchestrator.py`

- [ ] **Add imports to `agent_orchestrator.py` (after existing imports)**

```python
import agent_data_collector
import agent_data_interpreter
import agent_market_sentiment
import agent_data_reviewer
import agent_trade_prep
import agent_risk_mgmt
import agent_trade_monitor
from agent_types import AnalysisResult, TradePrepInput, RiskInput, MonitorInput
```

- [ ] **Add three pipeline functions to the bottom of `agent_orchestrator.py`**

```python
# ── Pipeline runners ───────────────────────────────────────────────────────────

def run_call_analysis(
    call_text: str,
    symbol: str,
    direction: str,
    account_equity: float,
    setup_type: str,
    open_positions: list,
    conn,
) -> AnalysisResult:
    """
    Full 5-stage pipeline for a trade call analysis.
    Returns AnalysisResult — a flat dict suitable for persistence to analyzed_calls.
    On blocking failure, returns AnalysisResult with error= and degraded=True.
    """
    try:
        # Stage 1: collect
        collected = agent_data_collector.run({
            "symbol": symbol, "direction": direction, "timeframes": ["4H", "1D"],
        })
    except Exception as e:
        return _degraded(str(e))

    # Stage 2: interpret + sentiment in parallel
    try:
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_interp = ex.submit(agent_data_interpreter.run, {"collected": collected})
            f_sent   = ex.submit(agent_market_sentiment.run,
                                 {"symbol": symbol, "direction": direction,
                                  "collected": collected})
        interpreted = f_interp.result()
        sentiment   = f_sent.result()
    except Exception:
        interpreted = _empty_interp(symbol)
        sentiment   = _empty_sent()

    # Stage 3: review
    try:
        reviewed = agent_data_reviewer.run({
            "interpreted": interpreted, "symbol": symbol,
            "direction": direction, "setup_type": setup_type,
        }, conn)
    except Exception:
        reviewed = _empty_review()

    # Stage 4: trade prep (blocking — raises on Claude API failure)
    prep = agent_trade_prep.run(TradePrepInput(
        collected=collected, interpreted=interpreted,
        reviewed=reviewed, sentiment=sentiment,
        call_text=call_text, account_equity=account_equity,
        setup_type=setup_type,
    ), conn)

    # Stage 5: risk (pure math — never raises)
    risk = agent_risk_mgmt.run(RiskInput(
        trade_prep=prep, account_equity=account_equity,
        open_positions=open_positions,
    ), conn)

    import json
    return AnalysisResult(
        # TradePrep fields
        setup_score=prep["setup_score"], direction=prep["direction"],
        entry_price=prep["entry_price"], sl_price=prep["sl_price"],
        tp1_price=prep["tp1_price"], tp2_price=prep["tp2_price"],
        rr_ratio=prep["rr_ratio"],
        key_conditions=prep["key_conditions"],
        pattern_warnings=prep["pattern_warnings"],
        cot_reasoning=prep["cot_reasoning"],
        gemini_score=prep["gemini_score"], consensus=prep["consensus"],
        raw_json=prep["raw_json"], chart_png_b64=prep["chart_png_b64"],
        # Risk fields
        risk_approved=risk["approved"],
        risk_verdict_json=json.dumps(risk),
        position_size_usdt=risk["position_size_usdt"],
        margin_usdt=risk["margin_usdt"],
        kelly_fraction=risk["kelly_fraction"],
        # Sentiment fields
        macro_bias=sentiment["macro_bias"],
        contra_signal=sentiment["contra_signal"],
        sentiment_score=sentiment["sentiment_score"],
        # Reviewer fields
        signal_quality=reviewed["signal_quality"],
        reviewer_warnings=reviewed["warnings"],
        error="", degraded=False,
    )


def run_scanner_prep(symbol: str, direction: str, collected, interpreted,
                     reviewed, sentiment, conn):
    """Stage 3b entry point for the scanner — replaces the inline Sonnet batch call."""
    return agent_trade_prep.run(TradePrepInput(
        collected=collected, interpreted=interpreted,
        reviewed=reviewed, sentiment=sentiment,
        call_text="", account_equity=0.0, setup_type="scanner",
    ), conn)


def run_monitor(position: dict, original_prep: dict) -> "MonitorResult":
    """Entry point for the monitor scheduler — runs the lightweight Haiku chain."""
    collected   = agent_data_collector.run({
        "symbol": position["symbol"],
        "direction": position.get("side", "long").title(),
        "timeframes": ["4H", "1D"],
    })
    interpreted = agent_data_interpreter.run({"collected": collected})
    sentiment   = agent_market_sentiment.run({
        "symbol": position["symbol"],
        "direction": position.get("side", "long").title(),
        "collected": collected,
    })
    return agent_trade_monitor.run(MonitorInput(
        position=position, original_prep=original_prep or {},
        interpreted=interpreted, sentiment=sentiment,
    ))


# ── Fallback helpers ───────────────────────────────────────────────────────────

def _degraded(error: str) -> "AnalysisResult":
    return AnalysisResult(
        setup_score=0, direction="", entry_price=0.0, sl_price=0.0,
        tp1_price=0.0, tp2_price=0.0, rr_ratio=0.0, key_conditions=[],
        pattern_warnings=[], cot_reasoning="", gemini_score=0, consensus={},
        raw_json={}, chart_png_b64="", risk_approved=False, risk_verdict_json="{}",
        position_size_usdt=0.0, margin_usdt=0.0, kelly_fraction=0.05,
        macro_bias="neutral", contra_signal=False, sentiment_score=5.0,
        signal_quality=0.0, reviewer_warnings=[], error=error, degraded=True,
    )


def _empty_interp(symbol: str) -> dict:
    from agent_types import InterpreterResult
    return InterpreterResult(symbol=symbol, by_timeframe={}, sr_levels={},
                              confluence_score={}, trend_direction="neutral",
                              momentum_bias="conflicted", prompt_text="")


def _empty_sent() -> dict:
    from agent_types import SentimentResult
    return SentimentResult(macro_bias="neutral", sentiment_score=5.0,
                           funding_bias="neutral", crowd_position="balanced",
                           contra_signal=False, key_factors=[], grok_summary="",
                           prompt_text="")


def _empty_review() -> dict:
    from agent_types import ReviewerResult
    return ReviewerResult(signal_quality=5.0, warnings=[], backtest_context="",
                          kpis={}, symbol_history={}, rubric="")
```

- [ ] **Commit**

```bash
git add agent_orchestrator.py
git commit -m "feat: add orchestrator pipeline runners — run_call_analysis, run_scanner_prep, run_monitor"
```

---

## Task 10: Migrate ai_call.py

**Files:**
- Modify: `ai_call.py`

- [ ] **Replace `analyze_call()` body in `ai_call.py`**

Find the `analyze_call` function (line ~208) and replace its body so it delegates to the orchestrator. The function signature and return shape must stay identical.

```python
def analyze_call(call_text: str, account_equity: float,
                 symbol: str = None, direction: str = None,
                 dca_price: float = None, dca_pct: int = 40,
                 leverage: int = LEVERAGE,
                 open_positions: list = None) -> dict:
    """
    Run full AI analysis for a trade call. Now delegates to the specialized
    agent pipeline via agent_orchestrator.run_call_analysis().
    External API is identical — routes/calls.py unchanged.
    """
    import agent_orchestrator

    if symbol is None:
        symbol = _extract_symbol(call_text)
    if direction is None:
        direction = "Short" if any(w in call_text.lower()
                                   for w in ("short", "sell", "bear")) else "Long"

    setup_type = _extract_setup_type(call_text)

    with db_conn() as conn:
        result = agent_orchestrator.run_call_analysis(
            call_text     = call_text,
            symbol        = symbol,
            direction     = direction,
            account_equity = account_equity,
            setup_type    = setup_type,
            open_positions = open_positions or [],
            conn          = conn,
        )

    if result.get("degraded"):
        raise RuntimeError(result.get("error", "Pipeline failed"))

    # Re-shape AnalysisResult to the existing return format that routes expect
    raw = result.get("raw_json", {})
    raw["setup_score"]    = result["setup_score"]
    raw["direction"]      = result["direction"]
    raw["entry_price"]    = result["entry_price"]
    raw["sl_price"]       = result["sl_price"]
    raw["tp1"]            = result["tp1_price"]
    raw["tp2"]            = result["tp2_price"]
    raw["rr_ratio"]       = result["rr_ratio"]
    raw["key_conditions"] = result["key_conditions"]
    raw["cot_reasoning"]  = result["cot_reasoning"]
    raw["chart_png_b64"]  = result["chart_png_b64"]

    # Sizing from RiskResult (replaces old _calc_sizing call)
    raw["sizing"] = {
        "position_size_usdt": result["position_size_usdt"],
        "margin_usdt":        result["margin_usdt"],
        "kelly_fraction":     result["kelly_fraction"],
        "risk_approved":      result["risk_approved"],
    }

    raw["_gemini"]    = {"score": result["gemini_score"]}
    raw["_consensus"] = result["consensus"]
    raw["_model"]     = MODEL
    raw["_signal_quality"] = result["signal_quality"]
    raw["_reviewer_warnings"] = result["reviewer_warnings"]
    raw["_contra_signal"]  = result["contra_signal"]
    raw["_sentiment_score"] = result["sentiment_score"]

    return raw


def _extract_symbol(text: str) -> str:
    """Best-effort symbol extraction from call text."""
    import re
    m = re.search(r'\b([A-Z]{2,10})USDT\b', text)
    if m:
        return m.group(0)
    return "UNKNOWN"


def _extract_setup_type(text: str) -> str:
    lower = text.lower()
    for t in ("breakout", "reversal", "continuation", "range"):
        if t in lower:
            return t
    return ""
```

- [ ] **Run existing tests to verify nothing broke**

```bash
python3 -m pytest tests/ -v -k "not agent" 2>&1 | tail -20
```

Expected: all previously passing tests still pass.

- [ ] **Commit**

```bash
git add ai_call.py
git commit -m "refactor: ai_call.analyze_call() delegates to agent pipeline"
```

---

## Task 11: Migrate ai_scanner.py — Stage 3b

**Files:**
- Modify: `ai_scanner.py`

Stage 3b currently builds an inline batch prompt and sends it to Claude. Replace it with per-symbol `run_scanner_prep()` calls.

- [ ] **Find `_batch_score()` (or equivalent Stage 3b function) in `ai_scanner.py`**

```bash
grep -n "def _batch\|Stage 3b\|batch_score\|sonnet\|MODEL" /Users/fbauer/Documents/ClaudeAIData/Trading-Journal/ai_scanner.py | head -20
```

- [ ] **Replace Stage 3b scoring loop**

Find the section that calls `ai_send` with the batch prompt and replace the per-finalist scoring with:

```python
# Stage 3b — TradePrep agent per finalist (replaces inline Sonnet batch call)
def _score_finalists_with_agents(finalists: list, ctx_map: dict, conn) -> list:
    """
    Run TradePrep agent for each finalist. Returns list of setup dicts
    with setup_score, entry_zone, sl_price, tp1, tp2, rr_ratio, key_conditions.
    """
    import agent_orchestrator
    import agent_data_collector
    import agent_data_interpreter
    import agent_market_sentiment
    import agent_data_reviewer

    results = []
    for sym, ctx, conf, direction, quick_score, rationale in finalists:
        try:
            collected = agent_data_collector.run({
                "symbol": sym, "direction": direction, "timeframes": ["4H", "1D"],
            })
            with ThreadPoolExecutor(max_workers=2) as ex:
                f_i = ex.submit(agent_data_interpreter.run, {"collected": collected})
                f_s = ex.submit(agent_market_sentiment.run,
                                {"symbol": sym, "direction": direction, "collected": collected})
            interpreted = f_i.result()
            sentiment   = f_s.result()
            reviewed = agent_data_reviewer.run({
                "interpreted": interpreted, "symbol": sym,
                "direction": direction, "setup_type": "scanner",
            }, conn)
            prep = agent_orchestrator.run_scanner_prep(
                sym, direction, collected, interpreted, reviewed, sentiment, conn,
            )
            results.append({
                "_symbol":      sym,
                "symbol":       sym,
                "direction":    direction,
                "setup_score":  prep["setup_score"],
                "entry_zone":   f"{prep['entry_price']:.4f}",
                "sl_price":     prep["sl_price"],
                "tp1":          prep["tp1_price"],
                "tp2":          prep["tp2_price"],
                "rr_ratio":     prep["rr_ratio"],
                "key_conditions": prep["key_conditions"],
                "chart_png_b64": prep["chart_png_b64"],
                "_quick_score": quick_score,
                "_rationale":   rationale,
            })
        except Exception as e:
            print(f"[Scanner] agent scoring failed for {sym}: {e}")
    return results
```

Wire this into the scanner's Stage 3b by replacing the existing `_batch_score()` call with `_score_finalists_with_agents(top_finalists, ctx_map, conn)`. The `conn` should be passed from the scanner's outer scope where `db_conn()` is already open.

- [ ] **Run scanner smoke test**

```bash
python3 scripts/self_test.py --host 192.168.1.21:8082 2>&1 | grep -E "scanner|PASS|FAIL" | head -15
```

- [ ] **Commit**

```bash
git add ai_scanner.py
git commit -m "refactor: ai_scanner Stage 3b uses agent pipeline per finalist"
```

---

## Task 12: Migrate ai_live_trade.py

**Files:**
- Modify: `ai_live_trade.py`

- [ ] **Replace `analyze_position()` body**

```python
def analyze_position(position: dict) -> dict:
    """
    Delegates to agent_orchestrator.run_monitor().
    External API unchanged — routes/live.py unchanged.
    """
    import agent_orchestrator

    # Look up original TradePrepResult if an analyzed_call exists for this symbol
    original_prep = {}
    try:
        with db_conn() as conn:
            row = conn.execute(
                """SELECT analysis_json FROM analyzed_calls
                   WHERE symbol=? AND status IN ('matched','saved')
                   ORDER BY created_at DESC LIMIT 1""",
                (position["symbol"],),
            ).fetchone()
            if row and row["analysis_json"]:
                import json
                analysis = json.loads(row["analysis_json"])
                original_prep = {
                    "sl_price":  analysis.get("sl_price"),
                    "tp1_price": analysis.get("tp1"),
                }
    except Exception:
        pass

    result = agent_orchestrator.run_monitor(position, original_prep)

    # Re-shape MonitorResult to existing return format
    return {
        "risk_rating":        {"value": result["risk_rating"],
                               "label": result["alert_level"].title()},
        "action":             result["action"],
        "action_reason":      result["action_reason"],
        "tp_recommendation":  result["tp_recommendation"],
        "sl_recommendation":  result["sl_recommendation"],
        "key_risks":          result["key_risks"],
        "summary":            result["summary"],
        "historical_context": "",
        "time_urgency":       "Immediate" if result["risk_rating"] >= 8 else
                              "Today" if result["risk_rating"] >= 6 else "No rush",
        "_symbol":            result["_symbol"],
        "_model":             FAST_MODEL,
    }
```

- [ ] **Run existing live-trade tests**

```bash
python3 -m pytest tests/ -v -k "live" 2>&1 | tail -10
```

- [ ] **Commit**

```bash
git add ai_live_trade.py
git commit -m "refactor: ai_live_trade delegates to agent_trade_monitor via orchestrator"
```

---

## Task 13: DB migrations + constants + app.py monitor thread

**Files:**
- Modify: `database.py`
- Modify: `constants.py`
- Modify: `app.py`
- Create: `monitor_scheduler.py`

- [ ] **Add constants to `constants.py`** (after existing constants)

```python
MONITOR_INTERVAL          = int(os.environ.get("MONITOR_INTERVAL",   "600"))   # 10 min
MONITOR_THRESHOLD_PCT     = float(os.environ.get("MONITOR_THRESHOLD_PCT", "-5.0"))
MONITOR_THRESHOLD_DURATION = int(os.environ.get("MONITOR_THRESHOLD_DURATION", "240"))
```

- [ ] **Add DB migrations to `database.py`**

Find the last `_apply()` call (currently migration 28) and add after it:

```python
_apply(conn, 29,
    "ALTER TABLE analyzed_calls ADD COLUMN risk_verdict_json TEXT")
_apply(conn, 30,
    "ALTER TABLE analyzed_calls ADD COLUMN monitor_alert INTEGER DEFAULT 0")
_apply(conn, 31,
    "ALTER TABLE analyzed_calls ADD COLUMN chart_png_b64 TEXT")
```

Migration 31 stores the chart so it can be retrieved by the UI and Telegram.

- [ ] **Create `monitor_scheduler.py`**

```python
"""
monitor_scheduler.py — Background thread that monitors open positions every 10 min.

Polls all open positions from Bitget. For each position that passes the
filter (unrealized_pct < MONITOR_THRESHOLD_PCT or duration > THRESHOLD_DURATION),
runs the TradeMonitor agent chain and:
  - On risk_rating >= 7 or action != "Hold": fires Telegram alert with chart
  - Sets monitor_alert=1 in analyzed_calls for UI badge
  - Logs result to analyzed_calls (updates existing matched row if present)
"""
import os
import threading
import time

import bitget_client
import telegram_notify
import agent_orchestrator
from constants import MONITOR_INTERVAL, MONITOR_THRESHOLD_PCT, MONITOR_THRESHOLD_DURATION
from database import db_conn

FIRST_DELAY = int(os.environ.get("MONITOR_FIRST_DELAY", "120"))  # 2 min


def _passes_filter(position: dict) -> bool:
    try:
        unrl = float(position.get("unrealized_pct", 0) or 0)
        dur  = float(position.get("duration_minutes", 0) or 0)
        return unrl < MONITOR_THRESHOLD_PCT or dur > MONITOR_THRESHOLD_DURATION
    except (TypeError, ValueError):
        return False


def _get_original_prep(conn, symbol: str) -> dict:
    try:
        row = conn.execute(
            """SELECT analysis_json FROM analyzed_calls
               WHERE symbol=? AND status IN ('matched','saved')
               ORDER BY created_at DESC LIMIT 1""",
            (symbol,),
        ).fetchone()
        if row and row["analysis_json"]:
            import json
            d = json.loads(row["analysis_json"])
            return {"sl_price": d.get("sl_price"), "tp1_price": d.get("tp1")}
    except Exception:
        pass
    return {}


def _run_once():
    try:
        positions = bitget_client.get_open_positions() or []
    except Exception as e:
        print(f"[Monitor] Failed to fetch positions: {e}")
        return

    to_check = [p for p in positions if _passes_filter(p)]
    if not to_check:
        return

    print(f"[Monitor] Checking {len(to_check)}/{len(positions)} positions")

    for pos in to_check:
        symbol = pos.get("symbol", "?")
        try:
            with db_conn() as conn:
                original_prep = _get_original_prep(conn, symbol)

            result = agent_orchestrator.run_monitor(pos, original_prep)

            should_alert = (result["risk_rating"] >= 7 or
                            result["action"] != "Hold")

            if should_alert:
                # Set monitor_alert flag in DB
                with db_conn() as conn:
                    conn.execute(
                        """UPDATE analyzed_calls SET monitor_alert=1
                           WHERE symbol=? AND status IN ('matched','saved')""",
                        (symbol,),
                    )
                    conn.commit()

                # Telegram alert
                _send_monitor_alert(pos, result)

            print(f"[Monitor] {symbol}: {result['action']} "
                  f"(risk {result['risk_rating']}/10) "
                  f"{'⚠ ALERTED' if should_alert else ''}")

        except Exception as e:
            print(f"[Monitor] Error for {symbol}: {e}")


def _send_monitor_alert(position: dict, result: dict):
    symbol   = position.get("symbol", "?")
    unrl     = float(position.get("unrealized_pct", 0) or 0)
    action   = result["action"]
    rating   = result["risk_rating"]
    reason   = result["action_reason"]
    summary  = result["summary"]
    emoji    = "🔴" if rating >= 8 else "🟡" if rating >= 6 else "🟢"

    msg = (
        f"{emoji} *Monitor Alert — {symbol}*\n"
        f"Action: `{action}` (Risk {rating}/10)\n"
        f"Reason: {reason}\n\n"
        f"{summary}"
    )
    telegram_notify.send_message(msg)


def start():
    def _loop():
        time.sleep(FIRST_DELAY)
        while True:
            try:
                _run_once()
            except Exception as e:
                print(f"[Monitor] Unexpected error: {e}")
            time.sleep(MONITOR_INTERVAL)

    t = threading.Thread(target=_loop, name="monitor-scheduler", daemon=True)
    t.start()
    print(f"[Monitor] Background monitor started (every {MONITOR_INTERVAL}s, "
          f"first run in {FIRST_DELAY}s)")
```

- [ ] **Add `send_message()` to `telegram_notify.py` if it doesn't exist**

```bash
grep -n "def send_message\|def send" /Users/fbauer/Documents/ClaudeAIData/Trading-Journal/telegram_notify.py | head -10
```

If `send_message(text)` does not exist, add it alongside `send_setup_alert`:

```python
def send_message(text: str):
    """Send a plain text message to the Telegram channel."""
    _send({"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"})
```

Where `_send` is the existing internal helper (check the file for its name).

- [ ] **Start monitor thread in `app.py`** (after existing thread starts)

```python
import monitor_scheduler
# after: scanner_scheduler.start() (or wherever the scanner thread is started)
monitor_scheduler.start()
```

- [ ] **Restart service and verify thread starts**

```bash
python3 -m pytest tests/ -v 2>&1 | tail -15
```

- [ ] **Commit**

```bash
git add database.py constants.py monitor_scheduler.py app.py telegram_notify.py
git commit -m "feat: DB migrations 29-31, monitor_scheduler background thread, Telegram send_message"
```

---

## Task 14: Telegram chart attachment

**Files:**
- Modify: `telegram_notify.py`
- Modify: `monitor_scheduler.py`

When a scanner alert or monitor alert fires, attach the chart PNG if one exists.

- [ ] **Add `send_photo()` to `telegram_notify.py`**

```python
import base64
import io

def send_photo(caption: str, png_b64: str):
    """
    Send a PNG chart to the Telegram channel with a caption.
    Falls back to send_message(caption) if photo send fails.
    """
    if not BOT_TOKEN or not CHAT_ID or not png_b64:
        send_message(caption)
        return
    try:
        img_bytes = base64.b64decode(png_b64)
        boundary  = "----TJBoundary"
        body      = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{CHAT_ID}\r\n'
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="parse_mode"\r\n\r\nMarkdown\r\n'
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption[:1024]}\r\n'
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="photo"; filename="chart.png"\r\n'
            f"Content-Type: image/png\r\n\r\n"
        ).encode() + img_bytes + f"\r\n--{boundary}--\r\n".encode()

        req = urllib.request.Request(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            r.read()
    except Exception as e:
        print(f"[Telegram] send_photo failed: {e} — falling back to text")
        send_message(caption)
```

- [ ] **Update `send_setup_alert()` to attach chart if available**

In the existing `send_setup_alert(setups)` function, after building the message text, check for `chart_png_b64` on the top setup and call `send_photo` if present, else `send_message`:

```python
# At the end of send_setup_alert(), replace the final send_message call with:
top_chart = (setups[0].get("chart_png_b64") or "") if setups else ""
if top_chart:
    send_photo(msg, top_chart)
else:
    send_message(msg)
```

- [ ] **Update `_send_monitor_alert()` in `monitor_scheduler.py` to include chart**

```python
def _send_monitor_alert(position: dict, result: dict):
    # ... (existing message building) ...
    # result doesn't include a chart — monitor uses text-only for now
    # (chart generation for monitor would require storing chart_png_b64
    # in analyzed_calls.chart_png_b64 at call analysis time and retrieving here)
    telegram_notify.send_message(msg)
```

- [ ] **Commit**

```bash
git add telegram_notify.py monitor_scheduler.py
git commit -m "feat: Telegram send_photo — attach annotated chart to scanner alerts"
```

---

## Task 15: Integration test + self_test --agents flag

**Files:**
- Modify: `scripts/self_test.py`

- [ ] **Add --agents flag and pipeline smoke test to `scripts/self_test.py`**

Find the `if __name__ == "__main__":` block and the argparse section. Add:

```python
parser.add_argument("--agents", action="store_true",
                    help="Run agent pipeline smoke test (requires live Pi)")
```

Add this test function alongside the existing tests:

```python
def run_agent_pipeline_tests(host: str):
    """Smoke-test the full agent pipeline against a live host."""
    base = f"http://{host}"
    print("\n=== Agent Pipeline Smoke Tests ===")

    # Test 1: Full call analysis pipeline
    r = POST(f"{base}/api/calls/analyze", {
        "call_text": "BTCUSDT Long breakout above 95000 resistance. Entry 95200, SL 94000, TP 98000.",
        "account_equity": 500.0,
    })
    assert r.get("ok"), f"analyze failed: {r}"
    data = r["data"]
    assert "setup_score" in data, "missing setup_score"
    assert "entry_price" in data or "entry_zone" in data, "missing entry"
    assert "risk_approved" in data or "sizing" in data, "missing risk"
    print("PASS  call analysis pipeline")

    # Test 2: Live trade monitor (if positions exist)
    positions_r = GET(f"{base}/api/live/positions")
    if positions_r.get("ok") and positions_r["data"].get("positions"):
        pos = positions_r["data"]["positions"][0]
        r2 = POST(f"{base}/api/live/analyze", {"symbol": pos["symbol"]})
        assert r2.get("ok"), f"live analyze failed: {r2}"
        d2 = r2["data"]
        assert "action" in d2, "missing action in monitor result"
        assert "risk_rating" in d2, "missing risk_rating"
        print(f"PASS  trade monitor pipeline ({pos['symbol']})")
    else:
        print("SKIP  trade monitor (no open positions)")

    print("=== Agent pipeline: ALL PASSED ===\n")
```

Wire into `main()`:

```python
if args.agents:
    run_agent_pipeline_tests(args.host)
```

- [ ] **Run against Pi**

```bash
python3 scripts/self_test.py --host 192.168.1.21:8082 --agents
```

Expected:
```
=== Agent Pipeline Smoke Tests ===
PASS  call analysis pipeline
PASS  trade monitor pipeline (BTCUSDT)  (or SKIP if no positions)
=== Agent pipeline: ALL PASSED ===
```

- [ ] **Run full test suite**

```bash
python3 -m pytest tests/ -v 2>&1 | tail -20
```

All tests pass.

- [ ] **Deploy to Pi**

```bash
git add scripts/self_test.py
git commit -m "test: add --agents flag to self_test.py for pipeline smoke tests"
git push origin main
```

Then deploy:
```bash
expect -c "
set timeout 90
spawn ssh -o StrictHostKeyChecking=no fbauer@192.168.1.21
expect \"password:\"
send \"laZHn0rd\r\"
expect \"\\\$\"
send \"cd /home/fbauer/trading-journal && git fetch origin && git reset --hard origin/main && pip3 install mplfinance --break-system-packages -q && sudo systemctl restart trading-journal && sleep 5 && sudo systemctl status trading-journal --no-pager | head -10\r\"
expect \"\\\$\"
"
```

---

## Self-Review Checklist

**Spec coverage check:**

| Spec requirement | Task |
|-----------------|------|
| agent_data_collector.py | Task 1 |
| agent_data_interpreter.py | Task 2 |
| agent_market_sentiment.py | Task 3 |
| agent_data_reviewer.py | Task 4 |
| agent_risk_mgmt.py | Task 5 |
| agent_trade_prep.py + chart | Tasks 6+7 |
| agent_trade_monitor.py | Task 8 |
| agent_orchestrator.py additions | Task 9 |
| ai_call.py migration | Task 10 |
| ai_scanner.py migration | Task 11 |
| ai_live_trade.py migration | Task 12 |
| DB migrations 29-30 (+31 chart) | Task 13 |
| constants.py MONITOR_* | Task 13 |
| app.py monitor thread | Task 13 |
| TradeMonitor Telegram alert | Tasks 13+14 |
| Chart in Telegram alerts (new req) | Tasks 6+14 |
| tests per agent | Tasks 2-8 |
| self_test --agents | Task 15 |

All spec requirements covered. Chart drawing added as Task 6 per user requirement.
