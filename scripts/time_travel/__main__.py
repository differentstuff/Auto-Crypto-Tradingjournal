#!/usr/bin/env python3
"""
scripts/time_travel/__main__.py -- CLI entry point for time-travel backtest.

Replays the daemon's scoring logic on historical OHLCV data, simulates
entries at multiple thresholds, walks forward to find exits, computes
dollar-math profitability metrics, and writes results to trade_learning.

Usage:
    python -m scripts.time_travel --start 2025-01-01 --symbols BTCUSDT ETHUSDT
    python scripts/time_travel/__main__.py --start 2025-06-01 --end 2025-12-01 \\
        --symbols BTCUSDT ETHUSDT SOLUSDT --thresholds 3,4,5,6.5
    python scripts/time_travel/__main__.py --start 2025-01-01 --strategy momentum_rising
    python scripts/time_travel/__main__.py --start 2025-01-01 --json
    python scripts/time_travel/__main__.py --start 2025-01-01 --per-symbol
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time as _time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd

# ── Project path setup ──────────────────────────────────────────────────────
import os
from dotenv import load_dotenv

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

from core.config_loader import ConfigLoader
from core.database import db_conn, init_db
from core.exchange import Exchange
from indicators.registry import compute_indicator

from scripts.time_travel.scoring import build_signals_at_entry, compute_confluence_score
from scripts.time_travel.simulation import (
    TradeCooldown,
    TradeResult,
    compute_backtest_trade,
    simulate_exit,
)
from scripts.time_travel.data import (
    _INDICATOR_WINDOW,
    bar_to_iso,
    fetch_historical_ohlcv,
    find_confirmation_bar,
    precompute_indicators,
    tf_to_minutes,
)
from scripts.time_travel.reporting import (
    compute_aggregate_metrics,
    compute_per_symbol_metrics,
    format_summary_json,
    format_summary_table,
)

_log = logging.getLogger("time_travel")


# ── Trade writing ────────────────────────────────────────────────────────────


def _write_trade(
    symbol: str,
    direction: str,
    strategy_name: str,
    strategy_uid: str,
    entry_time: str,
    exit_time: str,
    outcome: str,
    pnl_pct: float,
    pnl_usdt: float,
    duration_minutes: int,
    confluence_score: float,
    signals_at_entry: dict,
    indicators_aligned: int,
    entry_price: float,
    exit_price: float,
    exit_reason: str,
    sl_hit: int,
    trailing_stop_hit: int,
    mfe_pct: float,
    mae_pct: float,
    threshold_used: float,
    entry_threshold: float = 6.5,
    trade_result: Optional[TradeResult] = None,
) -> None:
    """Write a simulated trade to the trade_learning table.

    Uses the EXACT same columns as the production daemon
    (record_trade_outcome.py). Extra metadata is embedded in
    signals_at_entry_json with a '_' prefix.
    """
    enriched_signals = dict(signals_at_entry)
    enriched_signals["_entry_price"] = entry_price
    enriched_signals["_exit_price"] = exit_price
    enriched_signals["_effective_score"] = confluence_score
    enriched_signals["_indicators_aligned"] = indicators_aligned
    enriched_signals["_threshold_used"] = threshold_used
    enriched_signals["_threshold_bucket"] = "production" if threshold_used >= entry_threshold else "exploration"
    enriched_signals["_source"] = "time_travel"

    # Add dollar-math metadata if available
    if trade_result is not None:
        enriched_signals["_position_size_usd"] = trade_result.position_size_usd
        enriched_signals["_gross_pnl_usd"] = trade_result.gross_pnl_usd
        enriched_signals["_net_pnl_usd"] = trade_result.net_pnl_usd
        enriched_signals["_entry_fee_usd"] = trade_result.entry_fee_usd
        enriched_signals["_exit_fee_usd"] = trade_result.exit_fee_usd
        enriched_signals["_total_fees_usd"] = trade_result.total_fees_usd
        enriched_signals["_atr_cap_applied"] = trade_result.atr_cap_applied

    try:
        with db_conn() as conn:
            conn.execute(
                """INSERT INTO trade_learning
                   (strategy_name, strategy_uid, symbol, direction,
                    entry_time, exit_time, outcome, pnl_pct, pnl_usdt,
                    duration_minutes, confluence_score_at_entry,
                    signals_at_entry_json, exit_reason,
                    sl_hit, trailing_stop_hit,
                    max_favorable_excursion_pct, max_adverse_excursion_pct)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    strategy_name,
                    strategy_uid,
                    symbol,
                    direction,
                    entry_time,
                    exit_time,
                    outcome,
                    pnl_pct,
                    pnl_usdt,
                    duration_minutes,
                    confluence_score,
                    json.dumps(enriched_signals),
                    exit_reason,
                    sl_hit,
                    trailing_stop_hit,
                    mfe_pct,
                    mae_pct,
                ),
            )
    except Exception as e:
        _log.error("Failed to write trade to DB: %s", e, exc_info=True)


