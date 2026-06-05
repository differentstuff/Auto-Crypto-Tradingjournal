# Improvements Plan A — Backend Data & Logic

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix indicator divergence between backtest and scanner, make the watchlist liquidity-filtered, automate call outcome matching, and add API-based trade backfill.

**Architecture:** Four independent backend improvements. Each touches at most 3 files and is testable in isolation. No new dependencies required.

**Tech Stack:** Python 3.13, pandas, pandas_ta, SQLite, Flask, existing bitget_client + ccxt_client.

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `indicators.py` | **Create** | Single source of truth for raw indicator series (RSI, WaveTrend, ADX). Both backtest and chart code import from here. |
| `backtest_engine.py` | **Modify** | Replace `_rsi()` / `_adx()` private functions with imports from `indicators.py`. |
| `scanner_watchlist.py` | **Modify** | Add 24h cached dynamic watchlist filtered by Binance volume + OI. |
| `ccxt_client.py` | **Modify** | Add `get_binance_oi(symbol)` for OI threshold filtering. |
| `sync_base.py` | **Modify** | Add `auto_match_calls()` — matches 'saved' calls to newly-synced positions. |
| `bitget_sync.py` | **Modify** | Call `auto_match_calls()` after `_sync_positions()` each sync cycle. |
| `routes/sync.py` | **Modify** | Add `POST /api/sync/backfill` endpoint. |
| `tests/test_indicators.py` | **Create** | Tests for shared indicator functions. |
| `tests/test_watchlist_dynamic.py` | **Create** | Tests for dynamic watchlist caching and filtering. |
| `tests/test_auto_match.py` | **Create** | Tests for auto_match_calls. |
| `tests/test_backfill_route.py` | **Create** | Tests for the backfill endpoint. |

---

## Task 1: Shared Indicator Library

**Problem:** `backtest_engine.py` uses a hand-rolled EWM Wilder RSI (`_rsi()`). `chart_indicators.py` uses `pandas_ta.rsi()`. These produce slightly different values, so the optimizer tunes for a signal that the live scanner doesn't fire identically.

**Fix:** Create `indicators.py` with `rsi_series()` wrapping pandas_ta, and `wavetrend_series()` / `adx_series()` as canonical implementations. Update `backtest_engine.py` to import from there.

**Files:**
- Create: `indicators.py`
- Modify: `backtest_engine.py:26-56` (remove `_rsi`, `_adx`; import from indicators)
- Create: `tests/test_indicators.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_indicators.py
import numpy as np
import pandas as pd
import pytest

def _make_df(n=100, seed=42):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    high  = close + rng.uniform(0, 2, n)
    low   = close - rng.uniform(0, 2, n)
    vol   = rng.uniform(1000, 5000, n)
    return pd.DataFrame({"open": close, "high": high, "low": low,
                         "close": close, "volume": vol})

def test_rsi_series_length():
    import indicators
    df = _make_df(100)
    rsi = indicators.rsi_series(df["close"])
    assert len(rsi) == 100

def test_rsi_series_range():
    import indicators
    df = _make_df(100)
    rsi = indicators.rsi_series(df["close"]).dropna()
    assert (rsi >= 0).all() and (rsi <= 100).all()

def test_wavetrend_returns_two_series():
    import indicators
    df = _make_df(100)
    wt1, wt2 = indicators.wavetrend_series(df["high"], df["low"], df["close"])
    assert len(wt1) == len(wt2) == 100

def test_adx_series_nonnegative():
    import indicators
    df = _make_df(100)
    adx = indicators.adx_series(df["high"], df["low"], df["close"]).dropna()
    assert (adx >= 0).all()

def test_rsi_matches_chart_indicators():
    """RSI from indicators.py must match chart_indicators.compute_rsi() value within 0.5."""
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import indicators
    import pandas_ta as ta
    df = _make_df(200)
    our_rsi = indicators.rsi_series(df["close"], length=14).iloc[-1]
    pta_rsi = ta.rsi(df["close"], length=14).iloc[-1]
    assert abs(our_rsi - pta_rsi) < 0.5
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
python3 -m pytest tests/test_indicators.py -v
```
Expected: `ModuleNotFoundError: No module named 'indicators'`

- [ ] **Step 3: Create indicators.py**

