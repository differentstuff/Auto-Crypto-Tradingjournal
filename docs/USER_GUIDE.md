# Crypto Trading Journal — User Guide

**App URL:** http://`<your-host>`:8082  
**Exchange:** Bitget USDT-M Futures

---

## Overview

The Trading Journal is a personal analytics and AI-assisted decision tool for your Bitget futures trading. It has 11 sections accessible from the left sidebar:

| Icon | Section | What it does |
|------|---------|--------------|
| 🏠 | Dashboard | Overview KPIs, P&L curve, wallet balance, recent trades |
| 📒 | Journal | Full trade history with search, filters, notes |
| 🔬 | Deep Dive | Detailed pattern analysis (by symbol, day, hour, direction) |
| 🔭 | Edge Lab | Setup analysis, execution grading, pattern detector, R:R tracking, Trader Rulebook |
| 🤖 | AI Advisor | Full portfolio analysis by Claude |
| ⬆️ | Import Data | Upload new Bitget CSV exports |
| 📡 | Call Analyzer | Analyze analyst trade calls before entering |
| ⚡ | Live Trades | Real-time open positions with AI analysis |
| 🔴 | Live Sync | Bitget connection status and manual sync |
| ⏳ | Pending Orders | Track limit orders set but not yet triggered |
| 📈 | Chart Explorer | Interactive candlestick charts for any symbol with S/R, trendlines, and indicators |

At the top of every page there is a **status bar** showing the last sync time and your current account equity. The **Sync Now** button triggers an immediate update from Bitget.

---

## Dashboard

The main overview of your trading performance. Data updates automatically whenever new trades sync from Bitget.

### KPI Cards

The 11 cards at the top show your key numbers across all time (or filtered by the date range you set):

- **Total P&L** — sum of all realized_pnl (net of fees)
- **Total Fees** — total trading fees paid
- **Win Rate** — % of trades that closed positive
- **Profit Factor** — total wins ÷ abs(total losses). Above 1.0 = profitable overall; above 1.5 = good
- **Best Trade / Worst Trade** — single best and worst PnL
- **Avg Win / Avg Loss** — average size of winning vs losing trades
- **Max Drawdown** — largest peak-to-trough drop on the cumulative PnL curve
- **Total Trades** — count of closed positions
- **Open Position Risk** — maximum loss if all your stop-losses trigger simultaneously, shown as USDT and % of equity. Uses SL-based calculation when stops are set; falls back to full margin otherwise.

**Tip:** Hover any KPI card to see a plain-language explanation of the metric, what good/bad values look like, and how it is calculated.

### Monthly Target Tracker

Set a monthly P&L target (in USDT) to track your progress. The progress bar fills green when on track, red if in loss. Data auto-updates from the current month's closed trades.

1. Enter your target in the USDT field
2. Click **Set**
3. Clear it with **✕** if you want to reset

Target is saved in your browser (localStorage) so it persists across page reloads.

### Current Streak

Shows how many consecutive wins or losses you're on right now (based on all-time trade history in date order).

### Charts

- **Cumulative P&L** — line chart of running total profit over time
- **Wallet Balance History** — actual account equity over time (from Bitget transaction data)
- **Top 5 Symbols** — bar chart of your best performing pairs
- **Win vs Loss** — doughnut of win/loss trade counts

### Recent Trades

The 10 most recently closed trades. Click **Edit** on any row to add notes and tags.

---

## Journal

Full trade history with powerful filtering.

### Filters

- **Symbol** — filter to a specific pair (e.g. BTCUSDT)
- **Direction** — Long or Short only
- **Result** — Win, Loss, or All
- **Date From / To** — close date range
- **Search** — free text across symbol and notes fields

Apply filters and click **Load**.

### Trade Table

Columns: Symbol, Direction, Opened, Closed, Duration, Entry, Exit, Size USDT, P&L, Fees, Analyst, Notes.

The **Analyst** column shows `📡 Name` in blue if an analyst is assigned, or `—` if none. This lets you see at a glance which trades came from analyst calls.

Click any row → opens the **Edit Trade** modal. You can set or update:
- **Analyst** — who made this call (e.g. `CryptoGuru`). Works on any trade, including old ones you want to catch up on.
- **Notes** — freetext trade notes
- **Tags** — comma-separated tags

### Manual Trade Entry

