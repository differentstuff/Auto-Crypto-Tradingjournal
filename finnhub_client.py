"""
finnhub_client.py — Finnhub economic calendar for macro risk flagging.

Primary use: detect upcoming high-impact macro events (FOMC, CPI, NFP)
in the next 24 hours and flag setups as macro-risk.

API docs: https://finnhub.io/docs/api
Free tier: 60 calls/minute

Confirmed response shape (2026-05-15):
  {"economicCalendar": [{"time": "2026-05-15 14:00:00", "event": "FOMC",
    "country": "US", "impact": "high", "actual": null, "estimate": null,
    "prev": null, "unit": ""}]}
"""
import os
import urllib.request
import json
import logging
from datetime import datetime, timedelta, timezone

_log = logging.getLogger(__name__)
_API_KEY = os.environ.get("FINNHUB_API_KEY", "")
_BASE = "https://finnhub.io/api/v1"
_TIMEOUT = 8

# High-impact events that warrant a macro risk flag
_HIGH_IMPACT = {"fomc", "federal", "cpi", "nfp", "payroll", "gdp", "pce",
                "inflation", "interest rate", "fed ", "employment"}


def _get(path: str, params: dict) -> dict | list | None:
    if not _API_KEY:
        return None
    params["token"] = _API_KEY
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{_BASE}/{path}?{qs}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "TradingJournal/1.0"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            return json.loads(r.read())
    except Exception as e:
        _log.debug("Finnhub %s failed: %s", path, e)
        return None


def get_upcoming_events(hours_ahead: int = 48) -> dict:
    """
    Return high-impact economic events in the next `hours_ahead` hours.

    Returns:
        {
          "events": [{"time": str, "event": str, "country": str, "impact": str}],
          "macro_risk": bool,        # True if any high-impact event in next 24h
          "next_event": str | None,  # Description of soonest event
          "hours_until": float | None
        }
    """
    now = datetime.now(timezone.utc)
    end = now + timedelta(hours=hours_ahead)
    today = now.strftime("%Y-%m-%d")
    to_dt = end.strftime("%Y-%m-%d")

    data = _get("calendar/economic", {"from": today, "to": to_dt})
    try:
        raw_events = []
        if isinstance(data, dict):
            # Confirmed field name: "economicCalendar"
            raw_events = data.get("economicCalendar") or data.get("events") or []
        elif isinstance(data, list):
            raw_events = data

        events = []
        for e in raw_events:
            # Confirmed field names from live API test
            event_time = e.get("time") or e.get("datetime") or e.get("t") or ""
            event_name = e.get("event") or e.get("description") or e.get("e") or ""
            country    = e.get("country") or e.get("c") or ""
            impact     = (e.get("impact") or e.get("i") or "low").lower()

            # Only include US events and high/medium impact
            if country not in ("US", "United States", ""):
                continue
            if impact not in ("high", "medium"):
                continue

            events.append({
                "time":    event_time,
                "event":   event_name,
                "country": country,
                "impact":  impact,
            })

        # Determine macro risk (high-impact keyword match)
        macro_risk = False
        next_event = None
        hours_until = None

        for ev in events:
            name_lower = ev["event"].lower()
            if any(kw in name_lower for kw in _HIGH_IMPACT):
                macro_risk = True
                if next_event is None:
                    next_event = ev["event"][:80]
                    # Try to compute hours until event
                    try:
                        # Finnhub time format: "2026-05-15 14:00:00" (UTC)
                        ev_dt = datetime.fromisoformat(
                            ev["time"].replace(" ", "T").replace("Z", "+00:00")
                        )
                        if ev_dt.tzinfo is None:
                            ev_dt = ev_dt.replace(tzinfo=timezone.utc)
                        hours_until = round((ev_dt - now).total_seconds() / 3600, 1)
                    except Exception:
                        pass
                break

        return {
            "events":      events[:5],  # cap at 5 events
            "macro_risk":  macro_risk,
            "next_event":  next_event,
            "hours_until": hours_until,
        }
    except Exception:
        return {"events": [], "macro_risk": False, "next_event": None, "hours_until": None}
