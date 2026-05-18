# Trading Journal

> **Disclaimer:** Vibe-coded with [Claude Code](https://claude.ai/code). Not reviewed by professional security experts. Use at your own risk.

> I decided to remove versioning at the moment, as updates are pushed often. I'm now concentrating in scanner and setup improvements.

Self-hosted crypto futures trading journal with live exchange sync, a 7-agent AI pipeline, interactive Telegram assistant, and deep performance analytics. Runs on a Raspberry Pi 5 (or any Linux box).

<p align="center">
  <img src="docs/images/factsheet-preview.png" alt="Crypto Trading Journal fact sheet" width="720">
</p>

<p align="center">
  <a href="docs/architecture_detailed.pdf">Architecture PDF</a>
</p>

---

## Features

- **Trade Journal** — Bitget USDT-M + Blofin sync (5 min cadence), CSV import, per-trade notes/tags/setup type
- **Dashboard & Analytics** — P&L, win rate, Sharpe, Calmar, drawdown overlay, Deep Dive breakdown by symbol/month/hour/setup
- **7-Agent AI Pipeline** — DataCollector → Interpreter → Sentiment → Reviewer → RiskMgmt → TradePrep → TradeMonitor; typed contracts, parallel fetch
- **Setup Scanner** — 100+ USDT-M symbols, 3-stage pipeline (confluence → quality gate → Haiku/Sonnet), HTF→LTF (1D/4H/1H) breakdown, Telegram alerts with annotated chart, cancel button
- **Annotated Charts** — mplfinance PNG: entry zone band, S/R zones (A-F, color-coded), direction badge, TP1/TP2 colors, ATR-based width, confluence merging
- **Live Chart Popup** — LightweightCharts with direction badge, S/R overlay, WaveTrend pane, at-level highlights
- **Dominance Dashboard** — BTC.D, ETH.D, USDT.D, OTHERS.D, TOTAL2, TOTAL3, MEME.C, STABLE.C.D, ES1! via `/api/market/dominances`
- **Backtester + Optimizer** — vectorized 4H backtest, Optuna Bayesian optimizer, walk-forward validation
- **AI Learning** — personalised rulebook, hindsight scoring, token usage dashboard, prompt caching (40-60% savings)
- **Hermes Bot** — interactive Telegram assistant (separate from alert bot); queries journal API, sends charts, runs scans, tracks behavioral stats
- **12-Signal Confluence Engine** — liquidation cluster walls (11th signal), order flow delta/divergence (12th signal); HMM 3-state regime detection (trending/ranging/volatile) injected into every AI prompt
- **On-Chain Metrics** — MVRV, exchange net-flow via CoinMetrics Community API (keyless); injected as macro context
- **ML Win-Probability Scorer** — XGBoost trained on historical outcomes; predicts win probability per setup, injected into prompts after 20+ labeled trades
- **Backtesting Quality** — PBO (Probability of Backtest Overfitting), Deflated Sharpe, Bootstrap CI via `POST /api/backtest/quality`
- **Structured AI Rubrics** — 6-section technical analyst template (TREND/MOMENTUM/STRUCTURE/SIGNAL COUNT/BIAS/CONFIDENCE) + explicit risk decision table in agent_trade_prep.py
- **Browser Accessibility Baseline** — 16/16 tabs clean, 4/4 pages 100% accessibility score, 42 aria-label fixes across all form inputs

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.13 / Flask 3.1 / SQLite WAL |
| Frontend | Vanilla JS SPA (17 modules, no build step) |
| Charts | LightweightCharts v4.1.3 + mplfinance |
| AI — analysis | Claude Sonnet 4.6 |
| AI — fast scoring | Claude Haiku 4.5 |
| AI — consensus | Google Gemini 2.0 Flash |
| AI — social | xAI Grok (X/Twitter sentiment) |
| On-chain | Nansen.ai smart money |
| Market data | Binance · Bitget · Bybit · OKX · Coinalyze · CoinGecko · yfinance |
| ML / Regime | `hmmlearn` · `scikit-learn` · `xgboost` · `joblib` |
| Alerts | Telegram Bot API |
| Host | Raspberry Pi 5 / systemd |

---

## Key Modules

- `ai_scanner.py` + `scanner_stages.py` — 3-stage scanner with cancel event, macro cap, HTF→LTF
- `agent_chart_draw.py` — annotated PNG with entry zone, S/R bands, direction badge
- `liquidation_client.py` — Coinalyze historical liquidations, CSV cache in `data/liquidations/`
- `coingecko_client.py` — dominance indexes (TOTAL2/3, USDT.D, OTHERS.D, MEME.C, STABLE.C.D)
- `market_context.py` — macro regime: VIX, DXY, ES1!, F&G, BTC regime
- `liquidation_levels.py` — CCXT-based forced liquidation cluster detection (TTL-cached)
- `onchain_client.py` — CoinMetrics Community MVRV + exchange flow
- `market_regime.py` — GaussianHMM 3-state regime classifier on BTC 4H data
- `signal_scorer.py` — XGBoost win-probability from historical analyzed_calls
- `backtest_quality.py` — PBO + Deflated Sharpe + Bootstrap CI (Bailey et al. 2014)
- Hermes agent — `~/.hermes/` on Pi; `hermes-gateway.service` (user systemd)

---

## Recent Additions

- **12-signal confluence engine** — order flow delta (12th signal), liquidation wall (11th signal)
- **HMM market regime** — 3-state GaussianHMM (trending/ranging/volatile), injected into every prompt
- **On-chain: MVRV, exchange net-flow** — CoinMetrics Community API, keyless, macro context block
- **ML win-probability scorer** — XGBoost, activates after 20 labeled outcomes
- **Backtest quality** — PBO, Deflated Sharpe, Bootstrap CI (`POST /api/backtest/quality`)
- **Structured agent prompts** — 6-section analyst template + risk decision rubric in agent_trade_prep.py
- **Browser baseline** — 16/16 tabs clean, 42 aria-label fixes, 4/4 pages 100% accessibility score
- **442 tests** — up from 351 at v1.5.0

---

## Setup

See [CLAUDE.md](CLAUDE.md) for full deployment details (Pi SSH, rsync rules, DB backup, service commands).

```bash
git clone https://github.com/anvilfilbert/Auto-Crypto-Tradingjournal.git
cd Auto-Crypto-Tradingjournal
pip3 install -r requirements.txt
cp .env.example .env  # fill in credentials
python3 app.py        # or: sudo systemctl enable --now trading-journal
```

Access at `http://<host>:8082`.

---

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE).
