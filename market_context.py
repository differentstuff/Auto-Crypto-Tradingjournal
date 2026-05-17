import logging
logger = logging.getLogger(__name__)
"""
market_context.py — Real-time market context from three free sources.

Sources:
  1. Fear & Greed Index  — alternative.me (no auth)
  2. Bitget funding rate — per symbol, authenticated via bitget_client
  3. Bitget long/short   — per symbol, authenticated via bitget_client

All results are cached for 5 minutes to avoid rate-limiting.
"""

import json
import os
from constants import MARKET_CACHE_TTL
import time
import urllib.request
from typing import Optional

import bitget_client

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")

_cache: dict = {}


def _cached(key: str, fn, ttl: int = MARKET_CACHE_TTL):
    now = time.time()
    if key in _cache:
        ts, data = _cache[key]
        if now - ts < ttl:
            return data
    result = fn()
    _cache[key] = (now, result)
    return result


def get_market_str(symbols: list, fallback: str = "") -> str:
    """Fetch market context and format it as a prompt string. Returns fallback on error."""
    try:
        return format_for_prompt(get_market_context(symbols))
    except Exception as e:
        logger.warning("get_market_str failed: %s", e)
        return fallback


# ── Fear & Greed ───────────────────────────────────────────────────────────────

def get_fear_greed() -> dict:
    def _fetch():
        try:
            req = urllib.request.Request(
                "https://api.alternative.me/fng/?limit=1",
                headers={"User-Agent": "TradingJournal/1.0.1"},
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                d = json.loads(r.read())
            item = d["data"][0]
            val  = int(item["value"])
            return {
                "value":          val,
                "classification": item["value_classification"],
                "color": (
                    "var(--red)"     if val <= 25 else
                    "var(--yellow)"  if val <= 45 else
                    "var(--muted)"   if val <= 55 else
                    "var(--yellow)"  if val <= 75 else
                    "var(--red)"
                ),
                "ok": True,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return _cached("fear_greed", _fetch)


# ── Bitget market data ─────────────────────────────────────────────────────────

def get_funding_rate(symbol: str) -> dict:
    sym = symbol.upper()
    def _fetch():
        try:
            data = bitget_client._get(
                "/api/v2/mix/market/current-fund-rate",
                {"symbol": sym, "productType": "USDT-FUTURES"},
            )
            item = data[0] if isinstance(data, list) and data else data
            rate = float(item.get("fundingRate", 0))
            return {
                "symbol":    sym,
                "rate":      rate,
                "rate_pct":  round(rate * 100, 4),
                "direction": "longs paying" if rate > 0 else "shorts paying",
                "high":      abs(rate) >= 0.0005,   # flag if ≥ 0.05%
                "ok":        True,
            }
        except Exception as e:
            return {"symbol": sym, "ok": False, "error": str(e)}
    return _cached(f"funding_{sym}", _fetch)


def get_long_short_ratio(symbol: str) -> dict:
    sym = symbol.upper()
    def _fetch():
        try:
            data = bitget_client._get(
                "/api/v2/mix/market/account-long-short",
                {"symbol": sym, "productType": "USDT-FUTURES", "period": "1H"},
            )
            item = data[0] if isinstance(data, list) and data else data
            lp   = round(float(item.get("longAccountRatio",  0)) * 100, 1)
            sp   = round(float(item.get("shortAccountRatio", 0)) * 100, 1)
            return {
                "symbol":    sym,
                "long_pct":  lp,
                "short_pct": sp,
                "bias":      "crowded long" if lp > 65 else "crowded short" if sp > 65 else "balanced",
                "ok":        True,
            }
        except Exception as e:
            return {"symbol": sym, "ok": False, "error": str(e)}
    return _cached(f"ls_{sym}", _fetch)


# ── BTC Dominance ─────────────────────────────────────────────────────────────

def get_btc_dominance() -> dict:
    """Fetch BTC market dominance from CoinGecko (no auth)."""
    def _fetch():
        try:
            req = urllib.request.Request(
                "https://api.coingecko.com/api/v3/global",
                headers={"User-Agent": "TradingJournal/1.0.1"},
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                d = json.loads(r.read())
            data = d.get("data", {})
            dom  = round(float(data.get("market_cap_percentage", {}).get("btc", 0)), 2)
            chg  = round(float(data.get("market_cap_change_percentage_24h_usd", 0)), 2)
            return {"btc_dominance": dom, "change_24h": chg, "ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return _cached("btc_dominance", _fetch, ttl=900)   # 15-min cache


# ── Economic Calendar ──────────────────────────────────────────────────────────

def get_economic_calendar() -> list:
    """
    Fetch this week's high-impact USD events from ForexFactory community mirror.
    Returns events for today and tomorrow only.
    """
    def _fetch():
        try:
            req = urllib.request.Request(
                "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
                headers={"User-Agent": "TradingJournal/1.0.1"},
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                events = json.loads(r.read())

            from datetime import datetime, timezone, timedelta
            today    = datetime.now(timezone.utc).date()
            tomorrow = today + timedelta(days=1)

            result = []
            for e in events:
                if e.get("impact") != "High":
                    continue
                country = e.get("country", e.get("currency", ""))
                if country != "USD":
                    continue
                raw = e.get("date", "")
                try:
                    ev_date = datetime.strptime(raw, "%m-%d-%Y").date()
                except ValueError:
                    continue
                if ev_date not in (today, tomorrow):
                    continue
                result.append({
                    "title":    e.get("title", ""),
                    "time":     e.get("time", ""),
                    "forecast": e.get("forecast", ""),
                    "previous": e.get("previous", ""),
                    "when":     "today" if ev_date == today else "tomorrow",
                })
            return result
        except Exception as e:
            logger.warning("market context call failed: %s", e)
            return []
    return _cached("eco_calendar", _fetch, ttl=3600)   # 1-hour cache


# ── Combined ───────────────────────────────────────────────────────────────────

def get_market_context(symbols: Optional[list] = None) -> dict:
    """
    Fear & Greed + BTC dominance + per-symbol data:
      - Multi-exchange funding rates (Bitget + Bybit + Binance + OKX)
      - Long/short ratio
      - Open Interest + 24h change
      - Recent liquidations (last 60 min)
    Plus FRED macro data (Fed rate, CPI, M2, 10Y yield).
    """
    result = {
        "fear_greed":    get_fear_greed(),
        "btc_dominance": get_btc_dominance(),
        "fred_macro":    get_fred_macro(),
        "symbols":       {},
    }
    if symbols:
        for sym in list(dict.fromkeys(symbols))[:6]:
            result["symbols"][sym] = {
                "funding":    get_funding_rate(sym),          # Bitget (existing)
                "multi_fund": get_multi_exchange_funding(sym),# all 4 exchanges aggregated
                "long_short": get_long_short_ratio(sym),      # Bitget L/S
                "open_interest":    get_open_interest(sym),   # Binance OI + 24h change
                "sentiment_div":    get_sentiment_divergence(sym), # smart vs retail positioning
            }
    return result


# ── Multi-exchange funding rates ───────────────────────────────────────────────

def _fetch_url(url: str, timeout: int = 8) -> dict:
    """GET a JSON URL, return parsed dict or {} on error."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "TradingJournal/1.0.1"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        logger.warning("fetch failed: %s", e)
        return {}
def get_bybit_funding(symbol: str) -> dict:
    base = symbol.replace("USDT", "")
    sym  = f"{base}USDT"
    def _fetch():
        d = _fetch_url(f"https://api.bybit.com/v5/market/funding/history?category=linear&symbol={sym}&limit=1")
        item = ((d.get("result") or {}).get("list") or [{}])[0]
        rate = float(item.get("fundingRate", 0)) if item else 0
        return {"exchange": "bybit", "rate": rate, "rate_pct": round(rate * 100, 4), "ok": bool(item)}
    return _cached(f"bybit_fund_{sym}", _fetch, ttl=300)


def get_binance_funding(symbol: str) -> dict:
    base = symbol.replace("USDT", "")
    sym  = f"{base}USDT"
    def _fetch():
        d = _fetch_url(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={sym}")
        rate = float(d.get("lastFundingRate", 0))
        return {"exchange": "binance", "rate": rate, "rate_pct": round(rate * 100, 4), "ok": "lastFundingRate" in d}
    return _cached(f"bnb_fund_{sym}", _fetch, ttl=300)


def get_okx_funding(symbol: str) -> dict:
    base   = symbol.replace("USDT", "")
    inst   = f"{base}-USDT-SWAP"
    def _fetch():
        d    = _fetch_url(f"https://www.okx.com/api/v5/public/funding-rate?instId={inst}")
        item = (d.get("data") or [{}])[0]
        rate = float(item.get("fundingRate", 0)) if item else 0
        return {"exchange": "okx", "rate": rate, "rate_pct": round(rate * 100, 4), "ok": bool(item)}
    return _cached(f"okx_fund_{base}", _fetch, ttl=300)


def get_multi_exchange_funding(symbol: str) -> dict:
    """Aggregate funding rates from Bitget + Bybit + Binance + OKX."""
    sources = {}
    try:
        bg = get_funding_rate(symbol)
        if bg.get("ok"):
            sources["bitget"] = bg["rate_pct"]
    except Exception as e:
        logger.warning("market_context fetch failed: %s", e)
    for fn, key in [(get_bybit_funding, "bybit"), (get_binance_funding, "binance"), (get_okx_funding, "okx")]:
        try:
            r = fn(symbol)
            if r.get("ok"):
                sources[key] = r["rate_pct"]
        except Exception as e:
            logger.warning("market_context fetch failed: %s", e)
    if not sources:
        return {"ok": False}
    avg = round(sum(sources.values()) / len(sources), 4)
    spread = round(max(sources.values()) - min(sources.values()), 4) if len(sources) > 1 else 0
    crowded = avg > 0.05 or avg < -0.05
    return {
        "ok": True, "by_exchange": sources,
        "avg_pct": avg, "spread_pct": spread,
        "direction": "longs paying" if avg > 0 else "shorts paying",
        "crowded": crowded,
        "high": abs(avg) >= 0.1,
    }


# ── Open Interest ──────────────────────────────────────────────────────────────

def get_open_interest(symbol: str) -> dict:
    """Open Interest from Binance futures (public endpoint, no auth)."""
    base = symbol.replace("USDT", "")
    sym  = f"{base}USDT"
    def _fetch():
        # Current OI
        cur  = _fetch_url(f"https://fapi.binance.com/fapi/v1/openInterest?symbol={sym}")
        if "openInterest" not in cur:
            return {"ok": False}
        oi_now = float(cur["openInterest"])
        # Historical OI (last 25 hours, 1h periods)
        hist = _fetch_url(
            f"https://fapi.binance.com/futures/data/openInterestHist?symbol={sym}&period=1h&limit=25"
        )
        oi_24h = float(hist[0]["sumOpenInterest"]) if hist else None
        oi_val_now = float(hist[-1]["sumOpenInterestValue"]) if hist else None
        change_pct = round((oi_now - oi_24h) / oi_24h * 100, 2) if oi_24h else None
        trend = ("expanding" if change_pct and change_pct > 1 else
                 "contracting" if change_pct and change_pct < -1 else "stable")
        return {
            "ok": True,
            "oi_coins": round(oi_now, 0),
            "oi_usd_m": round(oi_val_now / 1e6, 1) if oi_val_now else None,
            "change_24h_pct": change_pct,
            "trend": trend,
        }
    return _cached(f"oi_{sym}", _fetch, ttl=300)


# ── Recent liquidations ────────────────────────────────────────────────────────

def get_sentiment_divergence(symbol: str) -> dict:
    """
    Compare retail vs top-trader L/S positioning from Binance (public endpoint).
    Divergence between smart money and retail is a strong directional signal.
    """
    base = symbol.replace("USDT", "")
    sym  = f"{base}USDT"
    def _fetch():
        retail = _fetch_url(
            f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={sym}&period=5m&limit=1"
        )
        smart  = _fetch_url(
            f"https://fapi.binance.com/futures/data/topLongShortAccountRatio?symbol={sym}&period=5m&limit=1"
        )
        r = retail[0] if isinstance(retail, list) and retail else None
        s = smart[0]  if isinstance(smart,  list) and smart  else None
        if not r or not s:
            return {"ok": False}
        r_long = round(float(r["longAccount"])  * 100, 1)
        r_sht  = round(float(r["shortAccount"]) * 100, 1)
        s_long = round(float(s["longAccount"])  * 100, 1)
        s_sht  = round(float(s["shortAccount"]) * 100, 1)
        # Divergence: smart money long while retail short = contrarian bullish
        div = round(s_long - r_long, 1)
        signal = (
            "smart money net LONG vs retail SHORT (contrarian bullish)" if div > 5 else
            "smart money net SHORT vs retail LONG (contrarian bearish)" if div < -5 else
            "aligned — no divergence signal"
        )
        return {
            "ok": True,
            "retail_long_pct":     r_long, "retail_short_pct":     r_sht,
            "top_trader_long_pct": s_long, "top_trader_short_pct": s_sht,
            "divergence_pct": div,
            "signal": signal,
        }
    return _cached(f"sent_div_{sym}", _fetch, ttl=300)


# ── FRED macro data ────────────────────────────────────────────────────────────

def _fred_series(series_id: str) -> float | None:
    """Fetch last observed value for a FRED series via the JSON API (requires FRED_API_KEY)."""
    def _fetch():
        key = FRED_API_KEY
        if not key:
            return None
        url = (
            f"https://api.stlouisfed.org/fred/series/observations"
            f"?series_id={series_id}&api_key={key}&limit=1&sort_order=desc&file_type=json"
        )
        try:
            d    = _fetch_url(url, timeout=10)
            obs  = d.get("observations", [])
            if obs:
                val = obs[0].get("value", ".")
                return float(val) if val not in (".", "") else None
        except Exception as e:
            logger.warning("market_context fetch failed: %s", e)
        return None
    return _cached(f"fred_{series_id}", _fetch, ttl=3600 * 12)


def get_fred_macro() -> dict:
    """Fetch key macro indicators from FRED API (free key at fred.stlouisfed.org)."""
    def _fetch():
        fed_rate = _fred_series("FEDFUNDS")   # Fed Funds Rate %
        cpi      = _fred_series("CPIAUCSL")   # CPI index value
        m2       = _fred_series("M2SL")       # M2 Money Supply (billions $)
        t10y     = _fred_series("DGS10")      # 10-Year Treasury yield %
        return {
            "ok":       any(v is not None for v in (fed_rate, cpi, m2, t10y)),
            "fed_rate": fed_rate,
            "cpi":      cpi,
            "m2_b":     m2,
            "t10y":     t10y,
        }
    return _cached("fred_macro", _fetch, ttl=3600 * 6)


def get_macro_regime() -> dict:
    """
    Fetch VIX, DXY, and ES1! (S&P 500 futures) via yfinance.

    Returns {"vix": float|None, "dxy": float|None, "es": float|None,
             "es_change_pct": float|None, "regime": str}
    regime: "risk-off" | "neutral" | "risk-on"

    ES1! (ES=F): equity risk appetite proxy.
      Falling ES + rising VIX = double risk-off signal for crypto longs.
      Rising ES + low VIX = equity tailwind, positive for crypto.
    """
    try:
        import yfinance as yf
        tickers = yf.download(
            ["^VIX", "DX-Y.NYB", "ES=F"],
            period="2d", interval="1h",
            group_by="ticker", auto_adjust=True, progress=False,
        )
        def _last(sym):
            try:
                col = tickers[sym]["Close"].dropna()
                return round(float(col.iloc[-1]), 2) if not col.empty else None
            except Exception:
                return None

        vix = _last("^VIX")
        dxy = _last("DX-Y.NYB")
        es  = _last("ES=F")

        # S&P 500 futures 24h change %
        es_chg = None
        try:
            col = tickers["ES=F"]["Close"].dropna()
            if len(col) >= 2:
                es_chg = round((col.iloc[-1] - col.iloc[-24]) / col.iloc[-24] * 100, 2)
        except Exception:
            pass

        if vix is None:
            regime = "unknown"
        elif vix > 30:
            regime = "risk-off"
        elif vix > 20:
            regime = "neutral"
        else:
            regime = "risk-on"

        return {
            "vix": vix, "dxy": dxy, "regime": regime,
            "es": es, "es_change_pct": es_chg,
        }
    except Exception:
        return {"vix": None, "dxy": None, "es": None,
                "es_change_pct": None, "regime": "unknown"}


# ── Multi-exchange long/short consensus ────────────────────────────────────────

def get_ls_consensus(symbol: str) -> dict:
    """Multi-exchange long/short ratio consensus. Degrades to {} on failure."""
    try:
        from ccxt_client import get_multi_exchange_ls_ratio
        return get_multi_exchange_ls_ratio(symbol)
    except Exception:
        return {}


# ── DefiLlama TVL ──────────────────────────────────────────────────────────────

# Maps common DeFi token symbols to their DefiLlama protocol slug.
_DEFILLAMA_SLUGS = {
    "AAVEUSDT":   "aave",
    "UNIUSDT":    "uniswap",
    "CRVUSDT":    "curve-dex",
    "COMPUSDT":   "compound-finance",
    "MKRUSDT":    "maker",
    "SUSHIUSDT":  "sushi",
    "SNXUSDT":    "synthetix",
    "GMXUSDT":    "gmx",
    "DYDXUSDT":   "dydx",
    "LQTYUSDT":   "liquity",
}


def get_defi_tvl(symbol: str) -> dict:
    """
    Fetch TVL and 7d change from DefiLlama for DeFi protocol tokens.
    Returns {} for non-DeFi tokens or on error.
    Returns {"tvl_usd": float, "tvl_7d_change_pct": float, "protocol": str}
    """
    slug = _DEFILLAMA_SLUGS.get(symbol.upper())
    if not slug:
        return {}
    try:
        import urllib.request, json, time
        url = f"https://api.llama.fi/protocol/{slug}"
        req = urllib.request.Request(url, headers={"User-Agent": "TradingJournal/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        tvl_history = data.get("tvl", [])
        if len(tvl_history) < 8:
            return {}
        current_tvl = float(tvl_history[-1].get("totalLiquidityUSD", 0))
        week_ago_tvl = float(tvl_history[-8].get("totalLiquidityUSD", 0))
        change_pct = round((current_tvl - week_ago_tvl) / max(week_ago_tvl, 1) * 100, 2)
        return {
            "protocol":          slug,
            "tvl_usd":           round(current_tvl, 0),
            "tvl_7d_change_pct": change_pct,
        }
    except Exception:
        return {}


# ── BTC mempool stats ──────────────────────────────────────────────────────────

def get_btc_mempool() -> dict:
    """
    BTC mempool size and transaction volume from blockchain.com public API.
    Returns {"mempool_bytes": int|None, "n_transactions": int|None,
             "avg_fee_usd": float|None, "congestion": str}
    congestion: "high"|"moderate"|"low"|"unknown"
    """
    try:
        import urllib.request, json
        url = "https://api.blockchain.info/stats"
        req = urllib.request.Request(url, headers={"User-Agent": "TradingJournal/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        mempool_bytes = data.get("mempool_size")
        n_tx = data.get("n_transactions_total")
        fees_per_tx = data.get("total_fees_btc", 0) / max(data.get("n_transactions", 1), 1)
        btc_price = data.get("market_price_usd", 0) or 0
        avg_fee_usd = round(fees_per_tx * btc_price, 2) if btc_price else None
        # Mempool over 100MB = high congestion
        if mempool_bytes is None:
            congestion = "unknown"
        elif mempool_bytes > 100_000_000:
            congestion = "high"
        elif mempool_bytes > 30_000_000:
            congestion = "moderate"
        else:
            congestion = "low"
        return {
            "mempool_bytes":  mempool_bytes,
            "n_transactions": n_tx,
            "avg_fee_usd":    avg_fee_usd,
            "congestion":     congestion,
        }
    except Exception:
        return {"mempool_bytes": None, "n_transactions": None,
                "avg_fee_usd": None, "congestion": "unknown"}


def get_btc_regime(as_of_ts: str = None) -> str:
    """
    Determine BTC market regime ('bull', 'bear', or 'range') at a given ISO timestamp.
    Uses 50-day EMA vs 200-day EMA cross: bull if EMA50 > EMA200, bear otherwise.
    Falls back to 'range' on any error.

    as_of_ts — ISO datetime string; if None, uses current market data.
    """
    try:
        import chart_context as _cc
        tfs  = ["1D"]
        ctx  = _cc.get_chart_context("BTCUSDT", tfs)
        inds = ctx.get("1D", {}).get("indicators", {})
        ema  = inds.get("ema", {}) or {}
        # ema contains: ema20, ema50, ema200, current_price
        ema50  = ema.get("ema50",  0) or 0
        ema200 = ema.get("ema200", 0) or 0
        price  = ema.get("current_price", 0) or 0
        if ema50 and ema200:
            spread = (ema50 - ema200) / ema200
            if spread > 0.03:
                return "bull"
            elif spread < -0.03:
                return "bear"
            else:
                return "range"
        return "range"
    except Exception as e:
        logger.warning("market context call failed: %s", e)
        return "range"


def format_for_prompt(ctx: dict) -> str:
    """Concise text block for Claude prompts — includes all market data sources."""
    lines = []

    # Fear & Greed
    fg = ctx.get("fear_greed", {})
    if fg.get("ok"):
        lines.append(f"Fear & Greed: {fg['value']}/100 — {fg['classification']}")

    # BTC Dominance
    bd = ctx.get("btc_dominance", {})
    if bd.get("ok"):
        arrow = "↑" if bd["change_24h"] >= 0 else "↓"
        lines.append(f"BTC Dominance: {bd['btc_dominance']}% ({arrow}{abs(bd['change_24h'])}% 24h)")

    # FRED Macro
    fm = ctx.get("fred_macro", {})
    if fm.get("ok"):
        macro_parts = []
        if fm.get("fed_rate") is not None:
            macro_parts.append(f"Fed {fm['fed_rate']:.2f}%")
        if fm.get("t10y") is not None:
            macro_parts.append(f"10Y {fm['t10y']:.2f}%")
        if fm.get("cpi") is not None:
            macro_parts.append(f"CPI idx {fm['cpi']:.1f}")
        if fm.get("m2_b") is not None:
            macro_parts.append(f"M2 ${fm['m2_b']:,.0f}B")
        if macro_parts:
            lines.append(f"Macro (FRED): {' | '.join(macro_parts)}")

    # Per-symbol data
    for sym, d in ctx.get("symbols", {}).items():
        parts = []

        # Multi-exchange funding
        mf = d.get("multi_fund", {})
        if mf.get("ok"):
            flag = " ⚠ VERY HIGH" if mf.get("high") else (" ⚠" if mf.get("crowded") else "")
            exch_str = " / ".join(f"{k[:3].upper()} {v:+.3f}%" for k, v in mf["by_exchange"].items())
            parts.append(f"funding avg {mf['avg_pct']:+.3f}% ({mf['direction']}){flag} [{exch_str}]")
        else:
            fr = d.get("funding", {})
            if fr.get("ok"):
                flag = " ⚠ HIGH" if fr.get("high") else ""
                parts.append(f"funding {fr['rate_pct']:+.4f}% ({fr['direction']}){flag}")

        # Long/Short ratio
        ls = d.get("long_short", {})
        if ls.get("ok"):
            parts.append(f"L/S {ls['long_pct']}%/{ls['short_pct']}% ({ls['bias']})")

        # Open Interest
        oi = d.get("open_interest", {})
        if oi.get("ok"):
            oi_str = f"OI {oi['oi_usd_m']}M" if oi.get("oi_usd_m") else f"OI {oi['oi_coins']:,.0f} coins"
            chg    = f" {oi['change_24h_pct']:+.1f}% 24h" if oi.get("change_24h_pct") is not None else ""
            parts.append(f"{oi_str}{chg} ({oi['trend']})")

        # Smart money vs retail sentiment divergence
        sd = d.get("sentiment_div", {})
        if sd.get("ok"):
            div = sd["divergence_pct"]
            flag = " ⚠" if abs(div) > 5 else ""
            parts.append(
                f"retail {sd['retail_long_pct']}%L/{sd['retail_short_pct']}%S "
                f"| top traders {sd['top_trader_long_pct']}%L/{sd['top_trader_short_pct']}%S"
                f" (div {div:+.1f}%){flag}"
            )

        if parts:
            lines.append(f"{sym}: " + " · ".join(parts))

    return "\n".join(lines)
