"""
grok_client.py — xAI Grok social intelligence for crypto call analysis.

Grok has real-time X (Twitter) access, surfacing social sentiment and news
that technical indicators cannot see. Most valuable for small/micro-cap coins
where social narrative drives short-term price action.

Weight by market cap (grok_weight):
  > $5B   large cap  → 0.00  (skip — well-covered, social noise > signal)
  $1B–$5B mid-large  → 0.15  (light supplementary context)
  $200M–$1B  small   → 0.40  (meaningful social signal)
  < $200M micro-cap  → 0.80  (primary intelligence source for obscure coins)
  unknown            → 0.60  (assume small-cap until market cap is known)

API: xAI Responses API — https://api.x.ai/v1/responses
Model: env GROK_MODEL (default grok-3-fast). Set to grok-4.20-reasoning for depth.
Config: GROK_API_KEY in .env
"""

import json
import os
import threading
import time
import urllib.request
from typing import Optional

GROK_API_KEY = os.environ.get("GROK_API_KEY", "")
GROK_BASE    = "https://api.x.ai/v1"
GROK_MODEL   = os.environ.get("GROK_MODEL", "grok-3-fast")
_TIMEOUT     = 25   # seconds — reasoning models can be slow

# ── Caches ─────────────────────────────────────────────────────────────────────

_ctx_cache: dict  = {}   # (base, direction) → (ts, text, weight)
_ctx_lock         = threading.Lock()
_CTX_TTL          = 1800  # 30 min

_mc_cache: dict   = {}   # base → (ts, market_cap_usd)
_mc_lock          = threading.Lock()
_MC_TTL           = 86400  # 24 h

# ── Symbol helpers ─────────────────────────────────────────────────────────────

_LARGE_CAPS = frozenset({
    "BTC", "BTCUSDT", "ETH", "ETHUSDT", "BNB", "BNBUSDT",
    "XRP", "XRPUSDT", "SOL", "SOLUSDT", "ADA", "ADAUSDT",
    "DOGE", "DOGEUSDT", "AVAX", "AVAXUSDT", "TRX", "TRXUSDT",
    "SUI", "SUIUSDT", "TON", "TONUSDT", "SHIB", "SHIBUSDT",
})

# CoinGecko IDs that differ from lowercase ticker
_CG_IDS: dict[str, str] = {
    "BTC": "bitcoin",     "ETH": "ethereum",    "BNB": "binancecoin",
    "SOL": "solana",      "XRP": "ripple",       "ADA": "cardano",
    "DOGE": "dogecoin",   "DOT": "polkadot",     "AVAX": "avalanche-2",
    "LINK": "chainlink",  "MATIC": "matic-network", "UNI": "uniswap",
    "ATOM": "cosmos",     "LTC": "litecoin",     "NEAR": "near",
    "SUI": "sui",         "TON": "the-open-network",
    "PEPE": "pepe",       "WIF": "dogwifcoin",   "BONK": "bonk",
}


def is_configured() -> bool:
    return bool(GROK_API_KEY)


def send_text(prompt: str, system: str = None,
              max_tokens: int = 2048, model: str = None) -> str | None:
    """
    Generic chat-completion shim — uses X.AI's OpenAI-compatible
    /chat/completions endpoint so this client can be used in the cascade
    alongside Cerebras, Groq, OpenRouter, etc. Defaults to grok-3.
    """
    from openai_compat_client import chat_completion
    return chat_completion(
        base_url=GROK_BASE,
        api_key=GROK_API_KEY,
        model=model or "grok-3",
        prompt=prompt,
        system=system,
        max_tokens=max_tokens,
    )


def _base(symbol: str) -> str:
    """Strip exchange suffixes: ARKMUSTDT → ARKM."""
    return symbol.upper().replace("USDT", "").replace("PERP", "").replace("USD", "").strip()


# ── Weight formula ─────────────────────────────────────────────────────────────

def grok_weight(market_cap_usd: Optional[float]) -> float:
    """Return Grok context weight (0.0–0.80). Smaller cap → higher weight."""
    if market_cap_usd is None:
        return 0.60
    if market_cap_usd > 5_000_000_000:
        return 0.0
    if market_cap_usd > 1_000_000_000:
        return 0.15
    if market_cap_usd > 200_000_000:
        return 0.40
    return 0.80


