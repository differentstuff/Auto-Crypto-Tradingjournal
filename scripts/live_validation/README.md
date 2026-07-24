# scripts/live_validation/README.md

# Auto-Trader Live Validation Scripts

Validate all live execution actions against a **real Bitget futures account** before running 24/7.

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

- **Symbol**: DOGEUSDT (minimum notional well under $5)
- **Leverage**: 1x (minimal risk)
- **Position size**: $5 USDT notional
- **Sync delay**: 5 seconds between open and close
- **SL distance**: 3% below entry
- **TP distance**: 5% above entry

## Exit Codes

- `0` = PASS
- `1` = FAIL

## Cleanup

Every script cleans up after itself:
1. Cancels all orders for the symbol
2. Closes any remaining position via `Exchange.close_position()` with `price=mark_price`
3. Verifies no orphan positions or orders remain

## Bugs Fixed in This Implementation

Three root-cause bugs were fixed in `core/exchange.py`:

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

## After All Tests Pass

1. Verify in Bitget GUI that positions/orders appeared and disappeared as expected
2. Review any FAIL results and fix issues before going live
3. You are cleared to run strategy_15 at 1x leverage 24/7
