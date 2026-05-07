"""
bitget_client.py — Authenticated Bitget REST API v2 client (read-only).

Confirmed field names from live API (2026-05-05):
  position history → positionId, holdSide, openAvgPrice, closeAvgPrice,
                     openTotalPos, pnl, netProfit, openFee, closeFee,
                     totalFunding, marginMode, ctime, utime  (lowercase!)
  order history    → orderId, priceAvg, quoteVolume, fee, totalProfits,
                     side, posSide, tradeSide, orderSource, cTime, uTime
  bills            → billId, amount, fee, businessType, balance, cTime
"""

import base64
import hashlib
import hmac as _hmac
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL   = "https://api.bitget.com"
API_KEY    = os.environ.get("BITGET_API_KEY",    "")
SECRET_KEY = os.environ.get("BITGET_SECRET_KEY", "")
PASSPHRASE = os.environ.get("BITGET_PASSPHRASE", "")


class BitgetAPIError(Exception):
    pass


def _sign(ts: str, method: str, path: str, query: str = "") -> str:
    qs  = ("?" + query) if query else ""
    msg = ts + method.upper() + path + qs
    return base64.b64encode(
        _hmac.new(SECRET_KEY.encode(), msg.encode(), hashlib.sha256).digest()
    ).decode()


def _get(path: str, params: dict) -> dict:
    ts  = str(int(time.time() * 1000))
    qs  = urllib.parse.urlencode(params)
    sig = _sign(ts, "GET", path, qs)
    url = BASE_URL + path + "?" + qs
    req = urllib.request.Request(url, headers={
        "ACCESS-KEY":       API_KEY,
        "ACCESS-SIGN":      sig,
        "ACCESS-TIMESTAMP": ts,
        "ACCESS-PASSPHRASE": PASSPHRASE,
        "Content-Type":     "application/json",
        "locale":           "en-US",
    })
    socket.setdefaulttimeout(15)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise BitgetAPIError(f"HTTP {e.code}")

    code = resp.get("code", "")
    if code != "00000":
        raise BitgetAPIError(f"Bitget API error {code}")
    return resp.get("data", {})


# ── Public API methods ─────────────────────────────────────────────────────────

def get_account_equity() -> dict:
    """Return current USDT equity and available balance."""
    rows = _get("/api/v2/mix/account/accounts", {"productType": "USDT-FUTURES"})
    if isinstance(rows, list) and rows:
        return rows[0]
    return {}


def _paginate(path: str, row_key: str, start_ms: int, end_ms: int,
              time_key: str = "cTime") -> list:
    """
    Generic paginated GET for Bitget history endpoints.

    Bitget cursor pagination rules:
      Page 1:  send startTime + endTime (max 90-day window per request).
      Page 2+: send ONLY endId — resending the time range causes Bitget to
               recompute the interval from startTime to the cursor position,
               which can exceed 90 days and triggers error 00001.

    To compensate, each row's timestamp is checked against start_ms. Once we
    receive a row older than our window, we stop — otherwise we'd page through
    the entire account history.
    """
    all_rows = []
    end_id   = None
    LIMIT    = 100

    while True:
        if end_id:
            params = {"productType": "USDT-FUTURES", "limit": str(LIMIT), "endId": end_id}
        else:
            params = {"productType": "USDT-FUTURES", "limit": str(LIMIT)}
            if start_ms: params["startTime"] = str(start_ms)
            if end_ms:   params["endTime"]   = str(end_ms)

        data    = _get(path, params)
        rows    = (data.get(row_key) or data.get("list", [])) if isinstance(data, dict) else []
        next_id = data.get("endId") if isinstance(data, dict) else None

        if not rows:
            break

        # On cursor pages: filter rows to our window and stop when we pass start_ms
        past_window = False
        for row in rows:
            ts = int(row.get(time_key) or row.get("ctime") or 0)
            if start_ms and ts < start_ms:
                past_window = True
                break
            all_rows.append(row)

        if past_window or len(rows) < LIMIT or not next_id:
            break
        end_id = next_id

    return all_rows


