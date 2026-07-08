"""
core/fees.py -- Exchange fee simulation for paper trading and backtest.

Pure functions, no substrate dependency. Matches the explicit-parameter
pattern of core/position_sizing.py so both live enzymes and any future
standalone tooling can call these the same way.

For paper/replay ONLY. Live trading uses broker fills where fees are
already baked into the fill price -- never apply these functions to live
trade data, as that would double-count fees already included in broker fills.

Fee model:
  - Entry fee: taker_rate * notional (charged once at trade open)
  - Exit fee: taker_rate * exit_notional (charged at each close event)
  - All entries and exits use market orders (taker rate only).
    Maker-rate support is deferred until limit-order entries exist.

Rate source: config/default.yaml fees.taker_rate (0.0006 = 0.06% per side,
Bitget VIP0). This is a system-wide default, not a per-strategy override.
Read via substrate.cfg("fees.taker_rate").
"""

from __future__ import annotations


def compute_entry_fee(notional_usdt: float, fee_rate: float) -> float:
    """Compute the exchange fee charged at trade entry.

    Entry fee = notional_usdt * fee_rate.
    Charged once at trade open on the full position notional.

    Args:
        notional_usdt: Position notional size in USDT (size_usdt).
        fee_rate: Exchange fee rate per side (e.g. 0.0006 for 0.06%).

    Returns:
        Entry fee in USDT.
    """
    return round(notional_usdt * fee_rate, 4)


def compute_exit_fee(exit_notional_usdt: float, fee_rate: float) -> float:
    """Compute the exchange fee charged at trade exit.

    Exit fee = exit_notional_usdt * fee_rate.
    exit_notional_usdt is the notional value at exit time:
      - For partial closes: sold_usdt + gross_pnl_on_sold_slice
      - For full closes: remaining size_usdt + gross_pnl_on_remaining

    Kept as a separate named function from compute_entry_fee for
    callsite clarity and future divergence (e.g. maker/taker may
    differ per side when limit orders are added).

    Args:
        exit_notional_usdt: Notional value at exit time in USDT.
        fee_rate: Exchange fee rate per side (e.g. 0.0006 for 0.06%).

    Returns:
        Exit fee in USDT.
    """
    return round(exit_notional_usdt * fee_rate, 4)