Click **+ Add Trade** to manually enter a trade that didn't come from a CSV or sync. Useful for trades on other exchanges or for paper trades you want to track.

Required fields: Symbol, Direction, Open Time, Close Time, Entry Price, Exit Price, Size USDT, Realized PnL.

### Pagination

50 trades per page. Use the page buttons at the bottom to navigate.

---

## Deep Dive

Advanced pattern analysis — answers "when do I trade best and worst?"

### Charts

- **P&L by Symbol** — all traded pairs, sorted by total P&L. Your best and worst symbols at a glance.
- **Monthly P&L** — bar chart by calendar month. Spot seasonal patterns.
- **P&L by Day of Week** — do you lose money on Fridays? This shows it.
- **P&L by Hour (UTC)** — what time of day do your best trades open? Based on open_time in UTC.
- **Long vs Short** — doughnut showing relative P&L for each direction.
- **Trade Duration** — how long do your trades run? Bucketed: `< 1h`, `1-4h`, `4-24h`, `1-7 days`, `> 7 days`.

### Stats Pills

Below the charts: current win/loss streak, total fees paid, average fee per trade, fees as % of gross P&L.

### Symbol Table

Complete breakdown for every symbol you've ever traded:
- Trade count, win rate, total PnL, fees, avg PnL per trade, best single trade, worst single trade

### Worst Symbols Table

Bottom 5 symbols by total realized PnL — your biggest loss leaders.

---

## Edge Lab

Advanced self-coaching tools that go beyond raw pattern stats — this is where you identify your actual trading edge (or lack of one).

### Setup Type Analysis

Once you start tagging trades with a **Setup Type** (Breakout / Pullback / Trend Continuation / Range Fade / Reversal / News-Event / Other), this section shows:
- Total P&L per setup type (bar chart)
- Win rate per setup type (bar chart)
- Summary table: trade count, win rate, total P&L, avg P&L per setup

To tag a trade: click any row in the Journal → Edit Trade → select Setup Type → Save.

### Execution Grade Analysis

After using the **⚡ Grade** button on journal rows to get Claude's execution grade (A/B/C/D), this table shows:
- Win rate per grade
- Average P&L per grade

If A-grade execution beats C-grade significantly, you know discipline pays off. If the grades are similar, entry quality may not be the bottleneck.

### AI Pattern Detector

Click **🔍 Detect Patterns** to run a full statistical analysis of your trade history through Claude. The AI looks for patterns in: setup types, weekdays, trading sessions (Asia/London/NY), direction, duration, and execution grade.

Output: up to 6 findings, each with a title, specific finding, recommendation, and confidence level.

Requires at least 20 total trades to produce meaningful results.

### Planned vs Realized R:R

Shows trades linked to analyst calls (via Call ID in the Journal). For each, compares:
- **Planned R:R** — from the saved analyst call (TP1 vs SL distance)
- **Realized R:R** — where you actually closed vs the planned entry and SL

Green = achieved ≥ 1R. Red = achieved < 1R. Reveals whether you're taking profit too early or letting losers run.

### Trader Rulebook

Click **🔄 Regenerate Rulebook** to ask Claude to synthesise 5–10 personalised rules from your full trade history. The rules are stored and automatically injected into every future AI analysis.

Rule types:
- **Warning (red)** — a losing pattern you must stop
- **Strength (green)** — a winning pattern to exploit more
- **Habit (yellow)** — an execution discipline note
- **Calibration (blue)** — how accurate the AI scores have been for you

The rulebook regenerates automatically once per week in the background. You can force it any time.

---

## Chart Explorer

Interactive candlestick charts for any symbol without leaving the app.

### Loading a Chart

1. Type a coin name in the symbol box (e.g. `BTC` or `BTCUSDT`) — a dropdown of all ~200+ Bitget USDT-M symbols appears as you type
2. Select the timeframe: **15m / 1H / 4H / 1D**
3. Click **Draw Chart** (or press Enter)

The chart title shows the full symbol + active timeframe as an overlay in the top-left corner.

### What's Shown

- **Candlesticks** — OHLCV from Bitget market data
- **Support/Resistance zones** — grey shaded boxes at key price levels, opacity proportional to touch count
- **Multi-timeframe trendlines** — uptrend (green) and downtrend (red) lines from 4 timeframes simultaneously:
  - Weekly = thickest, most opaque (weight 4)
  - Daily (weight 3)
  - 4H (weight 2)
  - 1H = thinnest, most transparent (weight 1)
