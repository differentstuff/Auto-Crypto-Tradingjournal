# CCXT Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the custom 225-line Blofin auth client with CCXT, expand the scanner watchlist with Binance top-100 USDT-M symbols, and add SMT Divergence as the 9th confluence signal.

**Architecture:** `ccxt_client.py` acts as a factory (Blofin + Binance instances). `blofin_client.py` keeps its public API unchanged but delegates auth/fetching to CCXT. `ai_scanner.py` merges the Binance symbol list into `DEFAULT_WATCHLIST`. `chart_context.py` adds `_smt_weight()` and wires it into `confluence_score()`.

**Tech Stack:** CCXT ≥4.0.0 (MIT), Flask, pandas, existing journal DB.

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `ccxt_client.py` | CCXT exchange factory + Binance price cache + symbol list |
| Modify | `blofin_client.py` | Replace HMAC internals with CCXT, keep 4 public functions identical |
| Modify | `ai_scanner.py` | Rename `DEFAULT_WATCHLIST`→`_BITGET_WATCHLIST`, add `BINANCE_WATCHLIST`, merge |
| Modify | `chart_context.py` | Add `_smt_weight()`, update `confluence_score()`, `_get_tf_weights()`, `max_val` |
| Modify | `requirements.txt` | Add `ccxt>=4.0.0` |
| Create | `tests/test_ccxt_client.py` | Unit tests for factory functions (monkeypatched CCXT) |
| Create | `tests/test_blofin_client.py` | Shape tests for blofin_client public API via mocked CCXT |
| Modify | `tests/test_chart_context_scoring.py` | Add 3 SMT weight tests |

---

## Task 1: Add CCXT dependency and create ccxt_client.py

**Files:**
- Modify: `requirements.txt`
- Create: `ccxt_client.py`
- Create: `tests/test_ccxt_client.py`

- [ ] **Step 1: Write failing tests for ccxt_client**

Create `tests/test_ccxt_client.py`:

```python
"""Tests for ccxt_client.py factory functions."""
import time
from unittest.mock import MagicMock, patch

import pytest


def test_get_blofin_exchange_returns_ccxt_instance():
    """get_blofin_exchange() must return an object with fetch_balance method."""
    mock_exchange = MagicMock()
    mock_exchange.fetch_balance = MagicMock(return_value={})

    with patch("ccxt.blofin", return_value=mock_exchange):
        import importlib
        import ccxt_client
        importlib.reload(ccxt_client)
        result = ccxt_client.get_blofin_exchange()
        assert hasattr(result, "fetch_balance")


def test_get_binance_exchange_no_auth():
    """get_binance_exchange() must not set apiKey (public-only)."""
    mock_exchange = MagicMock()
    with patch("ccxt.binance", return_value=mock_exchange) as mock_cls:
        import importlib
        import ccxt_client
        importlib.reload(ccxt_client)
        ccxt_client.get_binance_exchange()
        call_kwargs = mock_cls.call_args[0][0]
        assert "apiKey" not in call_kwargs
        assert call_kwargs.get("enableRateLimit") is True


def test_get_binance_price_cache_hit():
    """get_binance_price() returns cached value within TTL without calling exchange."""
    import importlib
    import ccxt_client
    importlib.reload(ccxt_client)

    ccxt_client._binance_price_cache["BTCUSDT"] = (50000.0, time.time())
    with patch("ccxt_client.get_binance_exchange") as mock_ex:
        result = ccxt_client.get_binance_price("BTCUSDT")
    assert result == 50000.0
    mock_ex.assert_not_called()


def test_get_binance_price_returns_none_on_error():
    """get_binance_price() returns None when exchange raises."""
    import importlib
    import ccxt_client
    importlib.reload(ccxt_client)
    ccxt_client._binance_price_cache.clear()

    mock_exchange = MagicMock()
    mock_exchange.fetch_ticker.side_effect = Exception("network error")
    with patch("ccxt_client.get_binance_exchange", return_value=mock_exchange):
        result = ccxt_client.get_binance_price("BTCUSDT")
    assert result is None


def test_get_binance_futures_symbols_filters_usdt_pairs():
    """get_binance_futures_symbols() returns symbols ending in USDT, filtered by volume."""
    import importlib
    import ccxt_client
    importlib.reload(ccxt_client)

    mock_tickers = {
        "BTC/USDT:USDT": {"quoteVolume": 1_000_000_000},
        "ETH/USDT:USDT": {"quoteVolume": 500_000_000},
        "TINY/USDT:USDT": {"quoteVolume": 1_000},  # below threshold
        "BTC/USD:BTC": {"quoteVolume": 900_000_000},  # inverse, wrong format
    }
    mock_exchange = MagicMock()
    mock_exchange.fetch_tickers.return_value = mock_tickers

    with patch("ccxt_client.get_binance_exchange", return_value=mock_exchange):
        result = ccxt_client.get_binance_futures_symbols(min_vol_usd=50_000_000)

    assert "BTCUSDT" in result
    assert "ETHUSDT" in result
    assert "TINYUSDT" not in result
    assert "BTCUSD" not in result
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
python3 -m pytest tests/test_ccxt_client.py -v
```

