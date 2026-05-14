# Spec C: Freqtrade Dry-Run Executor — Design Spec
*Date: 2026-05-14 · Status: Approved · Covers: F6*

---

## Overview

A Freqtrade dry-run bot that polls the journal's scanner API and automatically paper-trades 8+/10 signals using the journal's exact SL/TP levels. Runs locally on Mac alongside the journal. **Dry-run only** — no real funds at risk. After 30 days of dry-run validation, a config switch enables live trading.

**Prerequisite:** F4/F5 (Spec B) must be complete and produce a validated backtested strategy.

---

## Architecture

```
Mac (local)
├── Journal Flask app (port 8082, proxies Pi via Pi address OR runs locally)
└── Freqtrade process (dry-run)
    ├── JournalSignalStrategy.py
    │   ├── populate_indicators() → polls GET /api/scanner/status every candle close
    │   ├── populate_entry_trend() → enter_long=1 if scanner has 8+/10 for this pair
    │   ├── custom_stoploss() → journal's SL price
    │   └── custom_exit() → TP1/TP2 from scanner result
    └── config-dryrun.json
        ├── dry_run: true
        ├── exchange: bitget
        └── stake_amount: 100 USDT
```

---

## JournalSignalStrategy.py

### Signal cache design

The scanner API returns all active setups at once. `populate_indicators()` is called for each pair on each candle. To avoid N API calls per candle close (N = number of pairs), the strategy caches the full scanner response for 5 minutes:

```python
import requests, time

_signal_cache: dict = {}
_signal_cache_ts: float = 0
SIGNAL_CACHE_TTL = 300  # 5 minutes

def _get_scanner_signals(journal_url: str) -> dict:
    """Returns {symbol: setup_dict} for all 8+/10 signals. Cached 5 min."""
    global _signal_cache, _signal_cache_ts
    if time.time() - _signal_cache_ts < SIGNAL_CACHE_TTL:
        return _signal_cache
    try:
        r = requests.get(f"{journal_url}/api/scanner/status", timeout=5)
        data = r.json().get('data', {})
        setups = data.get('setups', [])
        _signal_cache = {
            s['symbol']: s for s in setups
            if (s.get('setup_score') or 0) >= 8
        }
        _signal_cache_ts = time.time()
    except Exception:
        pass  # keep stale cache on error
    return _signal_cache
```

### `populate_indicators`

```python
def populate_indicators(self, dataframe, metadata):
    pair   = metadata['pair']                    # e.g. 'BTC/USDT'
    symbol = pair.replace('/', '').replace(':', '').split('USDT')[0] + 'USDT'
    signals = _get_scanner_signals(self.journal_url)
    setup   = signals.get(symbol, {})
    dataframe['journal_score'] = float(setup.get('setup_score', 0))
    dataframe['journal_sl']    = float(setup.get('sl_price', 0) or 0)
    dataframe['journal_tp1']   = float(setup.get('tp1_price', 0) or 0)
    dataframe['journal_tp2']   = float(setup.get('tp2_price', 0) or 0)
    dataframe['journal_entry'] = float(
        (setup.get('entry_zone') or {}).get('low', 0) or
        setup.get('entry_price', 0) or 0
    )
    return dataframe
```

### `populate_entry_trend`

```python
def populate_entry_trend(self, dataframe, metadata):
    dataframe.loc[
        (dataframe['journal_score'] >= 8) &
        (dataframe['journal_sl'] > 0) &
        (dataframe['journal_tp1'] > 0),
        'enter_long'
    ] = 1
    return dataframe
```

### `custom_stoploss` — use journal's SL price

```python
def custom_stoploss(self, current_time, current_rate, current_profit,
                    dataframe, last_candle, **kwargs) -> float:
    sl_price = last_candle['journal_sl']
    if sl_price > 0 and current_rate > 0:
        return (sl_price / current_rate) - 1   # convert to ratio
    return self.stoploss   # fallback
```

### `custom_exit` — use journal's TP1/TP2

```python
def custom_exit(self, current_time, current_rate, current_profit,
                dataframe, last_candle, trade, **kwargs):
    tp1 = last_candle['journal_tp1']
    tp2 = last_candle['journal_tp2']
    if tp2 > 0 and current_rate >= tp2:
        return 'tp2_hit'
    if tp1 > 0 and current_rate >= tp1 and current_profit > 0.01:
        return 'tp1_hit'
    return None
```

### Fixed class attributes

```python
class JournalSignalStrategy(IStrategy):
    timeframe   = '4h'
    stoploss    = -0.12        # fallback if journal SL missing
    minimal_roi = {"0": 0.0}  # disabled — exit only via custom_exit or stoploss
    journal_url = 'http://192.168.1.21:8082'   # Pi address
```

---

## `config-dryrun.json`

```json
{
  "max_open_trades": 3,
  "stake_currency": "USDT",
  "stake_amount": 100,
  "dry_run": true,
  "dry_run_wallet": 1000,
  "exchange": {
    "name": "bitget",
    "key":    "",
    "secret": "",
    "password": ""
  },
  "strategy_path": "freqtrade/strategies/",
  "timeframe": "4h",
  "pairlists": [{"method": "StaticPairList"}],
  "pair_whitelist": [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT",
    "AAVE/USDT", "LINK/USDT", "INJ/USDT", "AVAX/USDT",
    "DOGE/USDT", "SUI/USDT", "APT/USDT", "ARB/USDT"
  ],
  "telegram": {
    "enabled": false,
    "token":   "",
    "chat_id": ""
  }
}
```

**To go live:** change `dry_run: false` and add Bitget API key/secret. No code changes needed.

---

## Running the Bot

```bash
# From journal repo root
freqtrade trade \
  --strategy JournalSignalStrategy \
  --config freqtrade/config-dryrun.json \
  --logfile freqtrade/logs/dryrun.log

# View results via FreqUI (Freqtrade web UI)
freqtrade webserver --config freqtrade/config-dryrun.json
# Open http://localhost:8080
```

---

## Dry-Run Validation Criteria (30-day gate before live)

The bot should demonstrate over 30 days of dry-run:
- Win rate ≥ 55%
- Profit factor ≥ 1.3
- Max drawdown ≤ 15%
- At least 10 completed trades (sufficient sample)

If these aren't met, review Hyperopt results from F5 and adjust thresholds.

---

## Files Created

| File | Purpose |
|------|---------|
| `freqtrade/strategies/JournalSignalStrategy.py` | Signal-polling IStrategy with custom SL/TP |
| `freqtrade/config-dryrun.json` | Dry-run config (Bitget exchange, no auth needed for dry-run) |

No journal files changed. No Pi deployment.

---

## Testing

Manual only:
1. Start dry-run bot → verify it polls `/api/scanner/status` without errors
2. Trigger a manual scanner scan with an 8+/10 result → verify bot picks it up within one 4H candle
3. Verify `custom_stoploss()` applies the journal's SL price correctly (check FreqUI trade details)
4. Run for 30 days → review against validation criteria before enabling live mode
