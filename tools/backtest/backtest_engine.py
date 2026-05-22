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


@dataclass
class BacktestParams:
    sl_pct:         float = 0.10
    tp1_pct:        float = 0.05
    tp2_pct:        float = 0.10
    min_confluence: float = 0.33
    wt_oversold:    float = -53.0
    rsi_max:        float = 65.0
    adx_min:        float = 15.0


@dataclass
class BacktestTrade:
    entry_price: float
    exit_price:  float
    outcome:     str        # 'sl', 'tp1', 'tp2'
    pnl_pct:     float
    entry_time:  datetime.datetime
    exit_time:   datetime.datetime


@dataclass
class BacktestResult:
    trades:        list = field(default_factory=list)
    sharpe:        float = 0.0
    sortino:       float = 0.0
    max_drawdown:  float = 0.0
    profit_factor: float = 0.0
    win_rate:      float = 0.0
    total_trades:  int   = 0


def _fetch_ohlcv(symbol: str, timeframe: str, limit: int,
                 end_ms: int | None = None) -> pd.DataFrame:
    """
    Fetch OHLCV data from Bitget with pagination (API caps at 200 per call).
    Walks backwards using endTime cursor until limit candles are collected.
    end_ms: optional upper bound timestamp in milliseconds (default: now).
    """
    MAX_PER_CALL = 200
    sym = symbol.upper()
    if not sym.endswith("USDT"):
        sym += "USDT"

    frames = []
    end_time = end_ms  # None means API defaults to now
    remaining = limit

    while remaining > 0:
        params = {
            "symbol":      sym,
            "productType": "USDT-FUTURES",
            "granularity": timeframe,
            "limit":       str(min(remaining, MAX_PER_CALL)),
        }
        if end_time is not None:
            params["endTime"] = str(end_time)

        try:
            raw = bitget_client._get("/api/v2/mix/market/candles", params)
        except Exception:
            break

        if not raw or not isinstance(raw, list) or len(raw) == 0:
            break

        chunk = pd.DataFrame(raw, columns=[
            "timestamp", "open", "high", "low", "close", "volume", "quote_volume"
        ])
        for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
            chunk[col] = pd.to_numeric(chunk[col], errors="coerce")
        chunk["timestamp"] = pd.to_numeric(chunk["timestamp"])
        frames.append(chunk)

        remaining -= len(raw)
        end_time = int(chunk["timestamp"].min()) - 1  # cursor: one ms before oldest

        if len(raw) < MAX_PER_CALL:
            break

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    return df


def _compute_signals(df: pd.DataFrame, params: BacktestParams) -> pd.DataFrame:
    """Add indicator columns to the full OHLCV dataframe in one vectorized pass."""
    df = df.copy()
    close, high, low, volume = df["close"], df["high"], df["low"], df["volume"]

    df["rsi"] = rsi_series(close, length=14)

    df["ema_20"]  = close.ewm(span=20,  adjust=False).mean()
    df["ema_50"]  = close.ewm(span=50,  adjust=False).mean()
    df["ema_200"] = close.ewm(span=200, adjust=False).mean()

    df["adx"] = adx_series(high, low, close, length=14).fillna(0.0)

    # WaveTrend — same formula as chart_indicators.py
    hlc3 = (high + low + close) / 3
    df["wt1"], df["wt2"] = wavetrend_series(high, low, close)

    # MFI proxy (journal formula)
    hlc3_vol = hlc3 * volume
    df["mfi"] = (rsi_series(hlc3_vol, length=60) - 50) * 2

    # CVD proxy — Money Flow Multiplier formula matching chart_indicators.py::compute_cvd()
    hl = (high - low).replace(0, np.nan)
    df["cvd"] = (volume * (2 * close - low - high) / hl).fillna(0).cumsum()
    df["cvd_trend"] = df["cvd"] > df["cvd"].shift(20)

    df["vol_ratio"] = volume / volume.rolling(20).mean()

    df["wt_buy"]   = (df["wt1"] < params.wt_oversold) & (df["wt1"] > df["wt1"].shift(1))
    df["ema_bull"] = df["ema_20"] > df["ema_50"]

    # Simplified confluence fraction (weights mirror chart_context.py)
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


