# scripts/live_validation/README.md

# Auto-Trader Live Validation Scripts

Validate all live execution actions against a **real Bitget futures account** before running 24/7.

## ⚠️ Safety Notice

These scripts open and close **REAL positions** on your Bitget futures account using your API credentials. After each test, all open orders for the test symbol are cancelled and the test position is closed. Manual positions on the same symbol are **NOT** closed, but manual orders ARE cancelled.

Estimated cost per test: ~$0.01 in fees (worst case ~$0.50 if SL hits).

## Prerequisites

1. **Bitget API credentials** in `.env`:
   ```
   BITGET_API_KEY=your_key
   BITGET_SECRET_KEY=your_secret
   BITGET_PASSPHRASE=your_passphrase
   ```

2. **Futures account balance**: ≥ $10 USDT (recommended $35)

3. **Strategy config**: `config/strategies/live_validation.yaml` (auto-created)
   - `paper_mode: false`
   - `leverage: 1`

## Test Scripts (8 total)

| # | Script | Phase | Method Tested | Risk |
|---|--------|-------|---------------|------|
| 1 | `test_connection.py` | Read-only | `test_connection()` + `fetch_balance()` | $0 |
| 2 | `test_open_position.py` | Write | `place_order()` with SL/TP | ~$0.01 |
| 3 | `test_modify_tpsl.py` | Write | `modify_tpsl_order()` | ~$0.01 |
| 4 | `test_partial_tp1.py` | Write | `place_tpsl_order()` (TP1) | ~$0.01 |
| 5 | `test_trailing_stop.py` | Write | `place_trailing_stop()` | ~$0.01 |
| 6 | `test_full_close.py` | Write | `close_position()` | ~$0.01 |
| 7 | `test_cancel_orders.py` | Write | `cancel_orders()` | ~$0.01 |
| 8 | `test_reconcile.py` | Reconciliation | `fetch_positions()` | ~$0.01 |

**Total estimated cost**: ~$0.12 in fees (worst case ~$3 if SL hits)

## Usage

Run a single test:
```bash
python scripts/live_validation/test_connection.py
```

Run all tests in sequence:
```bash
python scripts/live_validation/run_all.py
```

## Test Parameters

- **Symbol**: DOGEUSDT (default — change `SYMBOL` in helpers.py to use any pair)
- **Leverage**: 1x (minimal risk)
- **Position size**: Dynamically computed (5-10 USDT notional)
  - Fetches current price and market limits
  - Calculates minimum contracts to stay above Bitget's $5 notional minimum
  - Adapts to any price: BTC at $300k, DOGE at $0.01, etc.
- **Sync delay**: 5 seconds between open and close
- **SL distance**: 3% below entry
- **TP distance**: 5% above entry

## Exit Codes

- `0` = PASS
- `1` = FAIL

## Cleanup

Every script cleans up after itself:
1. Cancels all orders for the symbol (including manual ones)
2. Closes the test position (matched by entry price — manual positions left untouched)
3. Verifies the test position is gone

## Bugs Fixed in This Implementation

1. **`close_position()` passed `size_usdt` as `amount` (contracts)**
   - Fix: Added `price` parameter; computes `contracts = size_usdt / price`
   - `execute_exit.py` now passes `price=position.get("mark_price", 0)`

2. **`place_tpsl_order()` passed `size_usdt*(size_pct/100)` as `amount` (contracts)**
   - Fix: Added `entry_price` parameter; computes `contracts = (size_usdt * size_pct/100) / price`
   - `execute_trade.py` now passes `entry_price=entry_price`

3. **`modify_tpsl_order()` crashed on `amount=None` (old CCXT)**
   - Fix: Bitget's native in-place endpoint `POST /api/v2/mix/order/modify-tpsl-order`
     via CCXT `edit_order(amount=None, params={stopLossPrice/takeProfitPrice})`.
     Verified against Bitget API docs + CCXT PR #27674 (merged 2026-01-14,
     included in ccxt>=4.5.54 which requirements.txt pins).
   - Safety: on failure the EXISTING SL order stays active (never naked),
     and the daemon retries next cycle (only updates `_exchange_sl_last_pushed` on success).
   - `daemon.py` now passes `direction` for correct closing side.

4. **`place_order()` used wrong SL/TP params (Type 2 triggers instead of Type 3 attached)**
   - Fix: use CCXT unified Type 3: `{'stopLoss': {'triggerPrice': X}, 'takeProfit': {'triggerPrice': Y}}`

5. **`place_tpsl_order()` used raw Bitget params (holdSide, stopSurplusTriggerPrice)**
   - Fix: use CCXT unified Type 2: `takeProfitPrice`/`stopLossPrice` (one per call)

6. **`place_trailing_stop()` passed `amount=0` — invalid**
   - Fix: fetch position contracts from `fetch_positions()`, use `createTrailingPercentOrder()`

7. **`cancel_orders()` treated "no orders" (code 22001) as failure**
   - Fix: catch Bitget error code 22001 and return `True` (nothing to cancel = success)

8. **CCXT 4.x constructor incompatibility (`**kwargs` → `config` dict)**
   - Fix: `exchange_class(config)` instead of `exchange_class(**kwargs)`

9. **`load_dotenv()` never called in validation scripts**
   - Fix: `helpers.py` calls `load_dotenv()` at import time

## After All Tests Pass

1. Verify in Bitget GUI that positions/orders appeared and disappeared as expected
2. Review any FAIL results and fix issues before going live
3. You are cleared to run strategy_15 at 1x leverage 24/7
