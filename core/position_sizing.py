"""
core/position_sizing.py -- Pure position sizing and P&L math.

Extracted from enzymes/approve_trade.py and enzymes/record_trade_outcome.py
so that both live trading and backtest can use the same formulas.

All functions take explicit parameters — no substrate dependency.
Live enzymes wrap these with substrate.cfg() calls; backtest calls them
directly with config values.

Fee handling:
  - compute_pnl() returns GROSS P&L (no fees). Live trading uses this
    because the broker provides actual fill prices with fees baked in.
  - compute_net_pnl() deducts simulated fees. For paper/backtest ONLY.
    Never call compute_net_pnl() with live trade data — that would
    double-count fees already included in broker fills.
"""

from __future__ import annotations


# ── Position sizing ──────────────────────────────────────────────────────────


def kelly_fraction(
    score: float,
    kelly_min: float = 0.05,
    kelly_max: float = 0.25,
    wr_base: float = 0.35,
    wr_range: float = 0.40,
    avg_win_r: float = 2.0,
) -> float:
    """Kelly criterion using confluence score as edge proxy.

    Maps score (0-10) to win_rate proxy, then computes Kelly fraction.
    Capped between kelly_min and kelly_max.

    Args:
        score: Confluence score (0-10 scale)
        kelly_min: Minimum Kelly fraction (default from risk config)
        kelly_max: Maximum Kelly fraction (default from risk config)
        wr_base: Base win rate at score=0 (default from risk config)
        wr_range: Win rate range added at score=10 (default from risk config)
        avg_win_r: Average win ratio / reward:risk (default from risk config)

    Returns:
        Kelly fraction, capped between kelly_min and kelly_max
    """
    win_rate = wr_base + (score / 10) * wr_range
    f = (win_rate * avg_win_r - (1 - win_rate)) / avg_win_r
    return round(max(kelly_min, min(kelly_max, f)), 3)


def compute_atr_cap(equity: float, atr_value: float, atr_cap_pct: float) -> float:
    """ATR-based position size cap.

    Returns the maximum notional position size based on asset volatility.
    Formula: atr_cap_notional = (equity * atr_cap_pct) / ATR_value

    High ATR (volatile) → small cap. Low ATR (calm) → large cap.

    Returns 0.0 if the cap cannot be computed (missing/zero inputs).
    """
    if not equity or not atr_value or atr_value <= 0:
        return 0.0
    if not atr_cap_pct or atr_cap_pct <= 0:
        return 0.0
    return (equity * atr_cap_pct) / atr_value


def compute_size(
    equity: float,
    entry_price: float,
    sl_price: float,
    direction: str,
    kelly_frac: float,
    leverage: int,
    risk_per_trade_pct: float,
    max_size_pct: float,
    min_size_pct: float,
    atr_value: float = 0.0,
    atr_cap_pct: float = 0.0,
) -> dict:
    """Compute position size based on risk parameters.

    Applies ATR cap as an additional constraint:
        position_size = min(kelly_size, atr_cap_size)

    Args:
        equity: Account equity in USDT
        entry_price: Entry price
        sl_price: Stop-loss price
        direction: "Long" or "Short"
        kelly_frac: Kelly fraction from kelly_fraction()
        leverage: Leverage multiplier
        risk_per_trade_pct: Risk per trade as % of equity
        max_size_pct: Max position size as % of equity
        min_size_pct: Min position size as % of equity
        atr_value: ATR value (0 = no ATR cap)
        atr_cap_pct: ATR cap equity % (0 = no ATR cap)

    Returns:
        Dict with: size_usdt, margin_usdt, risk_pct, stop_dist_pct,
                   atr_cap_applied, atr_cap_notional
    """
    _empty = {
        "size_usdt": 0, "margin_usdt": 0, "risk_pct": 0,
        "stop_dist_pct": 0, "atr_cap_applied": False, "atr_cap_notional": 0.0,
    }
    if not equity or not entry_price or not sl_price:
        return _empty

    # Stop distance
    stop_dist_pct = abs(entry_price - sl_price) / entry_price
    if stop_dist_pct == 0:
        return _empty

    # Risk amount
    risk_amt = equity * risk_per_trade_pct / 100

    # Notional from risk
    notional = risk_amt / stop_dist_pct

    # Apply Kelly fraction
    notional *= kelly_frac

    # Cap at max_size_pct of equity
    max_notional = equity * max_size_pct / 100
    if notional > max_notional:
        notional = max_notional

    # ATR cap: reduce position for volatile assets
    atr_cap_applied = False
    atr_cap_notional = 0.0
    if atr_value > 0 and atr_cap_pct > 0:
        atr_cap_notional = compute_atr_cap(equity, atr_value, atr_cap_pct)
        if atr_cap_notional > 0 and notional > atr_cap_notional:
            notional = atr_cap_notional
            atr_cap_applied = True

    # Floor at min_size_pct of equity (only when ATR cap doesn't bind).
    # When ATR cap is applied, it's a hard maximum that overrides the soft
    # floor — the asset is too volatile for a normal-sized position.
    if not atr_cap_applied:
        min_notional = equity * min_size_pct / 100
        if notional < min_notional:
            notional = min_notional

    margin = notional / leverage

    return {
        "size_usdt": round(notional, 2),
        "margin_usdt": round(margin, 2),
        "risk_pct": round(risk_per_trade_pct, 2),
        "stop_dist_pct": round(stop_dist_pct * 100, 3),
        "atr_cap_applied": atr_cap_applied,
        "atr_cap_notional": round(atr_cap_notional, 2),
    }