- **Liquidation levels** (if you have an open position on that symbol) — yellow dashed lines

### Legend

Below the chart: chips for each trendline (with timeframe label and touch count) and each S/R level (with distance % from current price).

### Indicator Panel

Below the legend: 9 indicator cards for the selected timeframe:

| Card | What it shows |
|------|--------------|
| RSI (14) | Value + overbought/oversold signal |
| MACD | Bullish/bearish + crossover detection |
| EMAs | Stack alignment (fully bullish / mixed / fully bearish) |
| Bollinger Bands | Price percentile within the bands |
| ADX (14) | Trend strength + direction (+DI/-DI) |
| Stoch RSI | K/D values + overbought/oversold signal |
| ATR (14) | Volatility in price units + % of price |
| Volume | Ratio vs 20-period average + signal |
| Key S/R | Nearest support and resistance levels |

**Hover any indicator card** to see a tooltip explaining the metric, thresholds, and how to act on the value.

### Pop-out Chart

Click **🔗 Pop Out** to open the same chart in a dedicated window (resizable, stays on top while you work).

---

## AI Advisor

Full portfolio analysis using Claude AI. This reads all your historical stats and gives a structured assessment.

Click **Analyze My Trading** and wait ~15-20 seconds.

### Output Sections

- **Overall Status** — paragraph summary of your trading
- **Score (1-10)** — overall trading quality rating
- **Strengths** — what you're doing well (with specific numbers)
- **Areas to Improve** — concrete weaknesses with data backing
- **Action Plan** — prioritized list of recommendations
  - Each has: Priority (High/Medium/Low), Title, Specific Action, Expected Impact
- **Symbol Insights** — specific observations per symbol
- **Risk Management** — assessment of your risk practices
- **Mindset Note** — one closing observation

---

## Call Analyzer

Analyzes trade calls from crypto analysts before you enter. Paste the call text, optionally upload a chart screenshot, and Claude gives you a complete entry briefing.

### Workflow

**Step 1: Paste the call**

In the text box, paste the analyst's call. Format can be anything — the AI extracts what it can. Example:
```
$BOME LONG — Entry at market $0.0485, DCA at $0.042 
SL: 4H candle close under $0.038
TP1: $0.062, TP2: $0.078
```

**Step 2: Upload chart (optional but recommended)**

Drag a TradingView screenshot onto the drop zone, or click to browse. Claude will analyze the chart for patterns, key levels, and setup quality.

**Step 3: Set context**

- **Analyst** — who made this call (used for analyst tracking stats)
- **Market Regime** — select current BTC/market condition: Bull, Neutral, Bear, BTC Dump, or Altcoin Season. This affects scoring — a Long in a Bear market gets -1-2 score points.

**Step 4: Analyze**

Click **Analyze Call**. Wait ~10-15 seconds. Claude processes the call, chart (if provided), your trading history, and your personal patterns.

### Analysis Output

- **Quality Score** (1-10 with label: Poor/Weak/Moderate/Good/Strong/Excellent)
- **Chart Analysis** — what Claude sees in the chart: pattern, key levels, support/resistance
- **Risk:Reward** — computed ratio with exact entry/SL/TP prices
- **Pattern Flags** — personal warnings from your trading history
  - Examples: "Friday trade — your worst day", "BOME net loser for you"
- **Drawdown Warning** — if your account is down significantly from its peak, sizing is automatically reduced (25% of normal at -20%, 50% at -10%)
- **Bitget Settings** — exact settings to enter in Bitget:
  - Symbol, Direction, Margin Mode, Leverage
  - Order 1 (and Order 2 if DCA): type, notional USDT, notes
  - Stop Loss: price + type + exact instruction for candle-close SL
  - Take Profit 1 and 2
- **Position Sizing** — pre-calculated grid:
  - Risk %: 1% (no DCA) or 2% (with DCA) of account equity
  - Risk Amount USDT: what you lose if SL hits
  - Total Notional USDT: full position size
  - Margin Needed USDT: how much collateral to put up
- **Entry Timing** — market order now vs wait for retest vs set limit
- **Optimizations** — specific improvements to the analyst's setup
- **Risks** — what could go wrong

