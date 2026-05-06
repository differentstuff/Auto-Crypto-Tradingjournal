# AI Rating & Grading Criteria

This document describes every scoring, grading, and rating system used by Claude in the trading journal. Each section covers what is scored, the scale used, the exact criteria applied, and which inputs Claude receives.

---

## 1. Setup Quality Score — Call Analyzer

**Where:** Call Analyzer → analyze a new call  
**File:** `ai_call_analyzer.py`  
**Scale:** 1–10 integer, with a label

| Score | Label |
|-------|-------|
| 1–2 | Poor |
| 3–4 | Weak |
| 5–6 | Moderate |
| 7–8 | Good |
| 8–9 | Strong |
| 9–10 | Excellent |

**What Claude evaluates:**
- Chart pattern quality (if image attached): structure clarity, breakout zone, projected path, key levels visible
- Entry rationale: is the entry logical, is there a confluence of levels?
- Stop loss definition: is SL at a structural level or is it arbitrary? Is it a candle-close SL (adds management overhead)?
- Risk:reward ratio: derived from entry / SL / TP levels
- Take profit placement: set at resistance/supply zones vs thin air
- Trade type fit: is the setup labelled consistently with what the chart shows?
- DCA usage: is DCA justified by the setup, or does it dilute risk management?
- Entry timing: market order vs limit — is the urgency appropriate?

**Inputs Claude receives:**
- Raw analyst call text
- Optional TradingView chart screenshot (vision analysis)
- Pre-calculated position sizing (entry, SL, DCA, notional USDT, risk %)
- Trader's closed-trade history on that symbol (last 30 trades: win rate, avg win/loss, avg duration)
- Technical indicators: RSI, MACD, EMA stack, Bollinger Bands, Stoch RSI, ADX, ATR, volume — on 4H and 1D (from Bitget candles)
- Market regime / context note (optional, user-provided)

---

## 2. Execution Grade — Trade Grader

**Where:** Journal → any closed trade → ⚡ Grade button  
**File:** `ai_trade_grader.py`  
**Scale:** A / B / C / D

| Grade | Label | Criteria |
|-------|-------|----------|
| A | Excellent | Entry at or near planned level; exit disciplined (TP hit or clear rule-based close); risk managed throughout; strong realized R:R |
| B | Good | Minor flaw only — slippage < 1%, slightly early exit while in profit, small justified plan deviation |
| C | Average | One significant flaw — chased entry > 1–2%, moved SL against rules, cut winner below 0.5R, took poor R:R setup that luckily won |
| D | Poor | Multiple or severe flaws — no SL set, reckless position size, full avoidable loss, FOMO entry well outside the plan |

**Important:** Claude grades execution quality, not P&L outcome. A losing trade can receive an A if it was well-managed. A winning trade can receive a D if it relied on luck or rule-breaking.

**When a trade is linked to an analyst call (via Call ID), Claude additionally evaluates:**
- Entry slippage %: actual entry vs planned entry price
- Planned R:R vs Realized R:R (expressed in R multiples)
- Whether the outcome (TP1/TP2 hit, SL hit, manual close) matches what the setup score predicted
- Setup score (1–10) vs what actually happened

**Inputs Claude receives:**
- Symbol, direction, entry price, close price, position size (USDT)
- Trade duration (minutes)
- Realized P&L (USDT)
- Setup type tag (if set)
- Notes (if set)
- Linked analyst call data (if call_id is set): planned entry, SL, TP1, TP2, R:R, setup score, outcome
- Fear & Greed Index at time of grading

---

## 3. Live Position Risk Rating — Live Trade Analyzer

**Where:** Live Positions → 🤖 Analyze button on any open position  
**File:** `ai_live_trade.py`  
**Scale:** Two outputs: risk rating (1–10) and action recommendation

### Risk Rating

| Value | Label |
|-------|-------|
| 1–3 | Low |
| 4–6 | Medium |
| 7–8 | High |
| 9–10 | Critical |