```python
"""
indicators.py — Canonical indicator series functions.

Single source of truth for raw indicator math.
Both backtest_engine.py and chart_indicators.py must use these.
Returns pd.Series (not dicts) — callers decide how to slice/label.
"""
import numpy as np
import pandas as pd
import pandas_ta as ta


def rsi_series(close: pd.Series, length: int = 14) -> pd.Series:
    """RSI via pandas_ta — matches chart_indicators.compute_rsi()."""
    result = ta.rsi(close, length=length)
    if result is None:
        return pd.Series(np.nan, index=close.index)
    return result


def wavetrend_series(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    n1: int = 10,
    n2: int = 21,
    roll: int = 4,
) -> tuple[pd.Series, pd.Series]:
    """
    WaveTrend oscillator (VMC Cipher A/B).
    n1=10, n2=21, roll=4 — must match chart_indicators.py constants.
    Returns (wt1, wt2).
    """
    hlc3 = (high + low + close) / 3
    ema1 = hlc3.ewm(span=n1, adjust=False).mean()
    d    = (hlc3 - ema1).abs().ewm(span=n1, adjust=False).mean()
    ci   = (hlc3 - ema1) / (0.015 * d.replace(0, np.nan)).fillna(1)
    wt1  = ci.ewm(span=n2, adjust=False).mean()
    wt2  = wt1.rolling(roll, min_periods=1).mean()
    return wt1, wt2


def adx_series(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    length: int = 14,
) -> pd.Series:
    """
    Average Directional Index via pandas_ta.
    Returns the ADX column only (not DI+/DI-).
    """
    result = ta.adx(high, low, close, length=length)
    if result is None or result.empty:
        return pd.Series(np.nan, index=close.index)
    adx_col = [c for c in result.columns if c.startswith("ADX_")]
    if not adx_col:
        return pd.Series(np.nan, index=close.index)
    return result[adx_col[0]]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_indicators.py -v
```
Expected: all 5 tests PASS.

- [ ] **Step 5: Update backtest_engine.py to import from indicators.py**

Replace lines 1-56 (imports + `_rsi` + `_adx` functions) with:

```python
"""
backtest_engine.py — Embedded vectorized backtester for the trading journal.
Walk-forward trade simulation logic adapted from Freqtrade backtesting.py (GPL-3.0).
"""
import datetime
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import bitget_client
from backtest_metrics import sharpe_ratio, sortino_ratio, max_drawdown, profit_factor
from indicators import rsi_series, wavetrend_series, adx_series


# Confluence signal weights — mirror chart_context.py directional signals
# (SMT Divergence excluded: not available in OHLCV history)
_RSI_W = 0.5    # rsi < 40
_EMA_W = 1.0    # ema_bull (20 > 50)
_WT_W  = 0.85   # wt_buy (oversold cross)
_MFI_W = 0.3    # mfi > 10
_CVD_W = 0.4    # cvd_trend rising
_VOL_W = 0.5    # vol_ratio > 1.5x average
_CONFLUENCE_DENOM = _RSI_W + _EMA_W + _WT_W + _MFI_W + _CVD_W + _VOL_W
```

Then in `_compute_signals()`, replace the inline RSI/WaveTrend/ADX computations:

```python
def _compute_signals(df: pd.DataFrame, params: BacktestParams) -> pd.DataFrame:
    """Add indicator columns to the full OHLCV dataframe in one vectorized pass."""
    df = df.copy()
    close, high, low, volume = df["close"], df["high"], df["low"], df["volume"]

    # Use shared indicator library — identical results to live scanner
    df["rsi"]        = rsi_series(close, length=14)
    wt1, wt2         = wavetrend_series(high, low, close)
    df["wt1"]        = wt1
    df["wt2"]        = wt2
    df["adx"]        = adx_series(high, low, close, length=14).fillna(0.0)

    df["ema_20"]  = close.ewm(span=20,  adjust=False).mean()
    df["ema_50"]  = close.ewm(span=50,  adjust=False).mean()
    df["ema_200"] = close.ewm(span=200, adjust=False).mean()

    # MFI proxy (journal formula)
    hlc3     = (high + low + close) / 3
    hlc3_vol = hlc3 * volume
    df["mfi"] = (rsi_series(hlc3_vol, length=60) - 50) * 2

    # CVD proxy — Money Flow Multiplier formula matching chart_indicators.py::compute_cvd()
    hl = (high - low).replace(0, np.nan)
    df["cvd"]       = (volume * (2 * close - low - high) / hl).fillna(0).cumsum()
    df["cvd_trend"] = df["cvd"] > df["cvd"].shift(20)

    df["vol_ratio"] = volume / volume.rolling(20).mean()

    df["wt_buy"]   = (df["wt1"] < params.wt_oversold) & (df["wt1"] > df["wt1"].shift(1))
    df["ema_bull"] = df["ema_20"] > df["ema_50"]

    df["confluence"] = (
        (df["rsi"] < 40).astype(float) * _RSI_W
        + df["ema_bull"].astype(float) * _EMA_W
        + df["wt_buy"].astype(float) * _WT_W
        + (df["mfi"] > 10).astype(float) * _MFI_W
        + df["cvd_trend"].astype(float) * _CVD_W
        + (df["vol_ratio"] > 1.5).astype(float) * _VOL_W
    ) / _CONFLUENCE_DENOM

    df["entry_signal"] = (
        df["wt_buy"]
        & (df["rsi"] < params.rsi_max)
        & (df["adx"] >= params.adx_min)
        & (df["confluence"] >= params.min_confluence)
    )

    return df
```

