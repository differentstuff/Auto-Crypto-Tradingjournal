# Paper Learning Test V2 — Fast-Iteration Runbook

> **Purpose:** Validate the learning system closes the loop within 48-72h, with
> statistically meaningful verdicts by day 7-10. Tuned for visible weight
> adjustments and rulebook generation, not for one-day proof.

---

## ⚠️ Telegram is NOT implemented

`modules.telegram_logs`, `modules.telegram_interaction`, and the
`SendTelegramLog` enzyme are wired in code but **not implemented**.
The feature is postponed indefinitely. Keep both flags at `false` in your
strategy YAML. Enabling them will log "no Telegram token configured" and exit
cleanly — not a crash, but pointless work.

This applies to **all** strategies (momentum_rising, paper_learning_test,
paper_v2_learning_test, etc.).

---

## 1. What this test proves

The learning system must demonstrate three things before we trust it with live
trading. With Path B thresholds, you'll see the first two within 48-72h and the
third within 4-5 days:

| # | Claim | How we verify | Expected timing |
|---|-------|---------------|------------------|
| L1 | **Signal accuracy verdicts converge** beyond `insufficient_data` | `signal_accuracy` table has rows with verdicts other than `insufficient_data` | Day 2-3 (after 5+ trades per indicator) |
| L2 | **Weights actually changed** with justification text | `weight_history` table shows adjustments with audit trail | Day 3-4 (after 8+ total closed trades) |
| L3 | **Rulebook was generated** with ranked rules | `rulebook_versions` table has at least one row, with contrarian markers `ANTI-SIGNAL` | Day 4-5 (after 12+ trades) |

If all three hold, the learning loop is closed: trades → accuracy → weight
adjustment → better scoring → better trades.

---

## 2. Run the test as a systemd service

### 2.1 Copy files to the live project directory

```bash
# From the KnowledgeBase staging area to the live project root
sudo cp /opt/KnowledgeBase/user_workspaces/code/Auto-Trader/config/strategies/paper_v2_learning_test.yaml \
        /opt/Auto-Trader/config/strategies/

sudo cp /opt/KnowledgeBase/user_workspaces/code/Auto-Trader/auto-trader-learning-v2.service \
        /opt/Auto-Trader/auto-trader-learning-v2.service
```

### 2.2 One-time setup (if not already done)

```bash
cd /opt/Auto-Trader

# Create venv if it doesn't exist
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Initialize the database (safe to re-run; uses CREATE TABLE IF NOT EXISTS)
python3 -c "from core.database import init_db; init_db()"
```

### 2.3 Quick smoke test (single cycle, ~30s)

```bash
cd /opt/Auto-Trader
source venv/bin/activate
python3 main.py --paper --strategy paper_v2_learning_test --cycle-once --log-level INFO
```

Check the last few lines for:
- `Registered N enzymes` — all 19 enzymes loaded
- `Exchange initialized: primary=bitget, data_source=bitget, paper=True` — exchange OK
- `Strategy: paper_v2_learning_test`
- No Python tracebacks (warnings are OK; LLM router may log "non-fatal" if no key)

### 2.4 Install and start the systemd service

```bash
# Create log dir if needed
sudo -u trader mkdir -p /opt/Auto-Trader/logs

# Install service file
sudo cp /opt/Auto-Trader/auto-trader-learning-v2.service \
        /etc/systemd/system/auto-trader-learning-v2.service

# Reload systemd, enable, start
sudo systemctl daemon-reload
sudo systemctl enable auto-trader-learning-v2.service
sudo systemctl start auto-trader-learning-v2.service

# Verify it's running
sudo systemctl status auto-trader-learning-v2.service
```

### 2.5 Inspect logs without watching live

The daemon writes to two places — a rotating file and a systemd append-only log.
**Do not** use `journalctl -f` for long-running inspection; the file is easier
to grep and tail later.

```bash
# Primary log file (rotating, 10MB × 5 backups)
tail -n 100 /opt/Auto-Trader/logs/auto-trader.log

# systemd stdout/stderr (append-only, easier to grep for "ERROR" or "Updated")
sudo tail -n 100 /opt/Auto-Trader/logs/v2-learning-stdout.log
sudo grep -i "error\|exception\|traceback" /opt/Auto-Trader/logs/v2-learning-stderr.log
```

### 2.6 Stop / restart / switch strategy

```bash
# Stop
sudo systemctl stop auto-trader-learning-v2.service

# Restart (e.g. after editing the YAML)
sudo systemctl restart auto-trader-learning-v2.service

# Switch back to production: edit /etc/systemd/system/auto-trader-learning-v2.service
# change ExecStart to use --strategy momentum_rising, then restart
```

---

## 3. Verify learning results

The strategy UID for `paper_v2_learning_test` is fixed:

```
b2c3d4e5-6789-0abc-def1-2345678901bc
```

All queries below use this UID. Run them on the **VPS**, not the KnowledgeBase
staging area.

### 3.1 Quick status check (run after 24h)

