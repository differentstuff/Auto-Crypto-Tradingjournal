# Learning Engine Design

> The unique selling point. A self-improving system that learns from every trade and every idle cycle.

---

## Overview

The learning engine is Agent 5 in the reaction network -- the **HindsightAnalyzer**. It is the only enzyme that can modify the rulebook and adjust signal weights. It runs after every closed trade and periodically on idle cycles.

The engine has four components:

| Component | File | Purpose |
|-----------|------|---------|
| Tracker | `learning/tracker.py` | Record per-trade data: all signal states, trajectory, outcome |
| Analyzer | `learning/analyzer.py` | Per-signal accuracy: "RSI was right 71% of the time" |
| Combination | `learning/combination.py` | Pairwise accuracy: "RSI+MACD together = 83% win" |
| Trajectory | `learning/trajectory.py` | Pre-trade indicator trajectory: coincidence vs gradual alignment |
| Rulebook | `learning/rulebook.py` | Auto-generate rules from findings, max 10 entries |

---

## 1. Per-Trade Data Collection (Tracker)

### What Gets Recorded

Every trade (and every idle cycle) produces a learning record stored in the database.

**Trade learning record** (see `substrate-schema.yaml` for full structure):

```
trade_id: 142
symbol: SOLUSDT
direction: long
strategy_name: momentum_rising
entry_time: 2026-05-19 14:00 UTC
exit_time: 2026-05-19 18:30 UTC
outcome: won
pnl_pct: 2.3
duration_minutes: 270
confluence_score_at_entry: 7.8

signals_at_entry:
  rsi: {value: 28.5, signal: "bullish", strength: 0.7}
  macd: {value: "bullish_cross", signal: "bullish", strength: 0.8}
  ema_stack: {value: "21>55>200", signal: "bullish", strength: 0.9}
  adx: {value: 32.1, signal: "trending", strength: 0.6}

pre_trade_trajectory_pattern: gradual_alignment
pre_trade_coincidence_risk: low

max_favorable_excursion_pct: 4.1   (MFE: max profit reached)
max_adverse_excursion_pct: -1.2     (MAE: max loss reached)

sl_hit: false
trailing_stop_hit: true
exit_reason: trailing

rulebook_version: "2026-05-19-v3"
```

### Idle Cycle Recording

When no trade is made, the system records WHY:

```
idle_cycle_id: 847
timestamp: 2026-05-19 09:00 UTC
strategy_name: momentum_rising
action: wait

idle_reasons:
  - "no candidates above threshold"
  - "VIX > 35 (macro module)"

market_conditions_at_idle:
  vix: 38.2
  btc_dominance: 62.1
  avg_confluence_score: 3.2       (below entry_threshold)

what_would_have_happened:          (computed retrospectively)
  top_candidate: ETHUSDT
  candidate_score: 4.5
  hypothetical_direction: long
  hypothetical_pnl_if_entered: -1.8%  (would have lost)
  retrospect_validated: true      (waiting was the correct decision)
```

This is crucial: **the system tracks when NOT trading was the right decision**. Over time, it learns conditions where waiting is optimal (e.g., "during VIX > 30, waiting saved us from losses 80% of the time, n=15").

---

## 2. Per-Signal Accuracy (Analyzer)

### Calculation Method

After each trade closes, the analyzer updates the accuracy record for each signal that was present at entry:

```python
def update_signal_accuracy(trade_record, signal_accuracy_db):
    for indicator_name, signal_data in trade_record.signals_at_entry.items():
        entry = signal_accuracy_db.get(indicator_name)
        if entry is None:
            entry = SignalAccuracyEntry(indicator_name=indicator_name)

        # Did the signal direction match the outcome?
        if trade_record.outcome == "won":
            if signal_data.signal == trade_record.direction:
                entry.correct += 1
            else:
                # Signal was bearish but trade won (long) = signal was wrong
                pass
        elif trade_record.outcome == "lost":
            if signal_data.signal != trade_record.direction:
                entry.correct += 1  # correctly predicted failure
            else:
                pass  # signal was bullish but trade lost = signal was wrong

        entry.total_fired += 1
        entry.accuracy_pct = entry.correct / entry.total_fired * 100
        entry.confidence_95 = wilson_score_interval(entry.correct, entry.total_fired)
        entry.verdict = classify_verdict(entry.accuracy_pct, entry.total_fired)
```

### Verdict Classification

