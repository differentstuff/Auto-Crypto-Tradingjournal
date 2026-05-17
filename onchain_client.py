# onchain_client.py
"""
BTC on-chain metrics via CoinMetrics Community API (keyless).
Same data source as checkonchain (github.com/Tsunekazu/checkonchain).
Metrics: MVRV (mvrv_cur), SOPR (sopr), exchange in/out flows.
"""
import logging
import time
import requests

_log   = logging.getLogger(__name__)
_URL   = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
_TTL   = 3600   # 1 h — data is daily
_CACHE: dict[str, tuple[float, dict]] = {}


def _fetch() -> dict:
    params = {
        "assets":          "btc",
        "metrics":         "mvrv_cur,sopr,FlowInExUSD,FlowOutExUSD",
        "frequency":       "1d",
        "page_size":       1,
        "sort":            "time",
    }
    try:
        resp = requests.get(_URL, params=params, timeout=10)
        if not resp.ok:
            return {"ok": False, "reason": f"HTTP {resp.status_code}"}
        rows = resp.json().get("data", [])
        if not rows:
            return {"ok": False, "reason": "empty response"}
        row     = rows[-1]
        mvrv    = float(row.get("mvrv_cur")    or 0)
        sopr    = float(row.get("sopr")        or 1)
        inflow  = float(row.get("FlowInExUSD") or 0)
        outflow = float(row.get("FlowOutExUSD") or 0)
        net_flow = outflow - inflow   # positive = net outflow = accumulation
        if mvrv > 3.5 or sopr > 1.04:
            regime = "overvalued"
        elif mvrv < 1.0 or sopr < 0.98:
            regime = "undervalued"
        else:
            regime = "fair_value"
        return {
            "ok":                    True,
            "mvrv":                  round(mvrv, 3),
            "sopr":                  round(sopr, 4),
            "exchange_net_flow_usd": round(net_flow, 0),
            "regime":                regime,
            "date":                  row.get("time", ""),
        }
    except Exception as exc:
        _log.warning("onchain_client: %s", exc)
        return {"ok": False, "reason": str(exc)}


def get_btc_onchain() -> dict:
    """Return BTC on-chain metrics, TTL-cached."""
    now = time.time()
    if "btc" in _CACHE:
        ts, data = _CACHE["btc"]
        if now - ts < _TTL:
            return data
    result        = _fetch()
    _CACHE["btc"] = (now, result)
    return result