```bash
cd /opt/Auto-Trader

# How many closed trades so far?
sqlite3 data/auto_trader.db "
  SELECT COUNT(*) AS closed_trades
  FROM trade_learning
  WHERE strategy_name = 'paper_v2_learning_test'
    AND exit_time IS NOT NULL;
"

# Current equity (from last substrate snapshot)
sqlite3 data/auto_trader.db "
  SELECT json_extract(substrate_json, '$.portfolio.equity') AS equity
  FROM substrate_state
  WHERE strategy_name = 'paper_v2_learning_test'
  ORDER BY id DESC LIMIT 1;
"
```

**Expected after 24h:** 1-3 closed trades (1h TF, 2-4 trades/day).
**Expected after 48h:** 3-6 closed trades.
**Expected after 72h:** 5-10 closed trades — enough to start verdicts.

Equity should differ from 1000.00 (the starting fallback) after the first
closed trade. If equity is exactly 1000.00, no trade has closed yet.

### 3.2 L1: Signal accuracy verdicts (target: day 2-3)

```bash
sqlite3 -header -column data/auto_trader.db "
  SELECT
    indicator_name,
    total_fired,
    correct,
    ROUND(accuracy_pct, 1) AS accuracy_pct,
    verdict,
    ROUND(confidence_95_low, 1) AS ci_low,
    ROUND(confidence_95_high, 1) AS ci_high
  FROM signal_accuracy
  WHERE strategy_uid = 'b2c3d4e5-6789-0abc-def1-2345678901bc'
  ORDER BY total_fired DESC;
"
```

**What to look for:**
- All 4 weighted indicators (rsi, macd, ema_stack, adx) should have rows
- `verdict` should be something other than `insufficient_data` (means ≥5 trades per signal)
- `accuracy_pct` above 75% = signal is useful; below 30% = contrarian candidate
- Wilson CI is wide on small samples (e.g. 3/5 = 60% has CI [23%, 88%]) — that's
  expected and means the system is being honest about uncertainty

### 3.3 L2: Weight adjustments (target: day 3-4)

```bash
sqlite3 -header -column data/auto_trader.db "
  SELECT
    indicator_name,
    ROUND(old_weight, 4) AS old_w,
    ROUND(new_weight, 4) AS new_w,
    justification,
    ROUND(accuracy_at_time, 1) AS accuracy,
    sample_size_at_time AS n
  FROM weight_history
  WHERE strategy_uid = 'b2c3d4e5-6789-0abc-def1-2345678901bc'
  ORDER BY id;
"
```

**What to look for:**
- Rows exist (learning actually triggered weight changes)
- `justification` column explains why (e.g. "accuracy 78% (valid), highlight boost +20%")
- **Negative weights = contrarian signals inverted** — this is the key insight.
  A reliably-wrong signal is as valuable as a reliably-right one. If RSI fires
  bullish but the trade loses 70% of the time, RSI-bullish is a bearish signal.

### 3.4 L3: Rulebook generation (target: day 4-5)

```bash
sqlite3 data/auto_trader.db "
  SELECT rulebook_text
  FROM rulebook_versions
  WHERE strategy_uid = 'b2c3d4e5-6789-0abc-def1-2345678901bc'
  ORDER BY id DESC LIMIT 1;
"
```

**What to look for:**
- Non-empty output
- Rules ranked by priority (trades × |win_rate − 50|)
- Contrarian rules marked with `ANTI-SIGNAL` (these say "invert this signal")

### 3.5 Combination accuracy (bonus)

```bash
sqlite3 -header -column data/auto_trader.db "
  SELECT
    combination_name,
    direction_state,
    trades,
    won,
    ROUND(win_rate_pct, 1) AS win_rate,
    ROUND(p_value, 4) AS p_value,
    significance
  FROM combination_accuracy
  WHERE strategy_uid = 'b2c3d4e5-6789-0abc-def1-2345678901bc'
    AND significance != 'insufficient_data'
  ORDER BY trades DESC;
"
```

### 3.6 Trade equity curve (visual)

```bash
sqlite3 -header -column data/auto_trader.db "
  SELECT
    exit_time,
    direction,
    symbol,
    ROUND(pnl_pct, 2) AS pnl_pct,
    outcome
  FROM trade_learning
  WHERE strategy_name = 'paper_v2_learning_test'
    AND exit_time IS NOT NULL
  ORDER BY exit_time;
"
```

---

## 4. Automated verification script

A script that checks L1, L2, L3 and generates charts:

```bash
cd /opt/Auto-Trader
source venv/bin/activate
python3 scripts/verify_learning.py \
  --strategy paper_v2_learning_test \
  --uid b2c3d4e5-6789-0abc-def1-2345678901bc \
  --db data/auto_trader.db
```

Outputs:
1. **Pass/Fail for L1, L2, L3** with details
2. **Equity curve chart** — PNG saved to `data/learning_test_equity.png`
3. **Weight evolution chart** — original vs adjusted weights per indicator
4. **Accuracy bar chart** — per-indicator accuracy with Wilson CI error bars

