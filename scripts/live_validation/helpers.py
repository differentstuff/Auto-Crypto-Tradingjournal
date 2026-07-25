"""
scripts/live_validation/helpers.py — Shared utilities for live validation scripts.

Provides:
  - create_exchange():  Exchange instance with live_validation config
  - get_current_price(): Current ticker price for a symbol
  - cleanup_position(): Close position + cancel orders (uses fixed Exchange API)
  - wait_for_sync():    Sleep with progress message
  - TestResult:         PASS/FAIL tracking with evidence

All scripts import from this module. Add the script directory to sys.path
before importing (see any test script for the pattern).
"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import Optional

from core.config_loader import ConfigLoader
from core.exchange import Exchange
from dotenv import load_dotenv

_log = logging.getLogger(__name__)

# ── Project root ──────────────────────────────────────────────────────────────
# Resolve project root (3 levels up from scripts/live_validation/helpers.py)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env into os.environ on import — same pattern as main.py and replay_driver.py.
# This MUST happen before ConfigLoader reads os.getenv() for API keys.
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

# ── Constants ────────────────────────────────────────────────────────────────

SYMBOL = "DOGEUSDT"
LEVERAGE = 1
MIN_SYNC_SECONDS = 5
TEST_SIZE_USDT = 5.0  # $5 notional — well under $35 budget

# ── Exchange creation ────────────────────────────────────────────────────────


def create_exchange() -> Exchange:
    """
    Create Exchange instance with live_validation strategy.

    Ensures:
      - paper_mode is FALSE (aborts if TRUE)
      - Bitget API credentials are present (aborts if missing)
      - Exchange is not in paper mode (double-check)

    Returns:
      Exchange instance ready for live API calls.
    """
    config = ConfigLoader(strategy_name="live_validation")

    if config.paper_mode:
        print("❌ FATAL: paper_mode is TRUE — cannot run live validation!")
        print("   Set daemon.paper_mode: false in config/strategies/live_validation.yaml")
        print("   And set exchange.mode: live")
        sys.exit(1)

    # Check Bitget credentials
    creds = config.get_exchange_creds("bitget")
    if not creds.get("api_key"):
        print("❌ FATAL: BITGET_API_KEY not set in .env!")
        print("   Copy .env.example to .env and fill in your Bitget credentials.")
        sys.exit(1)
    if not creds.get("secret_key"):
        print("❌ FATAL: BITGET_SECRET_KEY not set in .env!")
        sys.exit(1)

    exchange = Exchange(config)

    # Double-check paper mode (Exchange reads it from config)
    if exchange.paper_mode:
        print("❌ FATAL: Exchange is in paper mode — cannot run live validation!")
        print("   Check daemon.paper_mode and exchange.mode in your config.")
        sys.exit(1)

    print(f"   ✅ Exchange ready: primary=bitget, paper_mode=false, leverage={LEVERAGE}x")
    return exchange


# ── Price fetching ────────────────────────────────────────────────────────────


def get_current_price(exchange: Exchange, symbol: str = SYMBOL) -> float:
    """
    Get current ticker price for a symbol.

    Aborts if price cannot be fetched (exchange connectivity issue).
    """
    ticker = exchange.fetch_ticker(symbol)
    if not ticker or not ticker.get("last"):
        print(f"❌ FATAL: Could not fetch ticker for {symbol}")
        print("   Check your internet connection and exchange status.")
        sys.exit(1)

    price = float(ticker["last"])
    bid = float(ticker.get("bid", price))
    ask = float(ticker.get("ask", price))
    spread_pct = ((ask - bid) / bid) * 100 if bid else 0

    print(f"   Current {symbol} price: ${price:.6f}  (spread: {spread_pct:.3f}%)")
    return price


# ── Sync wait ────────────────────────────────────────────────────────────────


def wait_for_sync(seconds: int = MIN_SYNC_SECONDS, reason: str = "") -> None:
    """
    Wait for exchange sync with progress dots.

    The user requested a minimum 5-second delay between opening
    and closing trades to allow time for data synchronization.
    """
    msg = f"   ⏳ Waiting {seconds}s for exchange sync"
    if reason:
        msg += f" ({reason})"
    print(msg, end="", flush=True)
    for _ in range(seconds):
        time.sleep(1)
        print(".", end="", flush=True)
    print(" done")


# ── Position cleanup ─────────────────────────────────────────────────────────


def cleanup_position(exchange: Exchange, symbol: str = SYMBOL) -> None:
    """
    Clean up position and orders for a symbol after a test.

    Uses the fixed Exchange.close_position() which correctly converts
    size_usdt to contracts. No workarounds needed.

    Steps:
      1. Cancel all orders for the symbol
      2. Fetch positions to find any remaining
      3. Close position via Exchange.close_position() with mark_price
      4. Verify no position remains
      5. Cancel any remaining orders
    """
    print(f"\n   🧹 Cleaning up {symbol}...")

    # Step 1: Cancel all orders
    try:
        exchange.cancel_orders(symbol)
        print(f"   ✅ Cancelled all orders for {symbol}")
    except Exception as e:
        print(f"   ⚠️  Cancel orders failed: {e}")

    time.sleep(2)

    # Step 2: Check for open position
    positions = exchange.fetch_positions()
    target = [p for p in positions if p.get("symbol") == symbol]

    if not target:
        print("   ✅ No open position — cleanup complete")
        return

    pos = target[0]
    direction = pos.get("direction", "Long")
    size_usdt = pos.get("size_usdt", 0)
    mark_price = pos.get("mark_price", 0)

    if size_usdt > 0:
        print(f"   Closing {direction} position: ${size_usdt:.2f} notional at ${mark_price:.6f}")
        try:
            result = exchange.close_position(
                symbol=symbol,
                direction=direction,
                size_usdt=size_usdt,
                reduce_only=False,
                price=mark_price,
            )
            if result:
                print(f"   ✅ Position closed (order_id={result.get('order_id', '?')})")
            else:
                print(f"   ⚠️  close_position returned None")
        except Exception as e:
            print(f"   ⚠️  close_position failed: {e}")
    else:
        print(f"   ⚠️  Position exists but size_usdt=0 — cannot close")

    time.sleep(3)

    # Step 3: Verify cleanup
    positions = exchange.fetch_positions()
    remaining = [p for p in positions if p.get("symbol") == symbol]

    if remaining:
        print(f"   ⚠️  WARNING: Position still open after cleanup!")
        for p in remaining:
            print(f"     {p.get('direction')} {p.get('symbol')} "
                  f"size_usdt={p.get('size_usdt')} mark_price={p.get('mark_price')}")
    else:
        print("   ✅ Cleanup verified — no open position")

    # Final: cancel any remaining orders
    try:
        exchange.cancel_orders(symbol)
    except Exception:
        pass


# ── Test result tracking ─────────────────────────────────────────────────────


class TestResult:
    """
    Track test results with evidence for PASS/FAIL reporting.

    Usage:
        result = TestResult("My Test")
        result.check(condition, "Description", "Detail")
        ...
        passed = result.report()
        sys.exit(result.exit_code())
    """

    def __init__(self, name: str):
        self.name = name
        self.passed = True
        self.evidence: list[str] = []
        self.errors: list[str] = []

    def check(self, condition: bool, description: str, detail: str = "") -> None:
        """Check a condition and record the result."""
        if condition:
            self.evidence.append(
                f"✅ {description}" + (f" — {detail}" if detail else "")
            )
        else:
            self.passed = False
            self.evidence.append(
                f"❌ {description}" + (f" — {detail}" if detail else "")
            )
            self.errors.append(description)

    def report(self) -> bool:
        """
        Print final PASS/FAIL report with all evidence.

        Returns:
            True if all checks passed, False otherwise.
        """
        status = "PASS" if self.passed else "FAIL"
        icon = "✅" if self.passed else "❌"

        print(f"\n{'=' * 60}")
        print(f"{icon} {self.name}: {status}")
        print(f"{'=' * 60}")
        for e in self.evidence:
            print(f"  {e}")

        if self.errors:
            print(f"\n  Failed checks ({len(self.errors)}):")
            for err in self.errors:
                print(f"    - {err}")

        print()
        return self.passed

    def exit_code(self) -> int:
        """Return 0 for PASS, 1 for FAIL."""
        return 0 if self.passed else 1