- [ ] **Step 6: Run existing backtest tests to confirm no regression**

```bash
python3 -m pytest tests/test_backtest_engine.py tests/test_backtest_calculations.py -v
```
Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add indicators.py backtest_engine.py tests/test_indicators.py
git commit -m "feat: shared indicator library — backtest and scanner now use identical RSI/WT/ADX"
```

---

## Task 2: Dynamic Liquidity-Filtered Watchlist

**Problem:** The static watchlist includes coins that have gone illiquid. The `_get_extended_watchlist()` function already filters by volume but has no OI filter and no daily cache — it refetches Binance on every scan.

**Fix:** Add OI-based filter to `get_binance_futures_symbols()` in `ccxt_client.py`, and add a 24h TTL cache in `scanner_watchlist.py` so the dynamic list refreshes once per day instead of per scan.

**Files:**
- Modify: `ccxt_client.py` — add `get_binance_oi_map()` helper
- Modify: `scanner_watchlist.py` — add 24h cache, OI filter in `_get_extended_watchlist()`
- Create: `tests/test_watchlist_dynamic.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_watchlist_dynamic.py
import time
import unittest.mock as mock

def test_cache_is_used_on_second_call(monkeypatch):
    """Second call within TTL must not refetch Binance."""
    import scanner_watchlist as wl
    # Reset cache state
    wl._dynamic_cache["symbols"] = None
    wl._dynamic_cache["ts"] = 0.0

    call_count = {"n": 0}
    def fake_fetch(*a, **kw):
        call_count["n"] += 1
        return ["BTCUSDT", "ETHUSDT"]

    monkeypatch.setattr("ccxt_client.get_binance_futures_symbols", fake_fetch)
    wl._get_dynamic_watchlist()
    wl._get_dynamic_watchlist()
    assert call_count["n"] == 1  # only fetched once

def test_cache_expires_after_ttl(monkeypatch):
    import scanner_watchlist as wl
    wl._dynamic_cache["symbols"] = ["BTCUSDT"]
    wl._dynamic_cache["ts"] = time.time() - wl._DYNAMIC_TTL - 1  # expired

    fetched = {"done": False}
    def fake_fetch(*a, **kw):
        fetched["done"] = True
        return ["BTCUSDT", "ETHUSDT"]

    monkeypatch.setattr("ccxt_client.get_binance_futures_symbols", fake_fetch)
    result = wl._get_dynamic_watchlist()
    assert fetched["done"]
    assert "ETHUSDT" in result

def test_oi_filter_excludes_low_oi(monkeypatch):
    """Symbols below OI threshold must not appear in the dynamic list."""
    import scanner_watchlist as wl
    wl._dynamic_cache["symbols"] = None
    wl._dynamic_cache["ts"] = 0.0

    monkeypatch.setattr("ccxt_client.get_binance_futures_symbols",
                        lambda **kw: ["BTCUSDT", "LOWUSDT"])
    monkeypatch.setattr("ccxt_client.get_binance_oi_map",
                        lambda syms: {"BTCUSDT": 500_000_000, "LOWUSDT": 100_000})

    result = wl._get_dynamic_watchlist(min_oi_usd=1_000_000)
    assert "BTCUSDT" in result
    assert "LOWUSDT" not in result
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_watchlist_dynamic.py -v
```
Expected: `AttributeError: module 'scanner_watchlist' has no attribute '_dynamic_cache'`

- [ ] **Step 3: Add get_binance_oi_map() to ccxt_client.py**

Add after the `get_binance_futures_symbols()` function (after line 162):

```python
def get_binance_oi_map(symbols: list) -> dict:
    """
    Return {symbol: open_interest_usd} for a list of USDT-M symbols.
    Uses Binance futures open interest endpoint (public, no auth).
    Returns empty dict on any error.
    symbol format: 'BTCUSDT' (not 'BTC/USDT:USDT').
    """
    try:
        import ccxt as _ccxt
        futures_ex = _ccxt.binance({
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        })
        target = set(symbols)
        result = {}
        # fetch_tickers includes openInterestValue on futures
        tickers = futures_ex.fetch_tickers()
        for sym, t in tickers.items():
            if not sym.endswith("/USDT:USDT"):
                continue
            journal_sym = sym.replace("/USDT:USDT", "USDT")
            if journal_sym not in target:
                continue
            oi = t.get("info", {}).get("openInterestValue") or t.get("openInterestValue")
            if oi is not None:
                try:
                    result[journal_sym] = float(oi)
                except (TypeError, ValueError):
                    pass
        return result
    except Exception:
        return {}
