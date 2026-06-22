# Backtest Analyzer

`scripts/analyze_backtest.py` — a CLI tool to filter huge backtest logs and summarize JSON result files.

## Problem

Backtest logs are enormous (4M+ lines) and 99.9%+ of cycles are `action=wait`. This tool filters out the noise so you can see what the system actually **did**: which trades it opened, why, with what entry/SL/TP, and what happened with exits.

## Quick Start

```bash
# See all non-wait cycles with full detail (trade entries, SL, TP, confluence, ISC blocks)
python scripts/analyze_backtest.py --log temp/backtest-stdout.log

# One-line summary per interesting cycle
python scripts/analyze_backtest.py --log temp/backtest-stdout.log --summary

# Analyze the latest JSON result
python scripts/analyze_backtest.py --results temp/results/

# Both at once
python scripts/analyze_backtest.py --log temp/backtest-stdout.log --results temp/results/
```

## Log Mode (`--log`)

Streams the log line-by-line (never loads it all into memory) and groups lines into cycle blocks. By default shows only cycles where `action != wait`.

### Flags

| Flag | Description |
|------|-------------|
| `--log PATH` | Path to the `.log` file |
| `--summary` | One-line summary per cycle instead of full detail |
| `--include-exits` | Also show wait cycles that contain exit signals (TP1/SL hits, exit approvals/denials, trailing stops) |
| `--cycle N` | Show only cycle number N (full detail) |
| `--action ACTION` | Filter to a specific action (e.g. `trade_open`) |

### What gets extracted per cycle

For each shown cycle, the tool extracts and highlights:

- **Trade entries**: direction, symbol, entry price, SL, TP1, size, R:R ratio, risk/reward %
- **Approval info**: effective score, Kelly fraction, ATR cap
- **Confluence**: how many symbols above threshold, top symbol
- **Exit signals**: TP1 hits, SL hits, trailing stop activations, exit approvals/denials
- **ISC blocks**: which enzymes were blocked and which ISCs failed
- **Position updates**: how many positions were mark-price updated

### Examples

```bash
# Just the trade-open cycles, one line each
python scripts/analyze_backtest.py --log temp/backtest-stdout.log --summary --action trade_open

# See cycle 66 in full (first trade)
python scripts/analyze_backtest.py --log temp/backtest-stdout.log --cycle 66

# Show trades AND cycles where exits were signalled but maybe not executed
python scripts/analyze_backtest.py --log temp/backtest-stdout.log --include-exits --summary
```

### Sample summary output

```
Cycle    66 | trade_open  | Long BTCUSDT | entry=89446.40 | SL=88359.63 | TP1=91619.95 | size=2.75 | eff=7.12 | kelly=0.250
Cycle    67 | trade_open  | Long BTCUSDT | entry=89446.40 | SL=88359.63 | TP1=91619.95 | size=2.75 | eff=7.12 | kelly=0.250
Cycle    68 | trade_open  | Long BTCUSDT | entry=89446.40 | SL=88359.63 | TP1=91619.95 | size=2.75 | eff=7.12 | kelly=0.250
```

### Sample detail output

```
────────────────────────────────────────────────────────────────────────────────
  CYCLE 66  |  action=trade_open
────────────────────────────────────────────────────────────────────────────────
  ▸ Extracted trade info:
    Direction:     Long
    Symbol:        BTCUSDT
    Entry:         89446.40
    Stop Loss:     88359.63  (risk 1.22%)
    TP1:           91619.95  (reward 2.43%)
    R:R ratio:     2.00
    Size (USDT):   2.75
    Eff. score:    7.12
    Kelly:         0.250
    ATR:           727.61  (cap 2.0%)
    Confluence:    3/3 above threshold (top=BTCUSDT, thresh=4.4)

  2026-06-16 16:29:55,796 [INFO] core.scheduler: Cycle 66 started (interval=30m)
  2026-06-16 16:29:55,796 [INFO] core.daemon: Step 0: firing ScoreConfluence ...
  ...
```

At the end, log statistics are printed:

```
================================================================================
LOG STATISTICS
================================================================================
  Total cycles:         178,619
  Wait cycles:          178,550
  Trade-open cycles:         69
  ─────────────────────────────
  Cycles shown:              69
  (0.04% of total)
```

## Results Mode (`--results`)

Loads a JSON result file (or picks the latest from a directory) and prints:

1. **Summary** — total cycles, trades, wins, losses, win rate, PnL
2. **Equity curve stats** — breakdown of actions (wait vs trade_open), non-wait entries
3. **Trades table** — every trade with entry, exit, SL, TP1, size, PnL, result, exit reason

### Flags

| Flag | Description |
|------|-------------|
| `--results PATH` | Path to a `.json` file or a results directory (picks latest) |
| `--trades-only` | Show only the trades table, skip summary and equity curve |

### Example

```bash
# Analyze latest result
python scripts/analyze_backtest.py --results temp/results/

# Specific file
python scripts/analyze_backtest.py --results temp/results/backtest_2026-01-01_2026-06-17_paper_v3_learning_test_2026-06-17T122059.json
```

### Sample output

```
Loading results: backtest_..._T122059.json
================================================================================

SUMMARY
----------------------------------------
  total_cycles           8065
  total_trades             3
  closed_trades            0
  open_trades              3
  wins                     0
  losses                   0
  win_rate_pct           0.0
  total_pnl_usd            0

EQUITY CURVE
----------------------------------------
  Total entries:       8,065
  wait                 8,062  (99.96%)
  trade_open               3  (0.04%)

  Non-wait entries (3):
    2026-01-02T08:30:00+00:00  equity=1000.00  positions=1  action=trade_open
    2026-01-02T09:00:00+00:00  equity=1000.00  positions=2  action=trade_open
    2026-01-02T09:30:00+00:00  equity=1000.00  positions=3  action=trade_open

TRADES
----------------------------------------
    #  Symbol     Dir   Entry        Exit         SL           TP1         Size   PnL        Result   Reason
  ───  ──────────  ───── ──────────── ──────────── ──────────── ──────────── ────── ────────── ──────── ────────────────────
    1  BTCUSDT    Long     89446.40  —            88359.63     91619.95      2.75  —          —
    2  BTCUSDT    Long     89446.40  —            88359.63     91619.95      2.75  —          —
    3  BTCUSDT    Long     89446.40  —            88359.63     91619.95      2.75  —          —

  Open trades:   3
  Closed trades: 0
```

## Tips

- **Use `--summary` first** to get an overview, then `--cycle N` to drill into specific cycles.
- **Use `--include-exits`** to find cycles where the system wanted to exit but couldn't (ISC blocks). This is useful for debugging why trades never close.
- **Combine log + results** to cross-reference: the log shows *why* a trade was taken, the JSON shows *if* it closed.
- The script uses only Python stdlib — no dependencies to install.