| Verdict | Accuracy | Sample Size | Action |
|---------|----------|-------------|--------|
| `valid` | >= 75% | >= min_trades_per_signal | Boost weight, include in rulebook highlights |
| `valid` | 55-75% | >= min_trades_per_signal | Keep current weight, monitor |
| `suppress` | < 55% | >= min_trades_per_signal | Remove from scoring (coin flip or worse) |
| `review` | 55-60% | >= min_trades_per_signal | Borderline, keep watching but flag |
| `insufficient_data` | any | < min_trades_per_signal | Do not adjust weight, keep collecting |

### Wilson Score Interval

Used instead of simple percentage for statistical confidence on small samples:

```
Wilson score = (p + z^2/2n - z*sqrt(p*(1-p)/n + z^2/4n^2)) / (1 + z^2/n)

where:
  p = correct / total (observed accuracy)
  n = total_fired (sample size)
  z = 1.96 (95% confidence)
```

This prevents the system from drawing conclusions from 5 trades. A signal with "80% accuracy on 5 trades" has a Wilson interval of [34%, 98%] -- too wide to be actionable.

---

## 3. Pairwise Combination Accuracy (Combination)

### What It Measures

Not just "was RSI accurate?" but "when RSI AND MACD both fired bullish, what happened?"

```python
def update_combination_accuracy(trade_record, combination_db):
    # Get all pairs of signals that were aligned at entry
    aligned_signals = [
        name for name, data in trade_record.signals_at_entry.items()
        if data.signal == trade_record.direction
    ]

    # Check pairwise combinations
    for i, sig_a in enumerate(aligned_signals):
        for sig_b in aligned_signals[i+1:]:
            combo_name = f"{sig_a}+{sig_b}"
            direction_state = "both_bullish" if trade_record.direction == "long" else "both_bearish"
            entry = combination_db.get(combo_name, direction_state)
            if entry is None:
                entry = CombinationEntry(combo_name, direction_state)

            entry.trades += 1
            if trade_record.outcome == "won":
                entry.won += 1
            entry.win_rate_pct = entry.won / entry.trades * 100
            entry.avg_pnl_pct += trade_record.pnl_pct / entry.trades
            entry.p_value = chi_squared_test(entry.won, entry.trades)
            entry.significance = classify_significance(entry.p_value, entry.trades)
```

### Statistical Significance (Chi-Squared)

```
chi_squared = sum((observed - expected)^2 / expected)

For a combination with win_rate_pct = 83% and n = 12 trades:
  observed wins = 10
  expected wins (null hypothesis: 50%) = 6
  chi_squared = (10-6)^2/6 + (2-6)^2/6 = 2.67 + 2.67 = 5.33

p_value = chi2.sf(5.33, df=1) = 0.02  (< 0.05, statistically significant)

Conclusion: This combination is NOT random. Form a rule from it.
```

### Anti-Signals

If a combination has a win rate below 30% (e.g., "RSI+MACD both bearish = 25% win rate"), this is an **anti-signal**: avoid this combination or use it as a contrarian indicator.

---

## 4. Pre-Trade Trajectory Analysis

### Why It Matters

Two trades can have identical indicator states at entry, but one is a **gradual alignment** (signals rising over 8 bars) and the other is a **sudden snap** (signals aligned in 1-2 bars). The gradual alignment is more likely to persist. The sudden snap may be coincidence.

### Trajectory Classification

| Pattern | Description | Coincidence Risk | Trading Implication |
|---------|-------------|-----------------|---------------------|
| `gradual_alignment` | Signals rose/fell together over 6+ bars | Low | Full position size, normal entry |
| `sudden_snap` | Signals aligned in 1-2 bars only | High | Reduce position size by 50%, or skip |
| `oscillating` | Signals flip back and forth over bars | Medium | Skip entry, market is undecided |
| `flat` | No meaningful change in indicators | Low | No entry signal at all (no candidate) |

### How It Is Computed

```python
def classify_trajectory(indicator_trajectory, lookback_bars=12):
    # For each enabled indicator, compute how it changed over lookback_bars
    # Check: did it move consistently in one direction, or oscillate?

    consistent_bars = 0
    for i in range(1, lookback_bars):
        # Was indicator moving in the same direction as the final signal?
        if indicator_trajectory[i] aligns with final_signal_direction:
            consistent_bars += 1

    consistency_ratio = consistent_bars / lookback_bars

    if consistency_ratio >= 0.75:
        return "gradual_alignment", "low"
    elif consistency_ratio <= 0.25:
        return "sudden_snap", "high"
    elif consistency_ratio between 0.4 and 0.6:
        return "oscillating", "medium"
    else:
        return "mixed", "medium"
```