If all three checks pass, the learning loop is verified working.

---

## 5. Interpreting results

### Good outcomes

| Pattern | Meaning |
|---------|---------|
| RSI accuracy 65%+, verdict "monitor" | RSI shows signal but not yet strong — keep watching |
| MACD accuracy 35%, verdict "review" | MACD borderline — weight reduced by 10% |
| EMA stack accuracy 52%, verdict "suppress" | Coin flip — weight set to 0 |
| Rulebook has 5+ rules | Enough data to generate actionable rules |
| Equity curve trending up | The system is making net-positive decisions |
| Weight history shows negative weight for some indicator | Contrarian detection working — the system is inverting a reliably-wrong signal |

### Warning signs (do not panic — they may self-resolve)

| Pattern | Meaning | Action |
|---------|---------|--------|
| All verdicts "insufficient_data" | Not enough trades yet (need 5+ per signal) | Wait 1-2 more days |
| All verdicts "monitor" (55-75%) | No signal is strong enough to adjust | Expected early on; needs 10+ samples per signal for "valid" |
| Weight history empty after 4 days | `min_trades_before_adjusting: 8` not yet hit | Expected if <8 closed trades total; check trade count |
| Equity flat at 1000 | No trades closed, OR PnL not applied (bug) | If 24h+ with no trades, check `is there candidates above threshold?` in logs |
| Verdicts flip between runs | Small-sample noise — Wilson CI is wide | Expected; stabilises around 15+ samples per signal |

### The contrarian insight

A signal with ≤30% accuracy is **not useless** — it's a reliably wrong signal.
If RSI fires "bullish" but the trade loses 70% of the time, then RSI-bullish
is actually a **bearish** signal. The weight adjuster assigns negative weights
to contrarian signals, which makes `ScoreConfluence` subtract their contribution
instead of adding it. This is the most important thing to verify: contrarian
signals should get negative weights in `weight_history`.

---

## 6. Reset for a fresh test

To wipe learning data and start over:

```bash
sqlite3 /opt/Auto-Trader/data/auto_trader.db "
  DELETE FROM trade_learning WHERE strategy_name = 'paper_v2_learning_test';
  DELETE FROM signal_accuracy WHERE strategy_uid = 'b2c3d4e5-6789-0abc-def1-2345678901bc';
  DELETE FROM combination_accuracy WHERE strategy_uid = 'b2c3d4e5-6789-0abc-def1-2345678901bc';
  DELETE FROM trajectory_accuracy WHERE strategy_uid = 'b2c3d4e5-6789-0abc-def1-2345678901bc';
  DELETE FROM idle_condition_accuracy WHERE strategy_uid = 'b2c3d4e5-6789-0abc-def1-2345678901bc';
  DELETE FROM weight_history WHERE strategy_uid = 'b2c3d4e5-6789-0abc-def1-2345678901bc';
  DELETE FROM rulebook_versions WHERE strategy_uid = 'b2c3d4e5-6789-0abc-def1-2345678901bc';
  DELETE FROM substrate_state WHERE strategy_name = 'paper_v2_learning_test';
"
```

Then restart the service:

```bash
sudo systemctl restart auto-trader-learning-v2.service
```

---

## 7. Comparison with paper_learning_test.yaml (V1)

| Setting | V1 (long-run, 7-14 days) | V2 (fast iteration, 48-72h) |
|---------|---------------------------|-------------------------------|
| Timeframe | 4h | 1h |
| Cycle interval | 10 min | 5 min |
| Symbols (always_watch) | 5 | 7 |
| entry_threshold | 5.5 | 5.0 |
| confluence_min_signals | 3 | 2 |
| min_trades_before_adjusting | 15 | 8 |
| min_trades_per_signal | 10 | 5 |
| retrain_every_n_trades | 5 | 3 |
| trajectory_lookback_hours | 24 | 24 |
| First weight adjustment | ~day 8-10 | ~day 3-4 |
| First rulebook | ~day 10-14 | ~day 4-5 |
| Statistical confidence by | day 21+ | day 7-10 |
| Strategy UID | a1b2c3d4-5678-9abc-def0-1234567890ab | b2c3d4e5-6789-0abc-def1-2345678901bc |

**When to use V1 vs V2:**
- **V2 (this file):** You want to validate the learning loop closes within a
  week. Fast feedback, slightly less statistical confidence initially.
- **V1 (paper_learning_test.yaml):** You have 2-3 weeks and want high-confidence
  verdicts before going live. More trades, more conservative thresholds.

Both strategies can run side-by-side if needed — they have separate UIDs and
write to distinct rows in all learning tables.

---

## 8. Architecture reference

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

The equity update path (from `substrate_state`):
```
ExecuteExit computes PnL → substrate.portfolio["equity"] += pnl_usdt
    │
    ▼
SyncPositions (paper mode) → preserves rolling equity (only resets to fallback if equity ≤ 0)
    │
    ▼
Substrate persisted to DB via save_substrate() → survives restart
```
