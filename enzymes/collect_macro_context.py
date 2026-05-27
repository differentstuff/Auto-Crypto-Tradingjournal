"""
enzymes/collect_macro_context.py -- Sensor enzyme: macro market context.

Fetches VIX, DXY, Fear & Greed, BTC dominance, funding rates, and
economic calendar data. Only activates when modules.macro_context is enabled.

Enzyme class: Sensor (optional module)
Activates when: modules.macro_context == true AND market.macro is empty

Port of: market_context.py, coingecko_client.py, finnhub_client.py
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from typing import Optional

from core.enzyme import Enzyme, EnzymeClass, register_enzyme
from core.substrate import Substrate

_log = logging.getLogger(__name__)

# Cache for macro data (5-minute TTL)
_macro_cache: dict = {}
_MACRO_TTL = 300


def _cached_fetch(key: str, url: str, ttl: int = _MACRO_TTL) -> dict:
    """Fetch URL with TTL cache. Returns {} on error."""
    now = time.time()
    if key in _macro_cache:
        ts, data = _macro_cache[key]
        if now - ts < ttl:
            return data
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "AutoTrader/2.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        _macro_cache[key] = (now, data)
        return data
    except Exception as e:
        _log.warning("Macro fetch failed for %s: %s", key, e)
        return {}


@register_enzyme
class CollectMacroContext(Enzyme):
    """
    Sensor enzyme: fetch macro market context.

    Only activates when modules.macro_context is enabled in config.
    Fetches: Fear & Greed, BTC dominance, VIX/DXY, economic calendar.

    Writes to substrate.market.macro.
    """

    name = "CollectMacroContext"
    enzyme_class = EnzymeClass.SENSOR
    priority = 4

    def requires(self) -> list[str]:
        return ["strategy.name is set"]

    def prohibits(self) -> list[str]:
        return ["market.macro not empty"]

    def can_activate(self, substrate: Substrate) -> bool:
        # Only activate if macro_context module is enabled
        modules = substrate.cfg("modules")
        if not modules.get("macro_context", False):
            return False
        macro_evaluated = substrate.analysis.get("macro_evaluated", False)
        return not macro_evaluated

    def transform(self, substrate: Substrate) -> Substrate:
        """Fetch macro context data."""
        macro = {}

        # 1. Fear & Greed Index
        try:
            fg_data = _cached_fetch(
                "fear_greed",
                "https://api.alternative.me/fng/?limit=1",
            )
            if fg_data and "data" in fg_data:
                item = fg_data["data"][0]
                val = int(item["value"])
                macro["fear_greed"] = {
                    "value": val,
                    "classification": item["value_classification"],
                    "ok": True,
                }
        except Exception as e:
            _log.warning("Fear & Greed fetch failed: %s", e)
            macro["fear_greed"] = {"ok": False, "error": str(e)}

        # 2. BTC Dominance
        try:
            global_data = _cached_fetch(
                "btc_dominance",
                "https://api.coingecko.com/api/v3/global",
                ttl=900,
            )
            if global_data and "data" in global_data:
                data = global_data["data"]
                dom = round(float(data.get("market_cap_percentage", {}).get("btc", 0)), 2)
                chg = round(float(data.get("market_cap_change_percentage_24h_usd", 0)), 2)
                macro["btc_dominance"] = {
                    "btc_dominance": dom,
                    "change_24h": chg,
                    "ok": True,
                }
        except Exception as e:
            _log.warning("BTC dominance fetch failed: %s", e)
            macro["btc_dominance"] = {"ok": False, "error": str(e)}

        # 3. VIX / DXY / Macro regime (requires yfinance)
        try:
            import yfinance as yf
            tickers = yf.download(
                ["^VIX", "DX-Y.NYB"],
                period="2d", interval="1h",
                group_by="ticker", auto_adjust=True, progress=False,
            )

            def _last(sym):
                try:
                    col = tickers[sym]["Close"].dropna()
                    return round(float(col.iloc[-1]), 2) if not col.empty else None
                except Exception:
                    return None

            vix = _last("^VIX")
            dxy = _last("DX-Y.NYB")

            if vix is None:
                regime = "unknown"
            elif vix > 30:
                regime = "risk-off"
            elif vix > 20:
                regime = "neutral"
            else:
                regime = "risk-on"

            macro["regime"] = {
                "vix": vix,
                "dxy": dxy,
                "regime": regime,
                "ok": True,
            }
        except Exception as e:
            _log.warning("Macro regime fetch failed: %s", e)
            macro["regime"] = {"vix": None, "dxy": None, "regime": "unknown", "ok": False}

        # 4. Economic calendar
        try:
            eco_data = _cached_fetch(
                "eco_calendar",
                "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
                ttl=3600,
            )
            if isinstance(eco_data, list):
                from datetime import datetime, timezone, timedelta
                today = datetime.now(timezone.utc).date()
                tomorrow = today + timedelta(days=1)
                events = []
                for e in eco_data:
                    if e.get("impact") != "High":
                        continue
                    country = e.get("country", e.get("currency", ""))
                    if country != "USD":
                        continue
                    raw = e.get("date", "")
                    try:
                        ev_date = datetime.strptime(raw, "%m-%d-%Y").date()
                    except ValueError:
                        continue
                    if ev_date not in (today, tomorrow):
                        continue
                    events.append({
                        "title": e.get("title", ""),
                        "time": e.get("time", ""),
                        "when": "today" if ev_date == today else "tomorrow",
                    })
                macro["economic_events"] = events
        except Exception as e:
            _log.warning("Economic calendar fetch failed: %s", e)

        substrate.market["macro"] = macro
        substrate.analysis["macro_evaluated"] = True

        self._log.info(
            "Macro context: F&G=%s, regime=%s",
            macro.get("fear_greed", {}).get("value", "?"),
            macro.get("regime", {}).get("regime", "?"),
        )

        return substrate

    def flux_score(self, substrate: Substrate) -> float:
        """Dynamic flux: high when module enabled and data missing."""
        if not self.can_activate(substrate):
            return 0.0
        # Higher priority when we have open positions (macro context matters more)
        positions = substrate.portfolio.get("open_positions", [])
        if positions:
            return 2.0  # Active positions — macro context is important for risk
        return 1.8