Expected: `ModuleNotFoundError: No module named 'ccxt_client'` (and possibly `ccxt`)

- [ ] **Step 3: Add ccxt to requirements.txt**

```
# in requirements.txt, add after existing lines:
ccxt>=4.0.0
```

Then install:

```bash
pip install ccxt>=4.0.0
```

- [ ] **Step 4: Create ccxt_client.py**

```python
"""ccxt_client.py — CCXT exchange factory. Provides initialized exchange instances."""
import os
import time

import ccxt

_binance_price_cache: dict = {}
BINANCE_PRICE_CACHE_TTL = 60  # seconds


def get_blofin_exchange() -> ccxt.Exchange:
    return ccxt.blofin({
        "apiKey":          os.environ.get("BLOFIN_API_KEY", ""),
        "secret":          os.environ.get("BLOFIN_SECRET_KEY", ""),
        "password":        os.environ.get("BLOFIN_PASSPHRASE", ""),
        "enableRateLimit": True,
    })


def get_binance_exchange() -> ccxt.Exchange:
    """Public-only Binance instance — no auth required for market data."""
    return ccxt.binance({"enableRateLimit": True})


def get_binance_price(symbol: str) -> float | None:
    """
    Fetch last price from Binance for SMT divergence check.
    symbol: 'BTCUSDT' → maps to 'BTC/USDT:USDT' for Binance futures.
    Returns None on any error. 60-second cache.
    """
    now = time.time()
    cached = _binance_price_cache.get(symbol)
    if cached and (now - cached[1]) < BINANCE_PRICE_CACHE_TTL:
        return cached[0]
    try:
        exchange = get_binance_exchange()
        ccxt_sym = symbol.replace("USDT", "/USDT:USDT")
        ticker = exchange.fetch_ticker(ccxt_sym)
        price = ticker["last"]
        _binance_price_cache[symbol] = (price, now)
        return price
    except Exception:
        return None


def get_binance_futures_symbols(min_vol_usd: float = 50_000_000) -> list:
    """
    Return top USDT-M linear futures symbols from Binance filtered by 24h volume.
    Strips '/USDT:USDT' suffix to match journal symbol format (e.g. 'BTCUSDT').
    """
    exchange = get_binance_exchange()
    tickers = exchange.fetch_tickers()
    symbols = []
    for sym, t in tickers.items():
        if not sym.endswith("/USDT:USDT"):
            continue
        vol = t.get("quoteVolume") or 0
        if vol >= min_vol_usd:
            symbols.append(sym.replace("/USDT:USDT", "USDT"))
    return sorted(
        symbols,
        key=lambda s: (tickers.get(s.replace("USDT", "/USDT:USDT")) or {}).get("quoteVolume", 0),
        reverse=True,
    )[:100]
```

- [ ] **Step 5: Run tests to verify passing**

```bash
python3 -m pytest tests/test_ccxt_client.py -v
```

