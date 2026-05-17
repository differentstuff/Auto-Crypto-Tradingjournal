# Telegram Journal Assistant — What You Can Do

The Trading Journal has **two separate Telegram bots**:

| Bot | Direction | Purpose |
|-----|-----------|---------|
| **Alert bot** (`TELEGRAM_BOT_TOKEN` in journal `.env`) | One-way push | Scanner alerts with annotated chart + SL/TP |
| **Hermes bot** (`~/.hermes/.env` on Pi) | Two-way interactive | Query journal data, run scans, send charts, analyze positions |

This guide covers the Hermes interactive bot only.

---

## Setup

Hermes runs as a user-level systemd service on the Pi:

```bash
hermes gateway status          # check it's running
hermes gateway start           # start if stopped
hermes gateway stop
journalctl --user -u hermes-gateway -f   # live logs
```

Service files live at `/home/fbauer/.hermes/`. Key files:
- `SOUL.md` — full journal API documentation Hermes uses
- `MEMORY.md` — trader profile + behavioral stats (updated by analyze_trader.py)
- `tools/analyze_trader.py` — queries `/api/positions`, computes behavioral stats, rewrites MEMORY.md

Send a message to the Hermes bot in Telegram to start. It reads SOUL.md on every conversation turn so it always has the latest API documentation.

---

## Query & Analysis

| What you want | Example message |
|---------------|-----------------|
| Live price | `What's the price of SOL?` |
| Full coin analysis | `Analyze ETH` or `Give me a coin summary for BNB` |
| Indicators only | `Show me RSI and WaveTrend for AVAX on 4H` |
| Open positions | `What are my open positions?` |
| Pending limits | `Show my pending limit orders` |
| Scanner results | `What did the scanner find?` or `Show last scan results` |
| Recent trades | `Show my last 10 trades` |
| P&L stats | `What's my P&L this week?` or `Give me my win rate` |
| Dominance dashboard | `Show dominance indexes` or `What's BTC dominance?` |
| Market regime | `What's the macro regime?` or `Show VIX and ES1!` |
| Behavioral analysis | `Analyze my trading behavior` |

Coin analysis (`/api/coin/summary/<symbol>`) returns:
- Live price (Binance → Bitget fallback)
- 4H + 1H indicators: RSI, EMA, WaveTrend, ADX, ATR
- Nansen smart money signal
- Coinalyze: OI, funding rate, 24h liquidation trend
- Liquidations last 14 days (dominant side, peak day)
- BTC market regime + Fear & Greed index

---

## Actions

| Action | Example message |
|--------|-----------------|
| Start full scan | `Run a full scanner scan` |
| Scan specific coins | `Scan BTC, ETH, SOL` |
| Cancel running scan | `Cancel the scan` |
| AI-analyze a position | `Analyze my open BTC position` |
| Rebuild behavioral memory | `Update my trader profile` |

---

## Charts

Hermes can send charts as Telegram photos via `/api/chart/annotated/<symbol>`.

| Chart type | Example message |
|------------|-----------------|
| Plain S/R chart for any coin | `Send me a chart for LINK` |
| Chart from scan result | `Send chart for the top scanner pick` |
| Chart for open position | `Chart my open ETH long with levels` |

The annotated chart includes:
- Direction badge (▲ LONG / ▼ SHORT)
- Entry zone (shaded blue band between entry and entry_high)
- SL = red dashed, TP1 = bright green, TP2 = cyan
- S/R zones (green = support, red = resistance, brightness scales with touch count)
- At-level highlight for zones within 0.5% of current price

Optional query params accepted by `/api/chart/annotated/<symbol>`:
`direction`, `entry`, `entry_high`, `sl`, `tp1`, `tp2`, `tf`

---

## Scheduled Jobs

Set up recurring tasks by sending these exact messages to the Hermes bot:

### 1. Morning Briefing (7 am, weekdays)

```
Schedule a morning briefing at 7am on weekdays: show open positions, pending limits, last scanner result, and dominance indexes
```

### 2. BTC RSI Conditional Scan Trigger (every 15 min)

```
Every 15 minutes check if BTC 4H RSI is below 35 or above 70, and if so run a full scanner scan and notify me
```

### 3. Weekly Behavioral Analysis (Sunday 8 am)

```
Every Sunday at 8am run the trader behavior analysis and update my memory profile, then send me a summary
```

---

## Additional Scheduled Job Ideas

| Job | Suggested frequency |
|-----|---------------------|
| Evening P&L digest — day's closed trades + net P&L | Daily at 8 pm |
| Funding rate alert — notify if any open position funding > 0.05% per 8h | Every 4 hours |
| Position staleness check — alert if any open position is > 24h old with no TP hit | Every 6 hours |
| USDT.D spike alert — notify if USDT.D rises > 0.5% in 1 hour (risk-off signal) | Every hour |
| Weekly scanner performance review — how many scanner alerts converted to closed wins | Sunday evening |

---

## Dominance Quick Reference

Use these when Hermes reports the dominance dashboard (`/api/market/dominances`):

| Index | Interpretation |
|-------|---------------|
| **BTC.D rising** | Alts weakening — BTC outperforming; reduce alt exposure |
| **BTC.D < 45%** | Alt season conditions — broadening participation |
| **USDT.D rising** | Fear / risk-off — capital moving to stablecoins |
| **USDT.D falling** | Capital deploying — bullish for alts |
| **TOTAL3 trending up** | Alt market (ex-BTC/ETH) healthy — good for scanner picks |
| **MEME.C spike** | Speculative top risk — retail chasing; caution on meme coins |
| **STABLE.C.D falling** | Stablecoins losing share to risk assets — bullish signal |
| **ES1! falling sharply** | Equity risk-off — crypto headwind; wait for stabilization |
| **OTHERS.D rising** | Small-cap rotation — broad alt participation underway |

---

## Notes

- The Hermes bot token is **separate** from the alert bot. Two different bot tokens.
- Both bots share the same `TELEGRAM_CHAT_ID` (your personal chat).
- The journal API runs on `http://localhost:8082` — Hermes only reaches it on the Pi.
- Hermes does not write to the journal database directly; it queries read endpoints only.
- Service linger is enabled: `hermes-gateway.service` survives SSH session close.
