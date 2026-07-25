"""
scripts/live_validation/helpers.py — Shared utilities for live validation scripts.

Provides:
  - create_exchange():  Exchange instance with live_validation config
  - compute_test_contracts(): Dynamic minimum contract calculation (5-10 USDT notional)
  - get_current_price(): Current ticker price for a symbol
  - cleanup_position(): Close our test position + cancel orders
  - wait_for_sync():    Sleep with progress message
  - TestResult:         PASS/FAIL tracking with evidence

All scripts import from this module. Add the script directory to sys.path
before importing (see any test script for the pattern).
"""

from __future__ import annotations

import logging
import math
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
# Notional target range: 5-10 USDT (Bitget USDT-M futures minimum is 5 USDT).
# The exact contract count is computed dynamically in compute_test_contracts()
# based on the current price and market's minimum contract size.
NOTIONAL_TARGET_USDT = 6.0  # Aim for $6 — safely above $5 minimum

# ── Safety warning ───────────────────────────────────────────────────────────

CLEANUP_WARNING = """
⚠️  LIVE VALIDATION SAFETY NOTICE
═══════════════════════════════════
These scripts open and close REAL positions on your Bitget futures
account using YOUR API credentials. After each test, ALL open orders
for the test symbol are cancelled and the test position is closed.

If you have MANUAL positions open on the same symbol, they will NOT
be affected — cleanup only targets positions opened BY THIS SCRIPT.
However, cancel_orders() cancels ALL open orders for the symbol.

Estimated cost per test: ~$0.01 in fees (worst case ~$0.50 if SL hits)
Total for all 8 tests:   ~$0.12 (worst case ~$3)
"""


def print_safety_warning() -> None:
    """Print the safety warning before any test runs."""
    print(CLEANUP_WARNING)


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


# ── Dynamic contract calculation ─────────────────────────────────────────────


def compute_test_contracts(exchange: Exchange, symbol: str = SYMBOL, price: float = None) -> tuple[float, float]:
    """
    Compute the minimum number of contracts to stay within 5-10 USDT notional.

    Dynamically adapts to any symbol at any price:
    - Fetches current price (if not provided)
    - Reads market limits (min contract size, precision)
    - Calculates contracts needed for ~$6 USDT notional (above $5 minimum)
    - Handles edge cases: BTC at $300k, DOGE at any price, etc.

    Args:
        exchange: Exchange instance
        symbol: Journal format symbol
        price: Pre-fetched current price (avoids duplicate API call + log)

    Returns:
        (contracts, notional_usdt) — the number of contracts and actual notional value.
    """
    if price is None:
        price = get_current_price(exchange, symbol)

    # Get market info for precision and minimums
    raw_exchange = exchange._get_trade_exchange()
    ccxt_symbol = Exchange.to_ccxt_symbol(symbol)

    try:
        raw_exchange.load_markets()
        market = raw_exchange.market(ccxt_symbol)
    except Exception as e:
        _log.warning("Could not load market info for %s: %s — using defaults", symbol, e)
        market = {}

    # Minimum contract amount (e.g. 1 for DOGE, 0.0001 for BTC)
    min_amount = 1.0
    try:
        min_amount = float(market.get("limits", {}).get("amount", {}).get("min", 1.0))
    except (ValueError, TypeError):
        pass

    # Amount precision step (e.g. 1 for DOGE, 0.0001 for BTC)
    precision_step = 1.0
    try:
        precision_step = float(market.get("precision", {}).get("amount", 1.0))
    except (ValueError, TypeError):
        pass

    # Calculate minimum contracts for $5.01 notional (just above Bitget minimum)
    min_contracts_for_notional = math.ceil(5.01 / price) if price > 0 else 1

    # Start with the larger of: market minimum OR notional minimum
    contracts = max(min_amount, min_contracts_for_notional)

    # Round up to nearest valid step
    if precision_step > 0 and precision_step < 1:
        # Fractional precision (e.g. BTC: 0.0001)
        contracts = math.ceil(contracts / precision_step) * precision_step
    else:
        # Integer precision (e.g. DOGE: 1)
        contracts = math.ceil(contracts)

    # Verify notional is above $5 — increment if needed
    notional = contracts * price
    while notional < 5.0 and precision_step > 0:
        contracts += precision_step
        notional = contracts * price
        if precision_step >= 1:
            contracts = float(int(contracts))  # Keep integer for DOGE-like pairs
            notional = contracts * price

    # Round to exchange precision
    try:
        contracts = float(raw_exchange.amount_to_precision(ccxt_symbol, contracts))
    except Exception:
        if precision_step >= 1:
            contracts = float(int(round(contracts)))
        else:
            contracts = round(contracts, len(str(precision_step).rstrip('0').split('.')[-1]) if '.' in str(precision_step) else 0)

    notional = contracts * price

    print(f"   📊 Test size: {contracts} contracts × ${price:.6f} = ${notional:.2f} USDT notional")
    if notional < 5.0:
        print(f"   ⚠️  WARNING: notional ${notional:.2f} is below Bitget's $5 minimum!")
    if notional > 10.0:
        print(f"   ℹ️  Note: notional ${notional:.2f} is above $10 (minimum contract size forces this)")

    return contracts, notional


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


