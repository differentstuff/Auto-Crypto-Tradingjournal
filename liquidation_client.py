"""
liquidation_client.py — Historical liquidation data from Binance Vision (public, no API key).

Downloads daily liquidation snapshot ZIPs, extracts CSV events, aggregates into
Shorts/Longs USD volume per day, and caches locally.

Data URL:
  https://data.binance.vision/data/futures/um/daily/liquidationSnapshot/
  {symbol}/{symbol}-liquidationSnapshot-{YYYY-MM-DD}.zip

Raw CSV columns: symbol, side (BUY=short liquidated / SELL=long liquidated),
  order_type, time_in_force, original_quantity, price, average_price,
  order_status, last_filled_quantity, time (ms)

Only covers Binance USDT-M futures. Bitget-only symbols return empty data.
"""

import csv
import io
import logging
import os
import zipfile
from datetime import date, datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

BASE_URL  = "https://data.binance.vision/data/futures/um/daily/liquidationSnapshot"
CACHE_DIR = os.path.join(os.path.dirname(__file__), "data", "liquidations")
TIMEOUT   = 12   # seconds per file


def _cache_path(symbol: str, day: str) -> str:
    d = os.path.join(CACHE_DIR, symbol)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{day}.csv")


def _download_day(symbol: str, day: str) -> dict | None:
    """
    Download and aggregate one day of liquidation data.
    Returns {"date": day, "shorts_usd": float, "longs_usd": float} or None on failure.
    BUY side = short positions liquidated (forced buy to close).
    SELL side = long positions liquidated (forced sell to close).
    """
    url = f"{BASE_URL}/{symbol}/{symbol}-liquidationSnapshot-{day}.zip"
    try:
        r = requests.get(url, timeout=TIMEOUT)
        if r.status_code == 404:
            return None    # symbol not on Binance or date too old/future
        r.raise_for_status()
    except Exception as e:
        logger.debug("liquidation download failed %s %s: %s", symbol, day, e)
        return None

    shorts_usd = 0.0
    longs_usd  = 0.0
    try:
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            for name in zf.namelist():
                if not name.endswith(".csv"):
                    continue
                with zf.open(name) as f:
                    reader = csv.DictReader(io.TextIOWrapper(f))
                    for row in reader:
                        try:
                            qty   = float(row.get("original_quantity") or 0)
                            price = float(row.get("average_price") or row.get("price") or 0)
                            vol   = qty * price
                            side  = (row.get("side") or "").upper()
                            if side == "BUY":
                                shorts_usd += vol
                            elif side == "SELL":
                                longs_usd += vol
                        except (ValueError, KeyError):
                            continue
    except Exception as e:
        logger.warning("liquidation parse failed %s %s: %s", symbol, day, e)
        return None

    return {"date": day, "shorts_usd": round(shorts_usd), "longs_usd": round(longs_usd)}


def _load_cached(symbol: str, day: str) -> dict | None:
    path = _cache_path(symbol, day)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            r = csv.DictReader(f)
            for row in r:
                return {
                    "date":       row["date"],
                    "shorts_usd": float(row["shorts_usd"]),
                    "longs_usd":  float(row["longs_usd"]),
                }
    except Exception:
        return None


def _save_cached(symbol: str, entry: dict):
    path = _cache_path(symbol, entry["date"])
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "shorts_usd", "longs_usd"])
        w.writeheader()
        w.writerow(entry)


def get_liquidations(symbol: str, days: int = 30) -> list[dict]:
    """
    Return daily liquidation data for the last `days` days.
    Downloads missing dates from Binance Vision, caches locally.
    Each entry: {date, shorts_usd, longs_usd, total_usd, net_usd}
      shorts_usd: USD value of short positions liquidated
      longs_usd:  USD value of long positions liquidated
      net_usd:    positive = more shorts liquidated (bullish squeeze)
    Returns [] for Bitget-only symbols not listed on Binance.
    """
    sym = symbol.upper()
    today = date.today()
    # Skip today (incomplete) and yesterday until UTC midnight to avoid partials
    cutoff = today - timedelta(days=1)

    results = []
    for i in range(days):
        d = cutoff - timedelta(days=i)
        day_str = d.isoformat()

        entry = _load_cached(sym, day_str)
        if entry is None:
            entry = _download_day(sym, day_str)
            if entry is not None:
                _save_cached(sym, entry)

        if entry:
            total = entry["shorts_usd"] + entry["longs_usd"]
            net   = entry["shorts_usd"] - entry["longs_usd"]
            results.append({
                "date":       entry["date"],
                "shorts_usd": entry["shorts_usd"],
                "longs_usd":  entry["longs_usd"],
                "total_usd":  total,
                "net_usd":    net,
            })

    results.sort(key=lambda x: x["date"])
    return results


def get_summary(symbol: str, days: int = 30) -> dict:
    """
    Aggregate summary over the last `days` days.
    Returns: {symbol, days, total_shorts_usd, total_longs_usd, total_usd,
              dominant, dominant_ratio, peak_day, peak_usd, data: [...]}
    """
    data = get_liquidations(symbol, days)
    if not data:
        return {"symbol": symbol, "days": days, "data": [], "available": False}

    total_s = sum(r["shorts_usd"] for r in data)
    total_l = sum(r["longs_usd"]  for r in data)
    total   = total_s + total_l
    dominant = "shorts" if total_s > total_l else "longs"
    dominant_usd  = max(total_s, total_l)
    dom_ratio = round(dominant_usd / (total - dominant_usd), 2) if (total - dominant_usd) > 0 else 0

    peak = max(data, key=lambda x: x["total_usd"]) if data else {}

    return {
        "symbol":           symbol,
        "days":             days,
        "available":        True,
        "total_shorts_usd": round(total_s),
        "total_longs_usd":  round(total_l),
        "total_usd":        round(total),
        "dominant":         dominant,
        "dominant_ratio":   dom_ratio,
        "peak_day":         peak.get("date"),
        "peak_usd":         peak.get("total_usd"),
        "data":             data,
    }