Expected: 5 tests PASS

- [ ] **Step 6: Commit**

```bash
git add requirements.txt ccxt_client.py tests/test_ccxt_client.py
git commit -m "feat(F1): add ccxt_client.py factory + install ccxt>=4.0.0"
```

---

## Task 2: Replace blofin_client.py internals with CCXT

**Files:**
- Modify: `blofin_client.py` — replace 225-line HMAC implementation with CCXT calls
- Create: `tests/test_blofin_client.py`

- [ ] **Step 1: Write failing tests for the new blofin_client public API shape**

Create `tests/test_blofin_client.py`:

```python
"""Tests for blofin_client.py — verifies output shapes via mocked CCXT exchange."""
from unittest.mock import MagicMock, patch


def _make_mock_exchange(balance=None, positions=None, orders=None):
    ex = MagicMock()
    ex.fetch_balance.return_value = balance or {
        "USDT": {"total": 1000.0, "free": 800.0},
    }
    ex.fetch_positions.return_value = positions or [
        {
            "symbol": "BTC/USDT:USDT",
            "side": "long",
            "contracts": 0.01,
            "entryPrice": 60000.0,
            "unrealizedPnl": 50.0,
            "leverage": 10,
            "notional": 600.0,
        }
    ]
    ex.fetch_closed_orders.return_value = orders or []
    return ex


def test_is_configured_false_without_env(monkeypatch):
    monkeypatch.delenv("BLOFIN_API_KEY", raising=False)
    monkeypatch.delenv("BLOFIN_SECRET_KEY", raising=False)
    import importlib
    import blofin_client
    importlib.reload(blofin_client)
    assert blofin_client.is_configured() is False


def test_get_account_equity_returns_equity_and_available():
    mock_ex = _make_mock_exchange()
    with patch("blofin_client.get_blofin_exchange", return_value=mock_ex):
        import blofin_client
        result = blofin_client.get_account_equity()
    assert "equity" in result
    assert "available" in result
    assert result["equity"] == 1000.0
    assert result["available"] == 800.0


def test_get_account_equity_returns_zeros_on_error():
    mock_ex = MagicMock()
    mock_ex.fetch_balance.side_effect = Exception("auth error")
    with patch("blofin_client.get_blofin_exchange", return_value=mock_ex):
        import blofin_client
        result = blofin_client.get_account_equity()
    assert result == {"equity": 0.0, "available": 0.0}


def test_get_open_positions_returns_list_with_correct_shape():
    mock_ex = _make_mock_exchange()
    with patch("blofin_client.get_blofin_exchange", return_value=mock_ex):
        import blofin_client
        result = blofin_client.get_open_positions()
    assert isinstance(result, list)
    assert len(result) == 1
    pos = result[0]
    assert "symbol" in pos
    assert "side" in pos
    assert pos["symbol"] == "BTCUSDT"


def test_get_open_positions_returns_empty_on_error():
    mock_ex = MagicMock()
    mock_ex.fetch_positions.side_effect = Exception("network")
    with patch("blofin_client.get_blofin_exchange", return_value=mock_ex):
        import blofin_client
        result = blofin_client.get_open_positions()
    assert result == []


def test_test_connection_returns_ok_true():
    mock_ex = _make_mock_exchange()
    with patch("blofin_client.get_blofin_exchange", return_value=mock_ex):
        import blofin_client
        result = blofin_client.test_connection()
    assert result.get("ok") is True


def test_test_connection_returns_ok_false_on_auth_error():
    import ccxt
    mock_ex = MagicMock()
    mock_ex.fetch_balance.side_effect = ccxt.AuthenticationError("bad key")
    with patch("blofin_client.get_blofin_exchange", return_value=mock_ex):
        import blofin_client
        result = blofin_client.test_connection()
    assert result.get("ok") is False
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python3 -m pytest tests/test_blofin_client.py -v
```

Expected: ImportError or AssertionError — `get_blofin_exchange` not yet imported in blofin_client.

- [ ] **Step 3: Rewrite blofin_client.py using CCXT**

