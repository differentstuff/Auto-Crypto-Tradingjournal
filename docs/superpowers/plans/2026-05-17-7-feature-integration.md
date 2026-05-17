# 7-Feature Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 7 independent capabilities — liquidation cluster signal, order flow delta, on-chain metrics, HMM regime detection, ML signal scoring, improved AI prompts, and backtesting quality — without breaking the existing 401 passing tests.

**Architecture:** Each feature is a single-responsibility module. New data sources follow the existing `data_sources.py` fetch-function pattern (return empty dict on error, never raise). New confluence signals follow the `_*_weight() -> float` pattern in `chart_confluence.py`. Prompt context slots into `prompt_builder.py`'s priority queue at a defined char budget. All phases are independently committable.

**Tech Stack:** `hmmlearn>=0.3.0`, `scikit-learn>=1.5.0`, `xgboost>=2.0.0`, `joblib>=1.3.0`, `backtester-mcp>=0.1.0` — all pip-installable, ARM64-compatible.

---

## File Map

### New Files
| File | Purpose |
|------|---------|
| `liquidation_levels.py` | Fetch + cluster forced liquidation orders via CCXT |
| `order_flow.py` | (unused — logic lives in chart_indicators.py) |
| `onchain_client.py` | CoinMetrics Community API — MVRV, SOPR, exchange flows |
| `market_regime.py` | GaussianHMM 3-state regime classifier |
| `signal_scorer.py` | XGBoost win-probability from 11 existing signals |
| `backtest_quality.py` | PBO, deflated Sharpe, bootstrap CI |
| `tests/test_liquidation_levels.py` | Unit tests |
| `tests/test_order_flow.py` | Unit tests |
| `tests/test_onchain_client.py` | Unit tests |
| `tests/test_market_regime.py` | Unit tests |
| `tests/test_signal_scorer.py` | Unit tests |
| `tests/test_backtest_quality.py` | Unit tests |

### Modified Files
| File | Change |
|------|--------|
| `chart_confluence.py` | + `_order_flow_weight()`, + `_liquidation_weight()`, update `max_per_tf`/`max_val` |
| `chart_indicators.py` | + `compute_order_flow_delta()` wired into `compute_all_indicators()` |
| `data_sources.py` | + `fetch_liquidation_levels()`, + `fetch_onchain_metrics()` |
| `prompt_builder.py` | + regime block, + ML score block, + on-chain block |
| `agent_data_interpreter.py` | Structured TradingAgents-style 6-section prompt |
| `agent_risk_mgmt.py` | Explicit decision rubric with VERDICT/SIZE/REASON/MAX_LOSS/BEST_CASE format |
| `routes/backtest.py` | + `POST /api/backtest/quality` endpoint |
| `requirements.txt` | New deps |
| `constants.py` | New TTL and threshold constants |
| `database.py` | Migration 38: `regime_label TEXT`, migration 39: `ml_win_prob REAL` |

---

## Phase A — Liquidation Cluster Signal (11th confluence signal)

Fetch recent forced liquidation orders from Binance USDM public API via CCXT, bin by price level, identify the heaviest short-wall (short-squeeze fuel for longs) and long-wall (cascade fuel for shorts). Add as a symbol-level weight in `confluence_score()`.

### Task A1: `liquidation_levels.py`

**Files:**
- Create: `liquidation_levels.py`
- Test: `tests/test_liquidation_levels.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_liquidation_levels.py
import pytest
from unittest.mock import patch


def _fake_liquidations(symbol, limit=500):
    return [
        {"price": 100.0, "side": "sell", "amount": 50000},
        {"price": 100.1, "side": "sell", "amount": 20000},
        {"price": 98.0,  "side": "buy",  "amount": 80000},
        {"price": 97.9,  "side": "buy",  "amount": 30000},
    ]


def test_clusters_ok():
    with patch("liquidation_levels.ccxt.binanceusdm") as mock_ex:
        mock_ex.return_value.fetch_liquidations = _fake_liquidations
        import liquidation_levels
        result = liquidation_levels._fetch("BTCUSDT")
    assert result["ok"] is True
    assert result["short_wall"] is not None
    assert result["long_wall"] is not None


def test_clusters_empty_returns_not_ok():
    with patch("liquidation_levels.ccxt.binanceusdm") as mock_ex:
        mock_ex.return_value.fetch_liquidations = lambda *a, **k: []
        import liquidation_levels
        result = liquidation_levels._fetch("BTCUSDT")
    assert result["ok"] is False


def test_clusters_exception_returns_not_ok():
    with patch("liquidation_levels.ccxt.binanceusdm") as mock_ex:
        mock_ex.return_value.fetch_liquidations = lambda *a, **k: 1/0
        import liquidation_levels
        result = liquidation_levels._fetch("BTCUSDT")
    assert result["ok"] is False
```

- [ ] **Step 2: Run to confirm FAIL**

```
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
python3 -m pytest tests/test_liquidation_levels.py -v
```

Expected: `ModuleNotFoundError: No module named 'liquidation_levels'`

- [ ] **Step 3: Implement `liquidation_levels.py`**

```python
# liquidation_levels.py
"""Forced-liquidation cluster detection from Binance USDM via CCXT."""
import logging
import time
import ccxt

_log  = logging.getLogger(__name__)
_CACHE: dict[str, tuple[float, dict]] = {}
_TTL  = 900  # 15 min


def _fetch(symbol: str) -> dict:
    try:
        ex  = ccxt.binanceusdm({"options": {"defaultType": "future"}})
        liq = ex.fetch_liquidations(symbol, limit=500)
        if not liq:
            return {"ok": False, "reason": "empty"}
        prices  = [float(x["price"])            for x in liq if x.get("price")]
        sides   = [x.get("side", "")            for x in liq]
        volumes = [float(x.get("amount") or 0)  for x in liq]
        if not prices:
            return {"ok": False, "reason": "no prices"}
        lo, hi = min(prices), max(prices)
        if lo == hi:
            return {"ok": False, "reason": "no range"}
        N  = 20
        bw = (hi - lo) / N
        bins: dict[int, dict] = {}
        for i, p in enumerate(prices):
            b = min(int((p - lo) / bw), N - 1)
            if b not in bins:
                bins[b] = {"price": lo + (b + 0.5) * bw,
                           "long_vol": 0.0, "short_vol": 0.0}
            v = volumes[i] if i < len(volumes) else 0
            if sides[i] == "buy":
                bins[b]["long_vol"] += v
            else:
                bins[b]["short_vol"] += v
        clusters   = sorted(bins.values(),
                             key=lambda x: x["long_vol"] + x["short_vol"],
                             reverse=True)
        long_wall  = max(bins.values(), key=lambda x: x["long_vol"])["price"]
        short_wall = max(bins.values(), key=lambda x: x["short_vol"])["price"]
        return {"ok": True, "long_wall": long_wall,
                "short_wall": short_wall, "clusters": clusters[:5],
                "total": len(prices)}
    except Exception as exc:
        _log.warning("liquidation_levels %s: %s", symbol, exc)
        return {"ok": False, "reason": str(exc)}


def get_liquidation_clusters(symbol: str) -> dict:
    """Return liquidation cluster data for symbol, TTL-cached."""
    now = time.time()
    if symbol in _CACHE:
        ts, data = _CACHE[symbol]
        if now - ts < _TTL:
            return data
    result      = _fetch(symbol)
    _CACHE[symbol] = (now, result)
    return result
```