### Learning from Trajectory

The learning engine tracks whether trajectory pattern correlates with outcome:

```
trajectory_accuracy:
  gradual_alignment:
    trades: 23
    won: 18
    win_rate_pct: 78.3
    verdict: "valid -- enter with full size when gradual"

  sudden_snap:
    trades: 12
    won: 4
    win_rate_pct: 33.3
    verdict: "suppress -- reduce size or skip when sudden"

  oscillating:
    trades: 8
    won: 2
    win_rate_pct: 25.0
    verdict: "suppress -- skip when oscillating"
```

This becomes a rulebook entry: "Avoid entries where indicators snapped together in 1-2 bars. Gradual alignment over 6+ bars wins 78% (18/23)."

---

## 5. Idle Cycle Learning

### The Problem

A system that only trades in perfect conditions will have a "high win rate" that is misleading. It cherry-picked easy trades. A real metric needs to account for **when the system correctly chose NOT to trade**.

### How It Works

Every idle cycle, the system records:

1. **Why it waited** (idle_reasons)
2. **What would have happened** if it had entered (computed retrospectively)
3. **Market conditions** during the idle period

After 30+ idle cycles, the learning engine can form rules:

```
idle_condition_accuracy:
  "VIX > 35":
    idle_cycles: 15
    hypothetical_loss_pct: -2.1     (average loss if we had entered)
    waiting_was_correct: true       (80% of the time, entering would have lost)
    verdict: "avoid entries during VIX > 35"

  "kill_zone active":
    idle_cycles: 20
    hypothetical_loss_pct: -1.8
    waiting_was_correct: true       (90% of the time)
    verdict: "avoid entries during kill_zone"

  "no candidates above threshold":
    idle_cycles: 40
    hypothetical_loss_pct: -0.3     (slight losses if we had forced entries)
    waiting_was_correct: true       (65% of the time)
    verdict: "correct to wait when no clear setups"
```

### Retrospective Validation

The system does NOT actually enter trades during idle cycles. Instead, it:

1. Records the top candidate that was below threshold
2. Tracks that candidate's price movement over the next few bars
3. Computes hypothetical PnL if it had entered at that point
4. Validates whether waiting was the right decision

This is done purely in the database, no real orders.

---

## 6. Rulebook Generation

### Input Sources

The rulebook is generated from three sources:

| Source | What it contributes |
|--------|---------------------|
| Signal accuracy | "RSI valid at 71%. MACD suppress at 50%." |
| Combination accuracy | "RSI+MACD both bullish = 83% (p=0.008)." |
| Trajectory accuracy | "Gradual alignment wins 78%. Sudden snap loses 67%." |
| Idle cycle accuracy | "VIX>35: waiting correct 80% of the time." |

### Generation Process

```python
def generate_rulebook(signal_accuracy, combination_accuracy,
                      trajectory_accuracy, idle_accuracy, max_rules=10):
    candidates = []

    # From combinations (highest priority)
    for combo, data in combination_accuracy.items():
        if data.significance == "significant" and data.trades >= min_trades:
            candidates.append(RuleCandidate(
                source="combination",
                text=f"{combo} {data.direction_state}: "
                     f"{data.win_rate_pct}% win rate "
                     f"({data.won}/{data.trades}, p={data.p_value})",
                priority=data.trades * abs(data.win_rate_pct - 50),
            ))

    # From trajectories
    for pattern, data in trajectory_accuracy.items():
        if data.trades >= min_trades:
            candidates.append(RuleCandidate(
                source="trajectory",
                text=f"{pattern} pattern: {data.win_rate_pct}% win "
                     f"({data.won}/{data.trades})",
                priority=data.trades * abs(data.win_rate_pct - 50),
            ))

    # From idle cycles
    for condition, data in idle_accuracy.items():
        if data.idle_cycles >= min_idle_cycles and data.waiting_was_correct:
            candidates.append(RuleCandidate(
                source="idle_condition",
                text=f"During {condition}: waiting was correct "
                     f"{data.correct_pct}% of the time "
                     f"(n={data.idle_cycles})",
                priority=data.idle_cycles * data.correct_pct,
            ))

    # From suppressed signals
    for signal, data in signal_accuracy.items():
        if data.verdict == "suppress":
            candidates.append(RuleCandidate(
                source="suppress_signal",
                text=f"{signal} solo accuracy {data.accuracy_pct}% "
                     f"-- coin flip, ignore without confirmation",
                priority=data.total_fired * (50 - data.accuracy_pct),
            ))

    # Sort by priority, take top max_rules
    candidates.sort(key=lambda c: c.priority, reverse=True)
    rules = candidates[:max_rules]

    # Format rulebook
    rulebook_text = ""
    for i, rule in enumerate(rules):
        prefix = "[x]" if rule.source in ("combination", "trajectory") else "[!]"
        rulebook_text += f"{prefix} Rule {i+1}: {rule.text}\n"

    return rulebook_text
```

