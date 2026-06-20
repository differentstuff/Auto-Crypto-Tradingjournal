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

Position sizing philosophy:
  - Sizing is RISK-%-BASED, not nominal-based. A coin at $0.0000222 can
    make the same % move as one at $60,000 — we size for % risk, not price.
  - The volatility cap uses ATR% (relative volatility), not absolute ATR.
    This makes the cap asset-price-agnostic: BTC at $80k with 1% ATR and
    SHIB at $0.00001 with 1% ATR get the same cap.
  - Leverage is accounted for in max_notional: higher leverage enables
    larger positions while the same risk_per_trade_pct controls loss at SL.
  - A hard notional exposure ceiling (max_notional_exposure_pct) prevents
    excessive exposure even at high leverage (flash crash protection).
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


def compute_volatility_cap(equity: float, atr_pct: float, volatility_cap_pct: float) -> float:
    """Volatility-based position size cap using relative ATR%.

    Returns the maximum notional position size based on asset volatility
    expressed as ATR% (relative to price). This is asset-price-agnostic:
    a $80,000 asset with 1% ATR and a $0.00001 asset with 1% ATR get
    the same cap.

    Formula: volatility_cap_notional = (equity * volatility_cap_pct) / atr_pct

    High atr_pct (volatile) → small cap. Low atr_pct (calm) → large cap.

    Args:
        equity: Account equity in USDT
        atr_pct: ATR as a percentage of price (e.g., 1.0 for 1% ATR)
        volatility_cap_pct: Max % of equity exposed per 1% of asset volatility

    Returns:
        Maximum notional position size, or 0.0 if inputs are invalid.
    """
    if not equity or equity <= 0:
        return 0.0
    if not atr_pct or atr_pct <= 0:
        return 0.0
    if not volatility_cap_pct or volatility_cap_pct <= 0:
        return 0.0
    return (equity * volatility_cap_pct) / atr_pct


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
    atr_pct: float = 0.0,
    volatility_cap_pct: float = 0.0,
    max_notional_exposure_pct: float = 0.0,
) -> dict:
    """Compute position size based on risk parameters.

    Sizing cascade:
        1. Base: risk_amt / stop_dist_pct (risk-%-based, price-agnostic)
        2. Kelly: multiply by kelly_frac (edge-proportional sizing)
        3. Max size: equity * max_size_pct / 100 * leverage (leverage-aware)
        4. Notional exposure ceiling: equity * max_notional_exposure_pct / 100
        5. Volatility cap: (equity * volatility_cap_pct) / atr_pct (backstop)
        6. Min size floor: equity * min_size_pct / 100

    Steps 3 and 4 together: leverage enables larger positions (more capital
    available), but the notional exposure ceiling prevents excessive exposure
    even at high leverage. This protects against flash crashes where SL fails.

    The volatility cap uses ATR% (relative), not absolute ATR. This makes it
    asset-price-agnostic: BTC at $80k with 1% ATR and an alt at $0.01 with
    1% ATR get the same cap.

    Args:
        equity: Account equity in USDT
        entry_price: Entry price
        sl_price: Stop-loss price
        direction: "Long" or "Short"
        kelly_frac: Kelly fraction from kelly_fraction()
        leverage: Leverage multiplier (1 = unleveraged, 5 = 5x, etc.)
        risk_per_trade_pct: Risk per trade as % of equity
        max_size_pct: Max position size as % of equity (before leverage)
        min_size_pct: Min position size as % of equity
        atr_pct: ATR as % of price (0 = no volatility cap)
        volatility_cap_pct: Max % of equity per 1% ATR (0 = no cap)
        max_notional_exposure_pct: Hard ceiling on notional as % of equity (0 = no ceiling)

    Returns:
        Dict with: size_usdt, margin_usdt, risk_pct, stop_dist_pct,
                   volatility_cap_applied, volatility_cap_notional
    """
    _empty = {
        "size_usdt": 0, "margin_usdt": 0, "risk_pct": 0,
        "stop_dist_pct": 0, "volatility_cap_applied": False, "volatility_cap_notional": 0.0,
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

    # Cap at max_size_pct of equity, leverage-enabled.
    # Leverage allows taking larger positions with the same risk_per_trade_pct
    # because the exchange provides the additional capital as margin loan.
    max_notional = equity * max_size_pct / 100 * leverage
    if notional > max_notional:
        notional = max_notional

    # Hard notional exposure ceiling — prevents excessive exposure at high
    # leverage. Even with 25x leverage, never expose more than this % of
    # equity as notional. Flash crash protection: if SL fails, loss is
    # bounded by notional × move_pct, not the full leveraged position.
    if max_notional_exposure_pct > 0:
        exposure_ceiling = equity * max_notional_exposure_pct / 100
        if notional > exposure_ceiling:
            notional = exposure_ceiling

    # Volatility cap: reduce position for volatile assets (ATR%-based).
    # Uses relative ATR% so the cap is asset-price-agnostic.
    volatility_cap_applied = False
    volatility_cap_notional = 0.0
    if atr_pct > 0 and volatility_cap_pct > 0:
        volatility_cap_notional = compute_volatility_cap(equity, atr_pct, volatility_cap_pct)
        if volatility_cap_notional > 0 and notional > volatility_cap_notional:
            notional = volatility_cap_notional
            volatility_cap_applied = True

    # Floor at min_size_pct of equity (only when volatility cap doesn't bind).
    # When volatility cap is applied, it's a hard maximum that overrides the
    # soft floor — the asset is too volatile for a normal-sized position.
    if not volatility_cap_applied:
        min_notional = equity * min_size_pct / 100
        if notional < min_notional:
            notional = min_notional

    margin = notional / leverage

    return {
        "size_usdt": round(notional, 2),
        "margin_usdt": round(margin, 2),
        "risk_pct": round(risk_per_trade_pct, 2),
        "stop_dist_pct": round(stop_dist_pct * 100, 3),
        "volatility_cap_applied": volatility_cap_applied,
        "volatility_cap_notional": round(volatility_cap_notional, 2),
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