def get_recent_positions(max_pages: int = 5) -> list:
    """
    Fetch the most recently CLOSED positions using cursor-only pagination.
    Returns up to max_pages * 100 rows, newest first.

    WHY no time filter: Bitget's /history-position startTime/endTime filters by
    OPEN time (ctime), NOT close time (utime).  A position held for 2 weeks but
    closed today has ctime=14 days ago → it never appears when startTime is
    set to anything within the last few days.  Cursor-only pagination avoids
    this entirely: we get the freshest N closed positions regardless of when
    they were opened, then stop on the first positionId we already know.
    """
    all_rows = []
    end_id   = None
    LIMIT    = 100

    for _ in range(max_pages):
        params = {"productType": "USDT-FUTURES", "limit": str(LIMIT)}
        if end_id:
            params["endId"] = end_id

        data    = _get("/api/v2/mix/position/history-position", params)
        rows    = (data.get("list") or []) if isinstance(data, dict) else []
        next_id = data.get("endId")        if isinstance(data, dict) else None

        all_rows.extend(rows)
        if not rows or len(rows) < LIMIT or not next_id:
            break
        end_id = next_id

    return all_rows


def get_position_history(start_ms: int = None, end_ms: int = None) -> list:
    """
    Kept for backward compatibility / manual queries.
    NOTE: startTime/endTime on this endpoint filter by OPEN time, not close time.
    Use get_recent_positions() for live sync.
    """
    return _paginate(
        "/api/v2/mix/position/history-position", "list",
        start_ms, end_ms, time_key="ctime"
    )


def get_order_history(start_ms: int = None, end_ms: int = None) -> list:
    """
    Fetch filled orders in the given time window.
    Field keys: orderId, priceAvg, quoteVolume, fee, totalProfits,
                side, posSide, tradeSide, orderSource, cTime, uTime
    """
    return _paginate(
        "/api/v2/mix/order/orders-history", "entrustedList",
        start_ms, end_ms
    )


def get_account_bills(start_ms: int = None, end_ms: int = None) -> list:
    """
    Fetch account bills in the given time window.
    Field keys: billId, symbol, amount, fee, businessType, balance, cTime
    Note: 'coin' param causes error — omit it, fetch all types together.
    """
    return _paginate(
        "/api/v2/mix/account/bill", "bills",
        start_ms, end_ms
    )


def get_open_positions() -> list:
    """
    Fetch all currently open positions (USDT-M Futures).
    Returns a list enriched with calculated fields:
      size_usdt, unrealized_pct, duration_minutes, direction (Long/Short)

    API: GET /api/v2/mix/position/all-position
    Confirmed field names: symbol, holdSide, openPriceAvg, markPrice,
    unrealizedPL, marginSize, total, leverage, takeProfit, stopLoss,
    liquidationPrice, breakEvenPrice, achievedProfits, totalFee, cTime
    """
    data = _get("/api/v2/mix/position/all-position",
                {"productType": "USDT-FUTURES", "marginCoin": "USDT"})
    rows = data if isinstance(data, list) else []

    now_ms = int(time.time() * 1000)
    result = []
    for r in rows:
        total      = float(r.get("total") or 0)
        mark       = float(r.get("markPrice") or 0)
        margin     = float(r.get("marginSize") or 1)
        unrl       = float(r.get("unrealizedPL") or 0)
        c_time_ms  = int(r.get("cTime") or 0)
        size_usdt  = total * mark
        unrl_pct   = (unrl / margin * 100) if margin else 0
        duration_m = int((now_ms - c_time_ms) / 60000) if c_time_ms else None

        result.append({
            "symbol":           r.get("symbol"),
            "direction":        "Long" if r.get("holdSide") == "long" else "Short",
            "leverage":         r.get("leverage"),
            "margin_mode":      "Cross" if "cross" in (r.get("marginMode") or "") else "Isolated",
            "total":            total,
            "size_usdt":        round(size_usdt, 2),
            "margin_usdt":      round(margin, 2),
            "entry_price":      r.get("openPriceAvg"),
            "mark_price":       r.get("markPrice"),
            "break_even_price": r.get("breakEvenPrice"),
            "liquidation_price":r.get("liquidationPrice"),
            "unrealized_pnl":   round(unrl, 4),
            "unrealized_pct":   round(unrl_pct, 2),
            "achieved_profits": round(float(r.get("achievedProfits") or 0), 4),
            "total_fee":        round(float(r.get("totalFee") or 0), 4),
            "take_profit":      r.get("takeProfit") or "",
            "stop_loss":        r.get("stopLoss") or "",
            "margin_ratio":     r.get("marginRatio"),
            "duration_minutes": duration_m,
            "open_time_ms":     c_time_ms,
        })

    # Sort by unrealized PnL ascending (worst losses first)
    result.sort(key=lambda x: x["unrealized_pnl"])
    return result


