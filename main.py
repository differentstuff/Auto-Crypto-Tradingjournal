#!/usr/bin/env python3
"""
main.py -- Single entrypoint for the Auto-Trader daemon.

Usage:
    python3 main.py                           # Live mode with default strategy
    python3 main.py --paper                   # Paper trading mode
    python3 main.py --strategy breakout       # Use a different strategy
    python3 main.py --cycle-once              # Run a single cycle and exit

The daemon runs 24/7, loading config on every cycle for hot-reload.
No restart needed to adjust strategy, risk limits, or indicator selection.
"""

import argparse
import logging
import os
import sys

# Add project root to Python path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from core.daemon import Daemon
from core.enzyme import create_enzyme, list_enzymes
from core.exchange import Exchange

# Import enzymes package to trigger @register_enzyme decorators
import enzymes  # noqa: F401


def setup_logging(level: str = "INFO") -> None:
    """Configure logging for the daemon."""
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=log_format,
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )

    # Reduce noise from third-party libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("ccxt").setLevel(logging.WARNING)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Auto-Trader: 24/7 automated crypto trading daemon"
    )
    parser.add_argument(
        "--strategy",
        default="momentum_rising",
        help="Strategy name (must match a YAML file in config/strategies/)",
    )
    parser.add_argument(
        "--paper",
        action="store_true",
        help="Paper trading mode: all enzymes run, no real orders placed",
    )
    parser.add_argument(
        "--cycle-once",
        action="store_true",
        help="Run a single cycle and exit (for testing)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging(args.log_level)
    log = logging.getLogger("main")

    log.info("=" * 60)
    log.info("Auto-Trader v2.0 -- Reaction Network Architecture")
    log.info("=" * 60)
    log.info("Strategy: %s", args.strategy)
    log.info("Paper mode: %s", args.paper)
    log.info("Cycle once: %s", args.cycle_once)

    # Initialize daemon
    daemon = Daemon(
        strategy_name=args.strategy,
        paper_mode=args.paper,
    )

    try:
        daemon.initialize()
    except Exception as e:
        log.error("Failed to initialize daemon: %s", e, exc_info=True)
        sys.exit(1)

    # --- Register enzymes by phase -------------------------------------------
    # Each enzyme is created via the registry (no direct class imports).
    # If an enzyme is missing from the registry, a warning is logged
    # and the system continues without it.

    # Phase B: Sensors and Evaluators
    phase_b_enzymes = [
        "CollectOHLCV",
        "ScoreConfluence",
        "DetectNoise",
        "ValidateEntryZone",
        "CollectPreTradeContext",
        "CollectMacroContext",
    ]
    for ename in phase_b_enzymes:
        enz = create_enzyme(ename, config=daemon.substrate._config)
        if enz:
            daemon.register_enzyme(enz)
        else:
            log.warning("Enzyme %s not found in registry (available: %s)", ename, list_enzymes())

    # Phase C: Regulators and Transporters
    # Exchange instance is created once and injected into enzymes that need it.
    exchange = Exchange(daemon.config) if daemon.config else None

    phase_c_enzymes = [
        "ApproveTrade",
        "ApproveExit",
        "RequestExit",
        "ExecuteTrade",
        "ExecuteExit",
        "SendTelegramLog",
    ]
    for ename in phase_c_enzymes:
        enz = create_enzyme(ename, config=daemon.substrate._config)
        if enz:
            daemon.register_enzyme(enz)
        else:
            log.warning("Enzyme %s not found in registry (available: %s)", ename, list_enzymes())

    # SyncPositions requires the Exchange instance for live mode
    sync_enz = create_enzyme("SyncPositions", config=daemon.substrate._config)
    if sync_enz and exchange:
        sync_enz.exchange = exchange
    if sync_enz:
        daemon.register_enzyme(sync_enz)
    else:
        log.warning("Enzyme SyncPositions not found in registry")

    # Wait enzyme (always available, lowest priority)
    wait_enz = create_enzyme("Wait", config=daemon.substrate._config)
    if wait_enz:
        daemon.register_enzyme(wait_enz)
    else:
        log.warning("Enzyme Wait not found in registry")

    # Log registered enzymes
    log.info("Registered enzymes: %s", [e.name for e in daemon.enzymes])
    log.info("Enzyme registry: %s", list_enzymes())

    if args.cycle_once:
        # Single cycle mode (for testing)
        log.info("Running single cycle...")
        result = daemon.run_cycle()
        log.info("Cycle result: %s", result)
        log.info("Substrate: %s", daemon.substrate)
    else:
        # Continuous daemon mode
        log.info("Starting daemon loop (Ctrl+C to stop)...")
        daemon.run()


if __name__ == "__main__":
    main()