- [ ] **Step 4: Run tests — expect PASS**

```
python3 -m pytest tests/test_liquidation_levels.py -v
```

- [ ] **Step 5: Commit**

```bash
git add liquidation_levels.py tests/test_liquidation_levels.py
git commit -m "feat: liquidation_levels.py — CCXT cluster detection, TTL-cached"
```

---

### Task A2: `_liquidation_weight()` in `chart_confluence.py`

**Files:**
- Modify: `chart_confluence.py`
- Test: `tests/test_chart_context_scoring.py`

- [ ] **Step 1: Write failing tests** (append to existing `test_chart_context_scoring.py`)

```python
def test_liquidation_weight_long_near_short_wall():
    from chart_confluence import _liquidation_weight
    liq = {"ok": True, "short_wall": 101.0, "long_wall": 90.0}
    assert _liquidation_weight(liq, 100.0) == pytest.approx(0.20)

def test_liquidation_weight_bearish_near_long_wall():
    from chart_confluence import _liquidation_weight
    liq = {"ok": True, "short_wall": 115.0, "long_wall": 99.0}
    assert _liquidation_weight(liq, 100.0) == pytest.approx(-0.20)

def test_liquidation_weight_far_is_zero():
    from chart_confluence import _liquidation_weight
    liq = {"ok": True, "short_wall": 120.0, "long_wall": 80.0}
    assert _liquidation_weight(liq, 100.0) == pytest.approx(0.0)

def test_liquidation_weight_not_ok_is_zero():
    from chart_confluence import _liquidation_weight
    assert _liquidation_weight({"ok": False}, 100.0) == pytest.approx(0.0)
```

- [ ] **Step 2: Run — expect FAIL**

```
python3 -m pytest tests/test_chart_context_scoring.py -k "liquidation" -v
```

- [ ] **Step 3: Add `_liquidation_weight()` to `chart_confluence.py`**

After the `_mfi_weight()` function (around line 238), insert:

```python
def _liquidation_weight(liq: dict, current_price: float) -> float:
    """
    +0.20: short-liq wall within 3% above current price (short-squeeze fuel, bullish).
    -0.20: long-liq wall within 3% below current price (cascade fuel, bearish).
    0.00 otherwise.
    """
    if not liq or not liq.get("ok"):
        return 0.0
    weight = 0.0
    try:
        p = float(current_price)
        if liq.get("short_wall"):
            dist = (float(liq["short_wall"]) - p) / p
            if 0 < dist <= 0.03:
                weight += 0.20
        if liq.get("long_wall"):
            dist = (p - float(liq["long_wall"])) / p
            if 0 < dist <= 0.03:
                weight -= 0.20
    except Exception:
        pass
    return weight
```

- [ ] **Step 4: Wire `_liquidation_weight()` into `confluence_score()`**

In `confluence_score()`, replace the `# Apply macro regime multiplier` comment block with:

```python
    # Symbol-level signals (not per-TF)
    liq_w = 0.0
    try:
        from liquidation_levels import get_liquidation_clusters
        current_price = None
        for tf in tfs:
            df_tf = ctx.get(tf, {}).get("df")
            if df_tf is not None and len(df_tf):
                current_price = float(df_tf["close"].iloc[-1])
                break
        if current_price:
            liq   = get_liquidation_clusters(symbol)
            liq_w = _liquidation_weight(liq, current_price)
            total_score += liq_w
    except Exception:
        pass

    # Apply macro regime multiplier (VIX-based, cached 5 min)
    vix_mult = _get_vix_multiplier()
    if vix_mult != 1.0:
        total_score = round(total_score * vix_mult, 2)

    smt_bonus  = 0.30 if symbol in SMT_SYMBOLS else 0.0
    max_per_tf = 5.4 + smt_bonus          # per-TF max (unchanged)
    max_val    = float(len(tfs) * max_per_tf) + 0.20  # +0.20 symbol-level liq
```

- [ ] **Step 5: Run — expect PASS**

```
python3 -m pytest tests/test_chart_context_scoring.py -v
python3 -m pytest tests/ -q --tb=no 2>&1 | tail -3
```

- [ ] **Step 6: Commit**

```bash
git add chart_confluence.py tests/test_chart_context_scoring.py
git commit -m "feat: _liquidation_weight() — 11th confluence signal, symbol-level liq wall proximity"
```

---

## Phase B — Order Flow Delta (12th confluence signal)

Compute a tick-rule proxy for per-candle buy vs sell aggressor volume from OHLCV. Add as a per-TF weight.

### Task B1: `compute_order_flow_delta()` in `chart_indicators.py`

**Files:**
- Modify: `chart_indicators.py`
- Test: `tests/test_order_flow.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_order_flow.py
import pytest
import pandas as pd
import numpy as np


def _make_df(closes, opens=None):
    n  = len(closes)
    o  = opens if opens is not None else [c * 0.999 for c in closes]
    return pd.DataFrame({
        "open":   o,
        "high":   [c * 1.001 for c in closes],
        "low":    [c * 0.999 for c in closes],
        "close":  closes,
        "volume": [100.0] * n,
    })


def test_order_flow_bullish():
    df = _make_df([100, 101, 102, 103, 104, 105],
                  opens=[99, 100, 101, 102, 103, 104])
    from chart_indicators import compute_order_flow_delta
    result = compute_order_flow_delta(df)
    assert result is not None
    assert result["signal"] == "buying_pressure"
    assert result["delta"] > 0


def test_order_flow_bearish():
    df = _make_df([104, 103, 102, 101, 100, 99],
                  opens=[105, 104, 103, 102, 101, 100])
    from chart_indicators import compute_order_flow_delta
    result = compute_order_flow_delta(df)
    assert result is not None
    assert result["signal"] == "selling_pressure"
    assert result["delta"] < 0


def test_order_flow_divergence_key_present():
    df = _make_df([100, 101, 102, 103, 104, 106],
                  opens=[99, 100, 101, 102, 103, 107])
    from chart_indicators import compute_order_flow_delta
    result = compute_order_flow_delta(df)
    assert result is not None
    assert "divergence" in result


def test_order_flow_none_on_short_df():
    df = _make_df([100, 101])
    from chart_indicators import compute_order_flow_delta
    assert compute_order_flow_delta(df) is None
```

- [ ] **Step 2: Run — expect FAIL**

```
python3 -m pytest tests/test_order_flow.py -v
```

- [ ] **Step 3: Append `compute_order_flow_delta()` to `chart_indicators.py`**

