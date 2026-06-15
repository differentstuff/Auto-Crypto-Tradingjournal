# Export Trades — CSV Export for TradingView Analysis

Export trades from `trade_learning` to CSV so you can cross-reference entries/exits on TradingView charts.

## Quick Start

```bash
# Activate venv from project root
source .venv/bin/activate

# The 207 wins at threshold 5.5
python -m scripts.time_travel.export_trades --threshold 5.5 --outcome win

# All trades at 5.5 (wins + losses)
python -m scripts.time_travel.export_trades --threshold 5.5

# Just the losses — where the strategy breaks down
python -m scripts.time_travel.export_trades --threshold 5.5 --outcome loss
```

## Filters

All filters combine freely. Each one narrows the result set.

| Flag | Example | Notes |
|---|---|---|
| `--threshold` | `5.5` | Exact threshold used (from `signals_at_entry_json`) |
| `--outcome` | `win`, `loss`, `breakeven` | Trade outcome |
| `--symbol` | `BTCUSDT` | Single symbol |
| `--from` | `2026-01-01` | Entry time ≥ this date |
| `--to` | `2026-06-01` | Entry time ≤ this date |
| `--source` | `time_travel` or `live` | Backtest vs production trades |
| `-o` | `my_analysis.csv` | Custom output path |

## Output Columns

| Column | Description | Use on TradingView |
|---|---|---|
| `entry_time` | ISO timestamp of entry | Jump to this bar on the chart |
| `exit_time` | ISO timestamp of exit | See where the trade closed |
| `symbol` | Trading pair | Which chart to open |
| `direction` | `long` / `short` | Trade side |
| `entry_price` | Entry price (from OHLCV) | Cross-reference with candle |
| `exit_price` | Exit price | Did it exit at a key level? |
| `pnl_pct` | Realized P&L % | How much the move captured |
| `pnl_usdt` | Net P&L in USDT (after fees) | Dollar-math profitability |
| `duration_hours` | Trade length | Scalp vs swing classification |
| `confluence_score` | Score at entry | Was 5.5 barely above threshold or way above? |
| `threshold` | Threshold used for this trade | Which bucket it belongs to |
| `threshold_bucket` | `production` / `exploration` | Production = ≥ entry_threshold |
| `exit_reason` | `tp`, `sl`, `trailing_stop`, `signal_flip` | Did the trail stop kill a good trade? |
| `sl_hit` | 0/1 | Did the stop-loss trigger? |
| `trailing_stop_hit` | 0/1 | Did the trailing stop trigger? |
| `mfe_pct` | Max favorable excursion % | How far did it go your way? |
| `mae_pct` | Max adverse excursion % | How much heat before profit? |
| `source` | `time_travel` / `live` | Backtest or production trade |
| `strategy_name` | Strategy that generated the trade | For multi-strategy DBs |
| `strategy_uid` | Unique strategy identifier | For multi-strategy DBs |

## Output Location

Default: `data/trades_{filters}.csv` — filename is built from your filters.

```bash
# Examples of auto-generated filenames:
--threshold 5.5 --outcome win       → data/trades_t5.5_win.csv
--threshold 5.5 --symbol btcusdt    → data/trades_t5.5_btcusdt.csv
--threshold 4.4 --outcome loss      → data/trades_t4.4_loss.csv
# No filters                        → data/trades.csv
```

Override with `-o`:
```bash
python -m scripts.time_travel.export_trades --threshold 5.5 -o ~/Desktop/high_conv.csv
```

## Typical Workflows

### 1. Verify high-conviction trades
```bash
python -m scripts.time_travel.export_trades --threshold 5.5 --outcome win
# Open each entry_time on TradingView → check if the signal was real
```

### 2. Find where the strategy fails
```bash
python -m scripts.time_travel.export_trades --threshold 5.5 --outcome loss
# Look for patterns: same symbol? same market regime? trailing_stop killing trades?
```

### 3. Compare threshold tiers
```bash
python -m scripts.time_travel.export_trades --threshold 3.6 -o data/t3_6.csv
python -m scripts.time_travel.export_trades --threshold 4.4 -o data/t4_4.csv
python -m scripts.time_travel.export_trades --threshold 5.5 -o data/t5_5.csv
# Compare win rates, mfe/mae ratios across tiers
```

### 4. Isolate a time period
```bash
python -m scripts.time_travel.export_trades --from 2026-03-01 --to 2026-05-01
# Was the strategy worse during a specific market phase?
```

### 5. Exclude backtest trades (production only)
```bash
python -m scripts.time_travel.export_trades --source live
```

## Data Source

Reads from `trade_learning` table in `data/trading_journal.db`.

For `time_travel` trades, prices and threshold metadata are stored inside `signals_at_entry_json` (prefixed with `_`) — this script unpacks them automatically. Live trades may not have `_entry_price` / `_exit_price` populated (they come from the exchange fill, not OHLCV).
