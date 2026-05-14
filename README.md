# Trading Journal

> **Disclaimer:** Vibe-coded with [Claude Code](https://claude.ai/code). Not reviewed by professional security experts. Use at your own risk.

A self-hosted crypto futures trading journal with live Bitget/Blofin sync, a 7-agent AI pipeline, and deep performance analytics. Runs on a Raspberry Pi (or any Linux box), accessible from any local browser.

**Community:** [📢 t.me/autocryptotradingjournal](https://t.me/autocryptotradingjournal) · [💬 t.me/autotradingjournal](https://t.me/autotradingjournal)

<p align="center">
  <img src="docs/images/factsheet-preview.png" alt="Crypto Trading Journal fact sheet" width="720">
</p>

<p align="center">
  <strong>v1.3.0</strong>
  &nbsp;·&nbsp;
  <a href="docs/architecture_detailed.pdf">📐 Architecture PDF</a>
  &nbsp;·&nbsp;
  <a href="https://t.me/autocryptotradingjournal">📢 Telegram</a>
  &nbsp;·&nbsp;
  <a href="https://t.me/autotradingjournal">💬 Community</a>
</p>

---

## Features

### 📒 Trade Journal
- Full history synced from Bitget USDT-M Futures (5 min cadence, cursor-based, no gaps)
- Blofin integration with per-exchange filter across all analytics
- CSV import for historical data
- Edit analyst, notes, tags, setup type on any trade

### 📊 Dashboard & Analytics
- KPIs: P&L, win rate, profit factor, Sharpe ratio, Calmar ratio
- Rolling 30-day metrics vs all-time comparison strip
- Cumulative P&L + drawdown overlay on wallet chart
- Deep Dive: breakdown by symbol, month, weekday, hour, direction, duration, setup type
- Expected Value per setup · MFE/MAE tracking · R:R planned vs realized

### 🤖 AI Agent Pipeline (7 Specialized Agents)
Each stage has a typed input/output contract and can be tested independently:

| Agent | Role |
|-------|------|
| **DataCollector** | Parallel fetch: OHLCV, funding rate, OI, Fear & Greed, FRED macro, Nansen, Grok |
| **DataInterpreter** | Pure indicator transforms: RSI/MACD/EMA/ADX/WaveTrend/S&R/confluence |
| **MarketSentiment** | Macro verdict: contra_signal flag, crowd position, funding bias |
| **DataReviewer** | Signal quality gate (0–10) + backtest context + KPIs from DB |
| **RiskManagement** | Pure math: position sizing, Kelly criterion (0.05–0.25), SL validation |
| **TradePrep** | Main Claude Sonnet call; assembles all upstream outputs; Gemini in parallel |
| **TradeMonitor** | Background Haiku chain for open position risk assessment; fires Telegram alerts |

- **Annotated trade charts**: every proposed trade generates a PNG (mplfinance, dark theme) with entry/SL/TP levels and decision criteria annotated — attached to Telegram scanner alerts
- **CoT learning loop**: prior chain-of-thought reasoning for the same symbol injected into next analysis
- **Consensus scoring**: Claude vs Gemini (|Δ|≤1=✓ Confirmed, ≤2=~ Aligned, ≤3=⚠ Divergent, >3=⚡ REVIEW)
- **Grok social intelligence**: xAI Grok X/Twitter sentiment, weighted by market cap (micro-cap 80%, large-cap skipped)
- Setup-type rubrics (Breakout / Reversal / Continuation / Range), ATR-aware SL check, portfolio correlation check

### 🔭 Setup Scanner
- Scans 100 USDT-M symbols every 30 min: confluence filter → technical quality gate → Haiku quick-score → per-symbol agent pipeline
- Telegram alerts with annotated chart + entry/SL/TP for setups ≥ 6/10
- **Auto-saves alerted setups to journal** — positions auto-link to the scanner signal that triggered them
- Nansen smart money signals for scanner finalists
- BTC market regime context (bull/bear/range) injected into scoring

### 📡 Live Positions + Background Monitor
- Real-time open positions (Bitget + Blofin merged) with unrealised P&L, duration, liquidation distance
- **Proactive monitor thread** (every 10 min): checks positions where `unrealized_pct < -5%` or `duration > 4h` — fires Telegram alert + sets UI badge on risk_rating ≥ 7
- Call targets panel: auto-links live position to its saved call — shows SL/TP distance, TP1 hit alert with break-even prompt
- Correlation warning for same-sector/same-direction concentration
- Economic calendar warning for high-impact USD events

### 📈 Chart Explorer
- Interactive candlestick charts (LightweightCharts) with S/R zones, trendlines, Fibonacci retracements
- VMC Cipher A/B (WaveTrend oscillator) in a synced lower pane
- Weekly S/R overlay, liquidation level lines, full indicator card suite

### 🧠 AI Learning System
- **Personalised Rulebook**: Claude synthesises 5–10 rules from your trade history; staleness decay; regen guard
- Hindsight analysis: retroactive blind scoring with TP/FP/TN/FN verdicts stored in DB
- Token usage dashboard with per-call cost tracking
- Prompt caching (Anthropic ephemeral): stable rulebook block cached — saves 40–60% tokens on repeated calls

### ⚙️ Settings & Ops
- In-app credential management (no restart needed)
- `scripts/self_test.py --agents`: smoke-tests the full agent pipeline against a live host
- systemd service with auto-restart

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.13 / Flask 3.1 |
| Database | SQLite 3 (WAL mode) |
| Frontend | Vanilla JS SPA (17 modules, no build step) |
| Charts | LightweightCharts v4.1.3 + Chart.js |
| Technical analysis | pandas-ta |
| Chart generation | mplfinance (annotated trade PNGs) |
| AI — analysis | Claude Sonnet 4.6 (calls / scanner / advisor) |
| AI — fast scoring | Claude Haiku 4.5 (hindsight / monitor / quick-score) |
| AI — consensus | Google Gemini 2.0 Flash (pre-proof scoring) |
| AI — social intel | xAI Grok (X/Twitter sentiment, weighted by market cap) |
| On-chain signals | Nansen.ai smart money screener |
| Market data | Bitget · Bybit · Binance · OKX funding · FRED macro |
| Prompt caching | Anthropic ephemeral `cache_control` |
| Exchange APIs | Bitget REST v2 · Blofin REST v1 (HMAC-SHA256) |
| Alerts | Telegram Bot API (stdlib only, photo + text) |
| Process manager | systemd |

---

## Requirements

- Python 3.10+
- [Bitget](https://www.bitget.com) API key (read-only)
- [Anthropic API key](https://console.anthropic.com) for AI features
- Linux host (tested on Raspberry Pi 5)

---

## Installation

```bash
git clone https://github.com/anvilfilbert/Auto-Crypto-Tradingjournal.git
cd Auto-Crypto-Tradingjournal
pip3 install -r requirements.txt
cp .env.example .env
# Edit .env with your credentials
python3 app.py
```

### Key env vars

```env
BITGET_API_KEY=        BITGET_SECRET_KEY=      BITGET_PASSPHRASE=
ANTHROPIC_API_KEY=
GEMINI_API_KEY=        # optional — Gemini consensus scoring
GROK_API_KEY=          # optional — xAI social intelligence
NANSEN_API_KEY=        # optional — on-chain smart money
FRED_API_KEY=          # optional — macro context (free at fred.stlouisfed.org)
TELEGRAM_BOT_TOKEN=    TELEGRAM_CHAT_ID=
PORT=8082
```

### Bitget API setup
Log in → **Profile → API Management → Create API** → Read-only permissions only.

---

## Running

```bash
# Direct
python3 app.py

# As a systemd service (recommended for Pi)
sudo cp trading-journal.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now trading-journal
```

Access at `http://<host>:8082` from any browser on your network.

---

## Project structure

```
app.py                   Flask startup, 9 blueprints, 3 background threads
database.py              Schema, 31 migrations, db_conn() context manager
constants.py             All models, cache TTLs, thresholds — single source of truth

# ── Specialized agents ───────────────────────────────────────────────────────
agent_types.py           All TypedDict contracts (single source of truth)
agent_data_collector.py  Parallel data fetch: OHLCV, funding, OI, F&G, FRED, Nansen, Grok
agent_data_interpreter.py Pure indicator transforms (no network/AI/DB)
agent_market_sentiment.py Pure macro sentiment: contra_signal, crowd_position, funding_bias
agent_data_reviewer.py   Signal quality gate + KPI/backtest context from DB
agent_risk_mgmt.py       Pure math: position sizing + Kelly criterion
agent_chart_draw.py      mplfinance annotated PNG: entry/SL/TP + criteria labels
agent_trade_prep.py      Main Claude + Gemini call; assembles all upstream outputs
agent_trade_monitor.py   Haiku position monitor — fires Telegram + UI badge on risk ≥ 7
agent_orchestrator.py    Consensus scoring + pipeline runners (run_call_analysis etc.)
monitor_scheduler.py     Background thread: polls positions every 10 min

# ── Core AI ───────────────────────────────────────────────────────────────────
ai_call.py               Call analysis entry point (delegates to agent pipeline)
ai_scanner.py            3-stage pipeline: confluence → quality gate → agent pipeline
ai_advisor.py            Full-portfolio coaching (Sonnet)
ai_rulebook.py           Personalised rulebook: guard, staleness decay, calibration
ai_hindsight.py          Retroactive blind scoring (Haiku)
ai_live_trade.py         Per-trade AI on live positions (delegates to TradeMonitor)
ai_limit.py              Pending limit analysis (Haiku)
ai_trade_grader.py       Execution grading A-D (Haiku)
ai_pattern_detector.py   Pattern detection + cross-pattern compounding
ai_client.py             Singleton Anthropic wrapper with auto token logging
gemini_client.py         Google Gemini pre-proof scoring
grok_client.py           xAI Grok social intelligence; CoinGecko MC lookup
prompt_builder.py        Stable prefix (cached) + dynamic context assembler

# ── Chart & data ─────────────────────────────────────────────────────────────
chart_context.py         OHLCV fetch + caching; trendlines, Fibonacci
chart_indicators.py      RSI/MACD/EMA/ADX/Bollinger/ATR/WaveTrend/CVD/StochRSI
chart_sr.py              S/R detection with ATR-relative tolerance + recency weighting
market_context.py        Fear & Greed, funding rates, L/S ratio, BTC regime, FRED macro
nansen_client.py         Nansen smart money screener (30-min cache)
analytics.py             All stat computations (KPIs, deep dive, backtest context)
trade_history.py         get_recent_trades / get_trade_stats / get_symbol_summary

# ── Exchange & sync ───────────────────────────────────────────────────────────
bitget_client.py         Bitget REST v2 (HMAC-SHA256, read-only)
bitget_sync.py           Background sync: positions, regime tagging, call auto-close
blofin_client.py         Blofin REST v1 (5-header HMAC)
blofin_sync.py           Blofin position sync + regime tagging
scanner_scheduler.py     Daemon: scan every 30 min, Telegram alert, persist setups to DB
telegram_notify.py       Telegram alerts with photo support (stdlib only)

routes/                  9 Flask blueprints (analytics, calls, journal, limits,
                         live, market, scanner, settings, sync, hindsight)
scripts/self_test.py     Smoke runner — --agents flag tests full agent pipeline
docs/architecture.md     ASCII flow maps of the full system
docs/architecture_detailed.pdf  10-section PDF for beginners + experts
```

---

## Notes

- Designed for personal use on a local network — no authentication layer. Do not expose to the public internet.
- All AI features require `ANTHROPIC_API_KEY`. The journal runs without it; AI buttons return an error.
- SQLite is sufficient for personal use (<100k trades, single user).

---

## Versioning

| Version | What it means |
|---------|--------------|
| v1.0.1 | Core journal + Grok/Gemini consensus + backtest loop + prompt caching |
| v1.1.0 | 7-agent pipeline + TradeMonitor + annotated charts + Kelly criterion |
| v1.2.0 | Phase 4 UI/UX + retroactive outcome recorder + accuracy progress tracker |
| **v1.3.0** | **SMC/ICT + VMC Cipher signal improvements: MFI signal, kill zone annotation, raised R:R thresholds, premium/discount zone, BOS/CHoCH rubric, 1H entry timeframe** |
| v2.0 | Major new capability (e.g. exchange, auth layer, new data source tier) |

---

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE) for details.