```python
def compute_order_flow_delta(df: pd.DataFrame) -> dict | None:
    """
    Tick-rule proxy for per-candle aggressor delta.
    Positive delta = net buying pressure; negative = net selling pressure.
    Returns: {delta, cumulative_delta, signal, divergence}
    """
    if df is None or len(df) < 3:
        return None
    try:
        body      = df["close"] - df["open"]
        body_abs  = body.abs()
        ratio     = (body_abs / (body_abs + 1e-9)).clip(0.10, 0.90)
        buy_vol   = df["volume"] * ratio.where(body >= 0, 1 - ratio)
        sell_vol  = df["volume"] - buy_vol
        delta_bar = buy_vol - sell_vol

        delta     = float(delta_bar.iloc[-1])
        cum_delta = float(delta_bar.sum())

        price_high    = df["close"].iloc[-1] > df["close"].iloc[-5:-1].max()
        prior_avg     = float(delta_bar.iloc[-5:-1].mean()) if len(delta_bar) >= 5 else 0.0
        divergence    = bool(price_high and delta < prior_avg)

        signal = ("buying_pressure"  if delta > 0 else
                  "selling_pressure" if delta < 0 else "neutral")

        return {"delta": delta, "cumulative_delta": cum_delta,
                "signal": signal, "divergence": divergence}
    except Exception:
        return None
```

Also wire into `compute_all_indicators()` — find the return dict assembly and add:

```python
    result["order_flow"] = compute_order_flow_delta(df)
```

- [ ] **Step 4: Run tests — expect PASS**

```
python3 -m pytest tests/test_order_flow.py -v
```

- [ ] **Step 5: Commit**

```bash
git add chart_indicators.py tests/test_order_flow.py
git commit -m "feat: compute_order_flow_delta() — tick-rule buy/sell pressure with divergence detection"
```

---

### Task B2: `_order_flow_weight()` in `chart_confluence.py`

**Files:**
- Modify: `chart_confluence.py`
- Test: `tests/test_chart_context_scoring.py`

- [ ] **Step 1: Write failing tests** (append to `test_chart_context_scoring.py`)

```python
def test_order_flow_weight_buying():
    from chart_confluence import _order_flow_weight
    assert _order_flow_weight({"signal": "buying_pressure", "divergence": False}) == pytest.approx(0.15)

def test_order_flow_weight_selling():
    from chart_confluence import _order_flow_weight
    assert _order_flow_weight({"signal": "selling_pressure", "divergence": False}) == pytest.approx(-0.15)

def test_order_flow_weight_divergence_is_negative():
    from chart_confluence import _order_flow_weight
    assert _order_flow_weight({"signal": "buying_pressure", "divergence": True}) == pytest.approx(-0.15)

def test_order_flow_weight_none_is_zero():
    from chart_confluence import _order_flow_weight
    assert _order_flow_weight(None) == pytest.approx(0.0)
```

- [ ] **Step 2: Run — expect FAIL**

```
python3 -m pytest tests/test_chart_context_scoring.py -k "order_flow" -v
```

- [ ] **Step 3: Add `_order_flow_weight()` and wire into `_get_tf_weights()` and `confluence_score()`**

Add after `_liquidation_weight()`:

```python
def _order_flow_weight(of: dict | None) -> float:
    """
    +0.15 buying pressure (positive delta, no divergence).
    -0.15 selling pressure OR divergence (bearish fade).
    """
    if not of:
        return 0.0
    if of.get("divergence"):
        return -0.15
    sig = of.get("signal", "neutral")
    if sig == "buying_pressure":
        return 0.15
    if sig == "selling_pressure":
        return -0.15
    return 0.0
```

In `_get_tf_weights()`, add `of_w` and include it in the return list:

```python
    of_w      = _order_flow_weight(inds.get("order_flow"))
    # ... existing caps ...
    return [_momentum, ema_w, adx_w, _oscillator, cvd_w, smt_w, smt_dir_w, of_w]
```

In the `confluence_score()` TF loop, add `of_w` after `cvd_w`:

```python
        of_w      = _order_flow_weight(inds.get("order_flow"))
```

And include it in `base_score`:

```python
        base_score = _momentum + ema_w + adx_w + _oscillator + cvd_w + smt_w + smt_dir_w + of_w
```

Update `max_per_tf` in `confluence_score()`:

```python
    smt_bonus  = 0.30 if symbol in SMT_SYMBOLS else 0.0
    max_per_tf = 5.55 + smt_bonus    # +0.15 order flow vs previous 5.40
    max_val    = float(len(tfs) * max_per_tf) + 0.20   # +0.20 liq symbol-level
```

- [ ] **Step 4: Run full suite — expect PASS**

```
python3 -m pytest tests/ -q --tb=no 2>&1 | tail -3
```

- [ ] **Step 5: Commit**

```bash
git add chart_confluence.py tests/test_chart_context_scoring.py
git commit -m "feat: _order_flow_weight() — 12th confluence signal, per-TF tick-rule delta"
```

---

## Phase C — On-Chain Metrics

Fetch MVRV, SOPR, and exchange net-flow for BTC from the CoinMetrics Community API (keyless, same data source as `checkonchain`). Inject as a macro context block.

### Task C1: `onchain_client.py`

**Files:**
- Create: `onchain_client.py`
- Test: `tests/test_onchain_client.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_onchain_client.py
import pytest
from unittest.mock import patch, MagicMock


_FAKE = {
    "data": [{
        "time": "2026-05-17",
        "mvrv_cur": "2.3",
        "sopr": "1.02",
        "FlowInExUSD": "120000000",
        "FlowOutExUSD": "150000000",
    }]
}


def test_get_btc_onchain_ok():
    with patch("onchain_client.requests.get") as mock_get:
        mock_get.return_value.ok = True
        mock_get.return_value.json.return_value = _FAKE
        import onchain_client
        result = onchain_client._fetch()
    assert result["ok"] is True
    assert result["mvrv"] == pytest.approx(2.3)
    assert result["sopr"] == pytest.approx(1.02)


def test_get_btc_onchain_regime_overvalued():
    with patch("onchain_client.requests.get") as mock_get:
        mock_get.return_value.ok = True
        mock_get.return_value.json.return_value = {"data": [{
            "time": "2026-05-17", "mvrv_cur": "4.1", "sopr": "1.06",
            "FlowInExUSD": "0", "FlowOutExUSD": "0",
        }]}
        import onchain_client
        result = onchain_client._fetch()
    assert result["regime"] == "overvalued"


def test_get_btc_onchain_regime_undervalued():
    with patch("onchain_client.requests.get") as mock_get:
        mock_get.return_value.ok = True
        mock_get.return_value.json.return_value = {"data": [{
            "time": "2026-05-17", "mvrv_cur": "0.8", "sopr": "0.96",
            "FlowInExUSD": "0", "FlowOutExUSD": "0",
        }]}
        import onchain_client
        result = onchain_client._fetch()
    assert result["regime"] == "undervalued"


def test_get_btc_onchain_error_not_ok():
    with patch("onchain_client.requests.get", side_effect=Exception("timeout")):
        import onchain_client
        result = onchain_client._fetch()
    assert result["ok"] is False
```

- [ ] **Step 2: Run — expect FAIL**

```
python3 -m pytest tests/test_onchain_client.py -v
```

- [ ] **Step 3: Implement `onchain_client.py`**