def _ms_to_str(ms: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')


def get_pending_orders() -> list:
    """
    Fetch all unfilled limit orders on USDT-M Futures.

    Returns two lists under keys 'entry' and 'exit':
      entry  — tradeSide=open  (limit orders to enter a position)
      exit   — tradeSide=close (TP/SL limit orders on open positions)

    Confirmed v2 fields: orderId, symbol, side, posSide, tradeSide, orderType,
    price, size, notionalUsd, leverage, marginMode, status, cTime, clientOid
    """
    try:
        data = _get("/api/v2/mix/order/orders-pending",
                    {"productType": "USDT-FUTURES"})
    except BitgetAPIError:
        return []

    rows = []
    if isinstance(data, dict):
        rows = data.get("entrustedList") or data.get("list") or []
    elif isinstance(data, list):
        rows = data

    entry_orders = []
    exit_orders  = []

    for r in rows:
        if (r.get("orderType") or "").lower() != "limit":
            continue

        pos_side   = (r.get("posSide") or "").lower()
        trade_side = (r.get("tradeSide") or "open").lower()
        direction  = "Long" if pos_side == "long" else "Short"
        price      = float(r.get("price") or 0)
        size       = float(r.get("size")  or 0)
        notional   = float(r.get("notionalUsd") or 0)
        if not notional and price and size:
            notional = round(price * size, 2)
        c_time_ms  = int(r.get("cTime") or 0)
        margin_mode = r.get("marginMode") or "crossed"

        order = {
            "order_id":    r.get("orderId"),
            "symbol":      r.get("symbol"),
            "direction":   direction,
            "trade_side":  trade_side,
            "price":       price,
            "size":        size,
            "notional_usdt": round(notional, 2),
            "leverage":    r.get("leverage"),
            "margin_mode": "Cross" if "cross" in margin_mode.lower() else "Isolated",
            "status":      r.get("status"),
            "client_oid":  r.get("clientOid"),
            "created_ms":  c_time_ms,
            "created_at":  _ms_to_str(c_time_ms) if c_time_ms else "",
        }
        if trade_side == "open":
            entry_orders.append(order)
        else:
            exit_orders.append(order)

    return {"entry": entry_orders, "exit": exit_orders}


def get_exchange_symbols() -> list:
    """
    Return sorted list of all USDT-M futures symbol strings available on Bitget.
    Uses the public tickers endpoint (auth headers accepted but not required).
    """
    data = _get("/api/v2/mix/market/tickers", {"productType": "USDT-FUTURES"})
    rows = data if isinstance(data, list) else []
    return sorted({r["symbol"] for r in rows if r.get("symbol")})


def get_mark_prices(symbols: list) -> dict:
    """
    Return {symbol: float(mark_price)} for the requested symbols.
    Falls back to lastPr when markPrice is absent.
    """
    target = {s.upper() for s in symbols}
    data   = _get("/api/v2/mix/market/tickers", {"productType": "USDT-FUTURES"})
    rows   = data if isinstance(data, list) else []
    result = {}
    for r in rows:
        sym = r.get("symbol", "")
        if sym in target:
            raw = r.get("markPrice") or r.get("lastPr") or r.get("last")
            try:
                result[sym] = float(raw)
            except (TypeError, ValueError):
                pass
    return result


def test_connection() -> dict:
    """Quick auth check — returns account equity dict or raises."""
    return get_account_equity()