### Saving a Call

Click **💾 Save This Call** to store the analysis in your database. Saved calls appear in the **Saved Calls** list below.

### Setting a Pending Limit

If you decide to enter at a limit (not market), click **⏳ Set Limit** on any saved call. This opens the Add Limit modal pre-filled with the call's prices. The limit then appears in **Pending Orders** as a shadow trade.

### Saved Calls List

Shows all saved call analyses. Each row shows:
- Symbol, direction, status badge, analyst tag, score, R:R
- SL/TP prices (TP1/TP2 with ✓ checkmark if they were hit)
- Trade size in USDT
- Outcome badge if recorded (✅ Won / ❌ Lost / ↩ Manual with PnL)
- **Record Outcome** button — opens a modal to log what happened (won/lost/manual close, actual PnL, which levels were hit)
- **Mark Closed** — for matched calls that are now finished
- **Set Limit** — create a pending limit order linked to this call
- **Delete** — remove from database

### Editing the Analyst Name

Each saved call shows the analyst name with a small ✏ button next to it.

Click ✏ → the analyst tag becomes an editable text field. Type the new name, then:
- Press **Enter** or click **Save** to update
- Press **Escape** or click **✕** to cancel

This is useful for correcting typos or updating attribution after saving a call.

**Call status flow:**
```
saved → matched (linked to live position) → closed
         ↓
      dismissed (not this call)
```

When you're in Live Trades and a saved call matches an open position (same symbol + direction), a yellow banner appears prompting you to confirm the match.

### Analyst Performance Stats

Visible below the saved calls list (when data exists). Shows a table: for each analyst, how many calls you analyzed, how many you entered, wins, losses, TP1 hit rate, avg PnL, avg setup score. Tells you which analysts' calls actually make money for you.

### Score Prediction Accuracy

Shows how well the setup scores predicted actual outcomes — e.g., "calls scored 8-10: 78% real win rate". Builds up as you record outcomes over time.

---

## Live Trades

Real-time open positions from Bitget. Auto-refreshes every 30 seconds.

### Summary KPIs

Five cards at the top of the page:
- **Open Positions** — count of open trades (hover for tooltip explaining the "critical" threshold)
- **Total Unrealized P&L** — combined mark-to-market PnL across all open positions
- **Margin In Use** — total collateral locked as margin
- **Account Equity** — total account value including unrealized PnL, with available balance shown below
- **Open Position Risk** — maximum loss if all stop-losses trigger at once (SL-based calculation)

**Tip:** Hover any KPI card for a tooltip explanation of what the value means and how it is calculated.

### Position Cards

One card per open trade. Color-coded:
- Green left border = currently in profit
- Red left border = in loss
- **CRITICAL** badge + pulse animation = unrealized loss > 30%
- **NO SL** badge = no stop loss set (high risk warning)
- **48H+** chip = position open more than 48 hours
- **LIQ NEAR** chip = current price within 15% of liquidation price
- **⏳ N limit(s)** chip = you have N pending limit orders for this same symbol (click to jump to Pending Orders)

Each card shows: Symbol, Direction + Leverage, Size, Entry, Mark Price, Unrealized PnL%, TP/SL prices, time open.

Click the card header to expand → shows Break Even price, Liquidation price, Margin, Fees, Achieved profits.

### AI Analysis Per Position

Click **🤖 AI Analysis** on any position card. Claude analyzes that specific trade using:
- Current live position data (entry, mark price, unrealized PnL, TP/SL, leverage, duration)
- Your last 30 closed trades on that symbol (win rate, avg win/loss, hold time)

Output:
- **Risk Rating** (1-10 with color: Low/Medium/High/Critical)
- **Action** — Close Now / Partial Close / Move SL / Hold
- **TP/SL Recommendations** with specific prices and rationale
- **Key Risks** — 3 specific risks to this trade right now
- **Historical Context** — your track record on this symbol
- **Time Urgency** — Immediate / Today / No Rush

Results are cached and survive the 30-second auto-refresh (panel stays open, Re-analyze button appears).

### Call Match Banners

If a saved call matches an open position (same symbol + direction), a yellow banner appears on the position card prompting you to confirm or dismiss the link. Once confirmed:
- The call's TP/SL levels appear in a targets panel below the card
- A break-even prompt appears if price has moved above TP1

### Correlation Warning