```python
# onchain_client.py
"""
BTC on-chain metrics via CoinMetrics Community API (keyless).
Same data source as checkonchain (github.com/Tsunekazu/checkonchain).
Metrics: MVRV (mvrv_cur), SOPR (sopr), exchange in/out flows.
"""
import logging
import time
import requests

_log   = logging.getLogger(__name__)
_URL   = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
_TTL   = 3600   # 1 h — data is daily
_CACHE: dict[str, tuple[float, dict]] = {}


def _fetch() -> dict:
    params = {
        "assets":          "btc",
        "metrics":         "mvrv_cur,sopr,FlowInExUSD,FlowOutExUSD",
        "frequency":       "1d",
        "page_size":       1,
        "sort":            "time",
    }
    try:
        resp = requests.get(_URL, params=params, timeout=10)
        if not resp.ok:
            return {"ok": False, "reason": f"HTTP {resp.status_code}"}
        rows = resp.json().get("data", [])
        if not rows:
            return {"ok": False, "reason": "empty response"}
        row     = rows[-1]
        mvrv    = float(row.get("mvrv_cur")    or 0)
        sopr    = float(row.get("sopr")        or 1)
        inflow  = float(row.get("FlowInExUSD") or 0)
        outflow = float(row.get("FlowOutExUSD") or 0)
        net_flow = outflow - inflow   # positive = net outflow = accumulation
        if mvrv > 3.5 or sopr > 1.04:
            regime = "overvalued"
        elif mvrv < 1.0 or sopr < 0.98:
            regime = "undervalued"
        else:
            regime = "fair_value"
        return {
            "ok":                    True,
            "mvrv":                  round(mvrv, 3),
            "sopr":                  round(sopr, 4),
            "exchange_net_flow_usd": round(net_flow, 0),
            "regime":                regime,
            "date":                  row.get("time", ""),
        }
    except Exception as exc:
        _log.warning("onchain_client: %s", exc)
        return {"ok": False, "reason": str(exc)}


def get_btc_onchain() -> dict:
    """Return BTC on-chain metrics, TTL-cached."""
    now = time.time()
    if "btc" in _CACHE:
        ts, data = _CACHE["btc"]
        if now - ts < _TTL:
            return data
    result        = _fetch()
    _CACHE["btc"] = (now, result)
    return result
```

- [ ] **Step 4: Run tests — expect PASS**

```
python3 -m pytest tests/test_onchain_client.py -v
```

- [ ] **Step 5: Add `fetch_onchain_metrics()` to `data_sources.py`**

Append at the end of `data_sources.py`:

```python
def fetch_onchain_metrics() -> dict:
    """BTC on-chain: MVRV, SOPR, exchange net-flow (CoinMetrics Community, keyless)."""
    try:
        from onchain_client import get_btc_onchain
        return get_btc_onchain()
    except Exception:
        return {}
```

- [ ] **Step 6: Inject on-chain context into `prompt_builder.py`**

In `build_context()`, after the macro regime block (~line 200), add:

```python
    # On-chain metrics (BTC macro context for all symbols)
    if remaining > 80:
        try:
            onchain = fetch_onchain_metrics() if "fetch_onchain_metrics" in dir() else {}
            if not onchain:
                from onchain_client import get_btc_onchain
                onchain = get_btc_onchain()
            if onchain and onchain.get("ok"):
                nf_m     = onchain["exchange_net_flow_usd"] / 1_000_000
                flow_dir = "outflow" if onchain["exchange_net_flow_usd"] > 0 else "inflow"
                block    = (f"On-chain BTC: MVRV {onchain['mvrv']} | "
                            f"SOPR {onchain['sopr']} | {onchain['regime']} | "
                            f"exchange {flow_dir} ${abs(nf_m):.0f}M")
                sections.append(block)
                remaining -= len(block)
        except Exception:
            pass
```

- [ ] **Step 7: Run full suite**

```
python3 -m pytest tests/ -q --tb=no 2>&1 | tail -3
```

- [ ] **Step 8: Commit**

```bash
git add onchain_client.py tests/test_onchain_client.py data_sources.py prompt_builder.py
git commit -m "feat: on-chain metrics (MVRV/SOPR/flows) via CoinMetrics community — checkonchain method"
```

---

## Phase D — HMM Regime Detection

Train a 3-state `GaussianHMM` on 90 days of BTC 4H data. Label current state `trending_up` / `ranging` / `trending_down`. Cache model 4 h. Inject into every prompt.

### Task D1: `market_regime.py`

**Files:**
- Create: `market_regime.py`
- Test: `tests/test_market_regime.py`
- Modify: `requirements.txt`, `database.py`, `prompt_builder.py`

- [ ] **Step 1: Add dependency**

In `requirements.txt`, append:
```
hmmlearn>=0.3.0
joblib>=1.3.0
```

Install:
```
pip3 install "hmmlearn>=0.3.0" "joblib>=1.3.0" --break-system-packages
```

- [ ] **Step 2: Write failing tests**

```python
# tests/test_market_regime.py
import pytest
import numpy as np
import pandas as pd
from unittest.mock import patch


def _fake_ohlcv(n=200):
    np.random.seed(42)
    closes = 100 * np.cumprod(1 + np.random.normal(0.001, 0.02, n))
    return pd.DataFrame({
        "ts": range(n), "open": closes * 0.999, "high": closes * 1.002,
        "low": closes * 0.998, "close": closes,
        "volume": np.random.uniform(1000, 5000, n),
    })


def test_detect_regime_valid_label():
    with patch("market_regime._fetch_ohlcv", return_value=_fake_ohlcv(200)):
        import market_regime
        result = market_regime._fit_and_predict()
    assert result["ok"] is True
    assert result["label"] in ("trending_up", "ranging", "trending_down")
    assert 0.0 <= result["confidence"] <= 1.0


def test_detect_regime_too_short():
    with patch("market_regime._fetch_ohlcv", return_value=_fake_ohlcv(10)):
        import market_regime
        result = market_regime._fit_and_predict()
    assert result["ok"] is False


def test_detect_regime_exception():
    with patch("market_regime._fetch_ohlcv", side_effect=Exception("API down")):
        import market_regime
        result = market_regime._fit_and_predict()
    assert result["ok"] is False
```

- [ ] **Step 3: Run — expect FAIL**

```
python3 -m pytest tests/test_market_regime.py -v
```

- [ ] **Step 4: Implement `market_regime.py`**

