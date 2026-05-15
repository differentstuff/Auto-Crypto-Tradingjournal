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
    except ccxt.AuthenticationError:
        return {"ok": False, "error": "Authentication failed — check API key and passphrase"}
    except Exception:
        return {"ok": False, "error": "Connection error"}


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
    """
    Return list of closed position dicts normalised for blofin_sync INSERT.
    Uses Blofin's private account/positions-history endpoint via CCXT.
    """
    try:
        exchange = get_blofin_exchange()
        params: dict = {"limit": min(limit, 100)}
        if symbol:
            params["instId"] = symbol.removesuffix("USDT") + "-USDT"
        if after:
            params["after"] = after

        raw = exchange.privateGetAccountPositionsHistory(params)
        rows = (raw.get("data") or {}).get("resultList") or []

        result = []
        for r in rows:
            sym_raw   = r.get("instId", "")                          # "BTC-USDT"
            sym_out   = sym_raw.replace("-USDT", "USDT")             # "BTCUSDT"
            base      = sym_raw.split("-")[0]                        # "BTC"
            direction = (r.get("posSide") or r.get("side") or "long").lower()
            if direction not in ("long", "short"):
                direction = "long" if r.get("side") == "buy" else "short"

            open_ms   = int(r.get("openTime")  or r.get("cTime")  or 0)
            close_ms  = int(r.get("closeTime") or r.get("uTime")  or 0)
            open_dt   = _ms_to_dt(open_ms)
            close_dt  = _ms_to_dt(close_ms)
            dur_min   = round((close_ms - open_ms) / 60000) if close_ms > open_ms else None

            entry_px  = float(r.get("openAvgPx")  or r.get("avgPx")      or 0)
            close_px  = float(r.get("closeAvgPx") or r.get("closePrice") or 0)
            contracts = float(r.get("closeTotalPos") or r.get("pos")      or 0)
            leverage  = int(float(r.get("lever") or 1))
            notional  = round(contracts * entry_px, 4)

            pnl       = float(r.get("realizedPnl") or r.get("pnl") or 0)
            fee       = float(r.get("fee") or 0)
            fee_open  = round(abs(fee) / 2, 6)
            fee_close = round(abs(fee) / 2, 6)

            result.append({
                "symbol":          sym_out,
                "base_asset":      base,
                "direction":       direction,
                "margin_mode":     (r.get("mgnMode") or "cross").lower(),
                "open_time":       open_dt,
                "close_time":      close_dt,
                "duration_minutes": dur_min,
                "entry_price":     entry_px,
                "close_price":     close_px,
                "size_contracts":  contracts,
                "size_usdt":       notional,
                "position_pnl":    pnl,
                "realized_pnl":    pnl,
                "opening_fee":     fee_open,
                "closing_fee":     fee_close,
                "total_fees":      round(abs(fee), 6),
                "external_id":     str(r.get("positionId") or r.get("tradeId") or ""),
                "exchange":        "blofin",
                "leverage":        leverage,
            })
        return result
    except Exception:
        return []


def _ms_to_dt(ms: int) -> str | None:
    """Convert millisecond timestamp to 'YYYY-MM-DD HH:MM:SS' string."""
    if not ms:
        return None
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
