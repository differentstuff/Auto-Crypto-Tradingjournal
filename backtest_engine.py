"""
backtest_engine.py — Embedded vectorized backtester for the trading journal.
Walk-forward trade simulation logic adapted from Freqtrade backtesting.py (GPL-3.0).
"""
import datetime
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from backtest_metrics import sharpe_ratio, sortino_ratio, max_drawdown, profit_factor


def _rsi(series: pd.Series, length: int = 14) -> pd.Series:
    """Wilder-smoothed RSI — mirrors pandas_ta.rsi()."""
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    avg_g = gain.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    avg_l = loss.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    rs    = avg_g / avg_l.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    """Average Directional Index — mirrors pandas_ta.adx()['ADX_<length>']."""
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    dm_plus  = np.where((high - high.shift(1)) > (low.shift(1) - low), (high - high.shift(1)).clip(lower=0), 0)
    dm_minus = np.where((low.shift(1) - low) > (high - high.shift(1)), (low.shift(1) - low).clip(lower=0), 0)

    dm_plus  = pd.Series(dm_plus,  index=high.index)
    dm_minus = pd.Series(dm_minus, index=high.index)

    atr   = tr.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    di_p  = 100 * dm_plus.ewm(alpha=1 / length, min_periods=length, adjust=False).mean() / atr.replace(0, np.nan)
    di_m  = 100 * dm_minus.ewm(alpha=1 / length, min_periods=length, adjust=False).mean() / atr.replace(0, np.nan)
    dx    = 100 * (di_p - di_m).abs() / (di_p + di_m).replace(0, np.nan)
    return dx.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()


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


def _fetch_ohlcv(symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    """
    Fetch OHLCV data from Bitget with pagination (API caps at 200 per call).
    Walks backwards using endTime cursor until limit candles are collected.
    """
    import bitget_client
    MAX_PER_CALL = 200
    sym = symbol.upper()
    if not sym.endswith("USDT"):
        sym += "USDT"

    frames = []
    end_time = None
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

    df["rsi"] = _rsi(close, length=14)

    df["ema_20"]  = close.ewm(span=20,  adjust=False).mean()
    df["ema_50"]  = close.ewm(span=50,  adjust=False).mean()
    df["ema_200"] = close.ewm(span=200, adjust=False).mean()

    df["adx"] = _adx(high, low, close, length=14).fillna(0.0)

    # WaveTrend — same formula as chart_indicators.py
    hlc3 = (high + low + close) / 3
    ema1 = hlc3.ewm(span=9, adjust=False).mean()
    d    = (hlc3 - ema1).abs().ewm(span=9, adjust=False).mean()
    ci   = (hlc3 - ema1) / (0.015 * d.replace(0, np.nan)).fillna(1)
    df["wt1"] = ci.ewm(span=13, adjust=False).mean()
    df["wt2"] = df["wt1"].rolling(3).mean()

    # MFI proxy (journal formula)
    hlc3_vol = hlc3 * volume
    df["mfi"] = (_rsi(hlc3_vol, length=60) - 50) * 2

    # CVD proxy
    df["cvd"]       = ((close - df["open"]) / (high - low + 1e-9) * volume).cumsum()
    df["cvd_trend"] = df["cvd"] > df["cvd"].shift(20)

    df["vol_ratio"] = volume / volume.rolling(20).mean()

    df["wt_buy"]   = (df["wt1"] < params.wt_oversold) & (df["wt1"] > df["wt1"].shift(1))
    df["ema_bull"] = df["ema_20"] > df["ema_50"]

    # Simplified confluence fraction (weights mirror chart_context.py)
    df["confluence"] = (
        (df["rsi"] < 40).astype(float) * 0.5
        + df["ema_bull"].astype(float) * 1.0
        + df["wt_buy"].astype(float) * 0.85
        + (df["mfi"] > 10).astype(float) * 0.3
        + df["cvd_trend"].astype(float) * 0.4
        + (df["vol_ratio"] > 1.5).astype(float) * 0.5
    ) / 3.55

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
            elif hi >= tp2:
                trades.append(BacktestTrade(entry, tp2, "tp2", (tp2 - entry) / entry, entry_time, exit_time))
                i = j
                exited = True
                break
            elif hi >= tp1:
                trades.append(BacktestTrade(entry, tp1, "tp1", (tp1 - entry) / entry, entry_time, exit_time))
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


def run_backtest(symbol: str, timeframe: str, days: int,
                 params: BacktestParams = None) -> BacktestResult:
    """
    Run a full vectorized backtest. Fetches ~6 candles per day + 200 warmup candles.
    Returns an empty BacktestResult if fewer than 250 rows are available.
    """
    if params is None:
        params = BacktestParams()

    candles_needed = max(int(days * 6) + 200, 500)
    df = _fetch_ohlcv(symbol, timeframe, limit=candles_needed)

    if df is None or len(df) < 250:
        return BacktestResult()

    df = _compute_signals(df, params)
    trades = _simulate_trades(df, params)
    metrics = _compute_metrics(trades)

    return BacktestResult(trades=trades, **metrics)
