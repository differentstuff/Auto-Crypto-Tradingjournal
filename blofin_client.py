"""blofin_client.py — Blofin exchange client via CCXT (read-only)."""
import os

import ccxt

from ccxt_client import get_blofin_exchange

API_KEY    = os.environ.get("BLOFIN_API_KEY",    "")
SECRET_KEY = os.environ.get("BLOFIN_SECRET_KEY", "")


def is_configured() -> bool:
    """Return True if API key + secret are both set."""
    return bool(API_KEY and SECRET_KEY)


def test_connection() -> dict:
    """Verify credentials are valid. Returns {"ok": bool, "error": str|None}."""
    try:
        exchange = get_blofin_exchange()
        exchange.fetch_balance()
        return {"ok": True, "error": None}
    except ccxt.AuthenticationError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_account_equity() -> dict:
    """Return {"equity": float, "available": float}. Returns zeros on any error."""
    try:
        exchange = get_blofin_exchange()
        balance = exchange.fetch_balance()
        usdt = balance.get("USDT") or {}
        return {
            "equity":    float(usdt.get("total") or 0.0),
            "available": float(usdt.get("free")  or 0.0),
        }
    except Exception:
        return {"equity": 0.0, "available": 0.0}


def get_open_positions() -> list:
    """Return list of open position dicts matching existing DB shape. Empty list on error."""
    try:
        exchange = get_blofin_exchange()
        positions = exchange.fetch_positions()
        result = []
        for p in positions:
            sym_raw = p.get("symbol") or ""
            symbol = sym_raw.replace("/USDT:USDT", "USDT").replace("/USD:BTC", "USD")
            result.append({
                "symbol":         symbol,
                "side":           p.get("side"),
                "size":           float(p.get("contracts") or 0),
                "entry_price":    float(p.get("entryPrice") or 0),
                "unrealized_pnl": float(p.get("unrealizedPnl") or 0),
                "leverage":       int(p.get("leverage") or 1),
                "notional":       float(p.get("notional") or 0),
            })
        return result
    except Exception:
        return []


def get_position_history(symbol: str = None, limit: int = 100, after: str = None) -> list:
    """Return list of closed order dicts. Empty list on error. 'after' accepted for backward compat."""
    try:
        exchange = get_blofin_exchange()
        sym_ccxt = (symbol.removesuffix("USDT") + "/USDT:USDT" if symbol else None)
        orders = exchange.fetch_closed_orders(sym_ccxt, limit=limit)
        result = []
        for o in orders:
            sym_raw = o.get("symbol") or ""
            symbol_out = sym_raw.replace("/USDT:USDT", "USDT")
            result.append({
                "symbol":    symbol_out,
                "side":      o.get("side"),
                "price":     float(o.get("average") or o.get("price") or 0),
                "amount":    float(o.get("filled") or 0),
                "pnl":       float((o.get("info") or {}).get("pnl") or 0),
                "timestamp": o.get("timestamp"),
                "order_id":  o.get("id"),
            })
        return result
    except Exception:
        return []
