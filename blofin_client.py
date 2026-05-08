"""
blofin_client.py — Authenticated Blofin REST API v1 client (read-only).

Auth: 5 required headers per request:
  ACCESS-KEY        — API key
  ACCESS-SIGN       — base64(hmac_sha256(secret, path+method+ts+nonce+body).hexdigest().encode())
  ACCESS-TIMESTAMP  — Unix ms as string
  ACCESS-NONCE      — UUID4 hex string (unique per request)
  ACCESS-PASSPHRASE — passphrase set when creating the API key

Base URL: https://openapi.blofin.com

Pagination: cursor-based via after/before (historyId for positions-history).
Instrument IDs use dash format: 'BTC-USDT' (converted to 'BTCUSDT' for the DB).
"""

import base64
import datetime
import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request
import uuid

BASE_URL    = "https://openapi.blofin.com"
API_KEY     = os.environ.get("BLOFIN_API_KEY",     "")
SECRET_KEY  = os.environ.get("BLOFIN_SECRET_KEY",  "")
PASSPHRASE  = os.environ.get("BLOFIN_PASSPHRASE",  "")


def is_configured() -> bool:
    """Return True if API key + secret are both set."""
    return bool(API_KEY and SECRET_KEY)


def _sign(method: str, path: str, timestamp: str, nonce: str, body: str = "") -> str:
    """
    Blofin signature: base64(hex(hmac_sha256(secret, path+METHOD+ts+nonce+body)))
    Note: hex digest encoded to bytes, then base64 — NOT raw digest bytes.
    """
    prehash = f"{path}{method.upper()}{timestamp}{nonce}{body}"
    hex_sig = hmac.new(SECRET_KEY.encode(), prehash.encode(), hashlib.sha256).hexdigest()
    return base64.b64encode(hex_sig.encode()).decode()


