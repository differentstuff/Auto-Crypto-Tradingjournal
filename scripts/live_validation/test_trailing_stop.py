#!/usr/bin/env python3
"""
test_trailing_stop.py — Validate Exchange.place_trailing_stop().

Phase 2 (write action): Opens position, then places native trailing stop.
Verifies:
  - place_trailing_stop() returns result (non-None)
  - Trailing stop order has an ID
  - Trail percentage matches requested value
  - Position still exists (trailing stop is pending, not yet triggered)

The native trailing stop is a daemon-offline backup — wider than ATR-based.
It activates after TP1 hit, protecting the position if the daemon goes down.

Cleanup: Closes position and cancels orders.

Usage:
    python scripts/live_validation/test_trailing_stop.py
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
    SYMBOL, LEVERAGE, TEST_SIZE_USDT,
)


def main() -> None:
    print(f"\n{'=' * 60}")
    print(f"  TEST 5/8: Trailing Stop ({SYMBOL})")
    print(f"  Phase 2 — Write action, ~$0.01 fee risk")
    print(f"{'=' * 60}\n")

    result = TestResult("Trailing Stop")
    exchange = None

    try:
        exchange = create_exchange()
        result.check(True, "Exchange created")

        # ── Get current price ─────────────────────────────────────────────
        price = get_current_price(exchange, SYMBOL)

        # ── Compute SL and trailing stop params ───────────────────────────
        sl_price = round(price * 0.97, 6)         # 3% below (safety net)
        trigger_price = round(price * 1.005, 6)    # Slightly above current (activates after TP1)
        trail_pct = 3.0                            # 3% trailing distance

        print(f"\n   Opening Long {SYMBOL}:")
        print(f"     SL=${sl_price:.6f} (-3%)")

        # ── Step 1: Open position ──────────────────────────────────────────
        order = exchange.place_order(
            symbol=SYMBOL,
            direction="Long",
            size_usdt=TEST_SIZE_USDT,
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

        # ── Step 2: Place trailing stop ────────────────────────────────────
        print(f"\n   Placing trailing stop:")
        print(f"     trigger=${trigger_price:.6f} (slightly above current)")
        print(f"     trail_pct={trail_pct}%")

        trail_result = exchange.place_trailing_stop(
            symbol=SYMBOL,
            direction="Long",
            trigger_price=trigger_price,
            trail_pct=trail_pct,
        )

        result.check(
            trail_result is not None,
            "place_trailing_stop() returned result",
            f"order_id={trail_result.get('order_id', '?') if trail_result else 'None'}",
        )

        if trail_result:
            result.check(
                bool(trail_result.get("order_id")),
                "Trailing stop order has ID",
                f"order_id={trail_result.get('order_id')}",
            )
            result.check(
                trail_result.get("trail_pct") == trail_pct,
                "Trail percentage matches requested",
                f"trail_pct={trail_result.get('trail_pct')}",
            )
            result.check(
                not trail_result.get("paper", True),
                "Order is NOT paper (live mode)",
                f"paper={trail_result.get('paper')}",
            )

            print(f"   Trailing stop result: {trail_result}")

        wait_for_sync(reason="trailing stop order to register on exchange")

        # ── Step 3: Verify position still exists ──────────────────────────
        positions = exchange.fetch_positions()
        target = [p for p in positions if p.get("symbol") == SYMBOL]

        result.check(
            len(target) > 0,
            "Position still exists after trailing stop placed",
            "trailing stop is pending, not yet triggered",
        )

        if target:
            pos = target[0]
            print(f"   Position after trailing stop:")
            print(f"     entry_price={pos.get('entry_price'):.6f}")
            print(f"     size_usdt={pos.get('size_usdt')}")
            print(f"     total_contracts={pos.get('total_contracts')}")

    except Exception as e:
        result.check(False, "Unexpected exception", str(e))
        import traceback
        traceback.print_exc()

    finally:
        if exchange:
            cleanup_position(exchange, SYMBOL)

    passed = result.report()
    sys.exit(result.exit_code())


if __name__ == "__main__":
    main()
