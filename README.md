# Auto-Trader

> **Disclaimer:** Use at your own risk. Not financial advice. Open-source and self-hosted — no guarantees. Built for personal use, offered free to private individuals.

Self-hosted, 24/7 automated crypto futures trading system. You write a strategy in YAML (symbols, indicators, risk limits, entry/exit rules). The daemon trades it, tracks which signals actually predict wins, suppresses the ones that didn't, and adapts over time. No user interaction required. Designed for Linux. LLM integration is optional — the system is designed to run fully without it.

---

## Current State

Fully operational. Daemon loop, 23 enzymes, indicator registry (RSI, MACD, EMA, ADX, ATR, S/R, OBV, VWAP, WaveTrend, momentum quality), learning engine (per-signal accuracy, pairwise combinations, trajectory, weight adjuster, rulebook), exchange integration (CCXT, paper mode), strategy YAML hot-plug, backtester with quality metrics (PBO, Deflated Sharpe, Bootstrap CI), soft penalties, learning-adjusted thresholds, setup script, smoke tests, systemd service.

---

## Strategy Configuration

Strategies are YAML files. The daemon picks up changes on every cycle — no restart needed. Copy the template and customize:

```bash
cp config/strategies/_template.yaml config/strategies/my_strategy.yaml
```

Every option is documented inline in `config/strategies/_template.yaml`.

---

## Setup

```bash
bash setup.sh
```

Idempotent. Installs Python 3.13, creates venv, installs dependencies, runs smoke tests.

Then edit `.env` with your exchange API keys. Paper mode needs no exchange keys and no LLM keys.

```bash
source venv/bin/activate
python main.py --paper --strategy paper_test --cycle-once   # quick test
python main.py --paper --strategy momentum_rising            # run continuously
```

---

## Backtest

```bash
source venv/bin/activate
python -m core.replay_driver --start 2025-01-01 --end 2025-06-01 --strategy momentum_rising
```

Runs the daemon's exact pipeline on historical bars. Results saved to `temp/backtest_<timestamp>/`.

Analyze results and filter logs:

```bash
python scripts/analyze_backtest.py --results temp/results/
python scripts/analyze_backtest.py --log logs/backtest-stdout.log --summary
```

See `docs/README_BACKTEST_ANALYZER.md` for full analyzer usage.

---

## Design References

- `docs/Network_Framework.md` — Architecture overview
- `docs/ReactionNetworkModel.md` — Reaction network design
- `docs/README_BACKTEST_ANALYZER.md` — Backtest analyzer usage
- `config/strategies/_template.yaml` — Full strategy config template

---

## License

This project is licensed under [Apache 2.0 with Commons Clause](./LICENSE).

Not free for commercial use: selling, SaaS, or commercial deployment requires permission — see LICENSE file.
