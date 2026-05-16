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
    Return up to max_symbols USDT futures sorted by liquidity.

    Strategy: Binance top-volume futures (reliable, keyless) merged with the
    hand-picked Bitget list.  Bitget's fetch_tickers() returns spot pairs via
    ccxt, not perpetuals, so we rely on Binance for volume-ranked discovery.

    Falls back to _get_default_watchlist() on any error.
    """
    try:
        import ccxt_client
        # Lower threshold to $3M — gives ~200-300 Binance symbols
        binance_syms = ccxt_client.get_binance_futures_symbols(min_vol_usd=min_vol_usd)
        if not binance_syms:
            raise RuntimeError("Binance returned empty list")

        # Merge: Bitget manual list first (preferred), then Binance additions
        bitget_set = set(_BITGET_WATCHLIST)
        extra      = [s for s in binance_syms if s not in bitget_set]
        merged     = list(dict.fromkeys(_BITGET_WATCHLIST + extra))[:max_symbols]
        print(
            f"[Watchlist] {len(merged)} symbols "
            f"(Bitget manual {len(_BITGET_WATCHLIST)} + Binance {len(extra)} extras, "
            f"vol>${min_vol_usd/1e6:.0f}M)",
            flush=True,
        )
        return merged
    except Exception as e:
        print(f"[Watchlist] Extended fetch failed: {e} — using default list")
        return _get_default_watchlist()
