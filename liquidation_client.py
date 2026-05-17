"""
liquidation_client.py — Historical liquidation volume via Coinalyze.

Uses the Coinalyze liquidation-history endpoint (already configured, COINALYZE_API_KEY).
Aggregates across ALL major exchanges: Binance, Bybit, OKX, Bitget, Deribit, etc.

Each day returns:
  longs_usd  — USD value of long positions liquidated (price fell)
  shorts_usd — USD value of short positions liquidated (price rose)
  net_usd    — positive = more longs liquidated (bearish pressure dominated)

Cached locally in data/liquidations/{symbol}/ to avoid re-fetching past days.
Falls back gracefully when Coinalyze is not configured.
"""

import csv
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)

CACHE_DIR = os.path.join(os.path.dirname(__file__), "data", "liquidations")


def _cache_path(symbol: str, day: str) -> str:
    d = os.path.join(CACHE_DIR, symbol)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{day}.csv")


def _load_cached(symbol: str, day: str) -> dict | None:
    path = _cache_path(symbol, day)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            for row in csv.DictReader(f):
                return {
                    "date":       row["date"],
                    "longs_usd":  float(row["longs_usd"]),
                    "shorts_usd": float(row["shorts_usd"]),
                }
    except Exception:
        return None


def _save_cached(symbol: str, entry: dict):
    path = _cache_path(symbol, entry["date"])
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "longs_usd", "shorts_usd"])
        w.writeheader()
        w.writerow({k: entry[k] for k in ["date", "longs_usd", "shorts_usd"]})


def _fetch_from_coinalyze(symbol: str, from_date: date, to_date: date) -> list[dict]:
    """
    Fetch daily liquidation history from Coinalyze.
    Returns list of {"date", "longs_usd", "shorts_usd"} or [] on failure.

    Coinalyze response: [{"symbol": "...", "t": <ms>, "l": <long_liq_usd>, "s": <short_liq_usd>}]
      "l" = long liquidations USD (longs got blown up — bearish)
      "s" = short liquidations USD (shorts got blown up — bullish)
    """
    try:
        import coinalyze_client
        if not coinalyze_client.is_configured():
            return []

        sym     = coinalyze_client._symbol(symbol)
        from_ms = int(datetime(from_date.year, from_date.month, from_date.day,
                               tzinfo=timezone.utc).timestamp() * 1000)
        to_ms   = int(datetime(to_date.year, to_date.month, to_date.day, 23, 59, 59,
                               tzinfo=timezone.utc).timestamp() * 1000)

        data = coinalyze_client._get("liquidation-history", {
            "symbols":  sym,
            "interval": "daily",
            "from":     from_ms,
            "to":       to_ms,
        })
        if not data or not isinstance(data, list):
            return []

        results = []
        for r in data:
            ts_ms = int(r.get("t") or 0)
            if not ts_ms:
                continue
            day_str = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            longs  = float(r.get("l") or r.get("longLiquidationUsd") or 0)
            shorts = float(r.get("s") or r.get("shortLiquidationUsd") or 0)
            results.append({
                "date":       day_str,
                "longs_usd":  round(longs),
                "shorts_usd": round(shorts),
            })
        return results
    except Exception as e:
        logger.debug("Coinalyze liquidation fetch failed: %s", e)
        return []


def get_liquidations(symbol: str, days: int = 30) -> list[dict]:
    """
    Return daily liquidation data for the last `days` days.
    Loads from cache where available, fetches missing ranges from Coinalyze in one batch.

    Returns list sorted by date asc. Each entry:
      {date, longs_usd, shorts_usd, total_usd, net_usd}
      net_usd > 0  → more longs liquidated (bearish cascade)
      net_usd < 0  → more shorts liquidated (bullish squeeze)
    """
    sym    = symbol.upper()
    today  = date.today()
    cutoff = today - timedelta(days=1)   # skip today (incomplete)

    # Check which days need fetching
    missing = []
    cached  = {}
    for i in range(days):
        d       = cutoff - timedelta(days=i)
        day_str = d.isoformat()
        entry   = _load_cached(sym, day_str)
        if entry:
            cached[day_str] = entry
        else:
            missing.append(d)

    # Fetch all missing days in one API call
    if missing:
        from_d  = min(missing)
        to_d    = max(missing)
        fetched = _fetch_from_coinalyze(sym, from_d, to_d)
        for entry in fetched:
            cached[entry["date"]] = entry
            _save_cached(sym, entry)

    # Assemble output
    results = []
    for i in range(days):
        d       = cutoff - timedelta(days=i)
        day_str = d.isoformat()
        entry   = cached.get(day_str)
        if entry:
            total = entry["longs_usd"] + entry["shorts_usd"]
            net   = entry["longs_usd"] - entry["shorts_usd"]
            results.append({
                "date":       entry["date"],
                "longs_usd":  entry["longs_usd"],
                "shorts_usd": entry["shorts_usd"],
                "total_usd":  total,
                "net_usd":    net,
            })

    results.sort(key=lambda x: x["date"])
    return results


def get_summary(symbol: str, days: int = 30) -> dict:
    """
    Aggregate summary over the last `days` days.

    Returns:
      {symbol, days, available, total_longs_usd, total_shorts_usd, total_usd,
       dominant (longs|shorts), dominant_ratio, peak_day, peak_usd, data: [...]}
    """
    import coinalyze_client
    if not coinalyze_client.is_configured():
        return {"symbol": symbol, "days": days, "available": False,
                "reason": "COINALYZE_API_KEY not configured"}

    data = get_liquidations(symbol, days)
    if not data:
        return {"symbol": symbol, "days": days, "available": False,
                "reason": "No data returned from Coinalyze"}

    total_l = sum(r["longs_usd"]  for r in data)
    total_s = sum(r["shorts_usd"] for r in data)
    total   = total_l + total_s

    dominant     = "longs"  if total_l >= total_s else "shorts"
    dominant_usd = max(total_l, total_s)
    other_usd    = total - dominant_usd
    dom_ratio    = round(dominant_usd / other_usd, 2) if other_usd > 0 else 0

    peak = max(data, key=lambda x: x["total_usd"]) if data else {}

    return {
        "symbol":           symbol,
        "days":             days,
        "available":        True,
        "source":           "Coinalyze (multi-exchange aggregated)",
        "total_longs_usd":  round(total_l),
        "total_shorts_usd": round(total_s),
        "total_usd":        round(total),
        "dominant":         dominant,
        "dominant_ratio":   dom_ratio,
        "peak_day":         peak.get("date"),
        "peak_usd":         peak.get("total_usd"),
        "data":             data,
    }