```

- [ ] **Step 4: Update scanner_watchlist.py**

Replace the entire file content:

```python
"""
scanner_watchlist.py — Watchlist symbols for the setup scanner.

Provides:
- _get_default_watchlist(): static Bitget list merged with Binance (unchanged, lazy)
- _get_dynamic_watchlist(): Binance USDT-M filtered by 24h volume + OI, cached 24h
- _get_extended_watchlist(): volume-filtered merge (used by scanner when dynamic fails)
"""
import time

_BITGET_WATCHLIST = [
    # BTC / ETH
    "BTCUSDT", "ETHUSDT",
    # Major L1s
    "SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT",
    "DOTUSDT", "ATOMUSDT", "NEARUSDT", "TRXUSDT", "XLMUSDT",
    "TONUSDT", "FTMUSDT", "ALGOUSDT", "EGLDUSDT",
    # Mid-cap L1s
    "SUIUSDT", "APTUSDT", "INJUSDT", "SEIUSDT", "ICPUSDT",
    "STXUSDT", "TIAUSDT", "HBARUSDT", "KASUSDT", "MINAUSDT",
    # L2 / ETH ecosystem
    "MATICUSDT", "ARBUSDT", "OPUSDT", "STRKUSDT", "LDOUSDT",
    "ZKUSDT", "METISUSDT", "ENSUSDT",
    # DeFi
    "UNIUSDT", "AAVEUSDT", "LINKUSDT", "CRVUSDT", "MKRUSDT",
    "SNXUSDT", "COMPUSDT", "DYDXUSDT", "CAKEUSDT", "GMXUSDT",
    "PENDLEUSDT", "JUPUSDT", "SUSHIUSDT", "RUNEUSDT",
    # AI / Infra
    "FETUSDT", "RENDERUSDT", "WLDUSDT", "TAOUSDT", "GRTUSDT",
    "AGIXUSDT", "OCEANUSDT", "ARKMUSDT", "ACTUSDT",
    # Meme
    "DOGEUSDT", "SHIBUSDT", "PEPEUSDT", "WIFUSDT", "BONKUSDT",
    "BOMEUSDT", "FLOKIUSDT", "MOGUSDT", "POPCATUSDT", "MEWUSDT",
    "TURBOUSDT",
    # BTC ecosystem
    "ORDIUSDT", "SATSUSDT",
    # Gaming / Metaverse
    "SANDUSDT", "AXSUSDT", "GALAUSDT", "IMXUSDT", "MANAUSDT",
    "APEUSDT", "YGGUSDT",
    # Solana ecosystem
    "JITOUSDT", "WUSDT", "PYTHUSDT", "RAYUSDT",
    # Other liquid
    "LTCUSDT", "BCHUSDT", "FILUSDT", "QNTUSDT", "VETUSDT",
    "OKBUSDT", "ONDOUSDT", "ZECUSDT", "ONEUSDT", "ROSAUSDT",
    "CELOUSDT", "THETAUSDT", "NEOUSDT", "ONTUSDT", "IOTAUSDT",
    "WOOUSDT", "KLAYUSDT", "GMTUSDT",
]

DEFAULT_WATCHLIST = _BITGET_WATCHLIST  # backward compat

# ── Lazy-loaded Binance list (unchanged behaviour) ────────────────────────────
BINANCE_WATCHLIST: list = []
_binance_watchlist_loaded = False


def _get_default_watchlist() -> list:
    """Return merged Bitget+Binance watchlist, fetching Binance on first call."""
    global BINANCE_WATCHLIST, _binance_watchlist_loaded
    if not _binance_watchlist_loaded:
        _binance_watchlist_loaded = True
        try:
            import ccxt_client as _ccxt
            BINANCE_WATCHLIST = _ccxt.get_binance_futures_symbols()
        except Exception:
            BINANCE_WATCHLIST = []
    return list(dict.fromkeys(
        _BITGET_WATCHLIST + [s for s in BINANCE_WATCHLIST if s not in set(_BITGET_WATCHLIST)]
    ))