If you have multiple open positions, the app checks for concentration risk:
- More than 2 longs simultaneously → yellow warning banner
- More than 2 shorts simultaneously → yellow warning banner

---

## Pending Orders

Tracks limit orders you've set on Bitget that haven't triggered yet. These are "shadow trades" — positions that will exist once the price reaches your limit.

### Why Use This

When you follow an analyst call but want to enter at a specific price rather than market, you set a limit on Bitget and can forget about it. This module lets you:
- Track all your pending limits in one place
- Calculate total capital at risk if ALL limits fill simultaneously
- Run AI analysis on each limit (before it fills)
- Keep a historical record of limits (triggered, cancelled)

### Risk Summary Banner

At the top of the Waiting tab: shows total notional USDT committed across all pending limits, broken down by symbol. This is the total capital that would be deployed if every limit fills.

### Adding a Limit Order

Click **+ Add Limit Order**.

Required:
- **Symbol** — e.g. BTCUSDT
- **Direction** — Long or Short
- **Limit Price** — the price at which you placed the limit order on Bitget

Optional but important:
- **Size USDT** — the notional size you set (from the call analysis recommended sizing)
- **Leverage** — default 10x
- **Stop Loss** — your planned SL level
- **Take Profit 1 / 2** — your TP targets
- **Analyst** — who made the call
- **Notes** — any context

### Adding from a Saved Call

In the **Call Analyzer** → Saved Calls list, click **⏳ Set Limit** on any saved call. The modal opens pre-filled with the call's symbol, direction, SL/TP levels, and suggested sizing. You just need to confirm and enter the actual limit price you set on Bitget.

You can add **multiple limits** for the same call — for example, if you're scaling in with two different limit prices (your DCA levels). Each becomes a separate entry linked to the same call.

### Pending Limit Cards

Each card shows:
- Symbol + direction badge + status chip (⏳ Waiting / ✅ Triggered / ✕ Cancelled)
- Analyst name
- Key prices: Limit, SL, TP1, TP2
- Size in USDT + Leverage
- Computed metrics: stop distance %, risk if SL hit (USDT), R:R to TP1
- Notes + date added
- **📍 X.X% from limit** proximity badge (Waiting tab only) — appears automatically when the current mark price is within 5% of your limit price. Red = <1% away, yellow = <3%, blue = <5%. This tells you limits that may fill soon.

### Actions

**🤖 AI Analysis** — Claude reviews the limit setup and gives:
- **Verdict:** Keep / Adjust / Cancel
- **Score** (1-10)
- Whether the limit price is still a good entry level
- SL quality assessment
- TP quality + R:R
- Correlation risk (from your other open + pending positions)
- Total exposure warning if you're over-committed
- Specific adjustment suggestions

Analysis is stored with the limit. Clicking AI Analysis again updates it.

**✏ Edit** — Change prices, size, notes.

**✅ Mark Triggered** — Use this when Bitget notifies you that your limit filled. The order moves to the Triggered tab. It's now a real position — track it in Live Trades.

**✕ Cancel** — Use when you cancel the order on Bitget. Moves to Cancelled tab.

### Live Bitget Orders Panel

At the top of the Pending Orders page there is a **⚡ Live on Bitget** section that reads your unfilled limit orders directly from the exchange in real time.

- Click **⟳ Refresh** to fetch the latest order list
- Orders are split into: **Entry orders** (open trades) and **Exit orders** (TP/SL limits on existing positions)
- Orders already tracked in your shadow trade list show a ✓ tracked badge
- Click **🔗 Track & Match** on any untracked entry order to create a shadow trade record and optionally link it to an analyst call

### Bulk Operations (Waiting tab)

When you have multiple pending limits (e.g., several scale-in entries across different calls), you can update them all at once.

**Selecting limits:**
- Each limit card on the **Waiting** tab has a checkbox in the top-left corner
- Selected cards get a purple highlight border
- The count of selected items appears in the **bulk action bar** at the bottom of the page

**Bulk actions:**

| Button | What it does |
|--------|-------------|
| **Set SL** | Prompts for a price and sets it as the stop loss on all selected orders |
| **Set TP1** | Sets the same TP1 price on all selected orders |
| **Set TP2** | Sets the same TP2 price on all selected orders |
| **🔗 Link to Call** | Opens a call picker — select one analyst call to link all selected orders to |
| **✕ Cancel All** | Cancels all selected orders (moves them to the Cancelled tab) |
| **✕ Clear** | Deselects everything without making changes |

