#!/usr/bin/env python3
"""
Backtest Log & Results Analyzer
===============================

Filters huge backtest logs to show only meaningful cycles (trades, exits,
signals) and summarizes JSON result files.

Usage examples:
  # Show all non-wait cycles from a log (full detail)
  python scripts/analyze_backtest.py --log temp/backtest-stdout-full.log

  # One-line summary per interesting cycle
  python scripts/analyze_backtest.py --log temp/backtest-stdout-full.log --summary

  # Also include cycles where exits were signalled/approved (even if action=wait)
  python scripts/analyze_backtest.py --log temp/backtest-stdout-full.log --include-exits

  # Show a specific cycle
  python scripts/analyze_backtest.py --log temp/backtest-stdout-full.log --cycle 66

  # Analyze the latest JSON result file
  python scripts/analyze_backtest.py --results temp/results/

  # Analyze a specific result file
  python scripts/analyze_backtest.py --results temp/results/backtest_...json

  # Combined: log + results
  python scripts/analyze_backtest.py --log temp/backtest-stdout-full.log --results temp/results/
"""

import argparse
import json
import re
import sys
from pathlib import Path

# ── Regex patterns ──────────────────────────────────────────────────────────

RE_CYCLE_START = re.compile(r"Cycle (\d+) started")
RE_CYCLE_COMPLETE = re.compile(
    r"Cycle (\d+) complete: action=(\w+), enzymes=\[([^\]]*)\], duration=(\d+)ms"
)
RE_PAPER_ENTRY = re.compile(
    r"PAPER ENTRY: (Long|Short) (\S+) entry=([\d.]+) sl=([\d.]+) tp1=([\d.]+) size=([\d.]+)"
)
RE_TRADE_EXECUTED = re.compile(
    r"Trade executed: (Long|Short) (\S+) size=([\d.]+) action=(\w+)"
)
RE_APPROVED_TRADE = re.compile(
    r"Approved: (Long|Short) (\S+) size=([\d.]+) kelly=([\d.]+) eff_score=([\d.]+)"
)
RE_TP1_HIT = re.compile(r"TP1 HIT: (\S+) (long|short) mark=([\d.]+) tp1=([\d.]+)")
RE_SL_HIT = re.compile(r"SL HIT: (\S+) (long|short) mark=([\d.]+) sl=([\d.]+)")
RE_EXIT_APPROVED = re.compile(r"Exit approved: (\S+) reason=(\S+)")
RE_EXIT_DENIED = re.compile(r"Exit denied for (\S+): (.+)")
RE_TRAIL_ACTIVATED = re.compile(
    r"Progressive trail activated for (\S+) at profit=([\d.]+)% .*"
)
RE_ISC_BLOCK = re.compile(r"ISC gate: blocking trade enzymes \[([^\]]*)\] — failed ISCs: \[([^\]]*)\]")
RE_VOL_CAP = re.compile(r"Volatility cap applied: notional ([\d.]+) → ([\d.]+) \(ATR%=([\d.]+)%, cap_pct=([\d.]+)%\)")
RE_CONFLUENCE = re.compile(r"Scored confluence: (\d+)/(\d+) symbols above relaxed_threshold=([\d.]+), top=(\S+)")
RE_MARK_PRICES = re.compile(r"Updated mark prices for (\d+)/(\d+) positions")


# ── Log Analysis ────────────────────────────────────────────────────────────