# ── Dynamic watchlist: volume + OI filtered, cached 24h ──────────────────────
_DYNAMIC_TTL = 24 * 3600  # 24 hours in seconds
_dynamic_cache: dict = {"symbols": None, "ts": 0.0}


def _get_dynamic_watchlist(
    max_symbols: int = 330,
    min_vol_usd: float = 5_000_000,
    min_oi_usd: float = 2_000_000,
) -> list:
    """
    Return up to max_symbols liquid USDT-M symbols, refreshed every 24h.
    Filters: 24h volume >= min_vol_usd AND open interest >= min_oi_usd.
    Falls back to _get_extended_watchlist() on any API error.
    """
    now = time.time()
    if _dynamic_cache["symbols"] is not None and (now - _dynamic_cache["ts"]) < _DYNAMIC_TTL:
        return _dynamic_cache["symbols"]

    try:
        import ccxt_client
        volume_syms = ccxt_client.get_binance_futures_symbols(min_vol_usd=min_vol_usd)
        if not volume_syms:
            raise RuntimeError("Binance volume fetch returned empty")

        oi_map = ccxt_client.get_binance_oi_map(volume_syms)

        # Filter by OI; symbols missing from OI map are kept (OI fetch is best-effort)
        filtered = [
            s for s in volume_syms
            if oi_map.get(s, min_oi_usd) >= min_oi_usd
        ]

        # Ensure hand-picked Bitget list is always included
        bitget_set = set(_BITGET_WATCHLIST)
        extra = [s for s in filtered if s not in bitget_set]
        merged = list(dict.fromkeys(_BITGET_WATCHLIST + extra))[:max_symbols]

        _dynamic_cache["symbols"] = merged
        _dynamic_cache["ts"] = now
        print(
            f"[Watchlist] Dynamic: {len(merged)} symbols "
            f"(vol>${min_vol_usd/1e6:.0f}M + OI>${min_oi_usd/1e6:.0f}M)",
            flush=True,
        )
        return merged
    except Exception as e:
        print(f"[Watchlist] Dynamic fetch failed: {e} — using extended static list", flush=True)
        return _get_extended_watchlist(max_symbols=max_symbols, min_vol_usd=min_vol_usd)


def _get_extended_watchlist(max_symbols: int = 500, min_vol_usd: float = 3_000_000) -> list:
    """
    Return up to max_symbols USDT futures sorted by liquidity (volume only).
    Falls back to _get_default_watchlist() on any error.
    """
    try:
        import ccxt_client
        binance_syms = ccxt_client.get_binance_futures_symbols(min_vol_usd=min_vol_usd)
        if not binance_syms:
            raise RuntimeError("Binance returned empty list")
        bitget_set = set(_BITGET_WATCHLIST)
        extra      = [s for s in binance_syms if s not in bitget_set]
        merged     = list(dict.fromkeys(_BITGET_WATCHLIST + extra))[:max_symbols]
        print(
            f"[Watchlist] {len(merged)} symbols "
            f"(Bitget manual {len(_BITGET_WATCHLIST)} + Binance {len(extra)} extras, "
            f"vol>${min_vol_usd/1e6:.0f}M)",
            flush=True,
        )
        return merged
    except Exception as e:
        print(f"[Watchlist] Extended fetch failed: {e} — using default list")
        return _get_default_watchlist()
```

- [ ] **Step 5: Update ai_scanner.py to use _get_dynamic_watchlist() as default**

Find the line that calls `_get_default_watchlist()` or `_get_extended_watchlist()` in `ai_scanner.py` (around the watchlist resolution in the scan thread). Replace with `_get_dynamic_watchlist()`:

```python
# In ai_scanner.py, inside the scan function where watchlist is built:
# Replace:
#   symbols = symbols or _get_extended_watchlist()
# With:
from scanner_watchlist import _get_dynamic_watchlist, _get_extended_watchlist
symbols = symbols or _get_dynamic_watchlist()
```

- [ ] **Step 6: Run the tests**

```bash
python3 -m pytest tests/test_watchlist_dynamic.py tests/test_scanner_lazy_init.py -v
```
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add ccxt_client.py scanner_watchlist.py ai_scanner.py tests/test_watchlist_dynamic.py
git commit -m "feat: dynamic watchlist with 24h cache — volume + OI filter via Binance"
```

---

## Task 3: Auto-Match 'Saved' Calls to Closed Positions