Switching between tabs (Waiting / Triggered / Cancelled) clears the selection automatically.

### Tabs

- **⏳ Waiting** — active pending limits (default view)
- **✅ Triggered** — filled limits (historical record)
- **✕ Cancelled** — cancelled limits (historical record)

Triggered and Cancelled records can be deleted from the list.

---

## Import Data

Upload Bitget CSV exports to add historical data.

### Getting the CSVs from Bitget

1. Log into Bitget → Futures → Order Center
2. Export each of: **Position History**, **Order History**, **Transactions**
3. Download the ZIP or individual CSV files

### Uploading

Drag the ZIP file (or individual CSVs) onto the upload zone. The app:
1. Detects each file type by filename keyword
2. Imports new records (skips duplicates automatically)
3. Shows a summary of what was imported

The import log below shows all previous imports with timestamps and row counts.

### After Import

Refresh the Dashboard to see updated data. The sync status bar at the top updates automatically.

---

## Live Sync

Shows the Bitget API connection status and lets you trigger a manual sync.

### What Syncs

- **Positions** — new closed trades since the last sync
- **Orders** — new individual order fills
- **Bills** — new transaction events (wallet snapshots for the equity curve)

### Sync Details

- Automatic sync runs every 15 minutes in the background
- **Sync Now** triggers an immediate sync
- First sync after a fresh install picks up from the latest trade in the DB — so old history comes from CSV import, not the API

### Account Details

Shows current Account Equity and Available Balance (fetched from Bitget API).

---

## Common Workflows

### Analyzing a new analyst call

1. Open **Call Analyzer**
2. Paste the call text
3. Upload chart if available
4. Enter analyst name
5. Select market regime
6. Click **Analyze Call**
7. Review the output — check score, pattern flags, sizing
8. If taking the trade: **Save This Call** → then either enter at market (confirm match when position appears in Live Trades) or **Set Limit** if waiting for a better entry

### Tracking a limit order

1. After placing a limit on Bitget, go to **Pending Orders**
2. Click **+ Add Limit Order** (or use **⏳ Set Limit** from a saved call)
3. Enter the limit price you set and the size
4. Click **🤖 AI Analysis** to get a pre-fill quality check
5. When Bitget notifies you it filled: click **✅ Mark Triggered**
6. The position now appears in **Live Trades** for ongoing monitoring

### Recording a trade outcome

1. When a trade closes: go to **Call Analyzer** → Saved Calls
2. Find the saved call and click **📊 Record Outcome**
3. Select Won / Lost / Manual Close
4. Enter the actual PnL
5. Check which targets were hit (TP1, TP2, SL)
6. Click Record

This feeds the **Analyst Stats** and **Score Accuracy** tables, making the AI analysis progressively more accurate over time.

### Reviewing your performance patterns

1. Open **Deep Dive**
2. Check **P&L by Day of Week** — which days are consistently bad for you?
3. Check **P&L by Hour** — are you trading at the wrong time?
4. Check the **Worst Symbols** table — are you overtrading losing pairs?
5. Use these insights to inform which analyst calls to skip (e.g., avoid Friday calls if Friday is your worst day)

---

## Tips

**Pattern flags in Call Analyzer** — These are personalized warnings based on YOUR actual history. A Friday trade flag means you specifically lose money on Fridays, not traders in general. Pay attention to them.

**Score caps** — The AI caps scores regardless of setup quality: R:R below 1:1.5 → max 6/10. Bear market + Long → -1-2 points. Drawdown → more conservative overall. This prevents overconfidence in technically good setups that have unfavorable conditions.

**Monthly target** — Set a realistic monthly target (Dashboard). Watching the progress bar helps with discipline: if you're already at 80% of target with 2 weeks left, you might not need to chase the last marginal setup.

**Pending limits risk summary** — Before adding a new position (live or limit), check how much capital is already committed across all your pending limits. It's easy to over-leverage if you have 5 pending limits all approaching their entry at once.

**Analyst stats** — After recording 10+ outcomes, the analyst stats table becomes valuable. If analyst X has 40% win rate but analyst Y has 70%, that shapes how seriously you take their calls even when the AI gives a high setup score.