# ── Main time-travel loop ──────────────────────────────────────────────────


def time_travel(
    symbols: List[str],
    start_date: str,
    end_date: str,
    thresholds: List[float],
    strategy_name: str = "momentum_rising",
    cooldown_bars: int = 3,
    batch_size: int = 100,
    dry_run: bool = False,
    min_threshold: Optional[float] = None,
    equity: Optional[float] = None,
    fee_rate: Optional[float] = None,
    per_symbol: bool = False,
    json_output: bool = False,
) -> dict:
    """Run the time-travel backtest with dollar-math metrics.

    Args:
        symbols: List of symbols to backtest
        start_date: ISO date string
        end_date: ISO date string or "now"
        thresholds: Entry thresholds to sweep
        strategy_name: Strategy config to use
        cooldown_bars: Bars to wait before re-entering same signal
        batch_size: Number of bars to fetch per API call
        dry_run: If True, don't write to DB
        min_threshold: Skip trades below this threshold
        equity: Starting equity for position sizing (default: from config)
        fee_rate: Exchange fee rate per side (default: from config)
        per_symbol: If True, compute per-symbol breakdown
        json_output: If True, return JSON-formatted results

    Returns:
        Summary dict with trade counts, dollar-math metrics, and per-symbol data
    """
    # Initialize
    init_db()
    config_loader = ConfigLoader(strategy_name=strategy_name)
    config = config_loader.config
    exchange = Exchange(config_loader)

    strategy_cfg = config.get("strategy", {})
    strategy_uid = strategy_cfg.get("uid", "legacy")
    primary_tf = strategy_cfg.get("timeframe", "1h")
    confirmation_tf = strategy_cfg.get("confirmation_tf", "4h")
    scoring = config.get("scoring", {})
    rsi_high = scoring.get("rsi_signal_high", 55)
    rsi_low = scoring.get("rsi_signal_low", 45)
    entry_threshold = scoring.get("entry_threshold", 6.5)

    # Position sizing params from config
    portfolio_cfg = config.get("portfolio", {})
    risk_cfg = config.get("risk", {})
    exit_rules = config.get("exit_rules", {})
    fees_cfg = config.get("fees", {})

    leverage = portfolio_cfg.get("leverage", 5)
    risk_per_trade_pct = portfolio_cfg.get("risk_per_trade_pct", 1.0)
    max_size_pct = risk_cfg.get("max_size_pct_of_equity", 25.0)
    min_size_pct = risk_cfg.get("min_size_pct_of_equity", 5.0)
    atr_cap_pct = portfolio_cfg.get("atr_cap_equity_pct", 2.0)
    kelly_min = risk_cfg.get("kelly_min", 0.05)
    kelly_max = risk_cfg.get("kelly_max", 0.25)
    wr_base = risk_cfg.get("kelly_win_rate_base", 0.35)
    wr_range = risk_cfg.get("kelly_win_rate_range", 0.40)
    avg_win_r = risk_cfg.get("kelly_avg_win_r", 2.0)

    # Equity: CLI flag > config fallback_equity_usdt
    starting_equity = equity if equity is not None else portfolio_cfg.get("fallback_equity_usdt", 1000.0)

    # Fee rate: CLI flag > config taker_rate > default 0.0006
    taker_rate = fee_rate if fee_rate is not None else fees_cfg.get("taker_rate", 0.0006)

    # Compute min_threshold
    if min_threshold is None:
        min_threshold = entry_threshold * 0.60

    # Build weight map
    indicator_configs = config.get("indicators", [])
    weight_map = {}
    compute_configs = []
    for ind_cfg in indicator_configs:
        name = ind_cfg.get("name", "")
        weight = ind_cfg.get("weight", 0)
        weight_map[name] = weight
        if weight > 0 or name in ("atr", "sr_levels", "momentum_quality"):
            compute_configs.append(ind_cfg)

    # Try to apply learning-adjusted weights
    try:
        from learning.weight_adjuster import compute_adjusted_weights
        adjusted = compute_adjusted_weights(
            weight_map, strategy_name,
            strategy_uid=strategy_uid,
            min_trades=config.get("learning", {}).get("min_trades_before_adjusting", 30),
            adjustment_boost=config.get("learning", {}).get("adjustment_boost", 1.2),
            adjustment_review_reduce=config.get("learning", {}).get("adjustment_review_reduce", 0.9),
        )
        if adjusted != weight_map:
            _log.info("Using learning-adjusted weights (changed: %s)",
                      [k for k in adjusted if adjusted.get(k) != weight_map.get(k)])
            weight_map = adjusted
    except Exception as e:
        _log.info("No learning-adjusted weights available, using config defaults: %s", e)

    # Parse dates
    start_dt = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
    if end_date.lower() == "now":
        end_dt = datetime.now(timezone.utc)
    else:
        end_dt = datetime.fromisoformat(end_date).replace(tzinfo=timezone.utc)

    _log.info("=" * 70)
    _log.info("TIME TRAVEL — Fast-forward daemon")
    _log.info("  Strategy: %s (uid: %s)", strategy_name, strategy_uid)
    _log.info("  Symbols: %s", symbols)
    _log.info("  Period: %s → %s", start_date, end_date)
    _log.info("  Thresholds: %s", thresholds)
    _log.info("  Primary TF: %s, Confirmation TF: %s", primary_tf, confirmation_tf)
    _log.info("  Weights: %s", {k: round(v, 3) for k, v in weight_map.items() if v != 0})
    _log.info("  Equity: $%.0f, Fee rate: %.4f (taker)", starting_equity, taker_rate)
    _log.info("  Dry run: %s", dry_run)
    _log.info("=" * 70)

    # Collect all trades for aggregate metrics
    all_trades: List[TradeResult] = []
    total_no_exit = 0
    trades_by_threshold = {t: {"wins": 0, "losses": 0, "trades": 0} for t in thresholds}
    trades_by_symbol = {s: 0 for s in symbols}

    for symbol in symbols:
        _log.info("-" * 50)
        _log.info("Processing %s", symbol)

        cooldown = TradeCooldown(cooldown_bars=cooldown_bars)

        # Fetch OHLCV for primary TF
        _log.info("  Fetching %s OHLCV (%s)...", symbol, primary_tf)
        df_primary = fetch_historical_ohlcv(exchange, symbol, primary_tf, start_dt, end_dt, batch_size)
        if df_primary is None or df_primary.empty:
            _log.warning("  No data for %s %s, skipping", symbol, primary_tf)
            continue

        # Fetch OHLCV for confirmation TF
        df_confirm = None
        if confirmation_tf and confirmation_tf != primary_tf:
            _log.info("  Fetching %s OHLCV (%s)...", symbol, confirmation_tf)
            df_confirm = fetch_historical_ohlcv(exchange, symbol, confirmation_tf, start_dt, end_dt, batch_size)

        _log.info("  Primary bars: %d, Confirmation bars: %d",
                  len(df_primary), len(df_confirm) if df_confirm is not None else 0)

        min_bars = 30
        total_bars = len(df_primary) - min_bars

        # Pre-compute indicators
        _log.info("  Pre-computing %s indicators (%s)...", symbol, primary_tf)
        t_precompute_start = _time.time()
        primary_indicators_all = precompute_indicators(
            df_primary, compute_configs, min_bars=min_bars,
            window=_INDICATOR_WINDOW, label=primary_tf,
        )
        t_precompute = _time.time() - t_precompute_start
        _log.info("  %s indicators done in %.1fs (%.0f bars/s)",
                  primary_tf, t_precompute, total_bars / max(t_precompute, 0.1))

        # Pre-compute confirmation TF indicators
        confirm_indicators_all: List[Optional[dict]] = [None] * len(df_primary)
        if df_confirm is not None and len(df_confirm) > min_bars:
            _log.info("  Pre-computing %s indicators (%s)...", symbol, confirmation_tf)
            t_confirm_start = _time.time()
            confirm_indicators_raw = precompute_indicators(
                df_confirm, compute_configs, min_bars=min_bars,
                window=_INDICATOR_WINDOW, label=confirmation_tf,
            )
            t_confirm = _time.time() - t_confirm_start
            _log.info("  %s indicators done in %.1fs", confirmation_tf, t_confirm)

            for bar_idx in range(min_bars, len(df_primary)):
                bar = df_primary.iloc[bar_idx]
                bar_time = bar.name if hasattr(bar, 'name') else df_primary.index[bar_idx]
                confirm_idx = find_confirmation_bar(df_confirm, bar_time, bar_idx)
                if confirm_idx is not None and confirm_idx < len(confirm_indicators_raw):
                    confirm_indicators_all[bar_idx] = confirm_indicators_raw[confirm_idx]

        # Score and simulate
        _log.info("  Scoring %d bars...", total_bars)
        t_score_start = _time.time()
        symbol_trades = 0
        entries_found = 0
        last_progress_log = _time.time()
        progress_interval_sec = 15

        for bar_idx in range(min_bars, len(df_primary)):
            # Progress logging
            bars_processed = bar_idx - min_bars
            if bars_processed > 0 and (_time.time() - last_progress_log >= progress_interval_sec or bar_idx == len(df_primary) - 1):
                elapsed = _time.time() - t_score_start
                pct = bars_processed / total_bars * 100
                bars_per_sec = bars_processed / max(elapsed, 0.01)
                eta_sec = (total_bars - bars_processed) / max(bars_per_sec, 0.1)
                _log.info(
                    "  [%s] %d/%d bars (%.1f%%) | %d entries | %d trades | %.0f bars/s | ETA: %s",
                    symbol, bars_processed, total_bars, pct,
                    entries_found, symbol_trades,
                    bars_per_sec,
                    "<1m" if eta_sec < 60 else f"{int(eta_sec // 60)}m{int(eta_sec % 60):02d}s",
                )
                last_progress_log = _time.time()

            # Use pre-computed indicators
            primary_indicators = primary_indicators_all[bar_idx]
            if primary_indicators is None:
                continue

            bar = df_primary.iloc[bar_idx]
            bar_time = bar.name if hasattr(bar, 'name') else df_primary.index[bar_idx]

            # Build indicator dict for scoring
            indicators = {primary_tf: primary_indicators}
            confirm_data = confirm_indicators_all[bar_idx]
            if confirm_data is not None and isinstance(confirm_data, dict) and confirm_data.get("ok"):
                indicators[confirmation_tf] = confirm_data

            # Compute confluence score
            normalized_score, _, indicators_aligned, confirmation_misaligned = compute_confluence_score(
                indicators, weight_map, config
            )

            if confirmation_misaligned:
                continue

            # Determine direction
            if normalized_score > 0:
                direction = "Long"
            elif normalized_score < 0:
                direction = "Short"
            else:
                continue

            # Check each threshold
            for threshold in thresholds:
                if abs(normalized_score) < threshold:
                    continue

                if threshold < min_threshold:
                    continue

                if not cooldown.can_enter(symbol, threshold, direction, bar_idx, normalized_score):
                    continue

                entries_found += 1

                # ENTRY
                entry_price = float(bar["close"])
                atr_result = primary_indicators.get("atr")
                atr_value = atr_result.get("value", 0) if isinstance(atr_result, dict) else 0

                if atr_value == 0:
                    try:
                        start_idx = max(0, bar_idx - _INDICATOR_WINDOW)
                        df_slice = df_primary.iloc[start_idx:bar_idx + 1]
                        atr_result = compute_indicator("atr", df_slice, period=14)
                        atr_value = atr_result.get("value", 0) if isinstance(atr_result, dict) else 0
                    except Exception:
                        pass

                if atr_value == 0:
                    _log.debug("  No ATR at bar %d, skipping entry", bar_idx)
                    continue

                # Simulate exit
                exit_info = simulate_exit(
                    df_primary, bar_idx, entry_price, direction, atr_value, config
                )

                if exit_info is None:
                    total_no_exit += 1
                    continue

                # Compute SL price for position sizing (same as simulate_exit)
                atr_mult = exit_rules.get("hard_stop", {}).get("width_atr_multiplier", 1.5)
                sl_distance = atr_value * atr_mult
                is_long = direction.lower() in ("long", "buy")
                if is_long:
                    sl_price = entry_price - sl_distance
                else:
                    sl_price = entry_price + sl_distance

                # Compute dollar-math for this trade
                trade_result = compute_backtest_trade(
                    entry_price=entry_price,
                    exit_price=exit_info["exit_price"],
                    direction=direction,
                    sl_price=sl_price,
                    atr_value=atr_value,
                    confluence_score=normalized_score,
                    equity=starting_equity,
                    leverage=leverage,
                    risk_per_trade_pct=risk_per_trade_pct,
                    max_size_pct=max_size_pct,
                    min_size_pct=min_size_pct,
                    atr_cap_pct=atr_cap_pct,
                    kelly_min=kelly_min,
                    kelly_max=kelly_max,
                    wr_base=wr_base,
                    wr_range=wr_range,
                    avg_win_r=avg_win_r,
                    fee_rate=taker_rate,
                    exit_reason=exit_info["exit_reason"],
                    pnl_pct=exit_info["pnl_pct"],
                    duration_bars=exit_info["duration_bars"],
                    sl_hit=exit_info["sl_hit"],
                    trailing_stop_hit=exit_info["trailing_stop_hit"],
                    mfe_pct=exit_info.get("mfe_pct", 0),
                    mae_pct=exit_info.get("mae_pct", 0),
                    symbol=symbol,
                    threshold=threshold,
                    indicators_aligned=indicators_aligned,
                )

                # Build signals_at_entry_json
                signals = build_signals_at_entry(primary_indicators, rsi_high, rsi_low)

                # Determine outcome (using dollar-math if available)
                if trade_result is not None:
                    outcome = "win" if trade_result.is_winner else "loss"
                    pnl_usdt = trade_result.net_pnl_usd
                else:
                    # Fallback to percentage-based outcome
                    pnl = exit_info["pnl_pct"]
                    if pnl > 0.01:
                        outcome = "win"
                    elif pnl < -0.01:
                        outcome = "loss"
                    else:
                        outcome = "breakeven"
                    pnl_usdt = 0.0

                # Calculate timestamps
                entry_time = bar_to_iso(bar_time)
                exit_bar_idx = exit_info["exit_bar"]
                exit_bar = df_primary.iloc[exit_bar_idx]
                exit_time = bar_to_iso(exit_bar.name if hasattr(exit_bar, 'name') else df_primary.index[exit_bar_idx])
                duration_minutes = exit_info["duration_bars"] * tf_to_minutes(primary_tf)

                # Write to DB
                if not dry_run:
                    _write_trade(
                        symbol=symbol,
                        direction=direction,
                        strategy_name=strategy_name,
                        strategy_uid=strategy_uid,
                        entry_time=entry_time,
                        exit_time=exit_time,
                        outcome=outcome,
                        pnl_pct=exit_info["pnl_pct"],
                        pnl_usdt=pnl_usdt,
                        duration_minutes=duration_minutes,
                        confluence_score=round(normalized_score, 2),
                        signals_at_entry=signals,
                        indicators_aligned=indicators_aligned,
                        entry_price=entry_price,
                        exit_price=exit_info["exit_price"],
                        exit_reason=exit_info["exit_reason"],
                        sl_hit=exit_info["sl_hit"],
                        trailing_stop_hit=exit_info["trailing_stop_hit"],
                        mfe_pct=exit_info.get("mfe_pct", 0),
                        mae_pct=exit_info.get("mae_pct", 0),
                        threshold_used=threshold,
                        entry_threshold=entry_threshold,
                        trade_result=trade_result,
                    )

                # Record entry for cooldown
                cooldown.record_entry(symbol, threshold, direction, bar_idx)

                # Collect trade result for aggregate metrics
                if trade_result is not None:
                    all_trades.append(trade_result)

                # Stats
                trades_by_threshold[threshold]["trades"] += 1
                if outcome == "win":
                    trades_by_threshold[threshold]["wins"] += 1
                elif outcome == "loss":
                    trades_by_threshold[threshold]["losses"] += 1
                trades_by_symbol[symbol] = trades_by_symbol.get(symbol, 0) + 1
                symbol_trades += 1

        t_score = _time.time() - t_score_start
        _log.info("  %s: %d trades generated (%d entries evaluated) in %.1fs",
                  symbol, symbol_trades, entries_found, t_score)

    # ── Compute aggregate metrics ────────────────────────────────────────────
    metrics = compute_aggregate_metrics(all_trades, starting_equity)
    per_symbol_metrics = compute_per_symbol_metrics(all_trades, starting_equity) if per_symbol else None

    # Per-threshold breakdown
    by_threshold_summary = {}
    for t in thresholds:
        tb = trades_by_threshold[t]
        t_resolved = tb["wins"] + tb["losses"]
        t_rate = (tb["wins"] / t_resolved * 100) if t_resolved > 0 else 0
        by_threshold_summary[t] = {
            "wins": tb["wins"],
            "losses": tb["losses"],
            "trades": tb["trades"],
            "win_rate_pct": round(t_rate, 1),
        }

    # Build summary
    summary = {
        "total_trades": metrics["total_trades"],
        "wins": metrics["wins"],
        "losses": metrics["losses"],
        "no_exit": total_no_exit,
        "win_rate_pct": metrics["win_rate_pct"],
        "by_threshold": by_threshold_summary,
        "by_symbol": trades_by_symbol,
        # Dollar-math metrics
        "total_return_pct": metrics["total_return_pct"],
        "profit_factor": metrics["profit_factor"],
        "expectancy_per_trade_usd": metrics["expectancy_per_trade_usd"],
        "avg_win_usd": metrics["avg_win_usd"],
        "avg_loss_usd": metrics["avg_loss_usd"],
        "win_loss_ratio": metrics["win_loss_ratio"],
        "max_drawdown_pct": metrics["max_drawdown_pct"],
        "total_fees_usd": metrics["total_fees_usd"],
        "equity": metrics["equity"],
    }

    # Output
    if json_output:
        print(format_summary_json(metrics, strategy_name, start_date, end_date, per_symbol_metrics))
    else:
        print(format_summary_table(metrics, strategy_name, start_date, end_date, per_symbol_metrics))

    # Also log the traditional summary
    _log.info("=" * 70)
    _log.info("TIME TRAVEL COMPLETE")
    _log.info("  Total trades: %d (resolved: %d, no exit: %d)",
              metrics["total_trades"], metrics["wins"] + metrics["losses"], total_no_exit)
    _log.info("  Wins: %d, Losses: %d, Win rate: %.1f%%",
              metrics["wins"], metrics["losses"], metrics["win_rate_pct"])
    _log.info("  Total Return: %+.1f%%, Profit Factor: %.2f",
              metrics["total_return_pct"], metrics["profit_factor"])
    _log.info("  Expectancy/Trade: $%.2f, Win/Loss Ratio: %.2f",
              metrics["expectancy_per_trade_usd"], metrics["win_loss_ratio"])
    _log.info("  Max Drawdown: -%.1f%%, Total Fees: $%.2f",
              metrics["max_drawdown_pct"], metrics["total_fees_usd"])
    _log.info("  By threshold:")
    for t in thresholds:
        tb = by_threshold_summary[t]
        _log.info("    %.1f: %d trades, %d wins, %d losses, %.1f%% win rate",
                  t, tb["trades"], tb["wins"], tb["losses"], tb["win_rate_pct"])
    _log.info("  By symbol: %s", trades_by_symbol)
    if dry_run:
        _log.info("  (DRY RUN — no trades written to DB)")
    _log.info("=" * 70)

    return summary


