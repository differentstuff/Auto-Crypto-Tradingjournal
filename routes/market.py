"""
routes/market.py — Market data Blueprint (split from routes/analytics.py v2.1).

Handles: /api/market/context, /api/market/calendar,
         /api/exchange/symbols, /api/market/prices
"""

import threading
import time
import traceback

from flask import Blueprint, request

from helpers import _ok, _err
import market_context
import bitget_client

_exchange_symbols_cache: list = []
_exchange_symbols_ts: float = 0
_prices_cache: dict = {}
_prices_ts: float = 0
_prices_lock = threading.Lock()

bp = Blueprint("market", __name__)


@bp.route("/api/market/context")
def api_market_context():
    try:
        symbols_raw = request.args.get("symbols", "")
        symbols = (
            [s.strip().upper() for s in symbols_raw.split(",") if s.strip()]
            if symbols_raw else []
        )
        return _ok(market_context.get_market_context(symbols or None))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/market/calendar")
def api_market_calendar():
    try:
        return _ok(market_context.get_economic_calendar())
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/exchange/symbols")
def api_exchange_symbols():
    """GET /api/exchange/symbols — all USDT-M futures symbols on Bitget (1-hour cache)."""
    global _exchange_symbols_cache, _exchange_symbols_ts
    try:
        if not _exchange_symbols_cache or (time.time() - _exchange_symbols_ts) > 3600:
            _exchange_symbols_cache = bitget_client.get_exchange_symbols()
            _exchange_symbols_ts = time.time()
        return _ok(_exchange_symbols_cache)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/liquidations/<symbol>")
def api_liquidations(symbol):
    """
    GET /api/liquidations/BTCUSDT?days=30
    Historical daily liquidation volume (Binance USDT-M, public data, cached locally).
    Returns shorts_usd, longs_usd, total_usd, net_usd per day + aggregate summary.
    Bitget-only symbols return available=False.
    """
    try:
        import liquidation_client
        days = min(90, max(1, int(request.args.get("days", 30))))
        return _ok(liquidation_client.get_summary(symbol.upper(), days))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/price/<symbol>")
def api_price_single(symbol):
    """GET /api/price/BTCUSDT — live price via Binance then Bitget fallback."""
    try:
        from ccxt_client import get_live_price, get_binance_price
        sym   = symbol.strip().upper()
        price = get_live_price(sym)
        if price is None:
            return _ok({"symbol": sym, "price": None, "source": None})
        source = "binance" if get_binance_price(sym) is not None else "bitget"
        return _ok({"symbol": sym, "price": price, "source": source})
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/coin/summary/<symbol>")
def api_coin_summary(symbol):
    """
    GET /api/coin/summary/BTCUSDT — rich single-coin snapshot for Hermes.
    Aggregates: live price, 4H/1H indicators, Nansen smart money, Coinalyze
    derivatives, market regime, funding rate. All sources degrade gracefully.
    """
    try:
        sym = symbol.strip().upper()
        result = {"symbol": sym}

        # Live price
        try:
            from ccxt_client import get_live_price
            result["price"] = get_live_price(sym)
        except Exception:
            result["price"] = None

        # Technical indicators (4H + 1H)
        try:
            from chart_context import get_chart_context
            ctx = get_chart_context(sym, ["4H", "1H"])
            for tf in ("4H", "1H"):
                inds = ctx.get(tf, {}).get("indicators", {})
                if inds.get("ok"):
                    result[f"indicators_{tf}"] = {
                        "rsi":      (inds.get("rsi") or {}).get("value"),
                        "trend":    (inds.get("ema") or {}).get("alignment"),
                        "macd":     (inds.get("macd") or {}).get("trend"),
                        "adx":      (inds.get("adx") or {}).get("value"),
                        "wt_signal":(inds.get("wavetrend") or {}).get("signal"),
                        "atr":      (inds.get("atr") or {}).get("value"),
                    }
        except Exception:
            pass

        # Nansen smart money
        try:
            import nansen_client
            if nansen_client.is_configured():
                ns = nansen_client.get_signals_for_symbols([sym])
                result["nansen"] = ns.get(sym, {})
        except Exception:
            pass

        # Coinalyze derivatives (OI, funding, liq trend)
        try:
            import coinalyze_client
            result["derivatives"] = coinalyze_client.get_all(sym)
        except Exception:
            pass

        # BTC market regime
        try:
            result["btc_regime"] = market_context.get_btc_regime()
        except Exception:
            pass

        # Fear & Greed
        try:
            result["fear_greed"] = market_context.get_fear_greed()
        except Exception:
            pass

        # Historical liquidations (last 14 days summary — no wait, fast cached)
        try:
            import liquidation_client
            liq = liquidation_client.get_summary(sym, days=14)
            if liq.get("available"):
                result["liquidations_14d"] = {
                    "total_shorts_usd": liq["total_shorts_usd"],
                    "total_longs_usd":  liq["total_longs_usd"],
                    "dominant":         liq["dominant"],
                    "dominant_ratio":   liq["dominant_ratio"],
                    "peak_day":         liq["peak_day"],
                    "peak_usd":         liq["peak_usd"],
                }
        except Exception:
            pass

        return _ok(result)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/market/prices")
def api_market_prices():
    """GET /api/market/prices?symbols=BTCUSDT,ETHUSDT — mark prices, 60-second cache."""
    global _prices_cache, _prices_ts
    try:
        symbols_raw = request.args.get("symbols", "").strip()
        if not symbols_raw:
            return _err("symbols param required")
        symbols = [s.strip().upper() for s in symbols_raw.split(",") if s.strip()]
        now = time.time()
        with _prices_lock:
            if (now - _prices_ts) > 60:
                _prices_cache.clear()
                _prices_ts = now
            missing = [s for s in symbols if s not in _prices_cache]
            if missing:
                _prices_cache.update(bitget_client.get_mark_prices(missing))
            result = {s: _prices_cache.get(s) for s in symbols}
        return _ok(result)
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)