**Problem:** `auto_close_calls()` in `sync_base.py` only resolves 'matched' calls (where the user manually linked the call to a live position). 'Saved' calls with no position link are only resolved retroactively via candle data (`retroactive_close_calls()`), not via the actual closed position record. When a position syncs in, we should automatically look for a 'saved' call for the same symbol/direction created recently.

**Fix:** Add `auto_match_calls(conn)` to `sync_base.py`. After `_sync_positions()` in `bitget_sync.py`, call it. It searches 'saved' calls within a 30-day window for the same symbol/direction, sets `call_id` on the position, and promotes the call to 'matched' so `auto_close_calls()` can resolve it next.

**Files:**
- Modify: `sync_base.py` — add `auto_match_calls(conn, exchange)`
- Modify: `bitget_sync.py:354-357` — call `auto_match_calls(conn)` after `_sync_positions(conn)`
- Create: `tests/test_auto_match.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auto_match.py
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

@pytest.fixture
def db_with_data(tmp_path, monkeypatch):
    import database as _db
    db_file = str(tmp_path / "test.db")
    monkeypatch.setattr(_db, "DB_PATH", db_file)
    _db.init_db()
    conn = _db.get_conn()
    yield conn
    conn.close()


def _insert_call(conn, symbol, direction, created_at, status="saved",
                 entry=0.04, sl=0.038, tp1=0.045):
    conn.execute("""
        INSERT INTO analyzed_calls
          (symbol, direction, entry_price, sl_price, tp1_price, status, created_at)
        VALUES (?,?,?,?,?,?,?)
    """, (symbol, direction, entry, sl, tp1, status, created_at))
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_position(conn, symbol, direction, open_time, close_time,
                     entry=0.041, close=0.046, pnl=10.0):
    conn.execute("""
        INSERT INTO positions
          (symbol, base_asset, direction, open_time, close_time,
           entry_price, close_price, realized_pnl, exchange)
        VALUES (?,?,?,?,?,?,?,?,'bitget')
    """, (symbol, symbol.replace("USDT",""), direction, open_time, close_time,
          entry, close, pnl))
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_saved_call_gets_matched(db_with_data):
    from sync_base import auto_match_calls
    conn = db_with_data
    call_id = _insert_call(conn, "CHZUSDT", "Long", "2026-05-01 10:00:00")
    pos_id  = _insert_position(conn, "CHZUSDT", "Long",
                                "2026-05-01 10:30:00", "2026-05-01 14:00:00")
    matched = auto_match_calls(conn, exchange="bitget")
    assert matched == 1
    call = conn.execute("SELECT status FROM analyzed_calls WHERE id=?",
                        (call_id,)).fetchone()
    assert call[0] == "matched"
    pos = conn.execute("SELECT call_id FROM positions WHERE id=?",
                       (pos_id,)).fetchone()
    assert pos[0] == call_id


def test_already_matched_call_not_touched(db_with_data):
    from sync_base import auto_match_calls
    conn = db_with_data
    _insert_call(conn, "BTCUSDT", "Long", "2026-05-01 10:00:00", status="matched")
    _insert_position(conn, "BTCUSDT", "Long",
                     "2026-05-01 10:30:00", "2026-05-01 14:00:00")
    matched = auto_match_calls(conn)
    assert matched == 0  # already matched — don't touch


def test_wrong_direction_not_matched(db_with_data):
    from sync_base import auto_match_calls
    conn = db_with_data
    call_id = _insert_call(conn, "ETHUSDT", "Short", "2026-05-01 10:00:00")
    _insert_position(conn, "ETHUSDT", "Long",
                     "2026-05-01 10:30:00", "2026-05-01 14:00:00")
    matched = auto_match_calls(conn)
    assert matched == 0  # direction mismatch


def test_call_too_old_not_matched(db_with_data):
    from sync_base import auto_match_calls
    conn = db_with_data
    _insert_call(conn, "SOLUSDT", "Long", "2026-01-01 10:00:00")  # 4+ months ago
    _insert_position(conn, "SOLUSDT", "Long",
                     "2026-05-01 10:30:00", "2026-05-01 14:00:00")
    matched = auto_match_calls(conn)
    assert matched == 0  # too old
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_auto_match.py -v
```
Expected: `ImportError: cannot import name 'auto_match_calls' from 'sync_base'`

- [ ] **Step 3: Add auto_match_calls() to sync_base.py**

Add after the `retroactive_close_calls()` function (after line 267):

