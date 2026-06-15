"""
scripts/time_travel/reporting.py -- Aggregate metrics, table/JSON output formatting.

Computes dollar-math metrics from per-trade results and formats them
as human-readable tables or machine-readable JSON.
"""

from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional

from learning.metrics import (
    avg_loss,
    avg_win,
    expectancy,
    max_drawdown,
    profit_factor,
    total_return_pct,
    win_loss_ratio,
)

_log = logging.getLogger("time_travel.reporting")


def compute_aggregate_metrics(
    trades: list,
    equity: float,
) -> dict:
    """Compute aggregate dollar-math metrics from a list of TradeResult objects.

    Args:
        trades: List of TradeResult objects (from simulation.compute_backtest_trade)
        equity: Starting equity in USDT

    Returns:
        Dict with all aggregate metrics
    """
    if not trades:
        return {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": 0.0,
            "total_return_pct": 0.0,
            "profit_factor": 0.0,
            "expectancy_per_trade_usd": 0.0,
            "avg_win_usd": 0.0,
            "avg_loss_usd": 0.0,
            "win_loss_ratio": 0.0,
            "max_drawdown_pct": 0.0,
            "total_fees_usd": 0.0,
            "equity": equity,
        }

    net_pnls = [t.net_pnl_usd for t in trades]
    wins = [t for t in trades if t.is_winner]
    losses = [t for t in trades if not t.is_winner]

    win_count = len(wins)
    loss_count = len(losses)
    resolved = win_count + loss_count
    win_rate = (win_count / resolved * 100) if resolved > 0 else 0.0

    # Build equity curve for max drawdown
    equity_curve = [equity]
    for pnl in net_pnls:
        equity_curve.append(equity_curve[-1] + pnl)

    total_fees = sum(t.total_fees_usd for t in trades)

    return {
        "total_trades": len(trades),
        "wins": win_count,
        "losses": loss_count,
        "win_rate_pct": round(win_rate, 1),
        "total_return_pct": total_return_pct(net_pnls, equity),
        "profit_factor": profit_factor(net_pnls),
        "expectancy_per_trade_usd": expectancy(net_pnls),
        "avg_win_usd": avg_win(net_pnls),
        "avg_loss_usd": avg_loss(net_pnls),
        "win_loss_ratio": win_loss_ratio(net_pnls),
        "max_drawdown_pct": round(max_drawdown(equity_curve) * 100, 2),
        "total_fees_usd": round(total_fees, 2),
        "equity": equity,
    }


def compute_per_symbol_metrics(
    trades: list,
    equity: float,
) -> Dict[str, dict]:
    """Compute aggregate metrics per symbol.

    Returns:
        Dict mapping symbol -> aggregate metrics dict
    """
    by_symbol: Dict[str, list] = {}
    for t in trades:
        by_symbol.setdefault(t.symbol, []).append(t)

    result = {}
    for symbol, symbol_trades in sorted(by_symbol.items()):
        result[symbol] = compute_aggregate_metrics(symbol_trades, equity)
    return result


