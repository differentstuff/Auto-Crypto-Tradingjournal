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

# Module-level cache dict (TTLs now come from config via substrate.cfg)
_macro_cache: dict = {}


def _cached_fetch(key: str, url: str, ttl: int) -> dict:
    """Fetch URL with TTL cache. ttl must be passed from config."""
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
        # Read cache TTLs from config (hot-reloaded every cycle)
        macro_ttl = substrate.cfg("cache.macro_ttl")
        dominance_ttl = substrate.cfg("cache.dominance_ttl")
        onchain_ttl = substrate.cfg("cache.onchain_ttl")

        macro = {}

        # 1. Fear & Greed Index
        fg_url = substrate.cfg("macro.fear_greed_url", "https://api.alternative.me/fng/?limit=1")
        try:
            fg_data = _cached_fetch(
                "fear_greed",
                fg_url,
                ttl=macro_ttl,
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
        dominance_url = substrate.cfg("macro.dominance_url", "https://api.coingecko.com/api/v3/global")
        try:
            global_data = _cached_fetch(
                "btc_dominance",
                dominance_url,
                ttl=dominance_ttl,
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
        # VIX thresholds and yfinance params from config (macro section)
        try:
            import yfinance as yf
            vix_risk_off = substrate.cfg("macro.vix_risk_off")
            vix_neutral = substrate.cfg("macro.vix_neutral")
            yf_period = substrate.cfg("macro.yfinance_period")
            yf_interval = substrate.cfg("macro.yfinance_interval")
            tickers = yf.download(
                ["^VIX", "DX-Y.NYB"],
                period=yf_period, interval=yf_interval,
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
            elif vix > vix_risk_off:
                regime = "risk-off"
            elif vix > vix_neutral:
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
        calendar_url = substrate.cfg("macro.calendar_url", "https://nfs.faireconomy.media/ff_calendar_thisweek.json")
        try:
            eco_data = _cached_fetch(
                "eco_calendar",
                calendar_url,
                ttl=onchain_ttl,
            )
            if isinstance(eco_data, list):
                from datetime import datetime, timezone, timedelta
                today = substrate.now_as_datetime().date()
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