```python
# market_regime.py
"""
3-state GaussianHMM regime classifier for BTC.
States labeled by mean log-return: trending_up / ranging / trending_down.
Retrains every 4 h; model saved via joblib for inspection.
"""
import logging
import os
import time
import numpy as np

_log        = logging.getLogger(__name__)
_TTL        = 14400   # 4 h
_CACHE: dict[str, tuple[float, dict]] = {}
_MODEL_PATH = os.path.join(os.path.dirname(__file__), ".hmm_regime_model.joblib")


def _fetch_ohlcv(limit: int = 540):
    import ccxt
    import pandas as pd
    ex  = ccxt.binance({"options": {"defaultType": "future"}})
    raw = ex.fetch_ohlcv("BTCUSDT", "4h", limit=limit)
    df  = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    df["close"] = df["close"].astype(float)
    return df


def _build_features(df) -> np.ndarray:
    log_ret  = np.log(df["close"] / df["close"].shift(1)).fillna(0).values
    rvol     = (df["close"].pct_change()
                .rolling(5, min_periods=2).std()
                .bfill().values)
    vol_norm = (df["volume"] /
                df["volume"].rolling(20, min_periods=5).mean()
                ).fillna(1.0).values
    X = np.column_stack([log_ret, rvol, vol_norm])
    return X[~np.isnan(X).any(axis=1)]


def _assign_labels(model, X: np.ndarray) -> dict[int, str]:
    states = model.predict(X)
    means  = {s: float(X[states == s, 0].mean()) if (states == s).any() else 0.0
              for s in range(model.n_components)}
    ordered = sorted(means, key=means.get)
    return {ordered[0]: "trending_down",
            ordered[1]: "ranging",
            ordered[2]: "trending_up"}


def _fit_and_predict() -> dict:
    try:
        from hmmlearn.hmm import GaussianHMM
    except ImportError:
        return {"ok": False, "reason": "hmmlearn not installed — pip install hmmlearn"}
    try:
        df = _fetch_ohlcv(540)
        if len(df) < 50:
            return {"ok": False, "reason": f"only {len(df)} bars available"}
        X = _build_features(df)
        if len(X) < 30:
            return {"ok": False, "reason": "insufficient clean features"}

        model = GaussianHMM(n_components=3, covariance_type="diag",
                            n_iter=100, random_state=42)
        model.fit(X)

        label_map     = _assign_labels(model, X)
        current_state = int(model.predict(X)[-1])
        label         = label_map.get(current_state, "ranging")

        _, posteriors = model.score_samples(X)
        confidence    = float(posteriors[-1, current_state])

        try:
            import joblib
            joblib.dump({"model": model, "label_map": label_map}, _MODEL_PATH)
        except Exception:
            pass

        return {"ok": True, "label": label, "state_idx": current_state,
                "confidence": round(confidence, 3), "label_map": label_map}
    except Exception as exc:
        _log.warning("market_regime: %s", exc)
        return {"ok": False, "reason": str(exc)}


def detect_regime() -> dict:
    """Return current BTC market regime, TTL-cached."""
    now = time.time()
    if "btc" in _CACHE:
        ts, data = _CACHE["btc"]
        if now - ts < _TTL:
            return data
    result        = _fit_and_predict()
    _CACHE["btc"] = (now, result)
    return result
```

- [ ] **Step 5: Run tests — expect PASS**

```
python3 -m pytest tests/test_market_regime.py -v
```

- [ ] **Step 6: DB migration 38 in `database.py`**

After the last `_apply(37, ...)` line, add:

```python
    _apply(38, "analyzed_calls.regime_label",
           "ALTER TABLE analyzed_calls ADD COLUMN regime_label TEXT DEFAULT NULL")
```

- [ ] **Step 7: Inject regime into `prompt_builder.py`**

After the macro regime block, add:

```python
    # HMM market regime
    if remaining > 60:
        try:
            from market_regime import detect_regime
            reg = detect_regime()
            if reg.get("ok"):
                conf  = reg.get("confidence", 0)
                block = f"Market regime (HMM/BTC): {reg['label']} — confidence {conf:.0%}"
                sections.append(block)
                remaining -= len(block)
        except Exception:
            pass
```

- [ ] **Step 8: Run full suite**

```
python3 -m pytest tests/ -q --tb=no 2>&1 | tail -3
```

- [ ] **Step 9: Commit**

```bash
git add market_regime.py tests/test_market_regime.py database.py requirements.txt prompt_builder.py
git commit -m "feat: HMM regime detection — 3-state GaussianHMM on BTC 4H, injected into every prompt"
```

---

## Phase E — ML Signal Scoring

Train XGBoost on historical `analyzed_calls` outcomes using 10 features from the existing confluence pipeline. Predict win probability and inject into prompts. Requires ≥ 20 labeled outcomes; silently skips otherwise.

### Task E1: `signal_scorer.py`

**Files:**
- Create: `signal_scorer.py`
- Test: `tests/test_signal_scorer.py`
- Modify: `requirements.txt`, `database.py`, `prompt_builder.py`

- [ ] **Step 1: Add dependencies to `requirements.txt`**

```
scikit-learn>=1.5.0
xgboost>=2.0.0
```

Install:
```
pip3 install "scikit-learn>=1.5.0" "xgboost>=2.0.0" --break-system-packages
```

- [ ] **Step 2: Add DB migration 39 in `database.py`**

After migration 38:

```python
    _apply(39, "analyzed_calls.ml_win_prob",
           "ALTER TABLE analyzed_calls ADD COLUMN ml_win_prob REAL DEFAULT NULL")
```

- [ ] **Step 3: Write failing tests**

```python
# tests/test_signal_scorer.py
import pytest
import sqlite3
import json
import random
import numpy as np


def _make_db(n=25):
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE analyzed_calls (
            id INTEGER PRIMARY KEY, symbol TEXT, direction TEXT,
            setup_score INTEGER, outcome TEXT, analysis_json TEXT
        )
    """)
    random.seed(0)
    for i in range(n):
        score   = random.randint(4, 9)
        outcome = "won" if random.random() > 0.45 else "lost"
        analysis = json.dumps({
            "rsi": random.uniform(30, 70),
            "macd_histogram": random.uniform(-1, 1),
            "ema_alignment":  random.choice([1, 0, -1]),
            "adx":            random.uniform(10, 40),
            "wt_signal":      random.choice([1, 0, -1]),
            "mfi":            random.uniform(20, 80),
            "cvd_trend":      random.choice([1, 0, -1]),
            "volume_ratio":   random.uniform(0.5, 2.0),
        })
        conn.execute("INSERT INTO analyzed_calls VALUES (?,?,?,?,?,?)",
                     (i, "BTCUSDT", "long", score, outcome, analysis))
    conn.commit()
    return conn


def test_train_ok():
    conn = _make_db(25)
    from signal_scorer import SignalScorer
    s = SignalScorer()
    assert s.train(conn) is True
    assert s.is_trained


def test_predict_probability_range():
    conn = _make_db(25)
    from signal_scorer import SignalScorer
    s = SignalScorer()
    s.train(conn)
    features = {"setup_score": 7, "rsi": 45.0, "macd_histogram": 0.5,
                "ema_alignment": 1, "adx": 25.0, "wt_signal": 1,
                "mfi": 55.0, "cvd_trend": 1, "volume_ratio": 1.2, "direction": "long"}
    prob = s.predict(features)
    assert prob is not None
    assert 0.0 <= prob <= 1.0


def test_train_fails_below_minimum():
    conn = _make_db(10)
    from signal_scorer import SignalScorer
    s = SignalScorer()
    assert s.train(conn) is False
    assert not s.is_trained


def test_predict_without_training_is_none():
    from signal_scorer import SignalScorer
    assert SignalScorer().predict({}) is None
```

- [ ] **Step 4: Run — expect FAIL**

```
python3 -m pytest tests/test_signal_scorer.py -v
```

- [ ] **Step 5: Implement `signal_scorer.py`**