def format_summary_table(
    metrics: dict,
    strategy_name: str,
    start_date: str,
    end_date: str,
    per_symbol: Optional[dict] = None,
) -> str:
    """Format aggregate metrics as a human-readable table.

    Args:
        metrics: Aggregate metrics dict from compute_aggregate_metrics()
        strategy_name: Strategy name for header
        start_date: Backtest start date
        end_date: Backtest end date
        per_symbol: Optional per-symbol metrics from compute_per_symbol_metrics()

    Returns:
        Formatted string with metrics table
    """
    total_trades = metrics["total_trades"]
    wins = metrics["wins"]
    losses = metrics["losses"]
    win_rate = metrics["win_rate_pct"]
    total_return = metrics["total_return_pct"]
    pf = metrics["profit_factor"]
    expectancy = metrics["expectancy_per_trade_usd"]
    avg_w = metrics["avg_win_usd"]
    avg_l = metrics["avg_loss_usd"]
    wlr = metrics["win_loss_ratio"]
    mdd = metrics["max_drawdown_pct"]
    fees = metrics["total_fees_usd"]
    equity = metrics["equity"]

    sign = "+" if total_return >= 0 else ""
    pf_str = f"{pf:.2f}" if pf > 0 else "inf" if sum(1 for t_pnl in [] if True) else "0.00"
    # Fix: profit_factor can be inf if no losses
    if metrics["losses"] == 0 and metrics["wins"] > 0:
        pf_str = "inf"
    elif pf == 0.0:
        pf_str = "0.00"
    else:
        pf_str = f"{pf:.2f}"

    exp_sign = "+" if expectancy >= 0 else ""
    w_sign = "+" if avg_w >= 0 else ""
    l_sign = "" if avg_l >= 0 else ""  # avg_loss is already negative

    lines = [
        "",
        "═" * 62,
        f"  BACKTEST RESULTS — {strategy_name} ({start_date} to {end_date})",
        "═" * 62,
        f"  Total Return:       {sign}{total_return:.1f}%",
        f"  Profit Factor:       {pf_str}",
        f"  Expectancy/Trade:   {exp_sign}${expectancy:.2f}",
        f"  Win/Loss Ratio:      {wlr:.2f}  (avg win ${avg_w:.2f} / avg loss ${avg_l:.2f})",
        f"  Win Rate:           {win_rate:.1f}%  ({wins} / {total_trades})",
        f"  Max Drawdown:       -{mdd:.1f}%",
        f"  Total Fees:         ${fees:.2f}",
        f"  Starting Equity:    ${equity:.0f}",
        f"  Trades:             {total_trades}",
        "═" * 62,
    ]

    if per_symbol:
        lines.append("")
        lines.append("  Per-Symbol Breakdown:")
        lines.append("  " + "-" * 58)
        for symbol, sym_metrics in per_symbol.items():
            sym_return = sym_metrics["total_return_pct"]
            sym_sign = "+" if sym_return >= 0 else ""
            sym_pf = sym_metrics["profit_factor"]
            sym_pf_str = f"{sym_pf:.2f}" if sym_pf not in (0.0,) else "0.00"
            if sym_metrics["losses"] == 0 and sym_metrics["wins"] > 0:
                sym_pf_str = "inf"
            lines.append(f"    {symbol:10s}  Return: {sym_sign}{sym_return:.1f}%  "
                         f"PF: {sym_pf_str:5s}  "
                         f"Trades: {sym_metrics['total_trades']:3d}  "
                         f"WR: {sym_metrics['win_rate_pct']:.1f}%")
        lines.append("  " + "-" * 58)

    lines.append("")
    return "\n".join(lines)


def format_summary_json(
    metrics: dict,
    strategy_name: str,
    start_date: str,
    end_date: str,
    per_symbol: Optional[dict] = None,
) -> str:
    """Format aggregate metrics as JSON.

    Args:
        metrics: Aggregate metrics dict from compute_aggregate_metrics()
        strategy_name: Strategy name
        start_date: Backtest start date
        end_date: Backtest end date
        per_symbol: Optional per-symbol metrics

    Returns:
        JSON string
    """
    output = {
        "strategy": strategy_name,
        "period": {
            "start": start_date,
            "end": end_date,
        },
        "metrics": {
            "total_return_pct": metrics["total_return_pct"],
            "profit_factor": metrics["profit_factor"],
            "expectancy_per_trade_usd": metrics["expectancy_per_trade_usd"],
            "avg_win_usd": metrics["avg_win_usd"],
            "avg_loss_usd": metrics["avg_loss_usd"],
            "win_loss_ratio": metrics["win_loss_ratio"],
            "max_drawdown_pct": metrics["max_drawdown_pct"],
            "win_rate_pct": metrics["win_rate_pct"],
            "total_trades": metrics["total_trades"],
            "wins": metrics["wins"],
            "losses": metrics["losses"],
            "total_fees_usd": metrics["total_fees_usd"],
            "equity": metrics["equity"],
        },
    }

    if per_symbol:
        output["per_symbol"] = per_symbol

    return json.dumps(output, indent=2)