### Example Generated Rulebook

```
[x] Rule 1: rsi_14+macd_12_26_9 both_bullish: 83% win rate (10/12, p=0.008)
    -> STRONG ENTER: Add +1.0 to score when both align long.

[x] Rule 2: ema_stack(21>55>200) bullish: 78% win rate (14/18)
    -> Confirm trend: Require this for any long entry. Deny if stack is bearish.

[x] Rule 3: gradual_alignment trajectory: 78% win rate (18/23)
    -> Prefer entries where indicators aligned over 6+ bars, not sudden snaps.

[!] Rule 4: During VIX>35: waiting was correct 80% of the time (n=15)
    -> Avoid entries when macro conditions are extreme.

[!] Rule 5: adx(14) > 40 (extreme trend): 44% win rate (7/16)
    -> WARNING: Avoid entries in extreme ADX. Wait for ADX 25-35 range.

[!] Rule 6: macd_12_26_9 solo (no RSI confirmation): 50% win rate (19/38)
    -> COIN FLIP: Ignore MACD alone. Only count MACD when RSI confirms.

[x] Rule 7: rsi_14+ema_stack both_bullish: 81% win rate (9/11, p=0.03)
    -> REQUIRES: Both aligned for long entries. If one is neutral, reduce score by 0.5.

[!] Rule 8: sudden_snap trajectory: 33% win rate (4/12)
    -> SUPPRESS: Reduce position size by 50% or skip when indicators snap together.

[!] Rule 9: During kill_zone: waiting was correct 90% of the time (n=20)
    -> Avoid entries during low-liquidity time windows.

[x] Rule 10: rsi_14 oversold (<30) + bullish divergence: 76% win rate (8/11)
    -> VALID: RSI divergence at support is a strong entry signal.
```

---

## 7. Weight Adjustment

### How Weights Change Over Time

Starting weights are defined in the strategy YAML. The learning engine adjusts them based on signal accuracy:

```python
def adjust_weights(current_weights, signal_accuracy, highlight_threshold=75, suppress_threshold=55):
    new_weights = {}
    for indicator, weight in current_weights.items():
        accuracy_entry = signal_accuracy.get(indicator)
        if accuracy_entry is None or accuracy_entry.verdict == "insufficient_data":
            # No data yet -- keep original weight
            new_weights[indicator] = weight
        elif accuracy_entry.verdict == "suppress":
            # Signal is worse than coin flip -- set weight to 0
            new_weights[indicator] = 0.0
        elif accuracy_entry.verdict == "valid" and accuracy_entry.accuracy_pct >= highlight_threshold:
            # Strong signal -- boost weight by 20%
            new_weights[indicator] = weight * 1.2
        elif accuracy_entry.verdict == "review":
            # Borderline -- reduce weight by 10%
            new_weights[indicator] = weight * 0.9
        else:
            # Normal -- keep weight unchanged
            new_weights[indicator] = weight

    # Re-normalize so weights sum to 1.0 (for scoring)
    total = sum(new_weights.values())
    if total > 0:
        for indicator in new_weights:
            new_weights[indicator] = new_weights[indicator] / total

    return new_weights
```

### Weight History

Every weight adjustment is recorded in the database with timestamp, old value, new value, and the accuracy data that justified the change. This allows auditing: "Why did RSI weight change from 0.25 to 0.30?" -> "RSI accuracy reached 78% on 23 trades, triggering highlight boost."

---

## 8. Activation Thresholds

The learning engine does NOT activate until sufficient data exists:

| Threshold | Value | Purpose |
|-----------|-------|---------|
| `min_trades_before_adjusting` | 30 | System needs 30 closed trades before adjusting weights |
| `min_trades_per_signal` | 15 | Need 15 observations per signal for statistical significance |
| `min_idle_cycles_for_rules` | 10 | Need 10 idle cycles in same condition before forming idle rules |
| `significance_level` | 0.05 | Chi-squared p-value threshold for combination rules |