```python
# signal_scorer.py
"""
XGBoost win-probability scorer trained on historical analyzed_calls.
Min 20 labeled outcomes required. Silently returns None if untrained.
Model persisted via joblib.
"""
import json
import logging
import os
import time
import numpy as np

_log          = logging.getLogger(__name__)
_MIN_SAMPLES  = 20
_RETRAIN_TTL  = 86400   # 24 h
_MODEL_PATH   = os.path.join(os.path.dirname(__file__), ".ml_scorer.joblib")


def _extract_features(row: dict) -> list[float] | None:
    try:
        analysis = row.get("analysis_json") or {}
        if isinstance(analysis, str):
            analysis = json.loads(analysis)
        direction = 1.0 if row.get("direction", "long") == "long" else -1.0
        return [
            float(row.get("setup_score")          or 5),
            float(analysis.get("rsi")             or 50),
            float(analysis.get("macd_histogram")  or 0),
            float(analysis.get("ema_alignment")   or 0),
            float(analysis.get("adx")             or 20),
            float(analysis.get("wt_signal")       or 0),
            float(analysis.get("mfi")             or 50),
            float(analysis.get("cvd_trend")       or 0),
            float(analysis.get("volume_ratio")    or 1),
            direction,
        ]
    except Exception:
        return None


class SignalScorer:
    def __init__(self):
        self._model      = None
        self._trained_at = 0.0

    @property
    def is_trained(self) -> bool:
        return self._model is not None

    def train(self, conn) -> bool:
        try:
            from xgboost import XGBClassifier
        except ImportError:
            _log.warning("signal_scorer: xgboost not installed")
            return False
        try:
            rows = conn.execute(
                "SELECT setup_score, direction, outcome, analysis_json "
                "FROM analyzed_calls WHERE outcome IN ('won','lost') "
                "ORDER BY id DESC LIMIT 500"
            ).fetchall()
            if len(rows) < _MIN_SAMPLES:
                return False
            X, y = [], []
            for r in rows:
                row   = dict(zip(["setup_score", "direction", "outcome", "analysis_json"], r))
                feats = _extract_features(row)
                if feats is None:
                    continue
                X.append(feats)
                y.append(1 if row["outcome"] == "won" else 0)
            if len(X) < _MIN_SAMPLES:
                return False
            model = XGBClassifier(
                n_estimators=50, max_depth=3, learning_rate=0.1,
                eval_metric="logloss", random_state=42, verbosity=0,
            )
            model.fit(np.array(X), np.array(y))
            self._model      = model
            self._trained_at = time.time()
            try:
                import joblib
                joblib.dump(model, _MODEL_PATH)
            except Exception:
                pass
            return True
        except Exception as exc:
            _log.warning("signal_scorer.train: %s", exc)
            return False

    def predict(self, features: dict) -> float | None:
        if not self.is_trained:
            return None
        try:
            vec = _extract_features(features)
            if vec is None:
                return None
            return round(float(self._model.predict_proba([vec])[0][1]), 3)
        except Exception:
            return None


_global_scorer = SignalScorer()


def get_scorer(conn=None) -> SignalScorer:
    """Return module-level scorer; retrains every 24 h when conn is provided."""
    global _global_scorer
    needs = (not _global_scorer.is_trained or
             (conn is not None and
              time.time() - _global_scorer._trained_at > _RETRAIN_TTL))
    if needs and conn is not None:
        _global_scorer.train(conn)
    return _global_scorer
```

- [ ] **Step 6: Run tests — expect PASS**

```
python3 -m pytest tests/test_signal_scorer.py -v
```

- [ ] **Step 7: Inject ML win-probability into `prompt_builder.py`**

After the backtest context injection (~line 145), add:

```python
    # ML win probability (XGBoost from historical outcomes)
    if remaining > 60 and conn is not None and chart_ctx:
        try:
            from signal_scorer import get_scorer
            scorer  = get_scorer(conn)
            inds_4h = chart_ctx.get("4H", {}).get("indicators", {})
            ema_s   = inds_4h.get("ema", {}).get("stack", "")
            wt_s    = str(inds_4h.get("wavetrend", {}).get("signal", "")).lower()
            cvd_t   = str(inds_4h.get("cvd", {}).get("trend", "")).lower()
            features = {
                "setup_score":    setup_score or 5,
                "rsi":            inds_4h.get("rsi", {}).get("value", 50),
                "macd_histogram": inds_4h.get("macd", {}).get("histogram", 0),
                "ema_alignment":  1 if "bull" in ema_s else -1 if "bear" in ema_s else 0,
                "adx":            inds_4h.get("adx", {}).get("value", 20),
                "wt_signal":      1 if "buy" in wt_s else -1 if "sell" in wt_s else 0,
                "mfi":            inds_4h.get("wavetrend", {}).get("mfi", 50),
                "cvd_trend":      1 if "bull" in cvd_t else -1 if "bear" in cvd_t else 0,
                "volume_ratio":   inds_4h.get("volume", {}).get("ratio", 1.0),
                "direction":      direction or "long",
            }
            prob = scorer.predict(features)
            if prob is not None:
                block = f"ML win probability: {prob:.0%} (historical pattern match)"
                sections.append(block)
                remaining -= len(block)
        except Exception:
            pass
```

- [ ] **Step 8: Run full suite**

```
python3 -m pytest tests/ -q --tb=no 2>&1 | tail -3
```

- [ ] **Step 9: Commit**

```bash
git add signal_scorer.py tests/test_signal_scorer.py database.py requirements.txt prompt_builder.py
git commit -m "feat: ML signal scorer — XGBoost win-probability injected into prompts, 24h retrain"
```

---

## Phase F — Improved AI Prompts (TradingAgents-style)

Replace free-form analysis instructions with structured rubrics that enforce explicit reasoning. Improves consistency and audit-ability.

### Task F1: Structured technical analyst in `agent_data_interpreter.py`

**Files:**
- Modify: `agent_data_interpreter.py`

- [ ] **Step 1: Locate the system prompt string**

```
grep -n "You are\|system\|INSTRUCTIONS\|analyst" agent_data_interpreter.py | head -15
```

- [ ] **Step 2: Replace the analyst instructions block**

Find the string assigned as the agent's system/role instructions and replace with:

```python
_ANALYST_INSTRUCTIONS = """You are a senior technical analyst specialising in crypto futures (USDT-M perpetuals, 10x leverage).

You receive pre-computed indicators. Do NOT restate raw numbers — synthesise them into trading insight.

## MANDATORY OUTPUT (exactly these 6 sections, no additions):

**TREND** (1 sentence): EMA stack + ADX direction and strength.
**MOMENTUM** (1 sentence): RSI + MACD + WaveTrend confluence verdict.
**STRUCTURE** (1 sentence): Nearest key S/R level and its significance to the setup.
**SIGNAL COUNT** (format: X/12 aligned): Count signals agreeing with the primary bias.
**BIAS** (one of: STRONG LONG | LONG | NEUTRAL | SHORT | STRONG SHORT)
**CONFIDENCE** (one of: HIGH | MED | LOW)

## CONFIDENCE RULES:
- HIGH: ≥8/12 signals aligned, ADX > 20, EMA stack clean, within kill zone
- MED: 6–7/12 aligned OR ADX 15–20 OR outside kill zone
- LOW: <6/12 aligned OR ADX < 15 OR VIX > 30 flagged in context OR HMM=ranging with low conviction

## BIAS RULES:
- STRONG: ≥8 aligned, ADX > 25, clear EMA stack
- LONG/SHORT: 6–7 aligned
- NEUTRAL: <6 aligned or signals conflicting
"""
```

