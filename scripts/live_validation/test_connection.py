#!/usr/bin/env python3
"""
test_connection.py — Validate Exchange.test_connection() + fetch_balance().

Phase 1 (read-only): Tests connectivity to data and trade exchanges.
Verifies:
  - Data exchange can fetch OHLCV (public endpoint)
  - Trade exchange can fetch balance (authenticated endpoint)
  - Balance has non-zero equity
  - Available balance is sufficient for testing

Usage:
    python scripts/live_validation/test_connection.py
"""

from __future__ import annotations

import os
import sys

# ── Path setup: project root + script directory ─────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_SCRIPT_DIR))
sys.path.insert(0, _PROJECT_ROOT)
sys.path.insert(0, _SCRIPT_DIR)

from helpers import create_exchange, TestResult, SYMBOL


def main() -> None:
    print(f"\n{'=' * 60}")
    print(f"  TEST 1/8: Connection & Balance ({SYMBOL})")
    print(f"  Phase 1 — Read-only, $0 risk")
    print(f"{'=' * 60}\n")

    result = TestResult("Connection & Balance")

    try:
        exchange = create_exchange()
        result.check(True, "Exchange created", "live_validation config, paper_mode=false")

        # ── Test test_connection() ────────────────────────────────────────
        print("\n   Testing Exchange.test_connection()...")
        conn = exchange.test_connection()

        result.check(
            conn.get("data_ok", False),
            "Data exchange connectivity (OHLCV public endpoint)",
            f"primary={conn.get('primary')}, data_source={conn.get('data_source')}",
        )
        result.check(
            conn.get("trade_ok", False),
            "Trade exchange connectivity (authenticated endpoint)",
            f"paper_mode={conn.get('paper_mode')}",
        )

        print(f"   Connection result: {conn}")

        # ── Test fetch_balance() ──────────────────────────────────────────
        print("\n   Testing Exchange.fetch_balance()...")
        balance = exchange.fetch_balance()

        result.check(
            bool(balance),
            "Balance fetched (non-empty response)",
        )

        if balance:
            equity = balance.get("equity", 0)
            available = balance.get("available", 0)
            margin = balance.get("total_margin", 0)

            result.check(
                equity > 0,
                "Non-zero equity",
                f"equity=${equity:.2f}",
            )
            result.check(
                available >= 5,
                "Sufficient available balance for testing",
                f"available=${available:.2f} (need ≥$5)",
            )

            print(f"   Balance: equity=${equity:.2f}, available=${available:.2f}, margin=${margin:.2f}")

            if equity < 10:
                print(f"   ⚠️  WARNING: Equity is below $10 — tests may fail due to insufficient margin")

        else:
            result.check(False, "Balance fetch returned empty dict", "check API credentials")

    except Exception as e:
        result.check(False, "Unexpected exception", str(e))
        import traceback
        traceback.print_exc()

    passed = result.report()
    sys.exit(result.exit_code())


if __name__ == "__main__":
    main()
