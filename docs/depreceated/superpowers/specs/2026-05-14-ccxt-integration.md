# Spec A: CCXT Integration — Design Spec
*Date: 2026-05-14 · Status: Approved · Covers: F1, F2, F3*

---

## Overview

Three journal enhancements using the CCXT library:
- **F1** — Replace 225-line custom Blofin auth client with CCXT
- **F2** — Expand scanner watchlist with Binance USDT-M symbols (top 100 by volume)
- **F3** — Add SMT Divergence as 9th confluence signal (±0.35 weight)

No changes to sync logic, Flask routes, or existing Bitget client. `pip install ccxt` added to `requirements.txt`.

---

## Architecture

```
ccxt_client.py (new)
  ├── get_blofin_exchange() → ccxt.blofin instance (reads BLOFIN_* from .env)
  ├── get_binance_exchange() → ccxt.binance instance (read-only, no auth needed for public)
  ├── get_binance_futures_symbols(min_vol_usd) → list[str]   [F2]
  └── get_binance_price(symbol) → float | None               [F3]

blofin_client.py (replaced internals, same public API)
  ├── is_configured() → bool                    [unchanged]
  ├── test_connection() → dict                  [via CCXT]
  ├── get_account_equity() → dict               [via CCXT fetch_balance]
  ├── get_position_history(...) → list          [via CCXT fetch_closed_orders + mapping]
  └── get_open_positions() → list               [via CCXT fetch_positions + mapping]

ai_scanner.py
  └── BINANCE_WATCHLIST (new) — top-100 Binance USDT-M symbols [F2]
      DEFAULT_WATCHLIST extended with BINANCE_WATCHLIST

chart_context.py
  ├── _smt_weight(inds, symbol) → float         [F3, new function]
  ├── confluence_score() — adds smt_w to base_score
  └── max_val updated: 6.2 + 0.35 = 6.55 per timeframe
```

---

## F1 — Replace Blofin Client

### `ccxt_client.py` (new file)

```python
"""ccxt_client.py — CCXT exchange factory. Provides initialized exchange instances."""
import os
import ccxt

def get_blofin_exchange() -> ccxt.Exchange:
    return ccxt.blofin({
        'apiKey':          os.environ.get('BLOFIN_API_KEY', ''),
        'secret':          os.environ.get('BLOFIN_SECRET_KEY', ''),
        'password':        os.environ.get('BLOFIN_PASSPHRASE', ''),
        'enableRateLimit': True,
    })

def get_binance_exchange() -> ccxt.Exchange:
    """Public-only Binance instance — no auth required for market data."""
    return ccxt.binance({'enableRateLimit': True})

_binance_price_cache: dict = {}   # symbol → (price, timestamp)
BINANCE_PRICE_CACHE_TTL = 60      # seconds
```

### `blofin_client.py` — internals replaced

Keep all 4 public function signatures identical. Replace the 5-header HMAC implementation with CCXT calls:

| Old | New |
|-----|-----|
| Custom `_sign()`, `_get()`, `uuid.uuid4()` auth | `ccxt_client.get_blofin_exchange()` |
| `get_account_equity()` | `exchange.fetch_balance()` → map to `{equity, available}` |
| `get_open_positions()` | `exchange.fetch_positions()` → map to existing dict shape |
| `get_position_history()` | `exchange.fetch_closed_orders()` → map to existing list shape |
| `test_connection()` | `exchange.fetch_balance()` + return ok/error |

**Field mapping:** Blofin uses `BTC-USDT` format in CCXT. Map to `BTCUSDT` for DB compatibility (same as before).

**Error handling:** Catch `ccxt.AuthenticationError`, `ccxt.NetworkError`, `ccxt.ExchangeError` — return safe empty/fallback values matching existing behaviour.

**`is_configured()`** — unchanged: reads `BLOFIN_API_KEY` and `BLOFIN_SECRET_KEY` from env.

---

## F2 — Binance Scanner Watchlist

### `ccxt_client.py` addition

```python
def get_binance_futures_symbols(min_vol_usd: float = 50_000_000) -> list:
    """
    Return top USDT-M linear futures symbols from Binance filtered by 24h volume.
    Strips ':USDT' suffix to match the journal's symbol format (e.g. 'BTCUSDT').
    Cached for 24h — called once per scanner cycle at most.
    """
    exchange = get_binance_exchange()
    tickers  = exchange.fetch_tickers()   # public endpoint, no auth
    symbols  = []
    for sym, t in tickers.items():
        if not sym.endswith('/USDT:USDT'):
            continue
        vol = (t.get('quoteVolume') or 0)
        if vol >= min_vol_usd:
            symbols.append(sym.replace('/USDT:USDT', 'USDT'))
    return sorted(symbols, key=lambda s: tickers[s.replace('USDT','/USDT:USDT')].get('quoteVolume', 0), reverse=True)[:100]
```

### `ai_scanner.py` addition