Set `_ANALYST_INSTRUCTIONS` as the system prompt in the `run()` function message construction.

- [ ] **Step 3: Run existing agent tests**

```
python3 -m pytest tests/test_agent_reviewer.py tests/test_agent_factories.py -v
```

- [ ] **Step 4: Commit**

```bash
git add agent_data_interpreter.py
git commit -m "feat: structured technical analyst prompt — 6-section output with signal count + confidence rubric"
```

---

### Task F2: Explicit risk rubric in `agent_risk_mgmt.py`

**Files:**
- Modify: `agent_risk_mgmt.py`

- [ ] **Step 1: Locate the risk manager system prompt**

```
grep -n "You are\|VERDICT\|APPROVE\|SKIP\|system\|rubric" agent_risk_mgmt.py | head -15
```

- [ ] **Step 2: Replace with explicit decision table**

Find the agent's system/instructions string and replace with:

```python
_RISK_INSTRUCTIONS = """You are a risk manager for crypto futures. Your job is to approve, reduce, or skip trades based on evidence — not optimism.

## DECISION TABLE (apply top-to-bottom, first match):

| Condition | Verdict | Size modifier |
|-----------|---------|---------------|
| Score ≥ 8 AND R:R ≥ 3:1 AND regime = trending_up | APPROVE | 1.5× |
| Score ≥ 7 AND R:R ≥ 2.5:1 | APPROVE | 1× |
| Score 6–7 AND R:R ≥ 2:1 | APPROVE | 1× |
| Score 5–6 AND R:R ≥ 1.5:1 | REDUCE | 0.5× |
| Score < 5 OR R:R < 1.5:1 | SKIP | 0 |
| No SL defined | SKIP | 0 |
| Outside kill zone AND score ≤ 6 | SKIP | 0 |
| Consensus flag = ⚡ (REVIEW) | SKIP | 0 |
| ML win probability < 40% AND score ≤ 6 | REDUCE | 0.5× |

Standard size = account_balance × risk_pct / (|entry − sl| / entry).
Cap: max 25% of account. Floor: 5% of account.

## MANDATORY OUTPUT (exactly these 5 lines):
VERDICT: APPROVE / REDUCE / SKIP
SIZE: {n} USDT
REASON: (one sentence citing the specific rule that triggered)
MAX_LOSS: {n} USDT
BEST_CASE: {n} USDT at TP2
"""
```

- [ ] **Step 3: Run tests**

```
python3 -m pytest tests/ -q --tb=no 2>&1 | tail -3
```

- [ ] **Step 4: Commit**

```bash
git add agent_risk_mgmt.py
git commit -m "feat: explicit risk manager decision table — VERDICT/SIZE/REASON/MAX_LOSS/BEST_CASE format"
```

---

## Phase G — Backtesting Quality

Add PBO, deflated Sharpe, and bootstrap confidence intervals. Expose via `POST /api/backtest/quality`.

### Task G1: `backtest_quality.py` + endpoint

**Files:**
- Create: `backtest_quality.py`
- Test: `tests/test_backtest_quality.py`
- Modify: `requirements.txt`, `routes/backtest.py`

- [ ] **Step 1: Add dependency**

In `requirements.txt`:
```
backtester-mcp>=0.1.0
```

Install:
```
pip3 install "backtester-mcp>=0.1.0" --break-system-packages
```

- [ ] **Step 2: Write failing tests**

```python
# tests/test_backtest_quality.py
import pytest
import numpy as np


def _make_data(n=200, seed=1):
    np.random.seed(seed)
    prices  = np.cumprod(1 + np.random.normal(0.001, 0.02, n)) * 100
    signals = np.where(np.random.random(n) > 0.5, 1.0, -1.0)
    return prices, signals


def test_quality_returns_required_keys():
    prices, signals = _make_data()
    from backtest_quality import run_quality_check
    result = run_quality_check(prices, signals)
    assert isinstance(result, dict)
    for key in ("sharpe", "deflated_sharpe", "pbo", "bootstrap_ci"):
        assert key in result, f"missing key: {key}"


def test_pbo_in_range():
    prices, signals = _make_data()
    from backtest_quality import run_quality_check
    result = run_quality_check(prices, signals)
    pbo = result["pbo"]
    if pbo is not None and not (pbo != pbo):   # not NaN
        assert 0.0 <= pbo <= 1.0


def test_bootstrap_ci_ordered():
    prices, signals = _make_data()
    from backtest_quality import run_quality_check
    result = run_quality_check(prices, signals)
    ci = result["bootstrap_ci"]
    if ci and len(ci) == 2:
        assert ci[0] <= ci[1]


def test_empty_input_returns_error():
    from backtest_quality import run_quality_check
    result = run_quality_check(np.array([]), np.array([]))
    assert "error" in result
```

- [ ] **Step 3: Run — expect FAIL**

```
python3 -m pytest tests/test_backtest_quality.py -v
```

- [ ] **Step 4: Implement `backtest_quality.py`**

