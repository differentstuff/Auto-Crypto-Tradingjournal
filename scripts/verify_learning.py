#!/usr/bin/env python3
"""
scripts/verify_learning.py — Verify that the learning system actually improved anything.

Checks L1 (signal accuracy verdicts), L2 (weight adjustments), L3 (rulebook generation).
Generates equity curve, weight evolution, and accuracy bar charts.

Usage:
    python3 scripts/verify_learning.py --strategy paper_learning_test
    python3 scripts/verify_learning.py --strategy paper_learning_test --uid a1b2c3d4-5678-9abc-def0-1234567890ab
    python3 scripts/verify_learning.py --strategy paper_learning_test --db data/auto_trader.db --output-dir data
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime

# -- CLI ---------------------------------------------------------------------

parser = argparse.ArgumentParser(description="Verify learning system results")
parser.add_argument("--strategy", required=True, help="Strategy name (e.g. paper_learning_test)")
parser.add_argument("--uid", default=None, help="Strategy UID (defaults to 'legacy')")
parser.add_argument("--db", default=None, help="Path to auto_trader.db")
parser.add_argument("--output-dir", default=None, help="Directory for chart PNGs")
parser.add_argument("--no-charts", action="store_true", help="Skip chart generation (DB queries only)")
parser.add_argument("--verbose", "-v", action="store_true", help="Show all DB rows, not just summaries")
args = parser.parse_args()

# Resolve DB path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if args.db:
    DB_PATH = args.db
else:
    DB_PATH = os.path.join(PROJECT_ROOT, "data", "auto_trader.db")

OUTPUT_DIR = args.output_dir or os.path.join(PROJECT_ROOT, "data")
STRATEGY_NAME = args.strategy
STRATEGY_UID = args.uid or "legacy"

# -- Helpers -----------------------------------------------------------------

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def query(sql, params=()):
    conn = get_conn()
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def query_one(sql, params=()):
    conn = get_conn()
    try:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def fmt_pct(val):
    try:
        return f"{float(val):.1f}%"
    except (TypeError, ValueError):
        return "N/A"

def fmt_num(val, decimals=2):
    try:
        return f"{float(val):.{decimals}f}"
    except (TypeError, ValueError):
        return "N/A"

# -- Checks ------------------------------------------------------------------

def check_l1_signal_accuracy():
    """L1: Signal accuracy verdicts have converged beyond 'insufficient_data'."""
    print("\n" + "=" * 70)
    print("L1: SIGNAL ACCURACY VERDICTS")
    print("=" * 70)

    rows = query(
        """SELECT indicator_name, total_fired, correct,
                  accuracy_pct, verdict,
                  confidence_95_low, confidence_95_high
           FROM signal_accuracy
           WHERE strategy_uid = ?
           ORDER BY total_fired DESC""",
        (STRATEGY_UID,),
    )

    if not rows:
        print("  ❌ FAIL — No signal_accuracy rows found.")
        print("     Either no trades have closed yet, or UpdateLearning hasn't run.")
        return False

    print(f"  {'Indicator':<15} {'Fired':>5} {'Correct':>7} {'Accuracy':>9} {'Verdict':<20} {'95% CI':>15}")
    print("  " + "-" * 70)

    has_non_insufficient = False
    for r in rows:
        ci = f"[{fmt_pct(r['confidence_95_low'])}, {fmt_pct(r['confidence_95_high'])}]"
        print(f"  {r['indicator_name']:<15} {r['total_fired']:>5} {r['correct']:>7} "
              f"{fmt_pct(r['accuracy_pct']):>9} {r['verdict']:<20} {ci:>15}")
        if r["verdict"] != "insufficient_data":
            has_non_insufficient = True

    if has_non_insufficient:
        print("\n  ✅ PASS — At least one indicator has a verdict beyond 'insufficient_data'.")
        return True
    else:
        print("\n  ❌ FAIL — All verdicts are 'insufficient_data'.")
        print("     Need more trades (minimum 15 per signal for verdict classification).")
        return False


def check_l2_weight_adjustments():
    """L2: Weights were actually adjusted based on accuracy data."""
    print("\n" + "=" * 70)
    print("L2: WEIGHT ADJUSTMENTS")
    print("=" * 70)

    rows = query(
        """SELECT indicator_name, old_weight, new_weight,
                  justification, accuracy_at_time, sample_size_at_time
           FROM weight_history
           WHERE strategy_uid = ?
           ORDER BY id""",
        (STRATEGY_UID,),
    )

    if not rows:
        # Check if we have enough trades for adjustment
        trade_count = query_one(
            """SELECT COUNT(*) as cnt FROM trade_learning
               WHERE strategy_name = ? AND exit_time IS NOT NULL""",
            (STRATEGY_NAME,),
        )
        cnt = trade_count["cnt"] if trade_count else 0
        threshold = 30  # hardcoded in weight_adjuster.py

        print(f"  ❌ FAIL — No weight adjustments recorded.")
        print(f"     Closed trades: {cnt}. Threshold: {threshold}.")
        if cnt < threshold:
            print(f"     Need {threshold - cnt} more closed trades for weight adjustment.")
        return False

    print(f"  {'Indicator':<15} {'Old Weight':>10} {'New Weight':>10} {'Δ':>8} {'Accuracy':>9} {'N':>4} Justification")
    print("  " + "-" * 90)

    has_changes = False
    for r in rows:
        old = r["old_weight"] or 0
        new = r["new_weight"] or 0
        delta = new - old
        sign = "+" if delta > 0 else ""
        has_changes = has_changes or (abs(delta) > 0.001)
        print(f"  {r['indicator_name']:<15} {old:>10.4f} {new:>10.4f} {sign}{delta:>7.4f} "
              f"{fmt_pct(r['accuracy_at_time']):>9} {r['sample_size_at_time']:>4} {r['justification']}")

    if has_changes:
        print("\n  ✅ PASS — Weights were adjusted based on accuracy data.")
        return True
    else:
        print("\n  ⚠️  MARGINAL — Weight adjustments exist but all changes are near-zero.")
        return False


def check_l3_rulebook():
    """L3: A rulebook was generated from accuracy data."""
    print("\n" + "=" * 70)
    print("L3: RULEBOOK GENERATION")
    print("=" * 70)

    rows = query(
        """SELECT id, version, trades_recorded_at_generation, source_counts_json, rulebook_text
           FROM rulebook_versions
           WHERE strategy_uid = ?
           ORDER BY id DESC""",
        (STRATEGY_UID,),
    )

    if not rows:
        print("  ❌ FAIL — No rulebook versions found.")
        print("     Either not enough trades, or UpdateRulebook hasn't triggered.")
        return False

    latest = rows[0]
    print(f"  Latest rulebook: v{latest['version']}")
    print(f"  Trades at generation: {latest['trades_recorded_at_generation']}")

    if latest["source_counts_json"]:
        sources = json.loads(latest["source_counts_json"])
        print(f"  Sources: {sources}")

    if latest["rulebook_text"]:
        print(f"\n  Rules:")
        for line in latest["rulebook_text"].strip().split("\n"):
            print(f"    {line}")
        print(f"\n  ✅ PASS — Rulebook generated with content.")
        return True
    else:
        print("\n  ❌ FAIL — Rulebook exists but is empty.")
        return False


def check_trade_summary():
    """Show trade summary and equity curve data."""
    print("\n" + "=" * 70)
    print("TRADE SUMMARY")
    print("=" * 70)

    trades = query(
        """SELECT id, symbol, direction, entry_time, exit_time,
                  pnl_pct, outcome, signals_at_entry_json
           FROM trade_learning
           WHERE strategy_name = ? AND exit_time IS NOT NULL
           ORDER BY exit_time""",
        (STRATEGY_NAME,),
    )

    if not trades:
        print("  No closed trades yet.")
        return []

    wins = sum(1 for t in trades if t["outcome"] and t["outcome"].lower() in ("win", "won"))
    losses = sum(1 for t in trades if t["outcome"] and t["outcome"].lower() in ("loss", "lost"))
    total = len(trades)
    win_rate = (wins / total * 100) if total > 0 else 0

    # Compute cumulative PnL
    cumulative = 1000.0  # starting equity
    equity_curve = []
    for t in trades:
        pnl = t["pnl_pct"] or 0
        cumulative *= (1 + pnl / 100)
        equity_curve.append({
            "trade_num": len(equity_curve) + 1,
            "symbol": t["symbol"],
            "direction": t["direction"],
            "pnl_pct": pnl,
            "outcome": t["outcome"],
            "equity": round(cumulative, 2),
            "exit_time": t["exit_time"],
        })

    print(f"  Total trades:  {total}")
    print(f"  Wins / Losses: {wins} / {losses}")
    print(f"  Win rate:      {win_rate:.1f}%")
    print(f"  Final equity:  {equity_curve[-1]['equity']:.2f} USDT (started at 1000.00)")
    print(f"  Total return:  {(equity_curve[-1]['equity'] / 1000 - 1) * 100:+.2f}%")

    print(f"\n  {'#':>3} {'Symbol':<10} {'Dir':<5} {'PnL%':>7} {'Outcome':<6} {'Equity':>10} {'Exit Time'}")
    print("  " + "-" * 65)
    for e in equity_curve:
        print(f"  {e['trade_num']:>3} {e['symbol']:<10} {e['direction']:<5} "
              f"{e['pnl_pct']:>+7.2f} {e['outcome']:<6} {e['equity']:>10.2f} {e['exit_time']}")

    return equity_curve


# -- Charts ------------------------------------------------------------------

def generate_charts(equity_curve):
    """Generate PNG charts if matplotlib is available."""
    try:
        import matplotlib
        matplotlib.use("Agg")  # Non-interactive backend
        import matplotlib.pyplot as plt
        import matplotlib.ticker as mticker
    except ImportError:
        print("\n  ⚠️  matplotlib not installed. Skipping chart generation.")
        print("     Install with: pip install matplotlib")
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # -- Chart 1: Equity curve ----------------------------------------------
    if equity_curve:
        fig, ax = plt.subplots(figsize=(12, 5))
        trade_nums = [e["trade_num"] for e in equity_curve]
        equities = [e["equity"] for e in equity_curve]
        outcomes = [e["outcome"].lower() if e["outcome"] else "unknown" for e in equity_curve]

        colors = ["#2ecc71" if o in ("win", "won") else "#e74c3c" for o in outcomes]

        ax.plot(trade_nums, equities, color="#3498db", linewidth=2, zorder=2)
        ax.scatter(trade_nums, equities, c=colors, s=40, zorder=3, edgecolors="white", linewidths=0.5)
        ax.axhline(y=1000, color="gray", linestyle="--", alpha=0.5, label="Starting equity")

        ax.set_xlabel("Trade #")
        ax.set_ylabel("Equity (USDT)")
        ax.set_title(f"Paper Learning Test — Equity Curve ({STRATEGY_NAME})")
        ax.legend()
        ax.grid(True, alpha=0.3)

        fig.tight_layout()
        path = os.path.join(OUTPUT_DIR, "learning_test_equity.png")
        fig.savefig(path, dpi=150)
        plt.close(fig)
        print(f"\n  📊 Equity curve saved to {path}")

    # -- Chart 2: Signal accuracy with Wilson CI ----------------------------
    signal_rows = query(
        """SELECT indicator_name, total_fired, accuracy_pct,
                  confidence_95_low, confidence_95_high, verdict
           FROM signal_accuracy
           WHERE strategy_uid = ?
           ORDER BY total_fired DESC""",
        (STRATEGY_UID,),
    )

    if signal_rows:
        fig, ax = plt.subplots(figsize=(10, 5))
        names = [r["indicator_name"] for r in signal_rows]
        accuracies = [r["accuracy_pct"] or 0 for r in signal_rows]
        ci_low = [r["confidence_95_low"] or 0 for r in signal_rows]
        ci_high = [r["confidence_95_high"] or 0 for r in signal_rows]
        errors_low = [max(0, a - lo) for a, lo in zip(accuracies, ci_low)]
        errors_high = [max(0, hi - a) for a, hi in zip(accuracies, ci_high)]

        verdict_colors = {
            "valid": "#2ecc71",
            "monitor": "#3498db",
            "suppress": "#f39c12",
            "contrarian": "#e74c3c",
            "review": "#9b59b6",
            "insufficient_data": "#95a5a6",
        }
        colors = [verdict_colors.get(r["verdict"], "#95a5a6") for r in signal_rows]

        bars = ax.bar(names, accuracies, color=colors, edgecolor="white", linewidth=0.5)
        ax.errorbar(names, accuracies, yerr=[errors_low, errors_high],
                    fmt="none", ecolor="black", capsize=5, elinewidth=1)

        ax.axhline(y=75, color="#2ecc71", linestyle="--", alpha=0.5, label="Valid threshold (75%)")
        ax.axhline(y=50, color="#f39c12", linestyle="--", alpha=0.5, label="Coin flip (50%)")
        ax.axhline(y=30, color="#e74c3c", linestyle="--", alpha=0.5, label="Contrarian threshold (30%)")

        # Legend for verdicts
        from matplotlib.patches import Patch
        legend_patches = [Patch(facecolor=c, label=l) for l, c in verdict_colors.items()]
        ax.legend(handles=legend_patches, loc="upper right", fontsize=8)

        ax.set_ylabel("Accuracy (%)")
        ax.set_title(f"Signal Accuracy with 95% Wilson CI ({STRATEGY_NAME})")
        ax.set_ylim(0, 105)
        ax.grid(True, alpha=0.3, axis="y")

        fig.tight_layout()
        path = os.path.join(OUTPUT_DIR, "learning_test_accuracy.png")
        fig.savefig(path, dpi=150)
        plt.close(fig)
        print(f"  📊 Accuracy chart saved to {path}")

    # -- Chart 3: Weight evolution ------------------------------------------
    weight_rows = query(
        """SELECT indicator_name, old_weight, new_weight, accuracy_at_time
           FROM weight_history
           WHERE strategy_uid = ?
           ORDER BY id""",
        (STRATEGY_UID,),
    )

    if weight_rows:
        # Group by indicator
        indicators = sorted(set(r["indicator_name"] for r in weight_rows))
        fig, ax = plt.subplots(figsize=(10, 5))

        x = range(len(indicators))
        old_weights = []
        new_weights = []
        for ind in indicators:
            # Take the last adjustment for each indicator
            matching = [r for r in weight_rows if r["indicator_name"] == ind]
            old_weights.append(matching[-1]["old_weight"])
            new_weights.append(matching[-1]["new_weight"])

        bar_width = 0.35
        bars_old = ax.bar([i - bar_width/2 for i in x], old_weights, bar_width,
                          label="Original weight", color="#3498db", alpha=0.7)
        bars_new = ax.bar([i + bar_width/2 for i in x], new_weights, bar_width,
                          label="Adjusted weight", color="#2ecc71", alpha=0.7)

        # Color negative weights red
        for i, w in enumerate(new_weights):
            if w < 0:
                bars_new[i].set_color("#e74c3c")
                bars_new[i].set_alpha(0.8)

        ax.set_xticks(list(x))
        ax.set_xticklabels(indicators, rotation=15)
        ax.axhline(y=0, color="black", linewidth=0.5)
        ax.set_ylabel("Weight")
        ax.set_title(f"Weight Adjustments ({STRATEGY_NAME})")
        ax.legend()
        ax.grid(True, alpha=0.3, axis="y")

        fig.tight_layout()
        path = os.path.join(OUTPUT_DIR, "learning_test_weights.png")
        fig.savefig(path, dpi=150)
        plt.close(fig)
        print(f"  📊 Weight chart saved to {path}")


# -- Main --------------------------------------------------------------------

def main():
    print("=" * 70)
    print(f"LEARNING VERIFICATION — {STRATEGY_NAME}")
    print(f"Strategy UID: {STRATEGY_UID}")
    print(f"Database:      {DB_PATH}")
    print(f"Timestamp:      {datetime.now().isoformat()}")
    print("=" * 70)

    if not os.path.exists(DB_PATH):
        print(f"\n❌ Database not found: {DB_PATH}")
        print("   Run the paper learning test first (see docs/reaction-design/PAPER-LEARNING-TEST.md)")
        sys.exit(1)

    l1 = check_l1_signal_accuracy()
    l2 = check_l2_weight_adjustments()
    l3 = check_l3_rulebook()
    equity_curve = check_trade_summary()

    if not args.no_charts:
        generate_charts(equity_curve)

    # -- Summary -------------------------------------------------------------
    print("\n" + "=" * 70)
    print("VERIFICATION SUMMARY")
    print("=" * 70)
    print(f"  L1 Signal accuracy verdicts:  {'✅ PASS' if l1 else '❌ FAIL'}")
    print(f"  L2 Weight adjustments:        {'✅ PASS' if l2 else '❌ FAIL'}")
    print(f"  L3 Rulebook generation:       {'✅ PASS' if l3 else '❌ FAIL'}")

    all_pass = l1 and l2 and l3
    if all_pass:
        print("\n  🎉 ALL CHECKS PASSED — Learning loop is verified working.")
    else:
        print("\n  ⚠️  Some checks failed. See details above.")
        print("  Possible causes:")
        print("    - Not enough trades yet (need 15+ per signal for verdicts, 30+ for weight adjustment)")
        print("    - Service hasn't been running long enough (try 24-48 hours)")
        print("    - UpdateLearning enzyme not triggering (check logs for errors)")

    print()
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())