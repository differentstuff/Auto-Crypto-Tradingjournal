"""
core/replay_driver.py -- Historical replay driver that runs the full enzyme pipeline.

Runs the daemon's exact run_cycle() on historical bars by replacing
the live daemon's two external dependencies — time and data — with
controlled historical substitutes.

Usage:
    python -m core.replay_driver --start 2025-01-01 --end 2025-03-01 --strategy momentum_rising
    python -m core.replay_driver --start 2025-06-01 --end 2025-12-01 --strategy momentum_rising --config-dir ./config

The driver does not re-implement any enzyme. It runs the daemon's own
run_cycle() in a loop, advancing a virtual clock instead of sleeping.

Enzymes disabled in replay mode:
  - DynamicFilter (universe frozen at replay start)
  - CollectExternalSignals (external APIs return current data)
  - CollectMacroContext (VIX/DXY/Fear&Greed are current)
  - SendTelegramLog (no notifications during backtest)
  - SyncPositions (paper mode manages positions internally)
  - RecordTradeOutcome (OutcomeRecorder handles this)
  - UpdateLearning (no learning updates during backtest)
  - UpdateRulebook (no rulebook generation during backtest)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import List, Optional

_log = logging.getLogger(__name__)

# Project root for imports
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


def build_cycle_timestamps(
    start_date: str,
    end_date: str,
    cycle_interval_minutes: int,
) -> List[datetime]:
    """
    Build a list of cycle timestamps from start_date to end_date.

    Args:
        start_date: ISO date string (e.g. '2025-01-01')
        end_date: ISO date string (e.g. '2025-03-01')
        cycle_interval_minutes: Minutes between cycles

    Returns:
        List of datetime objects representing each cycle's virtual time.
    """
    start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_raw = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    # Inverted date range — nothing to replay
    if start > end_raw:
        return []

    # end_date is inclusive of the full day — advance to midnight of the NEXT day
    end = end_raw + timedelta(days=1)
    interval = timedelta(minutes=cycle_interval_minutes)

    timestamps = []
    t = start
    while t <= end:
        timestamps.append(t)
        t += interval

    return timestamps


def _register_enzymes(daemon, replay_exchange) -> None:
    """
    Register enzymes for replay mode.

    Same as main.py, EXCEPT the following are disabled:
      - DynamicFilter (universe frozen at replay start)
      - CollectExternalSignals (external APIs return current data)
      - CollectMacroContext (VIX/DXY/Fear&Greed are current)
      - SendTelegramLog (no notifications during backtest)
      - SyncPositions (paper mode manages positions internally)
      - RecordTradeOutcome (OutcomeRecorder handles this)
      - UpdateLearning (no learning updates during backtest)
      - UpdateRulebook (no rulebook generation during backtest)
    """
    import enzymes  # noqa: F401 — triggers @register_enzyme decorators
    from core.enzyme import create_enzyme

    def _register(name: str, **kwargs) -> None:
        enz = create_enzyme(name, config=daemon.substrate._config)
        if enz is None:
            _log.warning("Enzyme %s not found in registry", name)
            return
        for k, v in kwargs.items():
            if hasattr(enz, k):
                setattr(enz, k, v)
            else:
                _log.debug("Enzyme %s has no attribute '%s' to inject", name, k)
        daemon.register_enzyme(enz)

    # Sensors
    _register("CollectOHLCV", exchange=replay_exchange)
    _register("DetectRegime", exchange=replay_exchange)
    _register("MarketGeometry")

    for name in [
        "ScoreConfluence",
        "DetectNoise",
        "ValidateEntryZone",
        "CollectPreTradeContext",
    ]:
        _register(name)

    # Regulators and Transporters
    for name in ["ApproveTrade", "ApproveExit", "RequestExit"]:
        _register(name)

    _register("ExecuteTrade", exchange=replay_exchange)
    _register("ExecuteExit")

    # Price updates (uses ReplayExchange.fetch_tickers() for cached close prices)
    _register("UpdateMarkPrices", exchange=replay_exchange)

    # Wait enzyme (always available)
    _register("Wait")

    _log.info(
        "Registered %d enzymes for replay: %s",
        len(daemon.enzymes),
        [e.name for e in daemon.enzymes],
    )


def run_replay(
    strategy_name: str,
    start_date: str,
    end_date: str,
    config_dir: Optional[str] = None,
) -> str:
    """
    Run the full enzyme pipeline on historical bars.

    1. Initialize daemon in paper + replay mode
    2. Initialize exchange and wrap with ReplayExchange
    3. Register enzymes (excluding replay-incompatible ones)
    4. Set equity from portfolio.fallback_equity_usdt
    5. Cache DynamicFilter universe at start
    6. Build cycle timestamps
    7. Run cycles: advance virtual clock, call run_cycle(), capture decisions
    8. Write results via OutcomeRecorder

    Returns:
        Path to the results file.
    """
    from core.daemon import Daemon
    from core.exchange import Exchange
    from core.replay_exchange import ReplayExchange
    from core.outcome_recorder import OutcomeRecorder

    # 1. Initialize daemon
    _log.info("Initializing daemon in replay mode: strategy=%s", strategy_name)
    daemon = Daemon(
        strategy_name=strategy_name,
        paper_mode=True,
        replay_mode=True,
        config_dir=config_dir,
    )
    daemon.initialize()

    # 2. Initialize exchange
    exchange = Exchange(daemon.config)
    replay_exchange = ReplayExchange(exchange)

    # 3. Get the virtual clock from substrate
    clock = daemon.substrate._clock
    replay_exchange.set_clock(clock)

    # 4. Set initial equity from config
    fallback_equity = daemon.substrate.cfg("portfolio.fallback_equity_usdt")
    daemon.substrate.portfolio["equity"] = fallback_equity
    _log.info("Initial equity set to %.2f USDT", fallback_equity)

    # 5. Cache DynamicFilter universe at start
    symbols_mode = daemon.substrate.cfg("symbols.mode", "static")
    if symbols_mode == "combined":
        _log.info("Combined mode: caching exchange universe at replay start")
        try:
            universe = replay_exchange.fetch_usdt_perps()
            daemon.substrate.market["_replay_universe"] = universe
            # Extract symbols from universe
            universe_symbols = [u["symbol"] for u in universe]
            # Merge with always_watch
            always_watch = daemon.substrate.cfg("symbols.always_watch", [])
            never_trade = daemon.substrate.cfg("symbols.never_trade", [])
            merged = list(dict.fromkeys(always_watch + universe_symbols))
            final = [s for s in merged if s not in never_trade]
            daemon.substrate.market["symbols_watched"] = final
            _log.info(
                "Universe cached: %d symbols (%d from exchange, %d always_watch)",
                len(final), len(universe_symbols), len(always_watch),
            )
        except Exception as e:
            _log.warning("Failed to cache universe: %s — using always_watch only", e)

    # 6. Register enzymes
    _register_enzymes(daemon, replay_exchange)

    # 7. Build cycle timestamps
    cycle_interval = daemon.substrate.cfg("strategy.cycle_interval_minutes")
    cycle_timestamps = build_cycle_timestamps(start_date, end_date, cycle_interval)
    _log.info(
        "Replay: %d cycles from %s to %s (interval=%dm)",
        len(cycle_timestamps), start_date, end_date, cycle_interval,
    )

    # 8. Initialize outcome recorder
    recorder = OutcomeRecorder(strategy_name, start_date, end_date)

    # 9. Run cycles
    for i, t_cursor in enumerate(cycle_timestamps):
        # Advance virtual clock
        clock.advance(t_cursor)

        # Run one daemon cycle (exact same code as live)
        try:
            result = daemon.run_cycle()
        except Exception as e:
            _log.error("Cycle %d failed at %s: %s", i, t_cursor.isoformat(), e)
            continue

        # Capture decisions for outcome recording
        recorder.capture_cycle(daemon.substrate, t_cursor)

        # Progress log every 100 cycles
        if (i + 1) % 100 == 0:
            action = daemon.substrate.decisions.get("action", "")
            equity = daemon.substrate.portfolio.get("equity", 0)
            n_pos = len(daemon.substrate.portfolio.get("open_positions", []))
            _log.info(
                "Progress: %d/%d cycles (%.1f%%) | action=%s equity=%.2f positions=%d",
                i + 1, len(cycle_timestamps),
                (i + 1) / len(cycle_timestamps) * 100,
                action, equity, n_pos,
            )

    # 10. Write results
    results_path = recorder.write_results()

    # 11. Cleanup
    clock.deactivate()

    _log.info("Replay complete: results written to %s", results_path)
    return results_path


def main() -> None:
    """CLI entry point for the replay driver."""
    parser = argparse.ArgumentParser(
        description="Historical replay driver — runs the full enzyme pipeline on historical bars"
    )
    parser.add_argument(
        "--start",
        required=True,
        help="Start date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end",
        default=None,
        help="End date (YYYY-MM-DD, default: today)",
    )
    parser.add_argument(
        "--strategy",
        default="momentum_rising",
        help="Strategy name (must match a YAML file in config/strategies/)",
    )
    parser.add_argument(
        "--config-dir",
        default=None,
        help="Config directory (default: project config/)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )

    args = parser.parse_args()

    # Default --end to today
    if args.end is None:
        args.end = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Setup logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Load .env
    from dotenv import load_dotenv
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

    _log.info("=" * 60)
    _log.info("Replay Driver: %s → %s (strategy: %s)", args.start, args.end, args.strategy)
    _log.info("=" * 60)

    results_path = run_replay(
        strategy_name=args.strategy,
        start_date=args.start,
        end_date=args.end,
        config_dir=args.config_dir,
    )

    _log.info("Done. Results: %s", results_path)


if __name__ == "__main__":
    main()
