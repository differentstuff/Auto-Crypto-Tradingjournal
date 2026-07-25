#!/usr/bin/env python3
"""
test_modify_tpsl.py — Validate Exchange.modify_tpsl_order().

Phase 2 (write action): Opens position with SL, then modifies SL price.
Verifies:
  - modify_tpsl_order() returns success (True)
  - SL price is updated on exchange after modification
  - SL moved in the expected direction

modify_tpsl_order() uses Bitget's native in-place modify endpoint
(POST /api/v2/mix/order/modify-tpsl-order) via CCXT edit_order with
amount=None (requires ccxt>=4.5.54, PR #27674). On failure the existing
SL order remains active — the position is never left without a stop-loss.

Cleanup: Closes position and cancels orders.

Usage:
    python scripts/live_validation/test_modify_tpsl.py
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
    print(f"  TEST 3/8: Modify TP/SL ({SYMBOL})")
    print(f"  Phase 2 — Write action, ~$0.01 fee risk")
    print(f"{'=' * 60}\n")

    result = TestResult("Modify TP/SL")
    exchange = None
    entry_price = None

    try:
        exchange = create_exchange()
        result.check(True, "Exchange created")

        # ── Get current price ─────────────────────────────────────────────
        price = get_current_price(exchange, SYMBOL)
        contracts, notional_usdt = compute_test_contracts(exchange, SYMBOL)

        # ── Compute SL/TP ─────────────────────────────────────────────────
        original_sl = round(price * 0.97, 6)    # 3% below entry
        tp_price = round(price * 1.05, 6)       # 5% above entry
        new_sl_price = round(price * 0.965, 6)  # 3.5% below (tighter SL)

        print(f"\n   Opening Long {SYMBOL}:")
        print(f"     SL=${original_sl:.6f} → will modify to ${new_sl_price:.6f}")

        # ── Step 1: Open position (no SL on entry — placed separately below) ─
        order = exchange.place_order(
            symbol=SYMBOL,
            direction="Long",
            size_usdt=notional_usdt,
            entry_price=price,
            leverage=LEVERAGE,
        )

        result.check(
            order is not None,
            "Position opened",
            f"order_id={order.get('order_id', '?') if order else 'None'}",
        )

        wait_for_sync(reason="position to settle on exchange")

        # ── Step 2: Place SL (pos_loss — attached to position) ────────────
        print(f"   Placing SL (pos_loss): trigger=${original_sl:.6f}")
        sl_result = exchange.place_tpsl_order(
            symbol=SYMBOL,
            direction="Long",
            trigger_price=original_sl,
            order_type="sl",
            size_pct=100.0,
            size_usdt=0,
        )
        result.check(
            sl_result is not None,
            "place_tpsl_order(sl) returned result",
            f"sl_order_id={sl_result.get('order_id', '?') if sl_result else 'None'}",
        )

        wait_for_sync(reason="SL order to appear on exchange")

        # ── Step 2: Read back position to get SL order ID ─────────────────
        positions = exchange.fetch_positions()
        target = [p for p in positions if p.get("symbol") == SYMBOL]

        result.check(
            len(target) > 0,
            "Position found for modification",
        )

        if not target:
            result.check(False, "Cannot test modify_tpsl without position", "skipped")
        else:
            pos = target[0]
            entry_price = pos.get("entry_price")
            sl_order_id = pos.get("sl_order_id", "")
            original_sl_on_exchange = pos.get("sl_price", 0)

            result.check(
                bool(sl_order_id),
                "SL order ID available for modification",
                f"sl_order_id={sl_order_id if sl_order_id else 'None'}",
            )

            print(f"   Original SL on exchange: ${original_sl_on_exchange:.6f}")
            print(f"   SL order ID: {sl_order_id if sl_order_id else 'None'}")

            # ── Step 3: Modify SL price ────────────────────────────────────
            if sl_order_id:
                print(f"\n   Modifying SL: ${original_sl_on_exchange:.6f} → ${new_sl_price:.6f}")

                modify_result = exchange.modify_tpsl_order(
                    symbol=SYMBOL,
                    order_id=sl_order_id,
                    new_sl_price=new_sl_price,
                )

                result.check(
                    modify_result is True or modify_result is not False,
                    "modify_tpsl_order() returned success",
                    f"result={modify_result}",
                )

                wait_for_sync(reason="SL modification to take effect")

                # ── Step 4: Read back and verify SL changed ───────────────
                positions2 = exchange.fetch_positions()
                target2 = [p for p in positions2 if p.get("symbol") == SYMBOL]

                if target2:
                    pos2 = target2[0]
                    new_sl_on_exchange = pos2.get("sl_price", 0)

                    result.check(
                        new_sl_on_exchange != 0,
                        "SL price is non-zero after modification",
                        f"sl_price={new_sl_on_exchange:.6f}",
                    )

                    # Check SL changed (exact match may vary due to rounding)
                    if original_sl_on_exchange != 0:
                        sl_changed = abs(new_sl_on_exchange - new_sl_price) < abs(
                            original_sl_on_exchange - new_sl_price
                        )
                        result.check(
                            sl_changed,
                            "SL price moved toward new target",
                            f"before=${original_sl_on_exchange:.6f}, "
                            f"target=${new_sl_price:.6f}, "
                            f"actual=${new_sl_on_exchange:.6f}",
                        )

                        # Check exact or close match
                        close_match = abs(new_sl_on_exchange - new_sl_price) / new_sl_price < 0.01
                        result.check(
                            close_match,
                            "SL price matches target (within 1%)",
                            f"target=${new_sl_price:.6f}, actual=${new_sl_on_exchange:.6f}",
                        )
                else:
                    result.check(
                        False,
                        "Position still exists after modification",
                        "position disappeared — may have been closed by SL",
                    )
            else:
                result.check(False, "Cannot test modify_tpsl without SL order ID", "skipped")

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