def analyze_log(log_path: str, summary_only: bool = False, include_exits: bool = False,
                cycle_filter: int | None = None, action_filter: str | None = None):
    """Stream the log and print interesting cycles."""

    current_cycle_lines: list[str] = []
    current_cycle_num: int | None = None
    current_action: str | None = None
    stats = {"total": 0, "wait": 0, "trade_open": 0, "other": 0, "shown": 0}

    with open(log_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            # Detect cycle start
            m_start = RE_CYCLE_START.search(line)
            if m_start:
                current_cycle_num = int(m_start.group(1))
                current_cycle_lines = [line]
                current_action = None
                continue

            # Accumulate lines within the cycle
            if current_cycle_num is not None:
                current_cycle_lines.append(line)

            # Detect cycle completion
            m_complete = RE_CYCLE_COMPLETE.search(line)
            if m_complete and current_cycle_num is not None:
                stats["total"] += 1
                action = m_complete.group(2)
                current_action = action
                stats[action if action in stats else "other"] = stats.get(action, 0) + 1
                if action not in stats:
                    stats[action] = 1

                # Decide whether to show this cycle
                should_show = False
                if cycle_filter is not None:
                    should_show = current_cycle_num == cycle_filter
                elif action_filter:
                    should_show = action == action_filter
                elif action != "wait":
                    should_show = True
                elif include_exits and _cycle_has_exit_signal(current_cycle_lines):
                    should_show = True

                if should_show:
                    stats["shown"] += 1
                    if summary_only:
                        _print_cycle_summary(current_cycle_num, action, current_cycle_lines)
                    else:
                        _print_cycle_detail(current_cycle_num, action, current_cycle_lines)

                # Reset
                current_cycle_num = None
                current_cycle_lines = []
                current_action = None

    # Print stats
    print("\n" + "=" * 80)
    print("LOG STATISTICS")
    print("=" * 80)
    print(f"  Total cycles:      {stats['total']:>8,}")
    print(f"  Wait cycles:       {stats.get('wait', 0):>8,}")
    print(f"  Trade-open cycles: {stats.get('trade_open', 0):>8,}")
    for k, v in stats.items():
        if k not in ("total", "wait", "trade_open", "other", "shown"):
            print(f"  {k} cycles:        {v:>8,}")
    other = stats.get("other", 0)
    if other:
        print(f"  Other cycles:      {other:>8,}")
    print(f"  ─────────────────────────────")
    print(f"  Cycles shown:      {stats['shown']:>8,}")
    pct = (stats["shown"] / stats["total"] * 100) if stats["total"] else 0
    print(f"  ({pct:.2f}% of total)")


def _cycle_has_exit_signal(lines: list[str]) -> bool:
    """Check if a wait cycle contains exit-related signals."""
    text = "".join(lines)
    return bool(
        RE_TP1_HIT.search(text)
        or RE_SL_HIT.search(text)
        or RE_EXIT_APPROVED.search(text)
        or RE_TRAIL_ACTIVATED.search(text)
        or ("Exit denied" in text)
    )


def _extract_trade_info(lines: list[str]) -> dict:
    """Extract trade-related numbers from cycle lines."""
    info = {}
    text = "".join(lines)

    if m := RE_PAPER_ENTRY.search(text):
        info["direction"] = m.group(1)
        info["symbol"] = m.group(2)
        info["entry"] = float(m.group(3))
        info["sl"] = float(m.group(4))
        info["tp1"] = float(m.group(5))
        info["size"] = float(m.group(6))

    if m := RE_APPROVED_TRADE.search(text):
        info["approved_direction"] = m.group(1)
        info["approved_symbol"] = m.group(2)
        info["approved_size"] = float(m.group(3))
        info["kelly"] = float(m.group(4))
        info["eff_score"] = float(m.group(5))

    if m := RE_VOL_CAP.search(text):
        info["atr_pct"] = float(m.group(3))
        info["volatility_cap_pct"] = float(m.group(4))

    if m := RE_CONFLUENCE.search(text):
        info["confluence_above"] = int(m.group(1))
        info["confluence_total"] = int(m.group(2))
        info["confluence_threshold"] = float(m.group(3))
        info["confluence_top"] = m.group(4)

    if m := RE_TP1_HIT.search(text):
        info["tp1_hit_symbol"] = m.group(1)
        info["tp1_hit_direction"] = m.group(2)
        info["tp1_hit_mark"] = float(m.group(3))
        info["tp1_hit_target"] = float(m.group(4))

    if m := RE_SL_HIT.search(text):
        info["sl_hit_symbol"] = m.group(1)
        info["sl_hit_direction"] = m.group(2)
        info["sl_hit_mark"] = float(m.group(3))
        info["sl_hit_target"] = float(m.group(4))

    if m := RE_EXIT_APPROVED.search(text):
        info["exit_symbol"] = m.group(1)
        info["exit_reason"] = m.group(2)

    if m := RE_EXIT_DENIED.search(text):
        info["exit_denied_symbol"] = m.group(1)
        info["exit_denied_reason"] = m.group(2)

    if m := RE_TRAIL_ACTIVATED.search(text):
        info["trail_symbol"] = m.group(1)
        info["trail_profit_pct"] = float(m.group(2))

    if m := RE_MARK_PRICES.search(text):
        info["positions_updated"] = int(m.group(1))
        info["positions_total"] = int(m.group(2))

    # ISC blocks
    isc_blocks = RE_ISC_BLOCK.findall(text)
    if isc_blocks:
        info["isc_blocked_enzymes"] = [b[0] for b in isc_blocks]
        info["isc_failed"] = list(set(b[1] for b in isc_blocks))

    return info


def _print_cycle_summary(cycle_num: int, action: str, lines: list[str]):
    """Print a one-line summary of a cycle."""
    info = _extract_trade_info(lines)
    parts = [f"Cycle {cycle_num:>5} | {action:<11}"]

    if "symbol" in info:
        parts.append(f"{info['direction']} {info['symbol']}")
        parts.append(f"entry={info['entry']:.2f}")
        parts.append(f"SL={info['sl']:.2f}")
        parts.append(f"TP1={info['tp1']:.2f}")
        parts.append(f"size={info['size']}")
        if "eff_score" in info:
            parts.append(f"eff={info['eff_score']}")
        if "kelly" in info:
            parts.append(f"kelly={info['kelly']}")

    if "tp1_hit_symbol" in info:
        parts.append(f"TP1 HIT {info['tp1_hit_symbol']} mark={info['tp1_hit_mark']:.2f}")

    if "sl_hit_symbol" in info:
        parts.append(f"SL HIT {info['sl_hit_symbol']} mark={info['sl_hit_mark']:.2f}")

    if "exit_reason" in info:
        parts.append(f"EXIT APPROVED {info['exit_symbol']} reason={info['exit_reason']}")

    if "trail_profit_pct" in info:
        parts.append(f"trail@{info['trail_profit_pct']}%")

    if "isc_failed" in info:
        parts.append(f"ISC blocked: {','.join(info['isc_failed'])}")

    print(" | ".join(parts))


def _print_cycle_detail(cycle_num: int, action: str, lines: list[str]):
    """Print full detail of a cycle with extracted trade info."""
    info = _extract_trade_info(lines)

    print("\n" + "─" * 80)
    print(f"  CYCLE {cycle_num}  |  action={action}")
    print("─" * 80)

    # Print extracted trade info first
    if info:
        print("  ▸ Extracted trade info:")
        if "symbol" in info:
            risk_pct = ((info["entry"] - info["sl"]) / info["entry"]) * 100
            reward_pct = ((info["tp1"] - info["entry"]) / info["entry"]) * 100
            rr = reward_pct / risk_pct if risk_pct else 0
            print(f"    Direction:     {info['direction']}")
            print(f"    Symbol:        {info['symbol']}")
            print(f"    Entry:         {info['entry']:.2f}")
            print(f"    Stop Loss:     {info['sl']:.2f}  (risk {risk_pct:.2f}%)")
            print(f"    TP1:           {info['tp1']:.2f}  (reward {reward_pct:.2f}%)")
            print(f"    R:R ratio:     {rr:.2f}")
            print(f"    Size (USDT):   {info['size']}")

        if "eff_score" in info:
            print(f"    Eff. score:    {info['eff_score']}")
        if "kelly" in info:
            print(f"    Kelly:         {info['kelly']}")
        if "atr_pct" in info:
            print(f"    ATR%:          {info['atr_pct']:.2f}%  (vol cap {info['volatility_cap_pct']}%)")
        if "confluence_top" in info:
            print(f"    Confluence:    {info['confluence_above']}/{info['confluence_total']} above threshold "
                  f"(top={info['confluence_top']}, thresh={info['confluence_threshold']})")

        if "tp1_hit_symbol" in info:
            print(f"    TP1 HIT:       {info['tp1_hit_symbol']} {info['tp1_hit_direction']} "
                  f"mark={info['tp1_hit_mark']:.2f} target={info['tp1_hit_target']:.2f}")
        if "sl_hit_symbol" in info:
            print(f"    SL HIT:        {info['sl_hit_symbol']} {info['sl_hit_direction']} "
                  f"mark={info['sl_hit_mark']:.2f} target={info['sl_hit_target']:.2f}")
        if "trail_profit_pct" in info:
            print(f"    Trail active:  {info['trail_symbol']} @ {info['trail_profit_pct']}% profit")
        if "exit_reason" in info:
            print(f"    Exit approved: {info['exit_symbol']} reason={info['exit_reason']}")
        if "exit_denied_reason" in info:
            print(f"    Exit denied:   {info['exit_denied_symbol']} — {info['exit_denied_reason']}")
        if "positions_total" in info:
            print(f"    Positions:     {info['positions_updated']}/{info['positions_total']} updated")
        if "isc_failed" in info:
            print(f"    ISC blocked:   enzymes={info['isc_blocked_enzymes']} failed={info['isc_failed']}")
        print()

    # Then print the raw log lines
    for line in lines:
        # Strip the timestamp prefix for readability, keep the rest
        # Format: "2026-06-16 16:29:55,796 [INFO] module: message"
        stripped = line.rstrip()
        if stripped:
            print(f"  {stripped}")


# ── Results Analysis ────────────────────────────────────────────────────────

def find_latest_result(results_path: str) -> Path:
    """Find the latest JSON result file in a directory, or return the path if it's a file."""
    p = Path(results_path)
    if p.is_file():
        return p
    jsons = sorted(p.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not jsons:
        raise FileNotFoundError(f"No JSON files found in {results_path}")
    return jsons[0]


def analyze_results(results_path: str, trades_only: bool = False):
    """Analyze a JSON result file."""
    path = find_latest_result(results_path)
    print(f"Loading results: {path.name}")
    print("=" * 80)

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    summary = data.get("summary", {})
    trades = data.get("trades", [])
    equity_curve = data.get("equity_curve", [])

    if not trades_only:
        # Summary
        print("\nSUMMARY")
        print("-" * 40)
        for k, v in summary.items():
            print(f"  {k:<20} {v}")

        # Equity curve stats
        print("\nEQUITY CURVE")
        print("-" * 40)
        actions = {}
        for e in equity_curve:
            actions[e["action"]] = actions.get(e["action"], 0) + 1
        print(f"  Total entries:     {len(equity_curve):,}")
        for action, count in sorted(actions.items(), key=lambda x: -x[1]):
            pct = count / len(equity_curve) * 100 if equity_curve else 0
            print(f"  {action:<15} {count:>6,}  ({pct:.2f}%)")

        # Non-wait equity entries
        non_wait = [e for e in equity_curve if e["action"] != "wait"]
        if non_wait:
            print(f"\n  Non-wait entries ({len(non_wait)}):")
            for e in non_wait:
                print(f"    {e['timestamp']}  equity={e['equity']:.2f}  "
                      f"positions={e['open_positions']}  action={e['action']}")

    # Trades table
    print("\nTRADES")
    print("-" * 40)
    if not trades:
        print("  No trades recorded.")
    else:
        print(f"  {'#':>3}  {'Symbol':<10} {'Dir':<5} {'Entry':>12} {'Exit':>12} "
              f"{'SL':>12} {'TP1':>12} {'Size':>6} {'PnL':>10} {'Result':<8} {'Reason':<20}")
        print(f"  {'─'*3}  {'─'*10} {'─'*5} {'─'*12} {'─'*12} {'─'*12} {'─'*12} {'─'*6} {'─'*10} {'─'*8} {'─'*20}")
        for i, t in enumerate(trades):
            entry = t.get("entry_price")
            exit_p = t.get("exit_price")
            sl = t.get("sl_price")
            tp1 = t.get("tp1")
            pnl = t.get("net_pnl_usd")
            is_winner = t.get("is_winner")
            reason = t.get("exit_reason") or ""

            result = ""
            if is_winner is True:
                result = "WIN"
            elif is_winner is False:
                result = "LOSS"
            elif exit_p is not None:
                result = "CLOSED"

            print(f"  {i+1:>3}  {t.get('symbol',''):<10} {t.get('direction',''):<5} "
                  f"{entry:>12.2f} {exit_p if exit_p else '—':>12} "
                  f"{sl if sl else '—':>12} {tp1 if tp1 else '—':>12} "
                  f"{t.get('size_usdt',0):>6.2f} "
                  f"{pnl if pnl is not None else '—':>10} "
                  f"{result:<8} {reason:<20}")

        # Trade stats
        closed = [t for t in trades if t.get("exit_price") is not None]
        open_trades = [t for t in trades if t.get("exit_price") is None]
        print(f"\n  Open trades:   {len(open_trades)}")
        print(f"  Closed trades: {len(closed)}")
        if closed:
            wins = [t for t in closed if t.get("is_winner")]
            losses = [t for t in closed if t.get("is_winner") is False]
            total_pnl = sum(t.get("net_pnl_usd", 0) for t in closed)
            print(f"  Wins:          {len(wins)}")
            print(f"  Losses:        {len(losses)}")
            print(f"  Win rate:      {len(wins)/len(closed)*100:.1f}%")
            print(f"  Total PnL:     {total_pnl:.2f} USDT")


# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Analyze backtest logs and results. Filters out 'wait' cycles to show what the system actually did.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--log", metavar="PATH", help="Path to backtest log file")
    parser.add_argument("--results", metavar="PATH", help="Path to JSON result file or results directory")
    parser.add_argument("--summary", action="store_true", help="Log: one-line summary per cycle (no full detail)")
    parser.add_argument("--include-exits", action="store_true",
                        help="Log: also show wait cycles that contain exit signals (TP1/SL hits, exit approvals)")
    parser.add_argument("--cycle", type=int, metavar="N", help="Log: show only this specific cycle number")
    parser.add_argument("--action", metavar="ACTION", help="Log: filter to a specific action (e.g. trade_open)")
    parser.add_argument("--trades-only", action="store_true", help="Results: show trades table only")
    args = parser.parse_args()

    if not args.log and not args.results:
        parser.print_help()
        sys.exit(1)

    if args.results:
        analyze_results(args.results, trades_only=args.trades_only)

    if args.log:
        analyze_log(
            args.log,
            summary_only=args.summary,
            include_exits=args.include_exits,
            cycle_filter=args.cycle,
            action_filter=args.action,
        )


if __name__ == "__main__":
    main()