Replace the entire file with the CCXT-backed implementation. Keep all 4 public function signatures identical.

```python
"""blofin_client.py — Blofin exchange client via CCXT (read-only)."""
import os

import ccxt

from ccxt_client import get_blofin_exchange

API_KEY    = os.environ.get("BLOFIN_API_KEY",    "")
SECRET_KEY = os.environ.get("BLOFIN_SECRET_KEY", "")


def is_configured() -> bool:
    """Return True if API key + secret are both set."""
    return bool(API_KEY and SECRET_KEY)


def test_connection() -> dict:
    """Verify credentials are valid. Returns {"ok": bool, "error": str|None}."""
    try:
        exchange = get_blofin_exchange()
        exchange.fetch_balance()
        return {"ok": True, "error": None}
    except ccxt.AuthenticationError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_account_equity() -> dict:
    """Return {"equity": float, "available": float}. Returns zeros on any error."""
    try:
        exchange = get_blofin_exchange()
        balance = exchange.fetch_balance()
        usdt = balance.get("USDT") or {}
        return {
            "equity":    float(usdt.get("total") or 0.0),
            "available": float(usdt.get("free")  or 0.0),
        }
    except Exception:
        return {"equity": 0.0, "available": 0.0}


def get_open_positions() -> list:
    """Return list of open position dicts matching existing DB shape. Empty list on error."""
    try:
        exchange = get_blofin_exchange()
        positions = exchange.fetch_positions()
        result = []
        for p in positions:
            sym_raw = p.get("symbol") or ""
            symbol = sym_raw.replace("/USDT:USDT", "USDT").replace("/USD:BTC", "USD")
            result.append({
                "symbol":         symbol,
                "side":           p.get("side"),
                "size":           float(p.get("contracts") or 0),
                "entry_price":    float(p.get("entryPrice") or 0),
                "unrealized_pnl": float(p.get("unrealizedPnl") or 0),
                "leverage":       int(p.get("leverage") or 1),
                "notional":       float(p.get("notional") or 0),
            })
        return result
    except Exception:
        return []


def get_position_history(symbol: str = None, limit: int = 50) -> list:
    """Return list of closed order dicts. Empty list on error."""
    try:
        exchange = get_blofin_exchange()
        sym_ccxt = (symbol.replace("USDT", "/USDT:USDT") if symbol else None)
        orders = exchange.fetch_closed_orders(sym_ccxt, limit=limit)
        result = []
        for o in orders:
            sym_raw = o.get("symbol") or ""
            symbol_out = sym_raw.replace("/USDT:USDT", "USDT")
            result.append({
                "symbol":     symbol_out,
                "side":       o.get("side"),
                "price":      float(o.get("average") or o.get("price") or 0),
                "amount":     float(o.get("filled") or 0),
                "pnl":        float((o.get("info") or {}).get("pnl") or 0),
                "timestamp":  o.get("timestamp"),
                "order_id":   o.get("id"),
            })
        return result
    except Exception:
        return []
```

- [ ] **Step 4: Run tests to verify passing**

```bash
python3 -m pytest tests/test_blofin_client.py -v
```

Expected: 7 tests PASS

- [ ] **Step 5: Smoke test import**

```bash
python3 -c "import blofin_client; print('is_configured:', blofin_client.is_configured())"
```

Expected: no ImportError, prints True or False.

- [ ] **Step 6: Commit**

```bash
git add blofin_client.py tests/test_blofin_client.py
git commit -m "feat(F1): replace blofin_client.py HMAC internals with CCXT"
```

---

## Task 3: Expand scanner watchlist with Binance USDT-M symbols (F2)

**Files:**
- Modify: `ai_scanner.py`

No new test file — the existing scanner tests don't test the watchlist list contents directly, and the Binance call is wrapped in try/except at module load so it can't break tests.

- [ ] **Step 1: Find the DEFAULT_WATCHLIST declaration in ai_scanner.py**

```bash
grep -n "DEFAULT_WATCHLIST" /Users/fbauer/Documents/ClaudeAIData/Trading-Journal/ai_scanner.py | head -5
```

