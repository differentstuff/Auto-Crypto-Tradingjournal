# Spec B: Freqtrade Backtesting — Design Spec
*Date: 2026-05-14 · Status: Approved · Covers: F4, F5*

---

## Overview

A standalone Freqtrade strategy that translates the journal's confluence scoring logic into `IStrategy`, enabling historical backtesting and Hyperopt-driven threshold calibration. Runs locally on Mac — not deployed to Pi. No changes to the journal codebase.

---

## Directory Structure

```
freqtrade/                           (new subdirectory in journal repo)
├── strategies/
│   └── JournalConfluenceStrategy.py  [F4 + F5]
├── config-backtest.json               [F4]
├── user_data/
│   └── data/                          (downloaded OHLCV data)
└── README.md                          (setup instructions)
```

---

## F4 — JournalConfluenceStrategy

### Core design

`JournalConfluenceStrategy(IStrategy)` reimplements the journal's 7-signal confluence scoring as native Freqtrade indicators + entry/exit conditions. Uses `pandas-ta` (bundled with Freqtrade) for RSI/EMA/ADX. WaveTrend computed manually (same formula as `chart_indicators.py`).

### `populate_indicators`

Adds these columns to the OHLCV dataframe:

| Column | Computation |
|--------|-------------|
| `rsi` | `ta.rsi(close, 14)` |
| `ema_20`, `ema_50`, `ema_200` | EMA periods |
| `adx` | `ta.adx(high, low, close, 14)['ADX_14']` |
| `wt1`, `wt2` | WaveTrend oscillator (see below) |
| `wt_signal` | `'buy'` when `wt1 < wt_oversold AND wt1 > wt1.shift(1)`, `'sell'` when `wt1 > 53 AND wt1 < wt1.shift(1)` |
| `mfi` | `(RSI(HLC3 * volume, 60) - 50) * 2` — same formula as journal |
| `cvd` | Cumulative delta proxy: `(close - open) / (high - low + 1e-9) * volume` rolling sum |
| `vol_ratio` | `volume / volume.rolling(20).mean()` |
| `confluence_bull` | Sum of all bullish signal weights (see weights table below) |

**WaveTrend formula** (from `chart_indicators.py`):
```python
hlc3 = (high + low + close) / 3
ema1 = hlc3.ewm(span=9).mean()
d    = (hlc3 - ema1).abs().ewm(span=9).mean()
ci   = (hlc3 - ema1) / (0.015 * d)
wt1  = ci.ewm(span=13).mean()
wt2  = wt1.rolling(3).mean()
```

**Signal weights** (mirrors `chart_context.py`):
| Signal | Long weight |
|--------|------------|
| RSI < 40 | +0.5 to +1.0 |
| RSI < 35 | +1.0 |
| MACD bullish + growing | +1.0 |
| EMA fully bullish stack | +1.0 |
| ADX direction × strength | +(ADX/50) |
| WaveTrend buy signal | +0.85 |
| MFI > 10 | +0.3 |
| CVD rising | +0.4 |
| Volume > 1.5× | +0.5 |

### `populate_entry_trend`

```python
dataframe.loc[
    (dataframe['wt_signal'] == 'buy') &           # WT oversold cross
    (dataframe['rsi'] < self.rsi_entry_max.value) &  # RSI not extended
    (dataframe['adx'] >= self.adx_min.value) &    # trending
    (dataframe['ema_20'] > dataframe['ema_50']) &  # EMA bullish stack
    (dataframe['confluence_bull'] / 6.2 >= self.confluence_pct.value),
    'enter_long'
] = 1
```

### `populate_exit_trend`

```python
dataframe.loc[
    (dataframe['wt_signal'] == 'sell') |
    (dataframe['rsi'] > 72),
    'exit_long'
] = 1
```

### Fixed parameters (pre-Hyperopt)

```python
timeframe   = '4h'
stoploss    = -0.10
minimal_roi = {"0": 0.05, "60": 0.03, "120": 0.01}
```

---

## F5 — Hyperopt Parameters

Added to the same `JournalConfluenceStrategy.py`:

```python
from freqtrade.strategy import IntParameter, DecimalParameter

# Entry thresholds
wt_oversold_level = IntParameter(-80, -40, default=-53, space="buy")
rsi_entry_max     = IntParameter(50, 70, default=65, space="buy")
adx_min           = IntParameter(10, 25, default=15, space="buy")
confluence_pct    = DecimalParameter(0.25, 0.55, decimals=2, default=0.33, space="buy")

# Exit thresholds
rsi_exit_min      = IntParameter(60, 85, default=72, space="sell")

# Risk parameters
stoploss          = DecimalParameter(-0.20, -0.05, decimals=3, default=-0.10, space="stoploss")
```

### Hyperopt commands

```bash
# Download data first (run from journal repo root)
freqtrade download-data \
  --config freqtrade/config-backtest.json \
  --pairs BTC/USDT ETH/USDT SOL/USDT BNB/USDT AAVE/USDT \
  --timeframes 4h --days 730

# Backtest first (validate strategy runs)
freqtrade backtesting \
  --strategy JournalConfluenceStrategy \
  --config freqtrade/config-backtest.json \
  --timerange 20240101-20260101 \
  --export trades

# Hyperopt
freqtrade hyperopt \
  --strategy JournalConfluenceStrategy \
  --config freqtrade/config-backtest.json \
  --hyperopt-loss SharpeHyperOptLoss \
  --spaces buy sell stoploss roi \
  --epochs 300 \
  -j 4
```

### How to apply Hyperopt results

Hyperopt outputs optimized values. Copy them into the strategy as class-level overrides:

```python
# Paste after hyperopt completes:
buy_params  = {'confluence_pct': 0.41, 'rsi_entry_max': 58, 'adx_min': 18, 'wt_oversold_level': -61}
sell_params = {'rsi_exit_min': 68}
stoploss    = -0.147
minimal_roi = {"0": 0.072, "60": 0.038, "180": 0.012}
```

These values feed back into `prompt_fragments.py` and `chart_context.py` scoring thresholds in v1.4.

---

## `config-backtest.json`

```json
{
  "max_open_trades": 3,
  "stake_currency": "USDT",
  "stake_amount": 100,
  "dry_run": true,
  "exchange": {
    "name": "binance",
    "key": "",
    "secret": ""
  },
  "strategy_path": "freqtrade/strategies/",
  "timeframe": "4h",
  "pairlists": [{"method": "StaticPairList"}],
  "pair_whitelist": [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT",
    "AAVE/USDT", "LINK/USDT", "INJ/USDT", "AVAX/USDT"
  ]
}
```

---

## Files Created

| File | Purpose |
|------|---------|
| `freqtrade/strategies/JournalConfluenceStrategy.py` | IStrategy with WaveTrend + confluence signals + Hyperopt params |
| `freqtrade/config-backtest.json` | Backtesting config (Binance public data, no auth) |
| `freqtrade/README.md` | Setup: `pip install freqtrade`, download-data, backtest, hyperopt commands |

No journal files changed. No Pi deployment.

---

## Testing

Manual verification only (Freqtrade has its own internal test framework):

1. `freqtrade backtesting --strategy JournalConfluenceStrategy --config freqtrade/config-backtest.json` — verify runs without error, produces trades
2. Check `Exit reason` table — `stop_loss` exits < 40% of total (validates SL is not triggering constantly)
3. Verify `Profit factor ≥ 1.2` on the backtest period (baseline edge exists)
4. After Hyperopt: re-run backtest with best params, verify Sharpe improves vs baseline