```python
def auto_match_calls(conn, exchange: str = "bitget") -> int:
    """
    For every recently-closed position with no call_id, search for a 'saved'
    analyzed_call with the same symbol + direction created within 30 days
    before the position opened. If found: set positions.call_id and promote
    the call to 'matched' so auto_close_calls() resolves it next cycle.

    Only touches positions inserted in the last 7 days (recent sync window).
    Safe to call repeatedly — idempotent (skips already-linked positions).
    Returns number of positions newly linked.
    """
    cur = conn.cursor()

    # Recent positions with no call link, on this exchange
    positions = cur.execute("""
        SELECT id, symbol, direction, open_time
        FROM positions
        WHERE call_id IS NULL
          AND COALESCE(exchange, 'bitget') = ?
          AND close_time >= datetime('now', '-7 days')
        ORDER BY close_time DESC
    """, (exchange,)).fetchall()

    matched = 0
    for pos_id, symbol, direction, open_time in positions:
        # Normalize direction: 'Long'/'Short' matches analyzed_calls direction field
        dir_filter = "Long" if "long" in (direction or "").lower() else "Short"

        # Find the most recent 'saved' call for this symbol+direction
        # created within 30 days before the position opened
        call = cur.execute("""
            SELECT id
            FROM analyzed_calls
            WHERE symbol    = ?
              AND direction LIKE ?
              AND status    = 'saved'
              AND entry_price IS NOT NULL
              AND sl_price    IS NOT NULL
              AND created_at >= datetime(?, '-30 days')
              AND created_at <= ?
            ORDER BY created_at DESC
            LIMIT 1
        """, (symbol, dir_filter + "%", open_time or "9999", open_time or "9999")).fetchone()

        if not call:
            continue

        call_id = call[0]
        cur.execute("UPDATE positions SET call_id=? WHERE id=?", (call_id, pos_id))
        cur.execute("UPDATE analyzed_calls SET status='matched' WHERE id=?", (call_id,))
        matched += 1
        print(f"[Sync] Auto-matched call #{call_id} → position #{pos_id} ({symbol} {dir_filter})",
              flush=True)

    conn.commit()
    return matched
```

- [ ] **Step 4: Call auto_match_calls() in bitget_sync.run_sync()**

In `bitget_sync.py`, in `run_sync()`, add after the existing `auto_close_calls(conn)` call:

```python
        # Positions: cursor-based — sees all recently closed trades regardless of open time
        n_pos    = _sync_positions(conn)
        # Auto-match unlinked 'saved' calls to newly synced positions
        try:
            from sync_base import auto_match_calls as _auto_match
            n_matched = _auto_match(conn, exchange="bitget")
            if n_matched:
                print(f"[Sync] Auto-matched {n_matched} calls to positions", flush=True)
        except Exception as e:
            print(f"[Sync] auto_match failed (non-fatal): {e}", flush=True)
        # Auto-close any matched calls whose position has now synced
        n_closed = auto_close_calls(conn)
```

- [ ] **Step 5: Run tests**

```bash
python3 -m pytest tests/test_auto_match.py tests/test_sync_base.py -v
```
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add sync_base.py bitget_sync.py tests/test_auto_match.py
git commit -m "feat: auto-match saved calls to closed positions on sync"
```

---

## Task 4: API-Based Historical Backfill

**Problem:** Adding trade history requires manually downloading a CSV from Bitget and uploading it. The Bitget API already supports position history. The `_sync_positions()` function uses cursor pagination with `max_pages=3`. A backfill just needs `max_pages=50` to walk back further.

**Fix:** Add `POST /api/sync/backfill` that calls `get_recent_positions(max_pages=50)` and pipes it through the existing `_sync_positions()` logic via a new helper that accepts explicit rows. Add a "Backfill from Exchange" button in the Settings tab.

**Files:**
- Modify: `bitget_sync.py` — add `run_backfill()` function
- Modify: `routes/sync.py` — add `POST /api/sync/backfill` route
- Create: `tests/test_backfill_route.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backfill_route.py
import sys, os, types, unittest.mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

@pytest.fixture
def client(tmp_path, monkeypatch):
    import database as _db
    db_file = str(tmp_path / "test_bf.db")
    monkeypatch.setattr(_db, "DB_PATH", db_file)
    _db.init_db()

    # Evict Flask stub, load real Flask
    for mod in [k for k in sys.modules if k == "flask" or k.startswith("flask.")]:
        del sys.modules[mod]
    import flask, helpers, importlib
    importlib.reload(helpers)

    import routes.sync as rs
    importlib.reload(rs)
    app = flask.Flask(__name__)
    app.register_blueprint(rs.bp)
    return app.test_client()