Note the line number. The list starts at that line.

- [ ] **Step 2: Rename DEFAULT_WATCHLIST to _BITGET_WATCHLIST**

In `ai_scanner.py`, rename the existing list declaration from:

```python
DEFAULT_WATCHLIST = [
```

to:

```python
_BITGET_WATCHLIST = [
```

(Only the declaration — the name inside the list stays identical. The closing `]` stays too.)

- [ ] **Step 3: Add Binance watchlist and merge logic after the _BITGET_WATCHLIST block**

After the closing `]` of `_BITGET_WATCHLIST`, add:

```python
# BINANCE_WATCHLIST: top-100 Binance USDT-M futures by 24h volume.
# Falls back to empty list if Binance unreachable at startup.
try:
    import ccxt_client as _ccxt
    BINANCE_WATCHLIST = _ccxt.get_binance_futures_symbols()
except Exception:
    BINANCE_WATCHLIST = []

# Merge: Bitget list first (home exchange), Binance additions after.
DEFAULT_WATCHLIST = list(dict.fromkeys(
    _BITGET_WATCHLIST + [s for s in BINANCE_WATCHLIST if s not in set(_BITGET_WATCHLIST)]
))
```

- [ ] **Step 4: Verify scanner still imports cleanly**

```bash
python3 -c "import ai_scanner; print('watchlist size:', len(ai_scanner.DEFAULT_WATCHLIST))"
```

Expected: prints a number ≥ the old watchlist size (no ImportError or exception). If Binance is unreachable, size = original list length.

- [ ] **Step 5: Run existing tests to check nothing broke**

```bash
python3 -m pytest tests/ -v --ignore=tests/test_ccxt_client.py --ignore=tests/test_blofin_client.py -x
```

Expected: all previously-passing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add ai_scanner.py
git commit -m "feat(F2): expand scanner watchlist with Binance top-100 USDT-M symbols"
```

---

## Task 4: Add SMT Divergence as 9th confluence signal (F3)

**Files:**
- Modify: `chart_context.py` — add `_smt_weight()`, update `confluence_score()`, `_get_tf_weights()`, `max_val`
- Modify: `tests/test_chart_context_scoring.py` — add 3 new tests

- [ ] **Step 1: Write failing tests for SMT weight**

Open `tests/test_chart_context_scoring.py` and add at the end:

```python
# ── SMT Divergence tests ──────────────────────────────────────────────────

def test_smt_weight_btc_prices_in_sync():
    """Bitget and Binance prices within 0.5% → +0.15 confirmation weight."""
    from unittest.mock import patch
    import chart_context

    inds = {"ema": {"current_price": 60000.0}}
    with patch("chart_context.get_binance_price", return_value=60200.0):
        result = chart_context._smt_weight(inds, "BTCUSDT")
    assert result == 0.15


def test_smt_weight_divergence_neutral():
    """Bitget and Binance prices differ > 0.5% → 0.0 (no reward, no penalty)."""
    from unittest.mock import patch
    import chart_context

    inds = {"ema": {"current_price": 60000.0}}
    with patch("chart_context.get_binance_price", return_value=62000.0):
        result = chart_context._smt_weight(inds, "BTCUSDT")
    assert result == 0.0


def test_smt_weight_non_smt_symbol():
    """Non-SMT symbols (e.g. AAVEUSDT) always return 0.0."""
    import chart_context

    inds = {"ema": {"current_price": 100.0}}
    result = chart_context._smt_weight(inds, "AAVEUSDT")
    assert result == 0.0
```

- [ ] **Step 2: Run new tests to confirm they fail**

```bash
python3 -m pytest tests/test_chart_context_scoring.py -v -k "smt"
```

Expected: `AttributeError: module 'chart_context' has no attribute '_smt_weight'`

- [ ] **Step 3: Add SMT_SYMBOLS constant and _smt_weight() function to chart_context.py**

Find `_mfi_weight` in `chart_context.py` (line ~632). Before that function, insert:

```python
SMT_SYMBOLS = {"BTCUSDT", "ETHUSDT"}


