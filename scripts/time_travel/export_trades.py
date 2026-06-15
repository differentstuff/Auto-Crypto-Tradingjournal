#!/usr/bin/env python3
"""
scripts/time_travel/export_trades.py — Export trade_learning rows to CSV for TradingView analysis.

Queries the SQLite trade_learning table, unpacks signals_at_entry_json metadata
(threshold, entry/exit price, source, dollar-math), and writes a flat CSV you can
sort/filter in Excel then jump to timestamps on TradingView.

Usage:
  # All trades at threshold 5.5
  python -m scripts.time_travel.export_trades --threshold 5.5

  # Only wins at 5.5
  python -m scripts.time_travel.export_trades --threshold 5.5 --outcome win

  # All losses across all thresholds (for failure analysis)
  python -m scripts.time_travel.export_trades --outcome loss

  # Specific symbol + date range
  python -m scripts.time_travel.export_trades --symbol BTCUSDT --from 2026-03-01 --to 2026-05-01

  # Full dump (no filters)
  python -m scripts.time_travel.export_trades

  # Custom output path
  python -m scripts.time_travel.export_trades --threshold 5.5 -o high_conv_trades.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
from typing import List, Optional

# ── Project path setup ──────────────────────────────────────────────────────
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)

# Load .env BEFORE importing core.database — same order as time_travel.
from dotenv import load_dotenv
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

from core.database import DB_PATH

COLUMNS = [
    "id",
    "symbol",
    "direction",
    "outcome",
    "entry_time",
    "exit_time",
    "entry_price",
    "exit_price",
    "pnl_pct",
    "pnl_usdt",
    "position_size_usd",
    "net_pnl_usd",
    "total_fees_usd",
    "duration_hours",
    "confluence_score",
    "threshold",
    "threshold_bucket",
    "exit_reason",
    "sl_hit",
    "trailing_stop_hit",
    "mfe_pct",
    "mae_pct",
    "source",
    "strategy_name",
    "strategy_uid",
]


def _build_query(
    symbol: Optional[str],
    outcome: Optional[str],
    from_date: Optional[str],
    to_date: Optional[str],
) -> tuple[str, list]:
    """Build SQL query with optional filters."""
    clauses: list[str] = []
    params: list = []

    if symbol:
        clauses.append("symbol = ?")
        params.append(symbol.upper())

    if outcome:
        clauses.append("outcome = ?")
        params.append(outcome.lower())

    if from_date:
        clauses.append("entry_time >= ?")
        params.append(from_date)

    if to_date:
        clauses.append("entry_time <= ?")
        params.append(to_date + "T23:59:59")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    query = f"SELECT * FROM trade_learning {where} ORDER BY entry_time ASC"
    return query, params


def _unpack_signals(signals_json: Optional[str]) -> dict:
    """Extract metadata from signals_at_entry_json."""
    if not signals_json:
        return {}
    try:
        data = json.loads(signals_json)
    except (json.JSONDecodeError, TypeError):
        return {}

    return {
        "entry_price": data.get("_entry_price", ""),
        "exit_price": data.get("_exit_price", ""),
        "threshold": data.get("_threshold_used", ""),
        "threshold_bucket": data.get("_threshold_bucket", ""),
        "source": data.get("_source", "live"),
        "indicators_aligned": data.get("_indicators_aligned", ""),
        "effective_score": data.get("_effective_score", ""),
        # Dollar-math fields (from time_travel backtest)
        "position_size_usd": data.get("_position_size_usd", ""),
        "gross_pnl_usd": data.get("_gross_pnl_usd", ""),
        "net_pnl_usd": data.get("_net_pnl_usd", ""),
        "total_fees_usd": data.get("_total_fees_usd", ""),
        "atr_cap_applied": data.get("_atr_cap_applied", ""),
    }


def _row_to_csv_row(row: sqlite3.Row, meta: dict) -> dict:
    """Map a DB row + unpacked metadata to flat CSV dict."""
    duration_min = row["duration_minutes"] or 0
    duration_h = round(duration_min / 60, 1) if duration_min else ""

    # Prefer dollar-math from signals_at_entry_json if available,
    # otherwise use pnl_usdt column (populated by live trading)
    net_pnl = meta.get("net_pnl_usd", "")
    position_size = meta.get("position_size_usd", "")
    total_fees = meta.get("total_fees_usd", "")

    return {
        "id": row["id"],
        "symbol": row["symbol"],
        "direction": row["direction"],
        "outcome": row["outcome"],
        "entry_time": row["entry_time"],
        "exit_time": row["exit_time"],
        "entry_price": meta.get("entry_price", ""),
        "exit_price": meta.get("exit_price", ""),
        "pnl_pct": row["pnl_pct"],
        "pnl_usdt": row["pnl_usdt"] if row["pnl_usdt"] else "",
        "position_size_usd": position_size if position_size else "",
        "net_pnl_usd": net_pnl if net_pnl else "",
        "total_fees_usd": total_fees if total_fees else "",
        "duration_hours": duration_h,
        "confluence_score": row["confluence_score_at_entry"],
        "threshold": meta.get("threshold", ""),
        "threshold_bucket": meta.get("threshold_bucket", ""),
        "exit_reason": row["exit_reason"],
        "sl_hit": row["sl_hit"],
        "trailing_stop_hit": row["trailing_stop_hit"],
        "mfe_pct": row["max_favorable_excursion_pct"],
        "mae_pct": row["max_adverse_excursion_pct"],
        "source": meta.get("source", "live"),
        "strategy_name": row["strategy_name"],
        "strategy_uid": row["strategy_uid"] or "legacy",
    }


def export_trades(
    output_path: str,
    symbol: Optional[str],
    outcome: Optional[str],
    threshold: Optional[float],
    from_date: Optional[str],
    to_date: Optional[str],
    source: Optional[str],
    verbose: bool = False,
) -> int:
    """Query, filter, and write trades to CSV. Returns row count."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    if verbose:
        total_rows = conn.execute("SELECT COUNT(*) FROM trade_learning").fetchone()[0]
        print(f"  DB: {DB_PATH}")
        print(f"  Total rows in trade_learning: {total_rows}")

    query, params = _build_query(symbol, outcome, from_date, to_date)
    rows = conn.execute(query, params).fetchall()
    conn.close()

    if verbose:
        print(f"  SQL matched: {len(rows)} rows")

    # Unpack + filter (threshold/source are in JSON)
    csv_rows: List[dict] = []
    skipped_no_threshold = 0
    skipped_threshold_mismatch = 0
    skipped_source = 0

    for row in rows:
        meta = _unpack_signals(row["signals_at_entry_json"])

        # Python-side filters for JSON-embedded fields
        if threshold is not None:
            row_threshold = meta.get("threshold")
            if row_threshold == "" or row_threshold is None:
                skipped_no_threshold += 1
                continue
            if abs(float(row_threshold) - threshold) > 0.01:
                skipped_threshold_mismatch += 1
                continue

        if source is not None:
            row_source = meta.get("source", "live")
            if row_source != source:
                skipped_source += 1
                continue

        csv_rows.append(_row_to_csv_row(row, meta))

    if verbose and (skipped_no_threshold or skipped_threshold_mismatch or skipped_source):
        print(f"  Filtered out: {skipped_no_threshold} no-threshold, "
              f"{skipped_threshold_mismatch} threshold-mismatch, "
              f"{skipped_source} source-mismatch")

    # Write CSV
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(csv_rows)

    return len(csv_rows)