def _get(path: str, params: dict = None) -> dict:
    """Make an authenticated GET request. Returns parsed JSON dict."""
    qs  = ("?" + urllib.parse.urlencode(params)) if params else ""
    url = BASE_URL + path + qs

    ts    = str(int(time.time() * 1000))
    nonce = uuid.uuid4().hex
    sig   = _sign("GET", path + qs, ts, nonce)

    req = urllib.request.Request(url, headers={
        "ACCESS-KEY":        API_KEY,
        "ACCESS-SIGN":       sig,
        "ACCESS-TIMESTAMP":  ts,
        "ACCESS-NONCE":      nonce,
        "ACCESS-PASSPHRASE": PASSPHRASE,
        "Content-Type":      "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return {"code": str(e.code), "msg": body, "data": []}
    except Exception as e:
        return {"code": "error", "msg": str(e), "data": []}


def _ms_to_iso(ms) -> str:
    """Convert Unix milliseconds to ISO datetime string (UTC)."""
    try:
        dt = datetime.datetime.utcfromtimestamp(int(ms) / 1000)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def _inst_to_symbol(inst_id: str) -> str:
    """'BTC-USDT' → 'BTCUSDT'"""
    return inst_id.replace("-", "")


def _direction(side: str, position_side: str) -> str:
    """Map Blofin side/positionSide to 'Long' or 'Short'."""
    if position_side in ("long",):
        return "Long"
    if position_side in ("short",):
        return "Short"
    # net mode: side == 'buy' = Long, 'sell' = Short
    return "Long" if side == "buy" else "Short"


# ── Public methods ─────────────────────────────────────────────────────────────

def test_connection() -> dict:
    """Quick auth check — returns {ok, msg}."""
    if not is_configured():
        return {"ok": False, "msg": "Blofin credentials not set"}
    r = _get("/api/v1/account/balance")
    if r.get("code") == "0":
        data = r.get("data", {})
        equity = data.get("totalEquity", "?")
        return {"ok": True, "msg": f"Connected — equity {equity} USDT"}
    return {"ok": False, "msg": r.get("msg", "Unknown error")}


def get_account_equity() -> dict:
    """Return {equity, available} in USDT."""
    r = _get("/api/v1/account/balance")
    if r.get("code") != "0":
        return {}
    data = r.get("data", {})
    # Find USDT detail
    for detail in data.get("details", []):
        if detail.get("currency", "").upper() == "USDT":
            return {
                "equity":    float(detail.get("equity", 0)),
                "available": float(detail.get("available", 0)),
            }
    # Fallback: top-level totalEquity
    try:
        return {"equity": float(data.get("totalEquity", 0)), "available": 0}
    except Exception:
        return {}


def get_position_history(limit: int = 100, after: str = None) -> list:
    """
    Fetch closed position history (newest first).
    Returns list of normalised dicts ready for DB insertion.
    Uses cursor pagination via historyId.
    """
    params = {"limit": min(limit, 100), "state": "closed"}
    if after:
        params["after"] = after

    r = _get("/api/v1/account/positions-history", params)
    if r.get("code") != "0":
        return []

    rows = []
    for p in (r.get("data") or []):
        symbol      = _inst_to_symbol(p.get("instId", ""))
        if not symbol or not symbol.endswith("USDT"):
            continue

        direction   = _direction(p.get("side", "buy"), p.get("positionSide", "net"))
        entry_price = float(p.get("openAveragePrice") or 0)
        close_price = float(p.get("closeAveragePrice") or 0)
        # Blofin realizedPnl is NET of fees (confirmed by user).
        # fee field is the absolute fee amount.
        # gross = net + fee  |  net is stored as realized_pnl as-is.
        fee         = abs(float(p.get("fee") or 0))
        net_pnl     = float(p.get("realizedPnl") or 0)
        pos_pnl     = round(net_pnl + fee, 6)   # gross (pre-fee)
        open_ms     = int(p.get("createTime") or 0)
        close_ms    = int(p.get("updateTime") or 0)
        open_iso    = _ms_to_iso(open_ms)
        close_iso   = _ms_to_iso(close_ms)
        dur_min     = int((close_ms - open_ms) / 60000) if close_ms > open_ms else 0

        rows.append({
            "external_id":    p.get("historyId", ""),
            "exchange":       "blofin",
            "symbol":         symbol,
            "base_asset":     symbol.replace("USDT", ""),
            "direction":      direction,
            "margin_mode":    p.get("marginMode", "").capitalize(),
            "open_time":      open_iso,
            "close_time":     close_iso,
            "duration_minutes": dur_min,
            "entry_price":    entry_price,
            "close_price":    close_price,
            "size_contracts": str(p.get("closePositions") or ""),
            "size_usdt":      round(entry_price * float(p.get("closePositions") or 0), 2),
            "position_pnl":   pos_pnl,              # gross (pre-fee)
            "realized_pnl":   round(net_pnl, 6),    # net (as returned by Blofin)
            "total_fees":     round(fee, 6),
            "opening_fee":    round(fee * 0.5, 6),  # Blofin charges both sides; split evenly
            "closing_fee":    round(fee * 0.5, 6),
            "leverage":       int(p.get("leverage") or 1),
        })
    return rows


def get_open_positions() -> list:
    """Return current open positions with mark price + unrealised P&L."""
    r = _get("/api/v1/account/positions")
    if r.get("code") != "0":
        return []

    positions = []
    for p in (r.get("data") or []):
        symbol = _inst_to_symbol(p.get("instId", ""))
        if not symbol:
            continue

        direction = _direction(p.get("side", "buy"), p.get("positionSide", "net"))
        size      = float(p.get("positions") or 0)
        avg_price = float(p.get("averagePrice") or 0)
        mark      = float(p.get("markPrice") or 0)

        positions.append({
            "symbol":            symbol,
            "direction":         direction,
            "size":              size,
            "entry_price":       avg_price,
            "mark_price":        mark,
            "unrealized_pnl":    float(p.get("unrealizedPnl") or 0),
            "unrealized_pct":    float(p.get("unrealizedPnlRatio") or 0) * 100,
            "leverage":          int(p.get("leverage") or 1),
            "margin_mode":       p.get("marginMode", ""),
            "liquidation_price": float(p.get("liquidationPrice") or 0),
            "size_usdt":         round(avg_price * size, 2),
            "open_time":         _ms_to_iso(p.get("createTime", 0)),
            "exchange":          "blofin",
        })
    return positions