def _smt_weight(inds: dict, symbol: str) -> float:
    """
    SMT Divergence: compare Bitget and Binance prices for the same symbol.
    +0.15 when both exchanges agree within 0.5% (confirmation, no divergence).
    0.0 when prices diverge > 0.5% OR symbol not in SMT_SYMBOLS.
    """
    if symbol not in SMT_SYMBOLS:
        return 0.0
    bitget_price = (inds.get("ema") or {}).get("current_price")
    if not bitget_price:
        return 0.0
    try:
        from ccxt_client import get_binance_price
        binance_price = get_binance_price(symbol)
    except Exception:
        return 0.0
    if binance_price is None:
        return 0.0
    delta_pct = abs(bitget_price - binance_price) / bitget_price
    if delta_pct < 0.005:
        return 0.15
    return 0.0
```

- [ ] **Step 4: Wire _smt_weight into _get_tf_weights()**

Find `_get_tf_weights()` (line ~644). It builds a list of weights. Add `_smt_weight(inds, symbol)` alongside the other weight calls.

Look for the list being built in `_get_tf_weights` — it will have calls like `_mfi_weight(...)`. Add `_smt_weight(inds, symbol)` to that same list. The function signature must also accept `symbol: str` — check whether it already does. If not, add it:

```python
def _get_tf_weights(ctx: dict, tf: str, symbol: str = "") -> list:
```

And add `_smt_weight(inds, symbol)` to the weights list inside it.

- [ ] **Step 5: Update confluence_score() to pass symbol to _get_tf_weights() and add smt_w to base_score**

In `confluence_score()` (line ~524):
1. `confluence_score()` already takes `symbol: str` as first arg — confirm this.
2. Find where `_get_tf_weights(ctx, tf)` is called and change to `_get_tf_weights(ctx, tf, symbol)`.
3. Find where `base_score` is computed from individual weights — add `smt_w` there if the weights list approach doesn't cover it automatically.

The current `base_score` line looks like:
```python
base_score = rsi_w + macd_w + ema_w + adx_w + wt_w + mfi_w + cvd_w
```

If individual weight variables are extracted, add:
```python
smt_w      = _smt_weight(inds, symbol)
base_score = rsi_w + macd_w + ema_w + adx_w + wt_w + mfi_w + cvd_w + smt_w
```

Also update the `pos`/`neg` breakdown line to include `smt_w`.

- [ ] **Step 6: Update max_val**

Find the `max_val` line (currently `float(len(tfs) * 6.2)`). Change to:

```python
max_val = float(len(tfs) * 6.35)  # adds SMT max +0.15 per TF
```

- [ ] **Step 7: Run tests to verify all passing**

```bash
python3 -m pytest tests/test_chart_context_scoring.py -v
```

Expected: all tests PASS including 3 new SMT tests.

- [ ] **Step 8: Run full test suite**

```bash
python3 -m pytest tests/ -v -x
```

Expected: all tests PASS.

- [ ] **Step 9: Smoke test the module**

```bash
python3 -c "import chart_context; print('max_val per TF:', 6.35); print('SMT symbols:', chart_context.SMT_SYMBOLS)"
```

Expected: no error, prints correct values.

- [ ] **Step 10: Commit**

```bash
git add chart_context.py tests/test_chart_context_scoring.py
git commit -m "feat(F3): add SMT Divergence as 9th confluence signal (+0.15 weight)"
```

---

## Self-Review Checklist

- [x] **F1 covered**: blofin_client.py public API unchanged, HMAC replaced with CCXT
- [x] **F2 covered**: DEFAULT_WATCHLIST merges Bitget + Binance, falls back gracefully
- [x] **F3 covered**: _smt_weight() + max_val updated + tests added
- [x] **No placeholders**: all code is complete
- [x] **Type consistency**: `get_binance_price` returns `float | None`, consumed correctly in `_smt_weight`
- [x] **Graceful degradation**: all network calls wrapped in try/except with safe fallbacks
- [x] **No DB migrations**: no schema changes needed