# ── Market cap lookup ──────────────────────────────────────────────────────────

def _get_market_cap(base: str) -> Optional[float]:
    """Fetch market cap from CoinGecko (cached 24 h). Returns None on failure."""
    if base in _LARGE_CAPS:
        return 10_000_000_000  # hard-code large cap; weight=0.0, no Grok call

    with _mc_lock:
        entry = _mc_cache.get(base)
        if entry and (time.time() - entry[0]) < _MC_TTL:
            return entry[1]

    cg_id = _CG_IDS.get(base, base.lower())
    try:
        url = (
            "https://api.coingecko.com/api/v3/simple/price"
            f"?ids={cg_id}&vs_currencies=usd&include_market_cap=true"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "TradingJournal/1.0.1"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        mc = data.get(cg_id, {}).get("usd_market_cap")
        if mc:
            mc = float(mc)
            with _mc_lock:
                _mc_cache[base] = (time.time(), mc)
            return mc
    except Exception:
        pass
    return None


# ── xAI API ────────────────────────────────────────────────────────────────────

def _call_grok(prompt: str) -> str:
    """POST to xAI Responses API. Returns response text or '' on failure."""
    if not GROK_API_KEY:
        return ""
    payload = json.dumps({"model": GROK_MODEL, "input": prompt}).encode()
    req = urllib.request.Request(f"{GROK_BASE}/responses", data=payload)
    req.add_header("Content-Type",  "application/json")
    req.add_header("Authorization", f"Bearer {GROK_API_KEY}")
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            resp = json.loads(r.read())
        # xAI Responses API format: output[] → type=message → content[] → output_text
        for item in resp.get("output", []):
            if item.get("type") == "message":
                for part in item.get("content", []):
                    if part.get("type") == "output_text":
                        return part.get("text", "").strip()
        # Fallback: OpenAI-compatible chat/completions format
        if "choices" in resp:
            return resp["choices"][0].get("message", {}).get("content", "").strip()
    except Exception as exc:
        print(f"[Grok] API error: {exc}", flush=True)
    return ""


# ── Public API ─────────────────────────────────────────────────────────────────

def get_coin_context(
    symbol: str,
    direction: str = "Long",
    market_cap_usd: Optional[float] = None,
) -> tuple[str, float]:
    """
    Fetch Grok social intelligence for a coin.

    Returns:
        (context_text, weight)  — both empty/0.0 when Grok is skipped.

    Caches responses 30 min. Thread-safe.
    """
    if not GROK_API_KEY:
        return "", 0.0

    base = _base(symbol)
    if base in _LARGE_CAPS:
        return "", 0.0

    mc     = market_cap_usd if market_cap_usd is not None else _get_market_cap(base)
    weight = grok_weight(mc)
    if weight < 0.10:
        return "", 0.0

    cache_key = f"{base}_{direction}"
    with _ctx_lock:
        entry = _ctx_cache.get(cache_key)
        if entry and (time.time() - entry[0]) < _CTX_TTL:
            return entry[1], entry[2]

    # Build the intelligence prompt
    mc_str   = f"${mc/1e6:.0f}M market cap" if mc else "market cap unknown"
    cap_tier = (
        "micro-cap" if (mc or 0) < 200_000_000 else
        "small-cap" if (mc or 0) < 1_000_000_000 else
        "mid-cap"
    )
    prompt = (
        f"Crypto futures intelligence brief for ${base} ({mc_str}, {cap_tier}).\n\n"
        f"A trader is evaluating a {direction} futures position. "
        f"Provide a 100–130 word brief covering:\n"
        f"1. X/Twitter sentiment this week — overall bias (bullish/bearish/mixed) "
        f"and dominant narratives\n"
        f"2. Recent news or developments (last 7 days): listings, partnerships, "
        f"protocol updates, exploits, FUD\n"
        f"3. Social quality: organic analysis vs coordinated hype/shill?\n"
        f"4. Key risk: biggest social or news threat to a {direction} trade right now\n\n"
        f"Be direct. Skip generic crypto market commentary. "
        f"Flag any red flags explicitly with ⚠."
    )

    text = _call_grok(prompt)
    if not text:
        return "", 0.0

    with _ctx_lock:
        _ctx_cache[cache_key] = (time.time(), text, weight)

    return text, weight
