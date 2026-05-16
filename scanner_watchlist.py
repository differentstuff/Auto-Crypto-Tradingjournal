"""
scanner_watchlist.py — Watchlist symbols for the setup scanner.

Provides the default Bitget watchlist and a lazy-loaded Binance watchlist.
Call _get_default_watchlist() to get the merged, deduplicated list.
"""

_BITGET_WATCHLIST = [
    # BTC / ETH
    "BTCUSDT", "ETHUSDT",
    # Major L1s
    "SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT",
    "DOTUSDT", "ATOMUSDT", "NEARUSDT", "TRXUSDT", "XLMUSDT",
    "TONUSDT", "FTMUSDT", "ALGOUSDT", "EGLDUSDT",
    # Mid-cap L1s
    "SUIUSDT", "APTUSDT", "INJUSDT", "SEIUSDT", "ICPUSDT",
    "STXUSDT", "TIAUSDT", "HBARUSDT", "KASUSDT", "MINAUSDT",
    # L2 / ETH ecosystem
    "MATICUSDT", "ARBUSDT", "OPUSDT", "STRKUSDT", "LDOUSDT",
    "ZKUSDT", "METISUSDT", "ENSUSDT",
    # DeFi
    "UNIUSDT", "AAVEUSDT", "LINKUSDT", "CRVUSDT", "MKRUSDT",
    "SNXUSDT", "COMPUSDT", "DYDXUSDT", "CAKEUSDT", "GMXUSDT",
    "PENDLEUSDT", "JUPUSDT", "SUSHIUSDT", "RUNEUSDT",
    # AI / Infra
    "FETUSDT", "RENDERUSDT", "WLDUSDT", "TAOUSDT", "GRTUSDT",
    "AGIXUSDT", "OCEANUSDT", "ARKMUSDT", "ACTUSDT",
    # Meme
    "DOGEUSDT", "SHIBUSDT", "PEPEUSDT", "WIFUSDT", "BONKUSDT",
    "BOMEUSDT", "FLOKIUSDT", "MOGUSDT", "POPCATUSDT", "MEWUSDT",
    "TURBOUSDT",
    # BTC ecosystem
    "ORDIUSDT", "SATSUSDT",
    # Gaming / Metaverse
    "SANDUSDT", "AXSUSDT", "GALAUSDT", "IMXUSDT", "MANAUSDT",
    "APEUSDT", "YGGUSDT",
    # Solana ecosystem
    "JITOUSDT", "WUSDT", "PYTHUSDT", "RAYUSDT",
    # Other liquid
    "LTCUSDT", "BCHUSDT", "FILUSDT", "QNTUSDT", "VETUSDT",
    "OKBUSDT", "ONDOUSDT", "ZECUSDT", "ONEUSDT", "ROSAUSDT",
    "CELOUSDT", "THETAUSDT", "NEOUSDT", "ONTUSDT", "IOTAUSDT",
    "WOOUSDT", "KLAYUSDT", "GMTUSDT",
]

# BINANCE_WATCHLIST: fetched lazily on first scan to avoid blocking startup.
BINANCE_WATCHLIST: list = []
_binance_watchlist_loaded = False


def _get_default_watchlist() -> list:
    """Return merged Bitget+Binance watchlist, fetching Binance on first call."""
    global BINANCE_WATCHLIST, _binance_watchlist_loaded
    if not _binance_watchlist_loaded:
        _binance_watchlist_loaded = True
        try:
            import ccxt_client as _ccxt
            BINANCE_WATCHLIST = _ccxt.get_binance_futures_symbols()
        except Exception:
            BINANCE_WATCHLIST = []
    return list(dict.fromkeys(
        _BITGET_WATCHLIST + [s for s in BINANCE_WATCHLIST if s not in set(_BITGET_WATCHLIST)]
    ))


DEFAULT_WATCHLIST = _BITGET_WATCHLIST  # backward compat; callers should use _get_default_watchlist()


def _get_extended_watchlist(max_symbols: int = 500, min_vol_usd: float = 3_000_000) -> list:
    """
    Fetch top Bitget USDT-M linear perpetuals sorted by 24h quote volume.
    Falls back to _get_default_watchlist() on any error.
    """
    try:
        import ccxt
        bitget = ccxt.bitget({"enableRateLimit": True})
        tickers = bitget.fetch_tickers()
        candidates = []
        for sym, t in tickers.items():
            if not sym.endswith("/USDT:USDT"):
                continue
            vol = t.get("quoteVolume") or 0
            if vol >= min_vol_usd:
                candidates.append((sym.replace("/USDT:USDT", "USDT"), vol))
        candidates.sort(key=lambda x: -x[1])
        result = [s[0] for s in candidates[:max_symbols]]
        print(f"[Watchlist] {len(result)} Bitget futures by volume (>${min_vol_usd/1e6:.0f}M 24h)", flush=True)
        return result
    except Exception as e:
        print(f"[Watchlist] Extended fetch failed: {e} — using default list")
        return _get_default_watchlist()