### Action Recommendations

| Action | When applied |
|--------|-------------|
| Hold | Position within plan, TP/SL set, favorable indicators |
| Adjust SL | Position drifted from plan but still viable; SL needs tightening or protection |
| Partial Close | Position is stretched or overexposed; reduce size to lock in gains or cut risk |
| Close Now | Severe drawdown, no SL, or multiple confluencing bearish signals |

**Hard rules Claude always applies:**
- If `unrealized_pct < -30%` → strongly bias toward "Close Now" or "Partial Close"
- If `stop_loss` is empty (no SL set) AND `unrealized_pct < -5%` → recommend setting a stop immediately
- If `stop_loss` is empty AND `unrealized_pct > 0` → still flag as missing risk management

**Market context factors:**
- Funding rate: high funding (≥ 0.05%) paying in the direction of the trade is a headwind
- Long/Short ratio: > 65% retail on same side = crowded, flag as risk
- Fear & Greed Index: Extreme Fear (≤25) or Extreme Greed (≥75) flagged as regime risk

**Technical indicator factors (4H + 1D):**
- RSI > 70 on 1D while long = overbought warning
- RSI < 30 on 1D while short = oversold warning
- MACD bearish crossover = momentum turning against longs
- MACD bullish crossover = momentum turning against shorts
- Price below EMA20/50/200 while long = bearish alignment
- Bollinger Band position > 80th percentile while long = stretched
- Stoch RSI K > 80 while long = overbought
- ADX < 20 = weak trend, position may stall
- ATR used to contextualise whether SL and TP levels are realistic given volatility

**Inputs Claude receives:**
- Full position data: symbol, direction, leverage, margin mode, size (USDT), entry price, mark price, break-even price, liquidation price, unrealized PnL (USDT and %), achieved profits, total fees, TP price, SL price, duration (minutes)
- Historical stats on that symbol (last 30 closed trades): trade count, win rate %, total P&L, avg win, avg loss, avg duration (hours), last 10 P&Ls
- Market context: Fear & Greed, BTC dominance, funding rate, long/short ratio
- Technical indicators: RSI(14), MACD(12,26,9), EMA 20/50/200, Bollinger Bands(20,2), Stoch RSI(14), ADX(14), ATR(14), volume vs 20-period average, last 3 candle descriptions — all on 4H and 1D timeframes

---

## 4. AI Advisor — Overall Portfolio Score

**Where:** Sync / AI Advisor button  
**File:** `ai_advisor.py`  
**Scale:** 1–10 integer, with a label

| Score | Label |
|-------|-------|
| 1–2 | Poor |
| 3–4 | Developing |
| 5–6 | Competent |
| 7–8 | Good |
| 9–10 | Excellent |

**What Claude evaluates:**
- Win rate vs benchmark (50% = break-even for 1:1 R:R)
- Profit factor (gross wins / gross losses — >1.5 is healthy)
- Average win vs average loss (should be positive ratio)
- Best and worst trade outliers (do single trades dominate the stats?)
- Consistency across symbols (are profits concentrated in 1-2 names?)
- Consistency across months (improving, declining, or erratic?)
- Day-of-week edge (specific days with meaningful win rate differences)
- Session edge (Asia / London / NY / Off-hours patterns)
- Long vs Short performance (directional bias, structural weakness)
- Duration buckets (best/worst holding period — scalp vs swing)
- Fee impact: total fees as % of gross profit
- Current streak (winning or losing)
- Recent form: last 20 trades vs all-time

**Inputs Claude receives:**
- All dashboard KPIs (total trades, win rate, P&L, fees, profit factor, drawdown, streaks)
- Deep Dive stats: by symbol, by month, by weekday, by hour, by direction, by duration bucket
- Worst symbols table
- Fee analysis
- Current market context: Fear & Greed, BTC dominance

---

## 5. AI Pattern Detector — Pattern Findings