Before these thresholds are met, the system uses the **original strategy config weights** and no rulebook rules. It trades, collects data, but does not adjust itself. This is the "learning phase" -- the system is humble, it watches first.

---

## 9. Database Schema for Learning

```sql
-- Trade learning records
CREATE TABLE trade_learning (
    id INTEGER PRIMARY KEY,
    trade_id INTEGER REFERENCES positions(id),
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    entry_time TEXT NOT NULL,
    exit_time TEXT,
    outcome TEXT,                   -- 'won', 'lost', 'breakeven'
    pnl_pct REAL,
    pnl_usdt REAL,
    duration_minutes INTEGER,
    confluence_score_at_entry REAL,
    signals_at_entry_json TEXT,     -- JSON: {indicator: {value, signal, strength}}
    pre_trade_trajectory_pattern TEXT,
    pre_trade_coincidence_risk TEXT,
    max_favorable_excursion_pct REAL,
    max_adverse_excursion_pct REAL,
    sl_hit INTEGER DEFAULT 0,
    trailing_stop_hit INTEGER DEFAULT 0,
    exit_reason TEXT,
    rulebook_version TEXT,
    analyzed_at TEXT DEFAULT (datetime('now'))
);

-- Per-signal accuracy
CREATE TABLE signal_accuracy (
    indicator_name TEXT PRIMARY KEY,
    total_fired INTEGER DEFAULT 0,
    correct INTEGER DEFAULT 0,
    accuracy_pct REAL DEFAULT 0,
    confidence_95_low REAL,
    confidence_95_high REAL,
    verdict TEXT DEFAULT 'insufficient_data',
    sample_size INTEGER DEFAULT 0,
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Combination accuracy
CREATE TABLE combination_accuracy (
    combination_name TEXT NOT NULL,
    direction_state TEXT NOT NULL,  -- 'both_bullish', 'both_bearish', 'conflicting'
    trades INTEGER DEFAULT 0,
    won INTEGER DEFAULT 0,
    win_rate_pct REAL DEFAULT 0,
    avg_pnl_pct REAL DEFAULT 0,
    p_value REAL,
    significance TEXT DEFAULT 'insufficient_data',
    updated_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (combination_name, direction_state)
);

-- Trajectory accuracy
CREATE TABLE trajectory_accuracy (
    trajectory_pattern TEXT PRIMARY KEY,
    trades INTEGER DEFAULT 0,
    won INTEGER DEFAULT 0,
    win_rate_pct REAL DEFAULT 0,
    avg_pnl_pct REAL DEFAULT 0,
    verdict TEXT DEFAULT 'insufficient_data',
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Idle cycle records
CREATE TABLE idle_cycles (
    id INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    idle_reasons_json TEXT,         -- JSON array of reasons
    market_conditions_json TEXT,    -- {vix, btc_d, avg_confluence_score}
    top_candidate_symbol TEXT,
    top_candidate_score REAL,
    hypothetical_pnl_if_entered REAL,
    retrospect_validated INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Idle condition accuracy (aggregated)
CREATE TABLE idle_condition_accuracy (
    condition_description TEXT PRIMARY KEY,
    idle_cycles INTEGER DEFAULT 0,
    hypothetical_avg_loss_pct REAL DEFAULT 0,
    waiting_was_correct_pct REAL DEFAULT 0,
    verdict TEXT DEFAULT 'insufficient_data',
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Weight adjustment history
CREATE TABLE weight_history (
    id INTEGER PRIMARY KEY,
    indicator_name TEXT NOT NULL,
    old_weight REAL NOT NULL,
    new_weight REAL NOT NULL,
    justification TEXT,             -- "accuracy 78%, highlight boost"
    accuracy_at_time REAL,
    sample_size_at_time INTEGER,
    changed_at TEXT DEFAULT (datetime('now'))
);

-- Rulebook versions
CREATE TABLE rulebook_versions (
    id INTEGER PRIMARY KEY,
    version TEXT NOT NULL,
    rulebook_text TEXT NOT NULL,
    generated_at TEXT DEFAULT (datetime('now')),
    trades_recorded_at_generation INTEGER,
    source_counts_json TEXT         -- {combination: 3, trajectory: 2, idle: 2, suppress: 3}
);