def test_backfill_returns_ok(client, monkeypatch):
    import bitget_sync
    monkeypatch.setattr(bitget_sync, "run_backfill", lambda: {"inserted": 3, "pages": 5})
    resp = client.post("/api/sync/backfill")
    data = resp.get_json()
    assert data["ok"] is True
    assert data["data"]["inserted"] == 3


def test_backfill_error_returns_err(client, monkeypatch):
    import bitget_sync
    def _raise(): raise RuntimeError("API timeout")
    monkeypatch.setattr(bitget_sync, "run_backfill", _raise)
    resp = client.post("/api/sync/backfill")
    data = resp.get_json()
    assert data["ok"] is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_backfill_route.py -v
```
Expected: `404 NOT FOUND` or attribute error.

- [ ] **Step 3: Add run_backfill() to bitget_sync.py**

Add after `run_sync()` (after the closing of that function):

```python
def run_backfill(max_pages: int = 50) -> dict:
    """
    Fetch up to max_pages*100 historical positions from Bitget and insert
    any not already in the DB. Uses the same dedup logic as run_sync().
    Returns {"inserted": N, "pages": max_pages}.
    Runs synchronously (blocking) — call from a background thread if needed.
    """
    print(f"[Backfill] Fetching up to {max_pages * 100} positions from Bitget...", flush=True)
    try:
        rows = bc.get_recent_positions(max_pages=max_pages)
    except Exception as e:
        raise RuntimeError(f"Bitget API error: {e}") from e

    if not rows:
        return {"inserted": 0, "pages": max_pages}

    conn = get_conn()
    try:
        _ensure_settings_table(conn)
        # Temporarily override the positions list for _sync_positions logic:
        # We monkey-patch bc.get_recent_positions to return our prefetched rows
        # rather than making another API call.
        import bitget_client as _bc_mod
        original = _bc_mod.get_recent_positions
        _bc_mod.get_recent_positions = lambda max_pages=3: rows
        try:
            inserted = _sync_positions(conn)
        finally:
            _bc_mod.get_recent_positions = original

        print(f"[Backfill] Done — {inserted} new positions inserted from {len(rows)} fetched",
              flush=True)
        return {"inserted": inserted, "pages": max_pages, "fetched": len(rows)}
    finally:
        conn.close()
```

- [ ] **Step 4: Add the route to routes/sync.py**

In `routes/sync.py`, add after the existing `POST /api/sync` route:

```python
@bp.route("/api/sync/backfill", methods=["POST"])
def api_sync_backfill():
    """
    POST /api/sync/backfill
    Fetch up to 5000 historical positions from Bitget API and insert any missing.
    Runs synchronously (may take 10-30s). Intended for one-time use.
    """
    try:
        import bitget_sync
        result = bitget_sync.run_backfill(max_pages=50)
        return _ok(result)
    except Exception as e:
        traceback.print_exc()
        return _err(f"Backfill failed: {str(e)[:100]}", 500)
```

- [ ] **Step 5: Add "Backfill from Exchange" button to Settings tab**

In `static/js/16-settings.js`, find the Settings tab HTML rendering. Add a backfill button section. Look for the existing sync status button and add after it:

```javascript
// In renderSettings() or the settings HTML section, add:
const backfillBtn = document.createElement('button');
backfillBtn.className = 'btn btn-secondary';
backfillBtn.textContent = 'Backfill from Exchange (last 5000 trades)';
backfillBtn.onclick = async () => {
    backfillBtn.disabled = true;
    backfillBtn.textContent = 'Backfilling...';
    try {
        const r = await fetch('/api/sync/backfill', { method: 'POST' });
        const d = await r.json();
        if (d.ok) {
            notify(`Backfill complete: ${d.data.inserted} new trades inserted`, 'success');
        } else {
            notify(d.error || 'Backfill failed', 'error');
        }
    } catch (e) {
        notify('Backfill request failed', 'error');
    } finally {
        backfillBtn.disabled = false;
        backfillBtn.textContent = 'Backfill from Exchange (last 5000 trades)';
    }
};
```

Bump the `?v=` cache-buster in `templates/index.html` for `16-settings.js`.

- [ ] **Step 6: Run tests**

```bash
python3 -m pytest tests/test_backfill_route.py -v
```
Expected: both tests PASS.

- [ ] **Step 7: Commit**

```bash
git add bitget_sync.py routes/sync.py static/js/16-settings.js templates/index.html tests/test_backfill_route.py
git commit -m "feat: API-based historical backfill — POST /api/sync/backfill fetches 5000 positions"
```

---

## Final Checks

```bash
python3 -m pytest tests/ -v --tb=short -q
```
Expected: all existing tests pass, 4 new test files pass.
