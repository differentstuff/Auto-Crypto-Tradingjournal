#!/usr/bin/env python3
"""Estimate fee-adjusted PnL from existing backtest JSONs for triage/ranking only.

ESTIMATE -- ranking/triage only, not a substitute for a real rerun.
Uses equity_curve (unaffected by the partial-PnL bug) for true gross PnL,
and a rough 2*taker_rate*total_notional fee estimate.
Does NOT account for second-order fee effects from multiple partial-exit legs.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None


def load_config_fees(config_path: str | None = None) -> float:
    if config_path and os.path.exists(config_path):
        with open(config_path) as f:
            if yaml:
                cfg = yaml.safe_load(f)
            else:
                for line in f:
                    if line.strip().startswith("taker_rate:"):
                        return float(line.split(":")[1].strip().split("#")[0].strip())
                return 0.0006
        return cfg.get("fees", {}).get("taker_rate", 0.0006)
    return 0.0006


def analyze_backtest(path: str, taker_rate: float) -> dict | None:
    with open(path) as f:
        data = json.load(f)

    equity_curve = data.get("equity_curve", [])
    trades = data.get("trades", [])

    if not equity_curve or not trades:
        return None

    true_gross_pnl = equity_curve[-1]["equity"] - equity_curve[0]["equity"]
    total_notional = sum(t.get("size_usdt", 0) or 0 for t in trades)
    est_total_fees = taker_rate * total_notional * 2
    est_net_pnl = true_gross_pnl - est_total_fees

    t0 = equity_curve[0]["timestamp"][:10]
    t1 = equity_curve[-1]["timestamp"][:10]
    try:
        d0 = datetime.strptime(t0, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        d1 = datetime.strptime(t1, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        months_elapsed = max((d1 - d0).days / 30.44, 0.01)
    except ValueError:
        months_elapsed = 1.0

    est_per_month = est_net_pnl / months_elapsed

    return {
        "strategy": data.get("strategy", os.path.basename(os.path.dirname(path))),
        "true_gross_pnl": round(true_gross_pnl, 2),
        "est_total_fees": round(est_total_fees, 2),
        "est_net_pnl": round(est_net_pnl, 2),
        "months_elapsed": round(months_elapsed, 2),
        "est_per_month": round(est_per_month, 2),
        "total_notional": round(total_notional, 2),
        "total_trades": len(trades),
    }


def main():
    print("=" * 90)
    print("ESTIMATE -- ranking/triage only, not a substitute for a real rerun")
    print("=" * 90)

    project_root = Path(__file__).resolve().parent.parent
    config_path = project_root / "config" / "default.yaml"
    taker_rate = load_config_fees(str(config_path))

    print(f"Fee config: taker_rate={taker_rate} (from {config_path})")
    print()

    base_dir = project_root.parent / "temp"

    results = []
    for sdir in sorted(base_dir.iterdir()):
        if not sdir.is_dir() or not sdir.name.startswith("strategy_"):
            continue
        for f in sdir.glob("backtest_*.json"):
            if "deepdive" in f.name:
                continue
            r = analyze_backtest(str(f), taker_rate)
            if r:
                results.append(r)

    if not results:
        print("No backtest results found.")
        return

    results.sort(key=lambda x: x["est_per_month"], reverse=True)

    hdr = f"{'Strategy':<14} {'Gross PnL':>12} {'Est Fees':>12} {'Est Net PnL':>14} {'Months':>8} {'Est $/mo':>12} {'Notional':>14} {'Trades':>8}"
    print(hdr)
    print("-" * len(hdr))
    for r in results:
        print(
            f"{r['strategy']:<14} "
            f"{r['true_gross_pnl']:>12.2f} "
            f"{r['est_total_fees']:>12.2f} "
            f"{r['est_net_pnl']:>14.2f} "
            f"{r['months_elapsed']:>8.2f} "
            f"{r['est_per_month']:>12.2f} "
            f"{r['total_notional']:>14.2f} "
            f"{r['total_trades']:>8d}"
        )

    print()
    print("ESTIMATE -- ranking/triage only, not a substitute for a real rerun")


if __name__ == "__main__":
    main()
