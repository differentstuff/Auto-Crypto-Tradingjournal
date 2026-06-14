#!/usr/bin/env python3
"""
scripts/export_trades.py — Export trade_learning rows to CSV for TradingView analysis.

Queries the SQLite trade_learning table, unpacks signals_at_entry_json metadata
(threshold, entry/exit price, source), and writes a flat CSV you can sort/filter
in Excel then jump to timestamps on TradingView.

Usage:
  # All trades at threshold 5.5
  python scripts/export_trades.py --threshold 5.5

  # Only wins at 5.5
  python scripts/export_trades.py --threshold 5.5 --outcome win

  # All losses across all thresholds (for failure analysis)
  python scripts/export_trades.py --outcome loss

  # Specific symbol + date range
  python scripts/export_trades.py --symbol BTCUSDT --from 2026-03-01 --to 2026-05-01

  # Full dump (no filters)
  python scripts/export_trades.py

  # Custom output path
  python scripts/export_trades.py --threshold 5.5 -o high_conv_trades.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import List, Optional

# ── Project path setup ──────────────────────────────────────────────────────
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.join(_PROJECT_ROOT, "data/trading_journal.db"),
)

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
    threshold: Optional[float],
    from_date: Optional[str],
    to_date: Optional[str],
    source: Optional[str],
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

    # Threshold and source are in signals_at_entry_json — filter in Python
    # (JSON extraction in SQLite is unreliable across versions).

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
    }


def _row_to_csv_row(row: sqlite3.Row, meta: dict) -> dict:
    """Map a DB row + unpacked metadata to flat CSV dict."""
    duration_min = row["duration_minutes"] or 0
    duration_h = round(duration_min / 60, 1) if duration_min else ""

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
) -> int:
    """Query, filter, and write trades to CSV. Returns row count."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    query, params = _build_query(symbol, outcome, threshold, from_date, to_date, source)
    rows = conn.execute(query, params).fetchall()
    conn.close()

    # Unpack + filter (threshold/source are in JSON)
    csv_rows: List[dict] = []
    for row in rows:
        meta = _unpack_signals(row["signals_at_entry_json"])

        # Python-side filters for JSON-embedded fields
        if threshold is not None:
            row_threshold = meta.get("threshold")
            if row_threshold == "" or row_threshold is None:
                continue
            if abs(float(row_threshold) - threshold) > 0.01:
                continue

        if source is not None:
            row_source = meta.get("source", "live")
            if row_source != source:
                continue

        csv_rows.append(_row_to_csv_row(row, meta))

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
  python scripts/export_trades.py --threshold 5.5

  # Only wins at 5.5 (the 207 you want to verify)
  python scripts/export_trades.py --threshold 5.5 --outcome win

  # All losses across all thresholds
  python scripts/export_trades.py --outcome loss

  # BTC only, Q1 2026
  python scripts/export_trades.py --symbol BTCUSDT --from 2026-01-01 --to 2026-03-31

  # Only time_travel trades (exclude live)
  python scripts/export_trades.py --source time_travel

  # Full dump
  python scripts/export_trades.py
        """,
    )
    parser.add_argument(
        "--threshold", type=float, default=None,
        help="Filter by threshold used (e.g., 5.5, 4.4, 3.6)",
    )
    parser.add_argument(
        "--outcome", default=None,
        choices=["win", "loss", "breakeven"],
        help="Filter by outcome",
    )
    parser.add_argument(
        "--symbol", default=None,
        help="Filter by symbol (e.g., BTCUSDT)",
    )
    parser.add_argument(
        "--from", dest="from_date", default=None,
        help="Entry time >= this date (ISO, e.g., 2026-01-01)",
    )
    parser.add_argument(
        "--to", dest="to_date", default=None,
        help="Entry time <= this date (ISO, e.g., 2026-06-01)",
    )
    parser.add_argument(
        "--source", default=None,
        choices=["time_travel", "live"],
        help="Filter by trade source (time_travel or live)",
    )
    parser.add_argument(
        "-o", "--output", default=None,
        help="Output CSV path (default: auto-generated with filters in name)",
    )

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
    )

    if count == 0:
        print(f"No trades matched your filters.")
    else:
        print(f"Exported {count} trades → {output_path}")


if __name__ == "__main__":
    main()
