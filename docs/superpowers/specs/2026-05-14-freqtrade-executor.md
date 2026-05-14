# Spec C: Freqtrade Dry-Run Executor — Design Spec
*Date: 2026-05-14 · Status: Approved · Covers: F6*

---

## Overview

A Freqtrade strategy that sources signals from the journal's scanner API and executes them in **dry-run mode only** on Bitget USDT-M futures. Runs as a separate process on the local Mac alongside the journal. Validates the full execution pipeline (signal → entry → SL/TP management → close) without risking real capital.

**Prerequisites:** Spec A complete (CCXT installed), Spec B backtested and Hyperopt params validated.

---

## Architecture

```
Journal (Pi :8082)                Mac (Freqtrade process)
─────────────────                 ───────────────────────
GET /api/scanner/status  ←──────  JournalSignalStrategy.informative_pairs()
                                        ↓
                                  populate_indicators():
                                    caches scanner setups (60s TTL)
                                        ↓
                                  populate_entry_trend():
                                    enter_long = 1 when pair has 8+/10 signal
                                        ↓
                                  custom_stoploss():
                                    returns scanner's SL price
                                        ↓
                                  custom_exit():
                                    exits at TP1 (50%) + TP2 (full)
                                        ↓
                                  Freqtrade dry-run wallet
                                  Telegram alerts (separate bot token)
                                  FreqUI web dashboard
```

---

## F6 — JournalSignalStrategy

### File: `freqtrade/strategies/JournalSignalStrategy.py`

```python
"""
JournalSignalStrategy — sources signals from the journal's scanner API.
Dry-run only. Requires journal running at JOURNAL_URL (default http://localhost:8082).
"""
import os
import time
import requests
from freqtrade.strategy import IStrategy, stoploss_from_absolute
from pandas import DataFrame

JOURNAL_URL     = os.environ.get('JOURNAL_URL', 'http://192.168.1.21:8082')
MIN_SCORE       = 8
SIGNAL_CACHE_TTL = 60   # seconds between API polls
```

**`_fetch_scanner_signals() -> dict`** — polls `GET /api/scanner/status`, returns `{symbol: setup_dict}` for all setups scoring ≥ `MIN_SCORE`. Caches result for `SIGNAL_CACHE_TTL` seconds. Returns empty dict on any error (graceful degradation).

**`populate_indicators(dataframe, metadata)`** — calls `_fetch_scanner_signals()`, stores the matching setup dict (if any) in a module-level cache keyed by `metadata['pair']`. Returns dataframe unchanged (no indicator columns needed — signals come from the journal).

**`populate_entry_trend(dataframe, metadata)`** — checks the signal cache. If the pair has a current 8+/10 signal, sets the last candle's `enter_long = 1`. Uses `metadata['pair']` → symbol mapping (e.g. `'AAVE/USDT'` → `'AAVEUSDT'`).

**`custom_stoploss(current_time, current_rate, current_profit, trade, **kwargs)`** — returns the scanner's `sl_price` as a percentage distance from entry. Uses `stoploss_from_absolute(sl_price, entry_price, is_short=False)`.

**`custom_exit(pair, trade, current_time, current_rate, current_profit, **kwargs)`** — implements two-target exit:
- When `current_profit ≥ tp1_ratio`: close 50% (via `partial_exit` if Freqtrade supports) or log and let ROI handle
- When `current_profit ≥ tp2_ratio`: close remaining

**`minimal_roi`** — derived from Hyperopt results (Spec B output). Fallback: `{"0": 0.05, "120": 0.03}`.

**`stoploss`** — derived from Hyperopt results. Fallback: `-0.10`.

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
    "key":    "YOUR_BITGET_API_KEY",
    "secret": "YOUR_BITGET_SECRET_KEY",
    "password": "YOUR_BITGET_PASSPHRASE"
  },
  "strategy_path": "freqtrade/strategies/",
  "timeframe": "4h",
  "pairlists": [{"method": "StaticPairList"}],
  "pair_whitelist": [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "AAVE/USDT",
    "LINK/USDT", "INJ/USDT", "BNB/USDT", "AVAX/USDT"
  ],
  "telegram": {
    "enabled": false,
    "token":   "",
    "chat_id": ""
  }
}
```

Note: Bitget API keys are read-only for position data. In dry-run mode Freqtrade never sends real orders regardless.

---

## Running the Executor

```bash
# From journal repo root, on Mac
freqtrade trade \
  --strategy JournalSignalStrategy \
  --config freqtrade/config-dryrun.json \
  --db-url sqlite:///freqtrade/dryrun.sqlite

# View results via FreqUI
freqtrade webserver --config freqtrade/config-dryrun.json
# → open http://localhost:8080
```

Stop with Ctrl+C. All trades are simulated.

---

## Signal → Freqtrade field mapping

| Journal field | Freqtrade usage |
|---------------|----------------|
| `setup_score` | Entry gate (≥ 8 → `enter_long=1`) |
| `sl_price` | `custom_stoploss()` absolute level |
| `tp1_price` | First partial exit target |
| `tp2_price` | Full exit target |
| `direction` | Long only in v1 (short ignored) |
| `symbol` (e.g. `AAVEUSDT`) | Mapped to `AAVE/USDT` for Freqtrade |

---

## Files Created

| File | Purpose |
|------|---------|
| `freqtrade/strategies/JournalSignalStrategy.py` | IStrategy that polls journal API for signals |
| `freqtrade/config-dryrun.json` | Dry-run config with Bitget exchange (no real orders) |

---

## Testing / Validation

1. Start journal on Pi (already running)
2. Run `freqtrade trade --strategy JournalSignalStrategy --config freqtrade/config-dryrun.json`
3. Trigger a scanner run → verify Freqtrade picks up 8+/10 signals within one polling cycle
4. Verify `custom_stoploss()` returns correct SL distance (log output)
5. Let dry-run run for 7 days → compare dry-run P&L vs manual trading outcomes in journal
6. Gate for live: profit factor ≥ 1.3 AND max drawdown < 15% over 30-day dry-run

---

## Dependency on Previous Specs

- Requires `ccxt` installed (Spec A)
- Requires `JournalConfluenceStrategy.py` Hyperopt results to set `minimal_roi` + `stoploss` (Spec B)
- Requires journal accessible at `JOURNAL_URL` with scanner API returning `setup_score`, `sl_price`, `tp1_price`, `tp2_price`
