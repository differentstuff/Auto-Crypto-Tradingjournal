#!/usr/bin/env python3
"""
test_cancel_orders.py — Validate Exchange.cancel_orders().

Phase 2 (write action): Opens position with SL+TP (creating conditional
orders), then cancels all orders for the symbol.
Verifies:
  - cancel_orders() returns success (not False)
  - SL and TP orders are cleared after cancellation
  - Position still exists (cancel_orders ≠ close position)

Cleanup: Closes position.

Usage:
    python scripts/live_validation/test_cancel_orders.py
"""

from __future__ import annotations

import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_SCRIPT_DIR))
sys.path.insert(0, _PROJECT_ROOT)
sys.path.insert(0, _SCRIPT_DIR)

from helpers import (
    create_exchange, get_current_price, wait_for_sync,
    cleanup_position, TestResult,
    SYMBOL, LEVERAGE, compute_test_contracts,
)


def main() -> None:
    print(f"\n{'=' * 60}")
    print(f"  TEST 7/8: Cancel Orders ({SYMBOL})")
    print(f"  Phase 2 — Write action, ~$0.01 fee risk")
    print(f"{'=' * 60}\n")

    result = TestResult("Cancel Orders")
    exchange = None
    entry_price = None

    try:
        exchange = create_exchange()
        result.check(True, "Exchange created")

        # ── Get current price ─────────────────────────────────────────────
        price = get_current_price(exchange, SYMBOL)
        contracts, notional_usdt = compute_test_contracts(exchange, SYMBOL)

        # ── Compute SL/TP ─────────────────────────────────────────────────
        sl_price = round(price * 0.97, 6)     # 3% below
        tp_price = round(price * 1.05, 6)     # 5% above

        print(f"\n   Opening Long {SYMBOL}:")
        print(f"     SL=${sl_price:.6f} (-3%), TP=${tp_price:.6f} (+5%)")

        # ── Step 1: Open position with SL+TP (creates conditional orders) ─
        order = exchange.place_order(
            symbol=SYMBOL,
            direction="Long",
            size_usdt=notional_usdt,
            entry_price=price,
            sl_price=sl_price,
            tp_price=tp_price,
            leverage=LEVERAGE,
        )

        result.check(
            order is not None,
            "Position opened with SL+TP",
            f"order_id={order.get('order_id', '?') if order else 'None'}",
        )

        wait_for_sync(reason="SL/TP orders to appear on exchange")

        # ── Step 2: Verify SL/TP are set before cancel ────────────────────
        positions_before = exchange.fetch_positions()
        target_before = [p for p in positions_before if p.get("symbol") == SYMBOL]

        if target_before:
            pos = target_before[0]
            entry_price = pos.get("entry_price")
            result.check(
                pos.get("sl_price", 0) > 0,
                "SL is set before cancel",
                f"sl_price=${pos.get('sl_price'):.6f}",
            )
            result.check(
                pos.get("tp_price", 0) > 0,
                "TP is set before cancel",
                f"tp_price=${pos.get('tp_price'):.6f}",
            )
            print(f"   Before cancel: SL=${pos.get('sl_price'):.6f}, TP=${pos.get('tp_price'):.6f}")
        else:
            result.check(False, "Position found before cancel", "position not found")

        # ── Step 3: Cancel all orders ─────────────────────────────────────
        print(f"\n   Cancelling all orders for {SYMBOL}...")

        cancel_result = exchange.cancel_orders(SYMBOL)

        result.check(
            cancel_result is not False,
            "cancel_orders() returned success",
            f"result={cancel_result}",
        )

        wait_for_sync(reason="order cancellation to take effect")

        # ── Step 4: Verify position still exists (cancel ≠ close) ─────────
        positions_after = exchange.fetch_positions()
        target_after = [p for p in positions_after if p.get("symbol") == SYMBOL]

        result.check(
            len(target_after) > 0,
            "Position still exists after cancel_orders()",
            "cancel_orders should only cancel orders, not close the position",
        )

        # ── Step 5: Verify SL/TP are cleared ──────────────────────────────
        if target_after:
            pos_after = target_after[0]
            sl_price_after = pos_after.get("sl_price", 0)
            tp_price_after = pos_after.get("tp_price", 0)
            sl_order_id_after = pos_after.get("sl_order_id", "")
            tp_order_id_after = pos_after.get("tp_order_id", "")

            result.check(
                sl_price_after == 0 or not sl_order_id_after,
                "SL order cleared after cancel",
                f"sl_price={sl_price_after:.6f}, sl_order_id={sl_order_id_after}",
            )
            result.check(
                tp_price_after == 0 or not tp_order_id_after,
                "TP order cleared after cancel",
                f"tp_price={tp_price_after:.6f}, tp_order_id={tp_order_id_after}",
            )

            print(f"   After cancel: SL={sl_price_after:.6f} (id={sl_order_id_after}), "
                  f"TP={tp_price_after:.6f} (id={tp_order_id_after})")

    except Exception as e:
        result.check(False, "Unexpected exception", str(e))
        import traceback
        traceback.print_exc()

    finally:
        if exchange:
            cleanup_position(exchange, SYMBOL, our_entry_price=entry_price)

    passed = result.report()
    sys.exit(result.exit_code())


if __name__ == "__main__":
    main()
