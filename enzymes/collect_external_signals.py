"""
enzymes/collect_external_signals.py -- Sensor enzyme: external confluence signals.

Fetches funding rate (Binance), Fear & Greed Index (alternative.me),
and liquidation clusters (Binance forceOrders). Produces confluence
signals that enrich the substrate before ScoreConfluence runs.

Confluence signals:
  - funding_squeeze:  funding_rate < threshold (crowded shorts → explosive upside)
  - fgi_contrarian:   FGI <= threshold (extreme fear → contrarian long)
  - liquidation_cascade: > $threshold liquidations in 5min (smart money fading)

Enzyme class: Sensor (optional module)
Activates when: modules.external_signals == true AND external signals not yet evaluated

Graceful degradation: if any API fails, log warning and skip — never crash.
All results cached with TTL to avoid redundant API calls.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from typing import Any, Dict, Optional

from core.enzyme import Enzyme, EnzymeClass, register_enzyme
from core.substrate import Substrate

_log = logging.getLogger(__name__)

# Module-level cache: {key: (timestamp, data)}
_cache: dict = {}


def _cached_fetch(key: str, url: str, ttl: int, timeout: int = 10) -> Any:
    """Fetch URL with TTL cache. Returns parsed JSON or None on failure."""
    now = time.time()
    if key in _cache:
        ts, data = _cache[key]
        if now - ts < ttl:
            return data
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "AutoTrader/2.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        _cache[key] = (now, data)
        return data
    except Exception as e:
        _log.warning("External signal fetch failed for %s: %s", key, e)
        return None


def _fetch_funding_rate(symbol: str, ttl: int) -> Optional[float]:
    """Fetch current funding rate for a symbol from Binance Futures.

    Returns the funding rate as a float (e.g. -0.0003 for -0.03%),
    or None on failure.
    """
    url = "https://fapi.binance.com/fapi/v1/premiumIndex"
    data = _cached_fetch("binance_funding", url, ttl)
    if data is None:
        return None
    try:
        # data is a list of premium index objects
        for item in data:
            if item.get("symbol") == symbol:
                return float(item.get("lastFundingRate", 0))
        _log.debug("Symbol %s not found in Binance premiumIndex response", symbol)
        return None
    except (ValueError, TypeError, KeyError) as e:
        _log.warning("Failed to parse funding rate for %s: %s", symbol, e)
        return None


def _fetch_fear_greed_index(ttl: int) -> Optional[dict]:
    """Fetch Fear & Greed Index from alternative.me.

    Returns {"value": int, "classification": str} or None on failure.
    """
    url = "https://api.alternative.me/v1/FearAndGreedIndex/?limit=1"
    data = _cached_fetch("fear_greed_index", url, ttl)
    if data is None:
        return None
    try:
        item = data["data"][0]
        return {
            "value": int(item["value"]),
            "classification": item.get("value_classification", ""),
        }
    except (ValueError, TypeError, KeyError, IndexError) as e:
        _log.warning("Failed to parse Fear & Greed Index: %s", e)
        return None


def _fetch_liquidations(symbol: str, ttl: int, limit: int = 50) -> Optional[list]:
    """Fetch recent forced liquidation orders from Binance Futures.

    Returns a list of forceOrder dicts, or None on failure.
    Each dict has: {symbol, side, price, origQty, executedQty, time, type}
    """
    url = (
        f"https://fapi.binance.com/fapi/v1/forceOrders"
        f"?symbol={symbol}&limit={limit}"
    )
    data = _cached_fetch(f"liquidations_{symbol}", url, ttl)
    if data is None:
        return None
    if not isinstance(data, list):
        _log.warning("Unexpected liquidations response format for %s", symbol)
        return None
    return data


def _compute_liquidation_cascade(
    liquidations: list,
    threshold_usd: float,
    window_seconds: int = 300,
) -> Dict[str, Any]:
    """Determine if a liquidation cascade is occurring.

    A cascade is triggered when the total USD value of forced
    liquidations within the last `window_seconds` exceeds
    `threshold_usd`.

    Returns {"triggered": bool, "total_usd": float, "count": int,
             "cluster_walls": {price: usd_volume}}.
    """
    now_ms = time.time() * 1000
    cutoff_ms = now_ms - (window_seconds * 1000)

    total_usd = 0.0
    count = 0
    # Group by price to identify cluster walls
    price_buckets: Dict[str, float] = {}

    for order in liquidations:
        try:
            order_time = int(order.get("time", 0))
            if order_time < cutoff_ms:
                continue
            price = float(order.get("price", 0))
            qty = float(order.get("origQty", 0))
            usd = price * qty
            total_usd += usd
            count += 1
            # Bucket price to nearest round number for clustering
            bucket = f"{round(price, -int(round(price, 0) and 0) or 1)}"
            price_buckets[bucket] = price_buckets.get(bucket, 0) + usd
        except (ValueError, TypeError):
            continue

    # Keep only the top cluster walls by USD volume
    sorted_walls = sorted(price_buckets.items(), key=lambda x: x[1], reverse=True)
    cluster_walls = {k: round(v, 2) for k, v in sorted_walls[:5]}

    return {
        "triggered": total_usd >= threshold_usd,
        "total_usd": round(total_usd, 2),
        "count": count,
        "cluster_walls": cluster_walls,
    }


@register_enzyme
class CollectExternalSignals(Enzyme):
    """
    Sensor enzyme: fetch external confluence signals.

    Fetches funding rate, Fear & Greed Index, and liquidation data.
    Writes confluence signals to substrate.analysis.confluence and
    raw data to substrate.market fields.

    Only activates when modules.external_signals is enabled in config.
    Runs on a configurable schedule (external_interval) to reduce API load.
    """

    name = "CollectExternalSignals"
    enzyme_class = EnzymeClass.SENSOR
    priority = 5  # After CollectMacroContext (4), before ScoreConfluence

    def requires(self) -> list[str]:
        return ["strategy.name is set"]

    def prohibits(self) -> list[str]:
        return ["analysis.external_signals_evaluated is true"]

    def can_activate(self, substrate: Substrate) -> bool:
        """Only activate if external_signals module is enabled."""
        modules = substrate.cfg("modules", {})
        if not modules.get("external_signals", False):
            return False
        evaluated = substrate.analysis.get("external_signals_evaluated", False)
        return not evaluated

    def transform(self, substrate: Substrate) -> Substrate:
        """Fetch external signals and write confluence data to substrate."""
        # Read config
        external_cfg = substrate.cfg("external", {})
        funding_threshold = external_cfg.get("funding_squeeze_threshold", -0.0003)
        fgi_threshold = external_cfg.get("fgi_contrarian_threshold", 20)
        liq_threshold_usd = external_cfg.get("liquidation_cascade_usd", 250000)
        liq_window_sec = external_cfg.get("liquidation_window_seconds", 300)
        ttl = external_cfg.get("cache_ttl", 3600)
        symbols = substrate.cfg("symbols.always_watch", ["BTCUSDT", "ETHUSDT"])

        # Initialize confluence dict (preserve existing keys)
        confluence = substrate.analysis.get("confluence", {})

        # -- 1. Funding Rate ----------------------------------------------
        # Use the first watched symbol for funding rate (typically BTCUSDT)
        funding_symbol = symbols[0] if symbols else "BTCUSDT"
        funding_rate = _fetch_funding_rate(funding_symbol, ttl)

        if funding_rate is not None:
            substrate.market["funding_rate"] = {
                "symbol": funding_symbol,
                "rate": funding_rate,
                "ok": True,
            }
            confluence["funding_squeeze"] = funding_rate < funding_threshold
            self._log.info(
                "Funding rate %s: %.6f (squeeze=%s, threshold=%.6f)",
                funding_symbol, funding_rate,
                confluence["funding_squeeze"], funding_threshold,
            )
        else:
            substrate.market["funding_rate"] = {"ok": False}
            # Do not set confluence key on failure — graceful degradation

        # -- 2. Fear & Greed Index (contrarian) ---------------------------
        # Re-use FGI from macro context if already fetched, otherwise fetch
        existing_fgi = substrate.market.get("macro", {}).get("fear_greed", {})
        if isinstance(existing_fgi, dict) and existing_fgi.get("ok"):
            fgi_value = existing_fgi.get("value")
        else:
            fgi_data = _fetch_fear_greed_index(ttl)
            if fgi_data is not None:
                fgi_value = fgi_data["value"]
                # Write to macro if not already set
                if "macro" not in substrate.market:
                    substrate.market["macro"] = {}
                substrate.market["macro"]["fear_greed"] = {
                    "value": fgi_value,
                    "classification": fgi_data["classification"],
                    "ok": True,
                }
            else:
                fgi_value = None

        if fgi_value is not None:
            confluence["fgi_contrarian"] = fgi_value <= fgi_threshold
            self._log.info(
                "FGI: %d (contrarian=%s, threshold=%d)",
                fgi_value, confluence["fgi_contrarian"], fgi_threshold,
            )

        # -- 3. Liquidation Cascade ---------------------------------------
        # Fetch liquidations for the primary symbol
        liq_symbol = funding_symbol
        liquidations = _fetch_liquidations(liq_symbol, ttl)

        if liquidations is not None:
            cascade = _compute_liquidation_cascade(
                liquidations, liq_threshold_usd, liq_window_sec,
            )
            confluence["liquidation_cascade"] = cascade["triggered"]
            substrate.market["liquidations"] = {
                "symbol": liq_symbol,
                "cluster_walls": cascade["cluster_walls"],
                "total_usd": cascade["total_usd"],
                "count": cascade["count"],
                "ok": True,
            }
            self._log.info(
                "Liquidations %s: $%.0f in %ds (cascade=%s, threshold=$%.0f)",
                liq_symbol, cascade["total_usd"], liq_window_sec,
                cascade["triggered"], liq_threshold_usd,
            )
        else:
            substrate.market["liquidations"] = {"ok": False}

        # -- Write confluence signals to substrate ------------------------
        substrate.analysis["confluence"] = confluence
        substrate.analysis["external_signals_evaluated"] = True

        active_signals = [k for k, v in confluence.items() if v is True]
        self._log.info(
            "External signals evaluated: %d active confluence signals %s",
            len(active_signals), active_signals,
        )

        return substrate

    def flux_score(self, substrate: Substrate) -> float:
        """Dynamic flux: high when module enabled and data missing."""
        if not self.can_activate(substrate):
            return 0.0
        # Higher priority when we have open positions (external context matters more)
        positions = substrate.portfolio.get("open_positions", [])
        if positions:
            return 2.0  # Active positions — external signals are important
        return 1.8