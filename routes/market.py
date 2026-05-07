"""
routes/market.py — Market data Blueprint (split from routes/analytics.py v2.3).

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
