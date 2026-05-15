"""
deribit_client.py — Deribit options market data for BTC and ETH.

Provides put/call skew as an institutional sentiment proxy.
- Positive skew (puts > calls by IV) = institutional downside hedging (bearish)
- Negative skew (calls > puts by IV) = institutional upside positioning (bullish)

Free public API — no authentication required.
Only meaningful for BTC and ETH (only liquid options markets).

API docs: https://docs.deribit.com/

Live-tested field names (2026-05-15):
  instrument_name  — e.g. "BTC-18MAY26-73000-P" (ends -P or -C)
  mark_iv          — implied volatility, float (e.g. 43.42)
  volume           — trading volume in base currency (e.g. 1.1)
"""
import urllib.request
import json
import logging
from datetime import datetime

_log = logging.getLogger(__name__)
_BASE = "https://www.deribit.com/api/v2/public"
_TIMEOUT = 10

# Only BTC and ETH have liquid options markets on Deribit
_SUPPORTED = {"BTCUSDT": "BTC", "ETHUSDT": "ETH"}


def _get(method: str, params: dict) -> dict | None:
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{_BASE}/{method}?{qs}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "TradingJournal/1.0"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            data = json.loads(r.read())
        return data.get("result")
    except Exception as e:
        _log.debug("Deribit %s failed: %s", method, e)
        return None


def get_options_skew(symbol: str) -> dict:
    """
    Compute put/call skew for BTC or ETH from Deribit options summary.

    Skew = avg put IV - avg call IV across all listed options.
    Positive skew → puts more expensive → institutional bearish hedging.
    Negative skew → calls more expensive → institutional bullish positioning.

    Returns {} for non-BTC/ETH symbols or on API failure.

    Returns:
        {
          "currency":       str,        # "BTC" or "ETH"
          "put_call_ratio": float,      # total put volume / total call volume
          "iv_skew":        float,      # avg put IV - avg call IV (positive = bearish)
          "sentiment":      str,        # "bearish_hedge"|"bullish_positioning"|"neutral"
          "near_term_iv":   float|None, # implied vol averaged across first 10 near-term options
        }
    """
    currency = _SUPPORTED.get(symbol.upper())
    if not currency:
        return {}

    result = _get("get_book_summary_by_currency", {
        "currency": currency,
        "kind":     "option",
    })
    try:
        if not result or not isinstance(result, list):
            return {}

        put_vols = []
        call_vols = []
        put_ivs = []
        call_ivs = []

        for opt in result:
            # Confirmed field names from live API test:
            # instrument_name: "BTC-18MAY26-73000-P"
            # mark_iv: 43.42 (implied volatility, float)
            # volume: 1.1 (base currency volume)
            instrument = opt.get("instrument_name", "")
            iv = float(opt.get("mark_iv") or 0)
            volume = float(opt.get("volume") or 0)

            if instrument.endswith("-P"):
                put_vols.append(volume)
                if iv > 0:
                    put_ivs.append(iv)
            elif instrument.endswith("-C"):
                call_vols.append(volume)
                if iv > 0:
                    call_ivs.append(iv)

        total_put_vol  = sum(put_vols)
        total_call_vol = sum(call_vols)
        pcr = round(total_put_vol / max(total_call_vol, 1), 3)

        avg_put_iv  = sum(put_ivs)  / len(put_ivs)  if put_ivs  else 0
        avg_call_iv = sum(call_ivs) / len(call_ivs) if call_ivs else 0
        iv_skew = round(avg_put_iv - avg_call_iv, 1)

        if pcr > 1.2 or iv_skew > 5:
            sentiment = "bearish_hedge"          # institutions buying downside protection
        elif pcr < 0.8 or iv_skew < -5:
            sentiment = "bullish_positioning"    # call buyers dominant
        else:
            sentiment = "neutral"

        # Near-term IV: sort contracts by expiry date, take first 10 puts + 10 calls
        def _expiry_key(opt):
            try:
                parts = opt.get("instrument_name", "").split("-")
                return datetime.strptime(parts[1], "%d%b%y")
            except Exception:
                return datetime.max

        sorted_opts = sorted(result, key=_expiry_key)
        near_put_ivs  = [float(o.get("mark_iv") or 0) for o in sorted_opts
                         if o.get("instrument_name", "").endswith("-P") and float(o.get("mark_iv") or 0) > 0]
        near_call_ivs = [float(o.get("mark_iv") or 0) for o in sorted_opts
                         if o.get("instrument_name", "").endswith("-C") and float(o.get("mark_iv") or 0) > 0]
        near_sample = near_put_ivs[:10] + near_call_ivs[:10]
        near_term_iv = round(sum(near_sample) / len(near_sample), 1) if near_sample else None

        return {
            "currency":       currency,
            "put_call_ratio": pcr,
            "iv_skew":        iv_skew,
            "sentiment":      sentiment,
            "near_term_iv":   near_term_iv,
        }
    except Exception:
        _log.debug("Deribit skew computation failed for %s", symbol, exc_info=True)
        return {}
