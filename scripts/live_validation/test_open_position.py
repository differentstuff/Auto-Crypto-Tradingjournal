#!/usr/bin/env python3
"""
test_open_position.py — Validate Exchange.place_order() with preset SL/TP.

Phase 2 (write action): Opens a small Long position on DOGEUSDT with SL/TP.
Verifies:
  - Order is placed successfully (non-None result, has order_id)
  - Position appears in fetch_positions() after sync
  - Position direction is Long, leverage is 1x
  - SL and TP are set on exchange (sl_price, tp_price > 0)
  - SL and TP order IDs exist (for modify_tpsl_order later)
  - Position ID exists (pos_id, for reconciliation)

Cleanup: Closes position and cancels orders.

Usage:
    python scripts/live_validation/test_open_position.py
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
    print(f"  TEST 2/8: Open Position ({SYMBOL})")
    print(f"  Phase 2 — Write action, ~$0.01 fee risk")
    print(f"{'=' * 60}\n")

    result = TestResult("Open Position")
    exchange = None

    try:
        exchange = create_exchange()
        result.check(True, "Exchange created")

        # ── Get current price ─────────────────────────────────────────────
        price = get_current_price(exchange, SYMBOL)

        # ── Compute SL/TP (safe distances for testing) ────────────────────
        sl_price = round(price * 0.97, 6)   # 3% below entry
        tp_price = round(price * 1.05, 6)   # 5% above entry

        print(f"\n   Opening Long {SYMBOL}:")
        print(f"     size=${TEST_SIZE_USDT}, entry≈${price:.6f}")
        print(f"     SL=${sl_price:.6f} (-3%), TP=${tp_price:.6f} (+5%)")
        print(f"     leverage={LEVERAGE}x")

        # ── Place order with preset SL/TP ─────────────────────────────────
        order = exchange.place_order(
            symbol=SYMBOL,
            direction="Long",
            size_usdt=TEST_SIZE_USDT,
            entry_price=price,
            sl_price=sl_price,
            tp_price=tp_price,
            leverage=LEVERAGE,
        )

        result.check(
            order is not None,
            "place_order() returned result",
            f"order_id={order.get('order_id', '?') if order else 'None'}",
        )

        if order:
            result.check(
                bool(order.get("order_id")),
                "Order has ID",
                f"order_id={order.get('order_id')}",
            )
            result.check(
                order.get("status") not in ("rejected", "canceled", None),
                "Order not rejected/canceled",
                f"status={order.get('status')}",
            )
            result.check(
                order.get("symbol") == SYMBOL,
                "Order symbol matches",
                f"symbol={order.get('symbol')}",
            )
            result.check(
                order.get("direction") == "Long",
                "Order direction is Long",
                f"direction={order.get('direction')}",
            )

        # ── Wait for exchange sync ────────────────────────────────────────
        wait_for_sync(reason="position to appear on exchange")

        # ── Read back position ─────────────────────────────────────────────
        positions = exchange.fetch_positions()
        target = [p for p in positions if p.get("symbol") == SYMBOL]

        result.check(
            len(target) > 0,
            "Position appears in fetch_positions()",
            f"found {len(target)} position(s) for {SYMBOL}",
        )

        if target:
            pos = target[0]

            result.check(
                pos.get("direction") == "Long",
                "Position direction is Long",
                f"direction={pos.get('direction')}",
            )
            result.check(
                pos.get("leverage") == LEVERAGE,
                "Leverage is 1x",
                f"leverage={pos.get('leverage')}",
            )
            result.check(
                pos.get("entry_price") > 0,
                "Entry price is set",
                f"entry_price={pos.get('entry_price'):.6f}",
            )
            result.check(
                pos.get("sl_price", 0) > 0,
                "SL is set on exchange",
                f"sl_price={pos.get('sl_price'):.6f}",
            )
            result.check(
                pos.get("tp_price", 0) > 0,
                "TP is set on exchange",
                f"tp_price={pos.get('tp_price'):.6f}",
            )
            result.check(
                bool(pos.get("sl_order_id")),
                "SL order ID exists (needed for modify_tpsl_order)",
                f"sl_order_id={pos.get('sl_order_id')}",
            )
            result.check(
                bool(pos.get("tp_order_id")),
                "TP order ID exists (needed for modify_tpsl_order)",
                f"tp_order_id={pos.get('tp_order_id')}",
            )
            result.check(
                bool(pos.get("pos_id")),
                "Position ID exists (needed for reconciliation)",
                f"pos_id={pos.get('pos_id')}",
            )
            result.check(
                pos.get("total_contracts", 0) > 0,
                "Total contracts is positive",
                f"total_contracts={pos.get('total_contracts')}",
            )

            # Full read-back for manual verification
            print(f"\n   Position read-back from exchange:")
            for key, value in sorted(pos.items()):
                print(f"     {key}: {value}")

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