```python
# backtest_quality.py
"""
Backtesting quality: PBO, deflated Sharpe, bootstrap Sharpe CI.
Uses backtester-mcp when available; pure-numpy fallback otherwise.
Reference: Bailey, Borwein, Lopez de Prado & Zhu (2014).
"""
import logging
import numpy as np

_log = logging.getLogger(__name__)


def _sharpe(returns: np.ndarray) -> float:
    std = returns.std()
    return float(returns.mean() / std * np.sqrt(252)) if std > 0 else 0.0


def _bootstrap_ci(returns: np.ndarray, n_boot: int = 1000,
                  alpha: float = 0.05) -> list[float]:
    if len(returns) < 10:
        return [float("nan"), float("nan")]
    rng     = np.random.default_rng(42)
    sharpes = sorted(
        _sharpe(rng.choice(returns, len(returns), replace=True))
        for _ in range(n_boot)
    )
    lo = float(np.percentile(sharpes, 100 * alpha / 2))
    hi = float(np.percentile(sharpes, 100 * (1 - alpha / 2)))
    return [round(lo, 3), round(hi, 3)]


def _deflated_sharpe(sharpe: float, n_trials: int, t: int,
                     skew: float = 0.0, kurt: float = 3.0) -> float:
    try:
        from scipy import stats as sp
        import math
        if t < 2 or n_trials < 1:
            return float("nan")
        sr_star = np.sqrt(
            (1 - skew * sharpe + (kurt - 1) / 4 * sharpe ** 2) / (t - 1)
        )
        e_max = ((1 - 0.5772) * sp.norm.ppf(1 - 1 / n_trials) +
                 0.5772 * sp.norm.ppf(1 - 1 / (n_trials * math.e)))
        return round(float(sp.norm.cdf((sharpe - e_max) / sr_star))
                     if sr_star > 0 else 0.0, 4)
    except Exception:
        return float("nan")


def _pbo(returns: np.ndarray, n_splits: int = 8) -> float:
    """CSCV-based Probability of Backtest Overfitting."""
    T = len(returns)
    if T < n_splits * 5:
        return float("nan")
    size   = T // n_splits
    chunks = [returns[i * size:(i + 1) * size] for i in range(n_splits)]
    from itertools import combinations
    overfit, total = 0, 0
    for k in range(1, n_splits):
        for combo in combinations(range(n_splits), k):
            oos = [i for i in range(n_splits) if i not in combo]
            if not oos:
                continue
            oos_sharpe = _sharpe(np.concatenate([chunks[i] for i in oos]))
            if oos_sharpe < 0:
                overfit += 1
            total += 1
    return round(overfit / total, 4) if total else float("nan")


def run_quality_check(prices: np.ndarray, signals: np.ndarray,
                      n_trials: int = 1) -> dict:
    """
    Full quality check on an equity curve.
    prices:   1-D close price array
    signals:  1-D position array (+1 long, -1 short, 0 flat)
    n_trials: number of parameter combinations tried (for deflated Sharpe)
    """
    if len(prices) < 10 or len(signals) < 10:
        return {"ok": False, "error": "need at least 10 data points"}

    # Try backtester-mcp for primary Sharpe
    sharpe = 0.0
    try:
        from backtester_mcp import backtest as bmt  # type: ignore
        res    = bmt(prices, signals)
        sharpe = float(getattr(getattr(res, "metrics", {}), "get", lambda k, d=0: d)("sharpe") or 0)
    except Exception:
        pass

    rets = np.diff(np.log(np.where(prices > 0, prices, 1e-9))) * signals[:-1]
    if sharpe == 0.0:
        sharpe = _sharpe(rets)

    skew = float(np.mean(rets ** 3) / (rets.std() ** 3 + 1e-12))
    kurt = float(np.mean(rets ** 4) / (rets.std() ** 4 + 1e-12))

    dsr = _deflated_sharpe(sharpe, n_trials, len(rets), skew, kurt)
    pbo = _pbo(rets)
    ci  = _bootstrap_ci(rets)

    overfitting = "unknown"
    if pbo == pbo and pbo is not None:    # not NaN
        overfitting = "likely genuine" if pbo < 0.5 else "possible overfitting"

    return {
        "ok":              True,
        "sharpe":          round(sharpe, 3),
        "deflated_sharpe": dsr,
        "pbo":             pbo,
        "bootstrap_ci":    ci,
        "n_trades":        int((np.diff(signals.astype(float)) != 0).sum()),
        "interpretation":  overfitting,
    }
```

- [ ] **Step 5: Run tests — expect PASS**

```
python3 -m pytest tests/test_backtest_quality.py -v
```

- [ ] **Step 6: Add `POST /api/backtest/quality` to `routes/backtest.py`**

Add after the existing walk-forward endpoint:

```python
@backtest_bp.route("/api/backtest/quality", methods=["POST"])
def quality_check():
    """
    POST body: {"prices": [float], "signals": [float], "n_trials": int}
    Returns PBO, deflated Sharpe, bootstrap CI.
    """
    from backtest_quality import run_quality_check
    body = request.get_json(silent=True) or {}
    try:
        prices   = np.array(body.get("prices",   []), dtype=float)
        signals  = np.array(body.get("signals",  []), dtype=float)
        n_trials = int(body.get("n_trials", 1))
    except (ValueError, TypeError) as exc:
        return _err(f"invalid input: {exc}", 400)
    if len(prices) < 10:
        return _err("prices must have ≥10 points", 400)
    if len(prices) != len(signals):
        return _err("prices and signals must be same length", 400)
    return _ok(run_quality_check(prices, signals, n_trials))
```

Ensure `import numpy as np` is present at the top of `routes/backtest.py`.

- [ ] **Step 7: Run full suite**

```
python3 -m pytest tests/ -q --tb=no 2>&1 | tail -3
```

- [ ] **Step 8: Commit**

```bash
git add backtest_quality.py tests/test_backtest_quality.py routes/backtest.py requirements.txt
git commit -m "feat: backtest quality — PBO/deflated-Sharpe/bootstrap-CI, POST /api/backtest/quality"
```

---

## Phase H — Final Wiring and Deploy

### Task H1: Constants

**Files:**
- Modify: `constants.py`

- [ ] **Step 1: Add constants**

```python
# Phase A–G additions
LIQ_PROXIMITY_PCT  = 0.03    # liquidation wall proximity threshold
LIQ_TTL            = 900     # 15-min cache for liquidation clusters
ONCHAIN_TTL        = 3600    # 1-h cache for on-chain daily metrics
REGIME_TTL         = 14400   # 4-h retrain window for HMM
ML_SCORER_TTL      = 86400   # 24-h retrain interval for XGBoost
ML_MIN_SAMPLES     = 20      # min labeled outcomes to activate ML scorer
```

Replace hardcoded values in each new module with `from constants import LIQ_TTL` etc.

- [ ] **Step 2: Commit**

```bash
git add constants.py liquidation_levels.py onchain_client.py market_regime.py signal_scorer.py
git commit -m "refactor: centralise new TTL and threshold constants"
```

---

### Task H2: Version bump + smoke test + deploy

- [ ] **Step 1: Bump version**

In `constants.py`:
```python
VERSION = "1.6.0"
```

- [ ] **Step 2: Run full test suite**

```
python3 -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected result: ≥ 431 passing (401 existing + ≥ 30 new), same pre-existing failures.

- [ ] **Step 3: Version commit**

```bash
git add constants.py
git commit -m "chore: bump to v1.6.0 — liquidation clusters, order flow, on-chain, HMM regime, ML scorer, prompt rubrics, backtest quality"
```

- [ ] **Step 4: Push**

```bash
git push
```

- [ ] **Step 5: Deploy to Pi**

```bash
rsync -avz --exclude="*.db" --exclude=".agents" --exclude="*.joblib" \
  ./ fbauer@<Pi-IP>:/home/fbauer/trading-journal/
ssh fbauer@<Pi-IP> "cd /home/fbauer/trading-journal && \
  pip3 install hmmlearn scikit-learn xgboost joblib 'backtester-mcp>=0.1.0' \
       --break-system-packages --quiet && \
  sudo systemctl restart trading-journal && \
  sleep 3 && sudo systemctl status trading-journal | head -5"
```

- [ ] **Step 6: Smoke test**

```
python3 scripts/self_test.py --host 192.168.1.21:8082
```

Expected: ≥ 50/54 pass.

---

## Dependency Summary

Add to `requirements.txt`:
```
hmmlearn>=0.3.0
joblib>=1.3.0
scikit-learn>=1.5.0
xgboost>=2.0.0
backtester-mcp>=0.1.0
```

Pi batch install:
```bash
pip3 install hmmlearn joblib scikit-learn xgboost backtester-mcp --break-system-packages
```

---

## Test Count Target

| Phase | New tests | File |
|-------|-----------|------|
| A1 | 3 | test_liquidation_levels.py |
| A2 | 4 | test_chart_context_scoring.py |
| B1 | 4 | test_order_flow.py |
| B2 | 4 | test_chart_context_scoring.py |
| C1 | 4 | test_onchain_client.py |
| D1 | 3 | test_market_regime.py |
| E1 | 4 | test_signal_scorer.py |
| G1 | 4 | test_backtest_quality.py |
| **Total** | **30** | |

**Expected final count: ~431 passing.**
