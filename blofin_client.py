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
    """Return open positions normalised to the same shape as bitget_client.get_open_positions()."""
    import time as _time
    try:
        exchange  = get_blofin_exchange()
        positions = exchange.fetch_positions()
        now_ms    = int(_time.time() * 1000)
        result    = []
        for p in positions:
            sym_raw     = p.get("symbol") or ""
            symbol      = sym_raw.replace("/USDT:USDT", "USDT").replace("/USD:BTC", "USD")
            side        = (p.get("side") or "long").lower()
            direction   = "Long" if side == "long" else "Short"

            entry_px    = float(p.get("entryPrice")       or 0)
            mark_px     = float(p.get("markPrice")        or 0)
            contracts   = float(p.get("contracts")        or 0)
            notional    = float(p.get("notional")         or contracts * entry_px)
            margin      = float(p.get("initialMargin")    or p.get("maintenanceMargin") or 0)
            unrl        = float(p.get("unrealizedPnl")    or 0)
            unrl_pct    = float(p.get("percentage")       or (unrl / margin * 100 if margin else 0))
            liq_px      = float(p.get("liquidationPrice") or 0) or None
            leverage    = int(float(p.get("leverage")     or 1))
            margin_mode = (p.get("marginMode") or "cross").lower()
            open_ms     = int(p.get("timestamp") or 0)
            dur_min     = int((now_ms - open_ms) / 60000) if open_ms else None

            # CCXT Blofin may expose SL/TP in the info dict
            info        = p.get("info") or {}
            sl          = str(info.get("stopLossPrice") or p.get("stopLossPrice") or "")
            tp          = str(info.get("takeProfitPrice") or p.get("takeProfitPrice") or "")

            result.append({
                "symbol":            symbol,
                "direction":         direction,
                "leverage":          leverage,
                "margin_mode":       "Cross" if "cross" in margin_mode else "Isolated",
                "total":             contracts,
                "size_usdt":         round(notional, 2),
                "margin_usdt":       round(margin, 2),
                "entry_price":       str(entry_px) if entry_px else None,
                "mark_price":        str(mark_px)  if mark_px  else None,
                "liquidation_price": str(liq_px)   if liq_px   else None,
                "unrealized_pnl":    round(unrl, 4),
                "unrealized_pct":    round(unrl_pct, 2),
                "take_profit":       tp,
                "stop_loss":         sl,
                "duration_minutes":  dur_min,
                "exchange":          "blofin",
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