def _simulate_trades(df: pd.DataFrame, params: BacktestParams) -> list:
    """
    Walk-forward trade simulation. Finds SL/TP1/TP2 hit candle-by-candle.
    Logic pattern adapted from Freqtrade backtesting.py (GPL-3.0).
    """
    trades = []
    i = 200  # skip warmup period
    n = len(df)
    while i < n - 1:
        if not df["entry_signal"].iloc[i]:
            i += 1
            continue
        entry = float(df["close"].iloc[i])
        sl  = entry * (1 - params.sl_pct)
        tp1 = entry * (1 + params.tp1_pct)
        tp2 = entry * (1 + params.tp2_pct)
        entry_time = df["timestamp"].iloc[i] if "timestamp" in df.columns else datetime.datetime.utcnow()
        exited = False
        for j in range(i + 1, min(i + 200, n)):
            lo = float(df["low"].iloc[j])
            hi = float(df["high"].iloc[j])
            exit_time = df["timestamp"].iloc[j] if "timestamp" in df.columns else datetime.datetime.utcnow()
            if lo <= sl:
                trades.append(BacktestTrade(entry, sl,  "sl",  (sl  - entry) / entry, entry_time, exit_time))
                i = j
                exited = True
                break
            elif hi >= tp1 and hi < tp2:
                # TP1 hit but not TP2 — conservative exit at smaller target
                trades.append(BacktestTrade(entry, tp1, "tp1", (tp1 - entry) / entry, entry_time, exit_time))
                i = j
                exited = True
                break
            elif hi >= tp2:
                # Price cleared both targets in one candle — record full TP2
                trades.append(BacktestTrade(entry, tp2, "tp2", (tp2 - entry) / entry, entry_time, exit_time))
                i = j
                exited = True
                break
        if not exited:
            i += 1
    return trades


def _compute_metrics(trades: list) -> dict:
    """Compute performance metrics from a list of BacktestTrade objects."""
    if not trades:
        return {"sharpe": 0.0, "sortino": 0.0, "max_drawdown": 0.0,
                "profit_factor": 0.0, "win_rate": 0.0, "total_trades": 0}

    pnls    = [t.pnl_pct for t in trades]
    winners = [t for t in trades if t.pnl_pct > 0]

    equity = [1.0]
    for p in pnls:
        equity.append(equity[-1] * (1 + p))

    # When all returns are identical positives (std=0, all winners), use a
    # large positive sentinel so callers can detect perfect-win scenarios.
    pnl_arr = np.array(pnls, dtype=float)
    if pnl_arr.std() < 1e-10 and pnl_arr.mean() > 0:
        sharpe = float(pnl_arr.mean() * 1460 ** 0.5)
    else:
        sharpe = sharpe_ratio(pnls)

    return {
        "sharpe":        sharpe,
        "sortino":       sortino_ratio(pnls),
        "max_drawdown":  max_drawdown(equity),
        "profit_factor": profit_factor(pnls),
        "win_rate":      round(len(winners) / len(trades) * 100, 1),
        "total_trades":  len(trades),
    }


def run_backtest(symbol: str, timeframe: str = "4h", days: int = 90,
                 params: BacktestParams = None,
                 end_offset_days: int = 0) -> BacktestResult:
    """
    Run a full vectorized backtest. Fetches ~6 candles per day + 200 warmup candles.
    Returns an empty BacktestResult if fewer than 250 rows are available.

    end_offset_days: how many days before now the backtest window ENDS (default 0 = now).
                     e.g. end_offset_days=30 means the window ends 30 days ago,
                     covering [now - (end_offset_days+days)*86400s, now - end_offset_days*86400s].
    """
    if params is None:
        params = BacktestParams()

    candles_needed = max(int(days * 6) + 200, 500)
    import time as _time_mod
    now_ms = int(_time_mod.time() * 1000)
    end_ms = now_ms - end_offset_days * 86400 * 1000 if end_offset_days > 0 else None
    df = _fetch_ohlcv(symbol, timeframe, limit=candles_needed, end_ms=end_ms)

    if df is None or len(df) < 250:
        return BacktestResult()

    df = _compute_signals(df, params)
    trades = _simulate_trades(df, params)
    metrics = _compute_metrics(trades)

    return BacktestResult(trades=trades, **metrics)