# ── P&L computation ─────────────────────────────────────────────────────────


def compute_pnl(
    entry_price: float,
    exit_price: float,
    direction: str,
    size_usdt: float,
) -> dict:
    """Compute gross P&L for a closing position.

    This is the GROSS P&L — no fees deducted. For live trading, the broker
    provides actual fill prices with fees baked in, so gross P&L is the
    correct measure. For backtest/paper mode, use compute_net_pnl() to
    deduct simulated fees on top of this gross figure.

    Args:
        entry_price: Entry price
        exit_price: Exit price (or mark price for unrealized P&L)
        direction: "Long" or "Short" (case-insensitive)
        size_usdt: Position notional size in USDT

    Returns:
        Dict with pnl_pct and pnl_usdt (gross, before fees)
    """
    if not entry_price or not exit_price or not size_usdt:
        return {"pnl_pct": 0.0, "pnl_usdt": 0.0}

    d = direction.lower()
    if d == "long":
        pnl_pct = ((exit_price - entry_price) / entry_price) * 100
    else:
        pnl_pct = ((entry_price - exit_price) / entry_price) * 100

    pnl_usdt = size_usdt * pnl_pct / 100

    return {
        "pnl_pct": round(pnl_pct, 2),
        "pnl_usdt": round(pnl_usdt, 2),
    }


def compute_net_pnl(
    gross_pnl_usdt: float,
    position_size_usdt: float,
    fee_rate: float,
) -> dict:
    """Compute net P&L after simulated exchange fees.

    For paper trading and backtest ONLY. Live trading uses broker-provided
    fills which already include actual fees — never call this function
    with live trade data, as that would double-count fees.

    Deducts entry fee and exit fee from gross P&L.

    Args:
        gross_pnl_usdt: Gross P&L in USDT (from compute_pnl)
        position_size_usdt: Position notional size in USDT
        fee_rate: Exchange fee rate per side (e.g., 0.0006 for 0.06%)

    Returns:
        Dict with:
            net_pnl_usdt: P&L after fees
            entry_fee_usdt: Fee paid at entry
            exit_fee_usdt: Fee paid at exit
            total_fees_usdt: Total fees paid
    """
    entry_fee = position_size_usdt * fee_rate

    # Exit notional = position_size + gross_pnl (can be negative)
    # For a winning long: exit_notional > position_size
    # For a losing long: exit_notional < position_size
    # We take abs() to handle short positions correctly
    exit_notional = position_size_usdt + gross_pnl_usdt
    exit_fee = abs(exit_notional) * fee_rate

    total_fees = entry_fee + exit_fee
    net_pnl = gross_pnl_usdt - total_fees

    return {
        "net_pnl_usdt": round(net_pnl, 2),
        "entry_fee_usdt": round(entry_fee, 4),
        "exit_fee_usdt": round(exit_fee, 4),
        "total_fees_usdt": round(total_fees, 4),
    }