def main():
    parser = argparse.ArgumentParser(
        description="Export trade_learning to CSV for TradingView analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # All trades at threshold 5.5
  python -m scripts.time_travel.export_trades --threshold 5.5

  # Only wins at 5.5
  python -m scripts.time_travel.export_trades --threshold 5.5 --outcome win

  # All losses across all thresholds
  python -m scripts.time_travel.export_trades --outcome loss

  # BTC only, Q1 2026
  python -m scripts.time_travel.export_trades --symbol BTCUSDT --from 2026-01-01 --to 2026-03-31

  # Only time_travel trades (exclude live)
  python -m scripts.time_travel.export_trades --source time_travel

  # Full dump
  python -m scripts.time_travel.export_trades

  # Debug: show DB path and filter stats
  python -m scripts.time_travel.export_trades -v
        """,
    )
    parser.add_argument("--threshold", type=float, default=None,
                        help="Filter by threshold used (e.g., 5.5, 4.4, 3.6)")
    parser.add_argument("--outcome", default=None,
                        choices=["win", "loss", "breakeven"],
                        help="Filter by outcome")
    parser.add_argument("--symbol", default=None,
                        help="Filter by symbol (e.g., BTCUSDT)")
    parser.add_argument("--from", dest="from_date", default=None,
                        help="Entry time >= this date (ISO, e.g., 2026-01-01)")
    parser.add_argument("--to", dest="to_date", default=None,
                        help="Entry time <= this date (ISO, e.g., 2026-06-01)")
    parser.add_argument("--source", default=None,
                        choices=["time_travel", "live"],
                        help="Filter by trade source (time_travel or live)")
    parser.add_argument("-o", "--output", default=None,
                        help="Output CSV path (default: auto-generated with filters in name)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Show DB path, row counts, and filter stats")

    args = parser.parse_args()

    # Auto-generate output filename if not specified
    if args.output:
        output_path = args.output
    else:
        parts = ["trades"]
        if args.threshold is not None:
            parts.append(f"t{args.threshold}")
        if args.outcome:
            parts.append(args.outcome)
        if args.symbol:
            parts.append(args.symbol.lower())
        if args.from_date or args.to_date:
            parts.append(f"{args.from_date or 'start'}_to_{args.to_date or 'now'}")
        output_path = os.path.join(_PROJECT_ROOT, "data", "_".join(parts) + ".csv")

    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    count = export_trades(
        output_path=output_path,
        symbol=args.symbol,
        outcome=args.outcome,
        threshold=args.threshold,
        from_date=args.from_date,
        to_date=args.to_date,
        source=args.source,
        verbose=args.verbose,
    )

    if count == 0:
        print(f"No trades matched your filters. Run with -v to see why.")
    else:
        print(f"Exported {count} trades → {output_path}")


if __name__ == "__main__":
    main()