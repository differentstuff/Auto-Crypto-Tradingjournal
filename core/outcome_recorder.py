"""
core/outcome_recorder.py -- Captures trade decisions per cycle and writes results.

Monitors substrate.decisions for:
  - trade_approved → record entry
  - exit_approved → record exit

Also tracks equity curve per cycle.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

_log = logging.getLogger(__name__)


class OutcomeRecorder:
    """
    Captures trade decisions from the replay driver and writes results.

    Monitors substrate.decisions for:
      - trade_approved → record entry
      - exit_approved → record exit

    Also tracks equity curve per cycle.
    """

    def __init__(self, strategy_name: str, start_date: str, end_date: str):
        self._trades: List[dict] = []
        self._equity_curve: List[dict] = []
        self._strategy_name = strategy_name
        self._start_date = start_date
        self._end_date = end_date

    def capture_cycle(self, substrate, t_cursor: datetime) -> None:
        """Capture decisions from this cycle."""
        action = substrate.decisions.get("action", "")

        # Track equity
        equity = substrate.portfolio.get("equity", 0)
        n_positions = len(substrate.portfolio.get("open_positions", []))
        self._equity_curve.append({
            "timestamp": t_cursor.isoformat(),
            "equity": equity,
            "open_positions": n_positions,
            "action": action,
        })

        # Capture entry
        if action == "trade_open":
            trade = substrate.decisions.get("trade_approved", {})
            if trade:
                self._trades.append({
                    "entry_timestamp": t_cursor.isoformat(),
                    "symbol": trade.get("symbol", ""),
                    "direction": trade.get("direction", ""),
                    "entry_price": trade.get("entry_price", 0),
                    "sl_price": trade.get("sl_price", 0),
                    "tp1": trade.get("tp1", 0),
                    "size_usdt": trade.get("size_usdt", 0),
                    "atr_value": trade.get("atr_value", 0),
                    "confluence_score": trade.get("score", 0),
                    "entry_fee_usd": trade.get("entry_fee_usdt", None),
                    "realized_gross_pnl_usd": 0.0,
                    "realized_net_pnl_usd": 0.0,
                    "exit_timestamp": None,
                    "exit_price": None,
                    "exit_reason": None,
                    "net_pnl_usd": None,
                    "gross_pnl_usd": None,
                    "exit_fees_usd": 0.0,
                    "total_fees_usd": None,
                    "is_winner": None,
                })

        # Capture partial close (TP1, TP2)
        elif action == "trade_managed":
            exit_approved = substrate.decisions.get("exit_approved", {})
            if exit_approved and self._trades:
                symbol = exit_approved.get("symbol", "")
                partial_fee = exit_approved.get("exit_fee_usdt", 0.0) or 0.0
                partial_gross = exit_approved.get("gross_pnl_usdt", 0.0) or 0.0
                partial_net = exit_approved.get("net_pnl_usdt", 0.0) or 0.0
                for trade in reversed(self._trades):
                    if trade["symbol"] == symbol and trade["exit_timestamp"] is None:
                        trade["exit_fees_usd"] = trade.get("exit_fees_usd", 0.0) + partial_fee
                        trade["realized_gross_pnl_usd"] = trade.get("realized_gross_pnl_usd", 0.0) + partial_gross
                        trade["realized_net_pnl_usd"] = trade.get("realized_net_pnl_usd", 0.0) + partial_net
                        break

        # Capture exit
        elif action == "trade_closed":
            exit_approved = substrate.decisions.get("exit_approved", {})
            if exit_approved and self._trades:
                symbol = exit_approved.get("symbol", "")
                for trade in reversed(self._trades):
                    if trade["symbol"] == symbol and trade["exit_timestamp"] is None:
                        trade["exit_timestamp"] = t_cursor.isoformat()
                        trade["exit_reason"] = exit_approved.get("reason", "")
                        trade["exit_price"] = exit_approved.get("exit_price", None)
                        final_gross = exit_approved.get("gross_pnl_usdt", 0.0) or 0.0
                        final_net = exit_approved.get("net_pnl_usdt", 0.0) or 0.0
                        trade["realized_gross_pnl_usd"] = trade.get("realized_gross_pnl_usd", 0.0) + final_gross
                        trade["realized_net_pnl_usd"] = trade.get("realized_net_pnl_usd", 0.0) + final_net
                        entry_fee = trade.get("entry_fee_usd", 0.0) or 0.0
                        trade["gross_pnl_usd"] = round(trade["realized_gross_pnl_usd"], 4)
                        trade["net_pnl_usd"] = round(trade["realized_net_pnl_usd"] - entry_fee, 4)
                        final_exit_fee = exit_approved.get("exit_fee_usdt", 0.0) or 0.0
                        trade["exit_fees_usd"] = trade.get("exit_fees_usd", 0.0) + final_exit_fee
                        trade["total_fees_usd"] = (trade.get("entry_fee_usd", 0.0) or 0.0) + trade["exit_fees_usd"]
                        trade["is_winner"] = trade["net_pnl_usd"] >= 0
                        break

    def write_results(self, output_dir: Optional[str] = None) -> str:
        """Write results to JSON file.

        Args:
            output_dir: Directory to write results. Defaults to core/results/.

        Returns:
            Path to the written results file.
        """
        if output_dir is None:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            output_dir = os.path.join(project_root, "core", "results")

        os.makedirs(output_dir, exist_ok=True)

        run_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S")
        filename = f"backtest_{self._start_date}_{self._end_date}_{self._strategy_name}_{run_ts}.json"
        filepath = os.path.join(output_dir, filename)

        # Compute summary stats
        closed_trades = [t for t in self._trades if t["exit_timestamp"] is not None]
        wins = [t for t in closed_trades if t.get("is_winner") is True]
        losses = [t for t in closed_trades if t.get("is_winner") is False]

        win_rate = (len(wins) / len(closed_trades) * 100) if closed_trades else 0.0
        total_pnl = sum(t.get("net_pnl_usd", 0) or 0 for t in closed_trades)
        total_fees = sum(t.get("total_fees_usd", 0) or 0 for t in closed_trades)

        results = {
            "strategy": self._strategy_name,
            "start_date": self._start_date,
            "end_date": self._end_date,
            "run_timestamp": run_ts,
            "summary": {
                "total_cycles": len(self._equity_curve),
                "total_trades": len(self._trades),
                "closed_trades": len(closed_trades),
                "open_trades": len([t for t in self._trades if t["exit_timestamp"] is None]),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate_pct": round(win_rate, 2),
                "total_pnl_usd": round(total_pnl, 2),
                "total_fees_usd": round(total_fees, 4),
            },
            "trades": self._trades,
            "equity_curve": self._equity_curve,
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, default=str)

        _log.info(
            "Backtest results written to %s: %d trades, %.1f%% win rate, $%.2f PnL",
            filepath, len(closed_trades), win_rate, total_pnl,
        )
        return filepath