**Where:** Edge Lab → AI Pattern Detector button  
**File:** `ai_pattern_detector.py`  
**Minimum data required:** 20 total closed trades; at least 5 trades in a category before it is analysed

**Output types:**

| Type | Meaning |
|------|---------|
| Warning | A clear losing pattern — something to stop or change immediately |
| Insight | A neutral or mixed pattern worth being aware of |
| Strength | A clear winning pattern to exploit and repeat |

**Confidence levels:**

| Level | Criteria |
|-------|---------|
| High | Large sample with a clear, consistent signal |
| Medium | Moderate sample size, moderate signal |
| Low | Borderline sample size — directionally interesting but not conclusive |

**Categories analysed:**
- By setup type (Breakout, Pullback, Trend Continuation, Range Fade, Reversal, News/Event, Other)
- By day of week (Monday–Sunday)
- By trading session: Asia (00–08 UTC), London (08–13 UTC), NY/Overlap (13–21 UTC), Late/Off-hours (21–24 UTC)
- By direction (Long / Short)
- By trade duration: < 1h / 1–4h / 4–24h / 1–7 days / > 7 days
- By execution grade (A / B / C / D) — if enough graded trades exist
- Recent form: last 20 trades vs all-time baseline

---

## 6. Analyst Leaderboard — Edge Score

**Where:** Edge Lab → Analyst Leaderboard  
**File:** Frontend computation (`static/app.js`)  
**Scale:** 0–100 composite score

**Formula:**
```
Edge Score = (trade_win_rate × 0.50)
           + (call_outcome_win_rate × 0.30)
           + (tp1_hit_rate × 0.20)
```

| Component | Weight | Source |
|-----------|--------|--------|
| Trade win rate | 50% | Closed trades linked to this analyst |
| Call outcome win rate | 30% | Saved calls where outcome was recorded |
| TP1 hit rate | 20% | Calls where TP1 was recorded as hit |

Medal rankings are assigned to the top 3 analysts. Rows are color-coded by Edge Score tier.

---

## 7. Technical Indicator Thresholds

**Where:** Used as context in live position AI and call analyzer  
**File:** `chart_context.py`  
**Data source:** Bitget candles endpoint, 200 × 4H + 100 × 1D per symbol

| Indicator | Overbought / Bullish | Neutral | Oversold / Bearish |
|-----------|---------------------|---------|-------------------|
| RSI(14) | > 70 | 30–70 | < 30 |
| Stoch RSI K | > 80 | 20–80 | < 20 |
| Bollinger Band position | > 80th percentile | 20–80th percentile | < 20th percentile |
| ADX(14) | > 25 (strong trend) | 20–25 (trending) | < 20 (weak/ranging) |
| Volume vs 20-avg | > 1.5× (high) | 0.7–1.5× (normal) | < 0.7× (low) |
| Candle body/range | > 20% body = directional | — | < 20% body = doji |
| EMA stack | 20 > 50 > 200 (bullish) | Mixed | 20 < 50 < 200 (bearish) |

**Funding rate flag:** ≥ 0.05% (absolute value) is flagged HIGH — relevant when the trader is holding in the paying direction.

**Long/Short ratio flag:** > 65% retail on one side = "crowded" label, flagged as contrarian risk.

---

## Summary Table

| System | Triggered by | Scale | Key input |
|--------|-------------|-------|-----------|
| Setup Quality Score | Analyzing a call | 1–10 | Call text, chart image, indicators |
| Execution Grade | Grading a closed trade | A–D | Actual vs planned entry/exit, R:R |
| Live Risk Rating | Analyzing an open position | 1–10 + action | Live position data, indicators, market context |
| AI Advisor Score | AI Advisor button | 1–10 | Full portfolio stats |
| Pattern Detector | Edge Lab button | Warning/Insight/Strength | Historical trade breakdown by category |
| Analyst Edge Score | Leaderboard (auto) | 0–100 | Win rate + call outcomes + TP1 hit rate |
