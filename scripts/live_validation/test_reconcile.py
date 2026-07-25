#!/usr/bin/env python3
"""
test_reconcile.py — Validate Exchange.fetch_positions() for reconciliation.

Phase 3 (reconciliation): Opens position, then compares local vs exchange state.
Verifies:
  - fetch_positions() returns complete data for reconciliation
  - All required fields are present (symbol, direction, entry_price, etc.)
  - Position data is consistent (size, leverage match expected values)
  - Reconciliation fields are populated (pos_id, sl_order_id, etc.)

This is the most thorough test — it checks every field that the daemon's
reconciliation logic depends on.

Cleanup: Closes position and cancels orders.

Usage:
    python scripts/live_validation/test_reconcile.py
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

# All fields required for reconciliation (from exchange.py fetch_positions)
REQUIRED_FIELDS = [
    "symbol", "direction", "entry_price", "mark_price", "size_usdt",
    "unrealized_pnl", "unrealized_pct", "leverage",
    "pos_id", "achieved_profits",
    "sl_price", "tp_price",
    "sl_order_id", "tp_order_id",
    "total_contracts", "available_contracts",
]


def main() -> None:
    print(f"\n{'=' * 60}")
    print(f"  TEST 8/8: Reconciliation ({SYMBOL})")
    print(f"  Phase 3 — Reconciliation, ~$0.01 fee risk")
    print(f"{'=' * 60}\n")

    result = TestResult("Reconciliation")
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

        # ── Step 1: Open position (no SL/TP on entry — placed separately) ─
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

        # ── Step 2: Place SL (pos_loss) and TP (pos_profit) ───────────────
        print(f"   Placing SL (pos_loss): trigger=${sl_price:.6f}")
        sl_result = exchange.place_tpsl_order(
            symbol=SYMBOL,
            direction="Long",
            trigger_price=sl_price,
            order_type="sl",
            size_pct=100.0,
            size_usdt=0,
        )
        print(f"   Placing TP (pos_profit): trigger=${tp_price:.6f}")
        tp_result = exchange.place_tpsl_order(
            symbol=SYMBOL,
            direction="Long",
            trigger_price=tp_price,
            order_type="tp",
            size_pct=100.0,
            size_usdt=0,
        )

        wait_for_sync(reason="position and SL/TP orders to fully settle on exchange")

        # ── Step 2: Fetch positions and check all fields ──────────────────
        positions = exchange.fetch_positions()
        target = [p for p in positions if p.get("symbol") == SYMBOL]

        result.check(
            len(target) > 0,
            "Position found in fetch_positions()",
        )

        if target:
            pos = target[0]
            entry_price = pos.get("entry_price")

            # ── Check all required fields exist ────────────────────────────
            for field in REQUIRED_FIELDS:
                has_field = field in pos and pos[field] is not None
                value_str = str(pos.get(field, "MISSING"))
                # Truncate long values for readability
                if len(value_str) > 50:
                    value_str = value_str[:47] + "..."
                result.check(
                    has_field,
                    f"Field '{field}' present",
                    f"value={value_str}",
                )

            # ── Check data consistency ────────────────────────────────────
            result.check(
                pos.get("symbol") == SYMBOL,
                "Symbol matches expected",
                f"expected={SYMBOL}, got={pos.get('symbol')}",
            )
            result.check(
                pos.get("direction") == "Long",
                "Direction matches expected",
                f"expected=Long, got={pos.get('direction')}",
            )
            result.check(
                pos.get("leverage") == LEVERAGE,
                "Leverage matches expected (1x)",
                f"expected={LEVERAGE}, got={pos.get('leverage')}",
            )
            result.check(
                pos.get("entry_price") > 0,
                "Entry price is positive",
                f"entry_price={pos.get('entry_price'):.6f}",
            )
            result.check(
                pos.get("mark_price") > 0,
                "Mark price is positive",
                f"mark_price={pos.get('mark_price'):.6f}",
            )
            result.check(
                pos.get("total_contracts", 0) > 0,
                "Total contracts is positive",
                f"total_contracts={pos.get('total_contracts')}",
            )
            result.check(
                pos.get("available_contracts", 0) > 0,
                "Available contracts is positive",
                f"available_contracts={pos.get('available_contracts')}",
            )
            result.check(
                pos.get("total_contracts", 0) >= pos.get("available_contracts", 0),
                "Total contracts ≥ available contracts",
                f"total={pos.get('total_contracts')}, available={pos.get('available_contracts')}",
            )
            result.check(
                pos.get("sl_price", 0) > 0,
                "SL price is set on exchange",
                f"sl_price={pos.get('sl_price'):.6f}",
            )
            result.check(
                pos.get("tp_price", 0) > 0,
                "TP price is set on exchange",
                f"tp_price={pos.get('tp_price'):.6f}",
            )

            # ── Print full position data for manual verification ──────────
            print(f"\n   Full position data from exchange (for manual Bitget GUI check):")
            print(f"   {'─' * 50}")
            for key, value in sorted(pos.items()):
                print(f"     {key:25s}: {value}")
            print(f"   {'─' * 50}")
            print(f"   → Verify in Bitget GUI that this matches the open position")

        # ── Step 3: Report other open positions on account ────────────────
        other_symbols = [p for p in positions if p.get("symbol") != SYMBOL]
        if other_symbols:
            print(f"\n   Other open positions on account: {len(other_symbols)}")
            for p in other_symbols:
                print(f"     {p.get('symbol')} {p.get('direction')} "
                      f"size=${p.get('size_usdt')} PnL={p.get('unrealized_pnl')}")
        else:
            print(f"\n   No other open positions on account (clean state)")

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
