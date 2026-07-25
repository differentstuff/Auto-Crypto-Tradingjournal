#!/usr/bin/env python3
"""
test_full_close.py — Validate Exchange.close_position().

Phase 2 (write action): Opens position, then closes it fully.
Verifies:
  - close_position() returns result (non-None)
  - Close order has an ID
  - Position no longer appears in fetch_positions() after close
  - No orphan position remains on exchange

The size_usdt-to-contracts conversion is now handled correctly inside
close_position() — it fetches the current price and computes contracts.
The test also passes price=mark_price explicitly to avoid an extra API call.

Usage:
    python scripts/live_validation/test_full_close.py
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
    print(f"  TEST 6/8: Full Close ({SYMBOL})")
    print(f"  Phase 2 — Write action, ~$0.02 fee risk")
    print(f"{'=' * 60}\n")

    result = TestResult("Full Close")
    exchange = None
    entry_price = None

    try:
        exchange = create_exchange()
        result.check(True, "Exchange created")

        # ── Get current price ─────────────────────────────────────────────
        price = get_current_price(exchange, SYMBOL)
        contracts, notional_usdt = compute_test_contracts(exchange, SYMBOL, price=price)

        # ── Compute SL ────────────────────────────────────────────────────
        sl_price = round(price * 0.97, 6)   # 3% below

        print(f"\n   Opening Long {SYMBOL}:")
        print(f"     size=${notional_usdt:.2f}, SL=${sl_price:.6f}")

        # ── Step 1: Open position ──────────────────────────────────────────
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

        wait_for_sync(reason="position to settle before closing")

        # ── Step 2: Read back position details ────────────────────────────
        positions_before = exchange.fetch_positions()
        target_before = [p for p in positions_before if p.get("symbol") == SYMBOL]

        if target_before:
            pos = target_before[0]
            entry_price = pos.get("entry_price")
            size_usdt = pos.get("size_usdt", notional_usdt)
            total_contracts = pos.get("total_contracts", 0)
            mark_price = pos.get("mark_price", 0)
            entry = pos.get("entry_price", 0)

            print(f"   Position before close:")
            print(f"     entry_price=${entry:.6f}")
            print(f"     size_usdt=${size_usdt} (notional in USDT)")
            print(f"     total_contracts={total_contracts} (actual contracts amount)")
            print(f"     mark_price=${mark_price:.6f}")

            expected_contracts = size_usdt / mark_price if mark_price > 0 else 0
            print(f"     expected_contracts={expected_contracts:.4f} (size_usdt / mark_price)")

            # ── Step 3: Close position with explicit price ────────────────
            print(f"\n   Closing position via Exchange.close_position():")
            print(f"     size_usdt={size_usdt}, price={mark_price:.6f}")
            print(f"     contracts={expected_contracts:.4f} (computed inside close_position)")

            close_result = exchange.close_position(
                symbol=SYMBOL,
                direction="Long",
                size_usdt=size_usdt,
                reduce_only=False,
                price=mark_price,
                total_contracts=total_contracts,
            )

            result.check(
                close_result is not None,
                "close_position() returned result",
                f"order_id={close_result.get('order_id', '?') if close_result else 'None'}",
            )

            if close_result:
                result.check(
                    bool(close_result.get("order_id")),
                    "Close order has ID",
                    f"order_id={close_result.get('order_id')}",
                )

            wait_for_sync(reason="close order to fill and position to disappear")

            # ── Step 4: Verify position is gone ───────────────────────────
            positions_after = exchange.fetch_positions()
            target_after = [p for p in positions_after if p.get("symbol") == SYMBOL]

            result.check(
                len(target_after) == 0,
                "Position no longer in fetch_positions()",
                f"remaining positions for {SYMBOL}: {len(target_after)}",
            )

            if target_after:
                remaining = target_after[0]
                print(f"\n   ⚠️  REMAINING POSITION after close_position():")
                print(f"     size_usdt=${remaining.get('size_usdt')}")
                print(f"     total_contracts={remaining.get('total_contracts')}")
                print(f"     mark_price=${remaining.get('mark_price')}")
        else:
            result.check(False, "Position found before close attempt", "position not found")

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