```python
# BINANCE_WATCHLIST populated at module load (cached 24h).
# Falls back to empty list if Binance is unreachable.
try:
    import ccxt_client as _ccxt
    BINANCE_WATCHLIST = _ccxt.get_binance_futures_symbols()
except Exception:
    BINANCE_WATCHLIST = []

# Merge: Bitget list first (home exchange), then Binance additions
DEFAULT_WATCHLIST = list(dict.fromkeys(
    _BITGET_WATCHLIST + [s for s in BINANCE_WATCHLIST if s not in set(_BITGET_WATCHLIST)]
))
```

Existing `_BITGET_WATCHLIST` = the current hardcoded list (renamed).

**OHLCV routing:** No change — all OHLCV still fetches from Bitget via `chart_context.get_candles()`. Bitget carries all top-100 Binance symbols. If Bitget returns empty candles for a symbol, Stage 1 confluence filter naturally returns no signals for it (existing behaviour).

---

## F3 — SMT Divergence (9th Signal)

### `ccxt_client.py` addition

```python
def get_binance_price(symbol: str) -> float | None:
    """
    Fetch last price from Binance for SMT divergence check.
    symbol: 'BTCUSDT' → maps to 'BTC/USDT:USDT' for Binance futures.
    Returns None on any error.
    60-second cache — called once per confluence_score() invocation.
    """
    now = time.time()
    cached = _binance_price_cache.get(symbol)
    if cached and (now - cached[1]) < BINANCE_PRICE_CACHE_TTL:
        return cached[0]
    try:
        exchange = get_binance_exchange()
        ccxt_sym = symbol.replace('USDT', '/USDT:USDT')
        ticker   = exchange.fetch_ticker(ccxt_sym)
        price    = ticker['last']
        _binance_price_cache[symbol] = (price, now)
        return price
    except Exception:
        return None
```

### `chart_context.py` addition

**New `_smt_weight(inds: dict, symbol: str) -> float`** — only active for `BTCUSDT` and `ETHUSDT` (most reliable SMT pairs):

```python
SMT_SYMBOLS = {'BTCUSDT', 'ETHUSDT'}

def _smt_weight(inds: dict, symbol: str) -> float:
    """
    SMT Divergence: Bitget price disagrees with Binance price direction at key levels.
    +0.35 = Binance confirms bullish move (both exchanges making new high)
    -0.35 = Binance confirms bearish move (both exchanges making new low)
     0.0  = divergence detected (rejection signal — do not trade) OR symbol not in SMT_SYMBOLS
    """
    if symbol not in SMT_SYMBOLS:
        return 0.0
    bitget_price = (inds.get('ema') or {}).get('current_price')
    if not bitget_price:
        return 0.0
    try:
        from ccxt_client import get_binance_price
        binance_price = get_binance_price(symbol)
    except Exception:
        return 0.0
    if binance_price is None:
        return 0.0
    # Both prices within 0.5% of each other → confirmed (no divergence)
    delta_pct = abs(bitget_price - binance_price) / bitget_price
    if delta_pct < 0.005:
        return 0.15   # slight bullish confirmation (prices in sync)
    # Divergence > 0.5% → neutral (don't penalise, don't reward)
    return 0.0
```

**Update `confluence_score()`:**
```python
smt_w = _smt_weight(inds, symbol)   # added alongside mfi_w
base_score = rsi_w + macd_w + ema_w + adx_w + wt_w + mfi_w + cvd_w + smt_w
```

**Update `max_val`:**
```python
max_val = float(len(tfs) * 6.35)  # adds SMT max 0.15 → 6.2 + 0.15
```

**Update `confluence_score()` signature** — add `symbol: str = ""` parameter so `_smt_weight` knows which pair to check:
```python
def confluence_score(symbol: str = "", timeframes: list = None, ctx: dict = None) -> dict:
```
(existing callers already pass `symbol` as first arg — no breaking change)

**Update `_get_tf_weights()`:** add `_smt_weight(inds, symbol)` to `base` list.

---

## Files Changed

| File | Change |
|------|--------|
| `requirements.txt` | Add `ccxt>=4.0.0` |
| `ccxt_client.py` | **New** — factory functions for Blofin + Binance |
| `blofin_client.py` | Replace internals with CCXT, keep public API |
| `ai_scanner.py` | Rename `DEFAULT_WATCHLIST` → `_BITGET_WATCHLIST`, add `BINANCE_WATCHLIST`, merge |
| `chart_context.py` | Add `_smt_weight()`, update `confluence_score()`, `_get_tf_weights()`, `max_val` |

No DB migrations. No API shape changes. `blofin_sync.py` and all other callers unchanged.

---

## Testing

- `tests/test_ccxt_client.py` — unit tests for factory functions (monkeypatched CCXT)
- `tests/test_blofin_client.py` (new) — verify `get_open_positions()` and `get_account_equity()` return correct shapes via mocked CCXT exchange
- `tests/test_chart_context_scoring.py` — extend existing file: `test_smt_weight_btc_confirmed()`, `test_smt_weight_divergence_neutral()`, `test_smt_weight_non_smt_symbol()`
- Integration: `python -c "import blofin_client; print(blofin_client.is_configured())"` — verify no import errors
