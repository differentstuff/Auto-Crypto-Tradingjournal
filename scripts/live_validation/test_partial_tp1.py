#!/usr/bin/env python3
"""
test_partial_tp1.py — Validate Exchange.place_tpsl_order() for TP1 partial exit.

Phase 2 (write action): Opens position, then places TP1 partial close order.
Verifies:
  - place_tpsl_order() returns result (non-None)
  - TP1 order has an ID
  - Order type is 'tp'
  - Position still exists after TP1 order placed (pending, not triggered)

place_tpsl_order() now correctly converts size_usdt to contracts
using entry_price (or fetched ticker price as fallback).

Cleanup: Closes position and cancels orders.

Usage:
    python scripts/live_validation/test_partial_tp1.py
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
    print(f"  TEST 4/8: Partial TP1 ({SYMBOL})")
    print(f"  Phase 2 — Write action, ~$0.01 fee risk")
    print(f"{'=' * 60}\n")

    result = TestResult("Partial TP1")
    exchange = None
    entry_price = None

    try:
        exchange = create_exchange()
        result.check(True, "Exchange created")

        # ── Get current price ─────────────────────────────────────────────
        price = get_current_price(exchange, SYMBOL)
        contracts, notional_usdt = compute_test_contracts(exchange, SYMBOL, price=price)

        # ── Compute SL and TP1 ────────────────────────────────────────────
        # TP1 at ~2% above entry — safe distance, won't trigger during test
        sl_price = round(price * 0.97, 6)     # 3% below
        tp1_price = round(price * 1.02, 6)    # 2% above (safe distance)

        print(f"\n   Opening Long {SYMBOL}:")
        print(f"     SL=${sl_price:.6f} (-3%)")

        # ── Step 1: Open position (with SL only, no preset TP) ────────────
        # We'll add TP1 manually via place_tpsl_order
        order = exchange.place_order(
            symbol=SYMBOL,
            direction="Long",
            size_usdt=notional_usdt,
            entry_price=price,
            sl_price=sl_price,
            leverage=LEVERAGE,
        )

        result.check(
            order is not None,
            "Position opened",
            f"order_id={order.get('order_id', '?') if order else 'None'}",
        )

        wait_for_sync(reason="position to settle")

        # ── Step 2: Place TP1 partial close order ─────────────────────────
        print(f"\n   Placing TP1 order:")
        print(f"     trigger=${tp1_price:.6f} (+2%)")
        print(f"     size_pct=40% of position")
        print(f"     entry_price=${price:.6f} (for contract conversion)")

        tp1_result = exchange.place_tpsl_order(
            symbol=SYMBOL,
            direction="Long",
            trigger_price=tp1_price,
            size_pct=40.0,
            size_usdt=notional_usdt,
            entry_price=price,
            order_type="tp",
            reduce_only=True,
        )

        result.check(
            tp1_result is not None,
            "place_tpsl_order() returned result",
            f"order_id={tp1_result.get('order_id', '?') if tp1_result else 'None'}",
        )

        if tp1_result:
            result.check(
                bool(tp1_result.get("order_id")),
                "TP1 order has ID",
                f"order_id={tp1_result.get('order_id')}",
            )
            result.check(
                tp1_result.get("order_type") == "tp",
                "Order type is 'tp'",
                f"order_type={tp1_result.get('order_type')}",
            )
            result.check(
                not tp1_result.get("paper", True),
                "Order is NOT paper (live mode)",
                f"paper={tp1_result.get('paper')}",
            )

            print(f"   TP1 order result: {tp1_result}")

        wait_for_sync(reason="TP1 order to register on exchange")

        # ── Step 3: Verify position still exists (TP1 pending) ────────────
        positions = exchange.fetch_positions()
        target = [p for p in positions if p.get("symbol") == SYMBOL]

        result.check(
            len(target) > 0,
            "Position still exists after TP1 order placed",
            "TP1 order is pending, not yet triggered",
        )

        if target:
            pos = target[0]
            entry_price = pos.get("entry_price")
            print(f"   Position after TP1 order:")
            print(f"     entry_price={pos.get('entry_price'):.6f}")
            print(f"     size_usdt={pos.get('size_usdt')}")
            print(f"     total_contracts={pos.get('total_contracts')}")
            print(f"     available_contracts={pos.get('available_contracts')}")

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
