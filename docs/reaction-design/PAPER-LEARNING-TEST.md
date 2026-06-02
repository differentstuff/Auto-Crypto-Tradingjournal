# Paper Learning Test — Manual (V1, long-run)

Run the paper trading strategy `paper_learning_test` for 7–14 days, then verify
that the learning engine actually improved indicator weights and produced a rulebook.

> **⚠️ This is the LONG-RUN variant.** It is tuned for high statistical confidence
> over 7-14 days. If you want to validate the learning loop in **48-72 hours**,
> use the V2 strategy instead: `paper_v2_learning_test` — see
> [`PAPER-LEARNING-TEST-V2.md`](./PAPER-LEARNING-TEST-V2.md). V1 needs ~15
> closed trades before any verdict (vs V2's ~5) and ~15 trades before any
> weight adjustment (vs V2's ~8).

> **⚠️ Telegram is NOT implemented.** `modules.telegram_logs`,
> `modules.telegram_interaction`, and the `SendTelegramLog` enzyme are wired in
> code but **not implemented** — the feature is postponed indefinitely. Keep
> both flags at `false` in your strategy YAML. Enabling them logs "no Telegram
> token configured" and exits cleanly. This applies to **all** strategies.

---

## 1. What this test proves

The learning system must demonstrate three things before we trust it with live trading:

| # | Claim | How we verify |
|---|-------|---------------|
| L1 | **Signal accuracy verdicts converge** — each indicator gets a classification (valid / monitor / suppress / contrarian / review) based on real outcomes | `signal_accuracy` table has rows with verdicts other than `insufficient_data` |
| L2 | **Weights actually changed** — `compute_adjusted_weights()` boosted good signals, suppressed coin-flips, inverted contrarians | `weight_history` table shows adjustments with justification text |
| L3 | **Rulebook was generated** — the top-10 ranked rules from accuracy data were written | `rulebook_versions` table has at least one row for the strategy UID |

If all three hold, the learning loop is closed: trades → accuracy → weight adjustment → better scoring → better trades.

---

## 2. Run the test as a systemd service

### 2.1 One-time setup

```bash
# Clone / navigate to project
cd /opt/Auto-Crypto-Tradingjournal

# Create venv if it doesn't exist
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Initialize the database
python3 -c "from core.database import init_db; init_db()"
```

### 2.2 Quick smoke test (single cycle)

```bash
source venv/bin/activate
python3 main.py --paper --strategy paper_learning_test --cycle-once --log-level DEBUG
```

Check the last few lines of output for:
- `Registered N enzymes` — all enzymes loaded
- `Paper sync: equity initialized to fallback 1000.00 USDT` — first cycle
- No Python tracebacks

### 2.3 Create the systemd service file

```bash
sudo tee /etc/systemd/system/auto-trader-learning.service << 'EOF'
[Unit]
Description=Auto Trader — Paper Learning Test (Reaction Network)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=trader
Group=trader
WorkingDirectory=/opt/Auto-Crypto-Tradingjournal
ExecStart=/opt/Auto-Crypto-Tradingjournal/venv/bin/python main.py --paper --strategy paper_learning_test
ExecStopPost=/opt/Auto-Crypto-Tradingjournal/scripts/backup_db.sh
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=/opt/Auto-Crypto-Tradingjournal/.env

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/opt/Auto-Crypto-Tradingjournal /opt/Auto-Crypto-Tradingjournal/logs /opt/Auto-Crypto-Tradingjournal/data /opt/Auto-Crypto-Tradingjournal/backups

# Logging
StandardOutput=append:/opt/Auto-Crypto-Tradingjournal/logs/learning-test-stdout.log
StandardError=append:/opt/Auto-Crypto-Tradingjournal/logs/learning-test-stderr.log

[Install]
WantedBy=multi-user.target
EOF
```

### 2.4 Start the service

```bash
# Create log directory if needed
sudo -u trader mkdir -p /opt/Auto-Crypto-Tradingjournal/logs

# Reload systemd and start
sudo systemctl daemon-reload
sudo systemctl enable auto-trader-learning.service
sudo systemctl start auto-trader-learning.service

# Verify it's running
sudo systemctl status auto-trader-learning.service

# Watch live logs
sudo journalctl -u auto-trader-learning.service -f
# Or:
tail -f /opt/Auto-Crypto-Tradingjournal/logs/learning-test-stdout.log
```

### 2.5 Stop / restart

```bash
sudo systemctl stop auto-trader-learning.service
sudo systemctl restart auto-trader-learning.service

# To switch back to the production strategy:
# Edit ExecStart in the service file, change --strategy paper_learning_test
# to --strategy momentum_rising, then restart.
```

---

## 3. Verify learning results

The strategy UID for `paper_learning_test` is fixed:
```
a1b2c3d4-5678-9abc-def0-1234567890ab
```

All queries below use this UID. Run them after **at least 24 hours** of continuous paper trading
(ideally 48 hours for more trades).

### 3.1 Quick status check

```bash
cd /opt/Auto-Crypto-Tradingjournal
source venv/bin/activate

# How many closed trades do we have?
sqlite3 data/trading_journal.db "
  SELECT COUNT(*) AS closed_trades
  FROM trade_learning
  WHERE strategy_name = 'paper_learning_test'
    AND exit_time IS NOT NULL;
"

# Current equity (from last substrate snapshot)
sqlite3 data/trading_journal.db "
  SELECT json_extract(substrate_json, '$.portfolio.equity') AS equity
  FROM substrate_state
  WHERE strategy_name = 'paper_learning_test'
  ORDER BY id DESC LIMIT 1;
"
```

Expected: 10+ closed trades after 24h, 25+ after 48h.
Equity should differ from 1000.00 (the starting value).

### 3.2 L1: Signal accuracy verdicts

```bash
sqlite3 -header -column data/trading_journal.db "
  SELECT
    indicator_name,
    total_fired,
    correct,
    ROUND(accuracy_pct, 1) AS accuracy_pct,
    verdict,
    ROUND(confidence_95_low, 1) AS ci_low,
    ROUND(confidence_95_high, 1) AS ci_high
  FROM signal_accuracy
  WHERE strategy_uid = 'a1b2c3d4-5678-9abc-def0-1234567890ab'
  ORDER BY total_fired DESC;
"
```

**What to look for:**
- All 4 weighted indicators (rsi, macd, ema_stack, adx) should have rows
- `verdict` should be something other than `insufficient_data` (means ≥15 trades per signal)
- `accuracy_pct` above 60% = signal is useful; below 40% = contrarian candidate

### 3.3 L2: Weight adjustments

```bash
sqlite3 -header -column data/trading_journal.db "
  SELECT
    indicator_name,
    ROUND(old_weight, 4) AS old_w,
    ROUND(new_weight, 4) AS new_w,
    justification,
    ROUND(accuracy_at_time, 1) AS accuracy,
    sample_size_at_time AS n
  FROM weight_history
  WHERE strategy_uid = 'a1b2c3d4-5678-9abc-def0-1234567890ab'
  ORDER BY id;
"
```

**What to look for:**
- Rows exist (learning actually triggered weight changes)
- `justification` column explains why (e.g. "accuracy 78% (valid), highlight boost +20%")
- Negative weights = contrarian signals inverted (this is the key insight — a reliably wrong signal is as valuable as a reliably right one)

### 3.4 L3: Rulebook generation

```bash
sqlite3 data/trading_journal.db "
  SELECT rulebook_text
  FROM rulebook_versions
  WHERE strategy_uid = 'a1b2c3d4-5678-9abc-def0-1234567890ab'
  ORDER BY id DESC LIMIT 1;
"
```

**What to look for:**
- Non-empty output
- Rules ranked by priority (trades × |win_rate − 50|)
- Contrarian rules marked with `[!]`

### 3.5 Combination accuracy (bonus)

```bash
sqlite3 -header -column data/trading_journal.db "
  SELECT
    combination_name,
    direction_state,
    trades,
    won,
    ROUND(win_rate_pct, 1) AS win_rate,
    ROUND(p_value, 4) AS p_value,
    significance
  FROM combination_accuracy
  WHERE strategy_uid = 'a1b2c3d4-5678-9abc-def0-1234567890ab'
    AND significance != 'insufficient_data'
  ORDER BY trades DESC;
"
```

### 3.6 Trade equity curve (visual)

```bash
# Generate a simple ASCII equity chart from closed trades
sqlite3 data/trading_journal.db "
  SELECT
    exit_time,
    direction,
    symbol,
    ROUND(pnl_pct, 2) AS pnl_pct,
    outcome
  FROM trade_learning
  WHERE strategy_name = 'paper_learning_test'
    AND exit_time IS NOT NULL
  ORDER BY exit_time;
"
```

---

## 4. Automated verification script

A script that checks L1, L2, L3 and generates charts:

```bash
python3 scripts/verify_learning.py \
  --strategy paper_learning_test \
  --uid a1b2c3d4-5678-9abc-def0-1234567890ab \
  --db data/trading_journal.db
```

This script is at `scripts/verify_learning.py`. It outputs:

1. **Pass/Fail for L1, L2, L3** — with details
2. **Equity curve chart** — PNG saved to `data/learning_test_equity.png`
3. **Weight evolution chart** — shows original vs adjusted weights per indicator
4. **Accuracy bar chart** — per-indicator accuracy with Wilson CI error bars

If all three checks pass, the learning loop is verified working.

---

## 5. Interpreting results

### Good outcomes

| Pattern | Meaning |
|---------|---------|
| RSI accuracy 65%+, verdict "valid" | RSI is a useful signal — weight boosted |
| MACD accuracy 35%, verdict "contrarian" | MACD fires bullish but market goes bearish — invert it |
| EMA stack accuracy 52%, verdict "suppress" | Coin flip — ignore without confirmation |
| Rulebook has 5+ rules | Enough data to generate actionable rules |
| Equity curve trending up | The system is making net-positive decisions |

### Warning signs

| Pattern | Meaning |
|---------|---------|
| All verdicts "insufficient_data" | Not enough trades yet — wait longer |
| All verdicts "monitor" (55–75%) | No signal is strong enough to adjust — may need different indicators or more data |
| Weight history empty | `min_trades_before_adjusting` threshold not met (default 30 in code, 15 in YAML) |
| Equity flat at 1000 | No trades closed, or PnL not being applied to equity (bug) |

### The contrarian insight

A signal with ≤30% accuracy is **not useless** — it's a reliably wrong signal. If RSI fires "bullish" but the trade loses 70% of the time, then RSI-bullish is actually a **bearish** signal. The weight adjuster assigns negative weights to contrarian signals, which makes `ScoreConfluence` subtract their contribution instead of adding it. This is the most important thing to verify: contrarian signals should get negative weights in `weight_history`.

---

## 6. Reset for a fresh test

To wipe learning data and start over:

```bash
sqlite3 data/trading_journal.db "
  DELETE FROM trade_learning WHERE strategy_name = 'paper_learning_test';
  DELETE FROM signal_accuracy WHERE strategy_uid = 'a1b2c3d4-5678-9abc-def0-1234567890ab';
  DELETE FROM combination_accuracy WHERE strategy_uid = 'a1b2c3d4-5678-9abc-def0-1234567890ab';
  DELETE FROM trajectory_accuracy WHERE strategy_uid = 'a1b2c3d4-5678-9abc-def0-1234567890ab';
  DELETE FROM idle_condition_accuracy WHERE strategy_uid = 'a1b2c3d4-5678-9abc-def0-1234567890ab';
  DELETE FROM weight_history WHERE strategy_uid = 'a1b2c3d4-5678-9abc-def0-1234567890ab';
  DELETE FROM rulebook_versions WHERE strategy_uid = 'a1b2c3d4-5678-9abc-def0-1234567890ab';
  DELETE FROM substrate_state WHERE strategy_name = 'paper_learning_test';
"
```

Then restart the service:
```bash
sudo systemctl restart auto-trader-learning.service
```

---

## 7. Architecture reference

```
Trade closes
    │
    ▼
RecordTradeOutcome ──► writes to trade_learning DB
    │
    ▼
UpdateLearning ──► reads trade_learning, computes:
    │                 • update_signal_accuracy()     → signal_accuracy table
    │                 • update_combination_accuracy() → combination_accuracy table
    │                 • update_trajectory_accuracy()   → trajectory_accuracy table
    │                 • compute_adjusted_weights()     → weight_history table
    │                                                 → substrate.learning["adjusted_weights"]
    ▼
UpdateRulebook ──► reads all accuracy tables, generates ranked rules:
    │                 • generate_rulebook() → rulebook_versions table
    │                                     → substrate.learning["rulebook"]
    ▼
Next cycle: ScoreConfluence reads adjusted_weights + rulebook
    │
    ▼
Better scoring → better entries → better trades → better accuracy data
    │
    └─── The loop is closed.
```

The equity update path (Gap 2 fix):
```
ExecuteExit computes PnL → substrate.portfolio["equity"] += pnl_usdt
    │
    ▼
SyncPositions (paper mode) → preserves rolling equity (only resets to fallback if equity ≤ 0)
    │
    ▼
Substrate persisted to DB via save_substrate() → survives restart
```