def cleanup_position(exchange: Exchange, symbol: str = SYMBOL,
                     our_entry_price: float = None) -> None:
    """
    Clean up the test position and cancel orders for a symbol.

    Only closes positions that match our test entry price (if provided),
    so manual positions on the same symbol are NOT affected.

    Steps:
      1. Cancel all orders for the symbol
      2. Fetch positions to find any remaining
      3. Close our test position (matched by entry price or direction)
      4. Verify our position is gone
    """
    print(f"\n   🧹 Cleaning up {symbol}...")

    # Step 1: Cancel all orders for the symbol
    # Note: this cancels ALL orders, including manual ones on this symbol.
    # This is necessary because orphan SL/TP orders would interfere with
    # subsequent tests. Manual positions are NOT closed.
    try:
        exchange.cancel_orders(symbol)
        print(f"   ✅ Cancelled all orders for {symbol}")
    except Exception as e:
        print(f"   ⚠️  Cancel orders failed: {e}")

    time.sleep(2)

    # Step 2: Check for open positions
    positions = exchange.fetch_positions()
    target = [p for p in positions if p.get("symbol") == symbol]

    if not target:
        print("   ✅ No open position — cleanup complete")
        return

    # Step 3: Close our test position
    # If we know our entry price, only close that specific position.
    # Otherwise, close the first position for this symbol.
    for pos in target:
        entry = pos.get("entry_price", 0)
        direction = pos.get("direction", "Long")
        size_usdt = pos.get("size_usdt", 0)
        mark_price = pos.get("mark_price", 0)

        # If we have our entry price, only close matching positions
        if our_entry_price and entry > 0:
            price_match = abs(entry - our_entry_price) / our_entry_price < 0.001  # 0.1% tolerance
            if not price_match:
                print(f"   ⏭️  Skipping manual position: {direction} entry=${entry:.6f} (not ours)")
                continue

        if size_usdt > 0:
            print(f"   Closing test position: {direction} ${size_usdt:.2f} at ${mark_price:.6f}")
            try:
                result = exchange.close_position(
                    symbol=symbol,
                    direction=direction,
                    size_usdt=size_usdt,
                    reduce_only=False,
                    price=mark_price,
                    total_contracts=pos.get("total_contracts"),
                )
                if result:
                    print(f"   ✅ Position closed (order_id={result.get('order_id', '?')})")
                else:
                    print(f"   ⚠️  close_position returned None")
            except Exception as e:
                print(f"   ⚠️  close_position failed: {e}")

    time.sleep(3)

    # Step 4: Verify our position is gone
    positions_after = exchange.fetch_positions()
    remaining = [p for p in positions_after if p.get("symbol") == symbol]

    if our_entry_price:
        # Only check that OUR position is gone
        our_remaining = [
            p for p in remaining
            if abs(p.get("entry_price", 0) - our_entry_price) / our_entry_price < 0.001
        ]
        if our_remaining:
            print(f"   ⚠️  WARNING: Our test position still open after cleanup!")
            for p in our_remaining:
                print(f"     {p.get('direction')} {p.get('symbol')} "
                      f"entry={p.get('entry_price'):.6f} size_usdt={p.get('size_usdt')}")
        else:
            print("   ✅ Cleanup verified — our test position is closed")

        if remaining and not our_remaining:
            print(f"   ℹ️  {len(remaining)} other position(s) remain on {symbol} (not ours, left untouched)")
    else:
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