# ── CLI ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Time-travel daemon: replay scoring on historical data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Backtest BTCUSDT from Jan 2025 with default thresholds
  python -m scripts.time_travel --start 2025-01-01 --symbols BTCUSDT

  # Backtest multiple symbols with custom thresholds
  python -m scripts.time_travel --start 2025-06-01 --end 2025-12-01 \\
      --symbols BTCUSDT ETHUSDT SOLUSDT --thresholds 3,4,5,6.5

  # Dollar-math metrics with per-symbol breakdown
  python -m scripts.time_travel --start 2025-01-01 --per-symbol

  # JSON output for machine processing
  python -m scripts.time_travel --start 2025-01-01 --json

  # Custom equity and fee rate
  python -m scripts.time_travel --start 2025-01-01 --equity 5000 --fee-rate 0.0004

  # Dry run (don't write to DB)
  python -m scripts.time_travel --start 2025-01-01 --symbols BTCUSDT --dry-run
        """,
    )
    parser.add_argument("--start", required=True, help="Start date (ISO format, e.g., 2025-01-01)")
    parser.add_argument("--end", default="now", help="End date (ISO format or 'now', default: now)")
    parser.add_argument("--symbols", nargs="+", default=None, help="Symbols to backtest. Default: from strategy config.")
    parser.add_argument("--thresholds", default=None, help="Comma-separated entry thresholds (default: derived from config)")
    parser.add_argument("--strategy", default="momentum_rising", help="Strategy name (default: momentum_rising)")
    parser.add_argument("--cooldown", type=int, default=3, help="Bars to wait before re-entering same signal (default: 3)")
    parser.add_argument("--dry-run", action="store_true", help="Don't write trades to DB (preview only)")
    parser.add_argument("--batch-size", type=int, default=500, help="OHLCV bars per API call (default: 500)")
    parser.add_argument("--min-threshold", type=float, default=None, help="Skip trades below this threshold (default: 60%% of entry_threshold)")
    parser.add_argument("--equity", type=float, default=None, help="Starting equity for position sizing (default: from config)")
    parser.add_argument("--fee-rate", type=float, default=None, help="Exchange fee rate per side (default: from config, Bitget VIP0 taker)")
    parser.add_argument("--per-symbol", action="store_true", help="Include per-symbol breakdown in output")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Output results as JSON")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Log level (default: INFO)")

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Load config
    config_loader = ConfigLoader(strategy_name=args.strategy)

    # Parse thresholds
    if args.thresholds is not None:
        thresholds = [float(t.strip()) for t in args.thresholds.split(",")]
    else:
        entry_threshold = config_loader.config.get("scoring", {}).get("entry_threshold", 6.5)
        threshold_fractions = [0.50, 0.65, 0.80, 1.00]
        thresholds = [round(entry_threshold * f, 1) for f in threshold_fractions]
        _log.info("Derived thresholds from entry_threshold=%.1f: %s", entry_threshold, thresholds)

    # Resolve symbols
    symbols = args.symbols
    if symbols is None:
        symbols_cfg = config_loader.config.get("symbols", {})
        always_watch = symbols_cfg.get("always_watch", [])
        if always_watch:
            symbols = always_watch
            _log.info("Using symbols from strategy config: %s", symbols)
        else:
            _log.error("No symbols specified and strategy config has no always_watch list")
            sys.exit(1)

    # Run time travel
    summary = time_travel(
        symbols=symbols,
        start_date=args.start,
        end_date=args.end,
        thresholds=thresholds,
        strategy_name=args.strategy,
        cooldown_bars=args.cooldown,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
        min_threshold=args.min_threshold,
        equity=args.equity,
        fee_rate=args.fee_rate,
        per_symbol=args.per_symbol,
        json_output=args.json_output,
    )

    sys.exit(0 if summary["total_trades"] > 0 else 1)


if __name__ == "__main__":
    main()