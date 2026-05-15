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
