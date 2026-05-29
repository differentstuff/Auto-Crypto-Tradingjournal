"""
core/exchange.py -- Unified CCXT exchange wrapper.

Provides a single interface for all exchange operations:
  - OHLCV data fetching (public, no auth required)
  - Account balance and position queries (authenticated)
  - Order placement and closing (authenticated, guarded in paper mode)

Credentials come from ConfigLoader (which reads exchange.yaml).
The daemon strips secrets before passing config to the substrate;
enzymes that need exchange access receive the Exchange instance directly.

Port of: ccxt_client.py, bitget_client.py (unified into one wrapper)
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import pandas as pd

_log = logging.getLogger(__name__)


class ExchangeError(Exception):
    """Custom exception for exchange operation failures."""
    pass


# Symbol format conversion: "BTCUSDT" -> "BTC/USDT:USDT" (CCXT futures)
def _to_ccxt_symbol(symbol: str) -> str:
    """Convert journal symbol format to CCXT futures format."""
    return symbol.replace("USDT", "/USDT:USDT")


def _to_journal_symbol(ccxt_symbol: str) -> str:
    """Convert CCXT futures symbol format to journal format."""
    return ccxt_symbol.replace("/USDT:USDT", "USDT")


class Exchange:
    """
    Unified CCXT exchange wrapper.

    Reads credentials from ConfigLoader and provides methods for:
      - fetch_ohlcv(): OHLCV candle data (DataFrame)
      - fetch_balance(): account equity and margin
      - fetch_positions(): open positions from exchange
      - place_order(): create a new order (paper mode guarded)
      - close_position(): close an existing position (paper mode guarded)

    In paper mode, order methods log and return mock data instead of
    calling the exchange API. Data fetching (OHLCV, tickers) always
    works — it uses public endpoints.
    """

    def __init__(self, config_loader):
        """
        Initialize exchange from ConfigLoader.

        Args:
            config_loader: ConfigLoader instance with exchange.yaml loaded.
        """
        self._config = config_loader
        self._primary: str = config_loader.get("exchange.primary", "bitget")
        self._data_source: str = config_loader.get("exchange.data_source", "bitget")
        self._fallback: str = config_loader.get("exchange.fallback", "bybit")
        self._paper_mode: bool = config_loader.paper_mode

        # Lazy-initialized exchange instances
        self._data_exchange = None  # For market data (public, from data_source)
        self._trade_exchange = None  # For trading (Bitget/Bybit authenticated)
        self._fallback_exchange = None  # For fallback ticker data (public, from fallback)

        _log.info(
            "Exchange initialized: primary=%s, data_source=%s, paper=%s",
            self._primary, self._data_source, self._paper_mode,
        )

    @property
    def paper_mode(self) -> bool:
        return self._paper_mode

    # --- Static symbol conversion (used by tests and other modules) -----------

    @staticmethod
    def to_ccxt_symbol(symbol: str) -> str:
        """Convert journal symbol format to CCXT futures format."""
        return _to_ccxt_symbol(symbol)

    @staticmethod
    def to_journal_symbol(ccxt_symbol: str) -> str:
        """Convert CCXT futures symbol format to journal format."""
        return _to_journal_symbol(ccxt_symbol)

    # --- Data Exchange (public, no auth) ----------------------------------------

    def _get_data_exchange(self):
        """Get or create the data source exchange (public, configurable via data_source)."""
        if self._data_exchange is None:
            import ccxt
            exchange_id = self._data_source
            exchange_class = getattr(ccxt, exchange_id, None)
            if exchange_class is None:
                fallback_id = self._data_source
                _log.warning("Unknown data exchange %s, falling back to %s", exchange_id, fallback_id)
                exchange_class = getattr(ccxt, fallback_id, ccxt.binance)
                exchange_id = fallback_id

            self._data_exchange = exchange_class()
            self._data_exchange.enableRateLimit = True
            self._data_exchange.options['defaultType'] = 'future'
            _log.info("Data exchange created: %s (public)", exchange_id)

        return self._data_exchange

    # --- Trade Exchange (authenticated) -----------------------------------------

    def _get_trade_exchange(self):
        """Get or create the primary trading exchange (authenticated)."""
        if self._trade_exchange is None:
            import ccxt
            exchange_id = self._primary
            exchange_class = getattr(ccxt, exchange_id, None)
            if exchange_class is None:
                _log.error("Unknown trade exchange %s", exchange_id)
                raise ValueError(f"Unsupported exchange: {exchange_id}")

            creds = self._config.get_exchange_creds(exchange_id)
            kwargs = {
                "apiKey": creds.get("api_key", ""),
                "secret": creds.get("secret_key", ""),
            }
            # Bitget requires passphrase
            if exchange_id == "bitget" and creds.get("passphrase"):
                kwargs["password"] = creds["passphrase"]
            # Blofin requires passphrase
            if exchange_id == "blofin" and creds.get("passphrase"):
                kwargs["password"] = creds["passphrase"]

            sandbox = creds.get("sandbox", False)

            self._trade_exchange = exchange_class(**kwargs)
            self._trade_exchange.enableRateLimit = True
            self._trade_exchange.options['defaultType'] = 'future'
            if sandbox:
                self._trade_exchange.options['sandboxMode'] = True
            _log.info("Trade exchange created: %s (auth=%s)", exchange_id, bool(creds.get("api_key")))

        return self._trade_exchange

    # --- OHLCV Data ------------------------------------------------------------

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "4h",
        limit: int = 200,
    ) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV candle data and return as pandas DataFrame.

        Uses the data_source exchange (public, from config).
        No authentication required — OHLCV is a public endpoint on all exchanges.

        Args:
            symbol: Journal format symbol (e.g. "BTCUSDT")
            timeframe: Candle timeframe (e.g. "4h", "1h")
            limit: Number of candles to fetch

        Returns:
            DataFrame with columns: ts, open, high, low, close, volume
            Index: datetime. Returns None on error.
        """
        ccxt_symbol = _to_ccxt_symbol(symbol)
        exchange = self._get_data_exchange()
        timeframe = timeframe.lower()  # Guarantee lowercase letters for API calls

        try:
            raw = exchange.fetch_ohlcv(ccxt_symbol, timeframe, limit=limit)
            if not raw:
                _log.warning("No OHLCV data returned for %s %s", symbol, timeframe)
                return None

            df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
            df.index = pd.to_datetime(df["ts"], unit="ms")

            if len(df) < 30:
                _log.warning("Insufficient data for %s %s: %d bars", symbol, timeframe, len(df))

            return df

        except Exception as e:
            _log.error("fetch_ohlcv failed for %s %s: %s", symbol, timeframe, e)
            # Try fallback exchange
            timeframe = timeframe.lower()  # Guarantee lowercase letters for API calls
            return self._fetch_ohlcv_fallback(symbol, timeframe, limit)

    def _fetch_ohlcv_fallback(
        self,
        symbol: str,
        timeframe: str = "4h",
        limit: int = 200,
    ) -> Optional[pd.DataFrame]:
        """Try fetching OHLCV from the fallback exchange."""
        import ccxt

        ccxt_symbol = _to_ccxt_symbol(symbol)
        fallback_id = self._fallback

        try:
            exchange_class = getattr(ccxt, fallback_id, None)
            if exchange_class is None:
                return None

            exchange = exchange_class()
            exchange.enableRateLimit = True
            exchange.options['defaultType'] = 'future'
            raw = exchange.fetch_ohlcv(ccxt_symbol, timeframe, limit=limit)
            if not raw:
                return None

            df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
            df.index = pd.to_datetime(df["ts"], unit="ms")
            return df

        except Exception as e:
            _log.error("Fallback fetch_ohlcv also failed for %s: %s", symbol, e)
            return None

    # --- Account Data ----------------------------------------------------------

    def fetch_balance(self) -> dict:
        """
        Fetch account balance from the primary exchange.

        Returns dict with: equity, available, total_margin
        """
        if self._paper_mode:
            _log.info("Paper mode: skipping fetch_balance")
            return {}

        try:
            exchange = self._get_trade_exchange()
            balance = exchange.fetch_balance()

            usdt = balance.get("USDT", {})
            return {
                "equity": float(usdt.get("total", 0)),
                "available": float(usdt.get("free", 0)),
                "total_margin": float(usdt.get("used", 0)),
            }
        except Exception as e:
            _log.error("fetch_balance failed: %s", e)
            return {}

    def fetch_positions(self) -> list:
        """
        Fetch open positions from the primary exchange.

        Returns list of dicts with normalized fields:
          symbol, direction, entry_price, mark_price, size_usdt,
          unrealized_pnl, unrealized_pct, leverage, sl_price, tp_price
        """
        if self._paper_mode:
            _log.info("Paper mode: skipping fetch_positions")
            return []

        try:
            exchange = self._get_trade_exchange()
            positions = exchange.fetch_positions()

            result = []
            for p in positions:
                contracts = float(p.get("contracts", 0) or 0)
                if contracts == 0:
                    continue

                symbol = _to_journal_symbol(p.get("symbol", ""))
                side = p.get("side", "")
                direction = "Long" if side == "long" else "Short"
                entry_price = float(p.get("entryPrice", 0) or 0)
                mark_price = float(p.get("markPrice", 0) or p.get("notional", 0) or 0)
                notional = float(p.get("notional", 0) or 0)
                unrealized_pnl = float(p.get("unrealizedPnl", 0) or 0)
                leverage = float(p.get("leverage", 1) or 1)

                unrealized_pct = 0.0
                if notional and entry_price:
                    unrealized_pct = (unrealized_pnl / notional) * 100

                result.append({
                    "symbol": symbol,
                    "direction": direction,
                    "entry_price": entry_price,
                    "mark_price": mark_price,
                    "size_usdt": round(abs(notional), 2),
                    "unrealized_pnl": round(unrealized_pnl, 4),
                    "unrealized_pct": round(unrealized_pct, 2),
                    "leverage": leverage,
                    "sl_price": 0.0,
                    "tp_price": 0.0,
                })

            return result

        except Exception as e:
            _log.error("fetch_positions failed: %s", e)
            return []

    # --- Order Methods (paper mode guarded) ------------------------------------

    def place_order(
        self,
        symbol: str,
        direction: str,
        size_usdt: float,
        entry_price: float = None,
        sl_price: float = None,
        tp_price: float = None,
        leverage: int = None,
    ) -> Optional[dict]:
        """
        Place a market or limit order.

        In paper mode: logs the order and returns mock data.
        In live mode: calls the exchange API.

        leverage defaults to portfolio.leverage from config if not passed.

        Returns dict with: order_id, symbol, direction, size_usdt, status
        """
        if leverage is None:
            leverage = self._config.get("portfolio", {}).get("leverage")
        if not leverage:
            _log.error("No leverage configured for %s — check portfolio.leverage in config", symbol)
            return None
        if self._paper_mode:
            _log.info(
                "PAPER ORDER: %s %s size=%.2f entry=%s sl=%s tp=%s",
                direction, symbol, size_usdt, entry_price, sl_price, tp_price,
            )
            return {
                "order_id": f"paper-{symbol}-{direction.lower()}",
                "symbol": symbol,
                "direction": direction,
                "size_usdt": size_usdt,
                "status": "paper_filled",
            }

        try:
            exchange = self._get_trade_exchange()
            ccxt_symbol = _to_ccxt_symbol(symbol)
            side = "buy" if direction.lower() == "long" else "sell"

            # Set leverage before placing order
            try:
                exchange.set_leverage(leverage, ccxt_symbol)
            except Exception as e:
                _log.warning("Could not set leverage for %s: %s", symbol, e)

            # Place market order
            order = exchange.create_market_order(
                symbol=ccxt_symbol,
                side=side,
                amount=size_usdt / (entry_price or 1),
            )

            _log.info(
                "LIVE ORDER placed: %s %s size=%.2f order_id=%s",
                direction, symbol, size_usdt, order.get("id", "?"),
            )

            return {
                "order_id": order.get("id", ""),
                "symbol": symbol,
                "direction": direction,
                "size_usdt": size_usdt,
                "status": order.get("status", "unknown"),
            }

        except Exception as e:
            _log.error("place_order failed for %s: %s", symbol, e)
            return None

    def place_market_order(
        self,
        symbol: str,
        side: str = None,
        direction: str = None,
        size_usdt: float = 0,
        leverage: int = None,
    ) -> Optional[dict]:
        """
        Place a market order (convenience wrapper).

        Accepts both 'side' (buy/sell) and 'direction' (Long/Short) params.
        In paper mode: logs and returns mock data with paper=True.
        In live mode: calls the exchange API.

        leverage defaults to portfolio.leverage from config if not passed.

        Returns dict with: order_id, symbol, side/direction, size_usdt, status, paper
        Raises: ExchangeError on live mode failure.
        """
        if leverage is None:
            leverage = self._config.get("portfolio", {}).get("leverage")
        if not leverage:
            raise ExchangeError(f"No leverage configured for {symbol} — check portfolio.leverage in config")
        # Normalize: accept both 'side' and 'direction'
        if direction is None and side is not None:
            direction = "Long" if side.lower() == "buy" else "Short"
        if side is None and direction is not None:
            side = "buy" if direction.lower() == "long" else "sell"

        if self._paper_mode:
            _log.info(
                "PAPER MARKET ORDER: %s %s size=%.2f leverage=%d",
                direction, symbol, size_usdt, leverage,
            )
            return {
                "order_id": f"paper-{symbol}-{direction.lower()}",
                "symbol": symbol,
                "side": side,
                "direction": direction,
                "size_usdt": size_usdt,
                "status": "paper_filled",
                "paper": True,
            }

        try:
            exchange = self._get_trade_exchange()
            ccxt_symbol = _to_ccxt_symbol(symbol)

            # Set leverage before placing order
            try:
                exchange.set_leverage(leverage, ccxt_symbol)
            except Exception as e:
                _log.warning("Could not set leverage for %s: %s", symbol, e)

            order = exchange.create_market_order(
                symbol=ccxt_symbol,
                side=side,
                amount=size_usdt,
            )

            return {
                "order_id": order.get("id", ""),
                "symbol": symbol,
                "side": side,
                "direction": direction,
                "size_usdt": size_usdt,
                "status": order.get("status", "unknown"),
                "paper": False,
            }

        except Exception as e:
            _log.error("place_market_order failed for %s: %s", symbol, e)
            raise ExchangeError(f"Market order failed for {symbol}: {e}")

    def place_stop_order(
        self,
        symbol: str,
        side: str = None,
        direction: str = None,
        trigger_price: float = None,
        stop_price: float = None,
        size: float = None,
        size_usdt: float = None,
        sl_price: float = None,
        tp_price: float = None,
    ) -> Optional[dict]:
        """
        Place a stop/trigger order (SL or TP).

        Accepts both 'side' (buy/sell) and 'direction' (Long/Short).
        Accepts both 'trigger_price' and 'stop_price' as the trigger level.
        Accepts both 'size' and 'size_usdt' for the order amount.

        In paper mode: logs and returns mock data with paper=True.
        In live mode: calls the exchange API.

        Returns dict with: order_id, symbol, status, paper
        """
        # Normalize params
        effective_trigger = trigger_price or stop_price or 0.0
        effective_size = size_usdt or size or 0.0
        if direction is None and side is not None:
            direction = "Long" if side.lower() == "buy" else "Short"
        if side is None and direction is not None:
            side = "buy" if direction.lower() == "long" else "sell"

        if self._paper_mode:
            _log.info(
                "PAPER STOP ORDER: %s %s trigger=%.2f sl=%s tp=%s",
                direction, symbol, effective_trigger, sl_price, tp_price,
            )
            return {
                "order_id": f"paper-stop-{symbol}",
                "symbol": symbol,
                "side": side,
                "direction": direction,
                "status": "paper_pending",
                "paper": True,
            }

        try:
            exchange = self._get_trade_exchange()
            ccxt_symbol = _to_ccxt_symbol(symbol)
            side = "sell" if direction.lower() == "long" else "buy"

            params = {}
            if sl_price:
                params["stopLossPrice"] = sl_price
            if tp_price:
                params["takeProfitPrice"] = tp_price

            order = exchange.create_order(
                symbol=ccxt_symbol,
                type="stop_market",
                side=side,
                amount=size_usdt,
                price=trigger_price,
                params=params,
            )

            return {
                "order_id": order.get("id", ""),
                "symbol": symbol,
                "status": order.get("status", "unknown"),
            }

        except Exception as e:
            _log.error("place_stop_order failed for %s: %s", symbol, e)
            raise ExchangeError(f"Stop order failed for {symbol}: {e}")

    def cancel_orders(self, symbol: str) -> bool:
        """
        Cancel all open orders for a symbol.

        In paper mode: no-op, returns True.
        In live mode: calls the exchange API, returns True on success.

        Returns: True if successful
        """
        if self._paper_mode:
            _log.info("PAPER CANCEL: %s (no-op)", symbol)
            return True

        try:
            exchange = self._get_trade_exchange()
            ccxt_symbol = _to_ccxt_symbol(symbol)
            orders = exchange.cancel_all_orders(ccxt_symbol)
            return {
                "symbol": symbol,
                "cancelled_count": len(orders) if isinstance(orders, list) else 0,
            }
        except Exception as e:
            _log.error("cancel_orders failed for %s: %s", symbol, e)
            return {"symbol": symbol, "cancelled_count": 0}

    def close_position(
        self,
        symbol: str,
        direction: str,
        size: float = None,
        size_usdt: float = None,
    ) -> bool:
        """
        Close an open position.

        In paper mode: logs and returns True.
        In live mode: calls the exchange API, returns True on success.

        Returns: True if successful
        """
        if self._paper_mode:
            _log.info("PAPER CLOSE: %s %s", direction, symbol)
            return True

        try:
            exchange = self._get_trade_exchange()
            ccxt_symbol = _to_ccxt_symbol(symbol)
            side = "sell" if direction.lower() == "long" else "buy"

            # Close by placing opposite market order
            positions = exchange.fetch_positions([ccxt_symbol])
            for p in positions:
                if float(p.get("contracts", 0) or 0) > 0:
                    amount = float(p["contracts"])
                    order = exchange.create_market_order(
                        symbol=ccxt_symbol,
                        side=side,
                        amount=amount,
                        params={"reduceOnly": True},
                    )
                    _log.info("LIVE CLOSE: %s %s order_id=%s", direction, symbol, order.get("id", "?"))
                    return {
                        "order_id": order.get("id", ""),
                        "symbol": symbol,
                        "status": order.get("status", "unknown"),
                    }

            _log.warning("No open position found to close for %s", symbol)
            return None

        except Exception as e:
            _log.error("close_position failed for %s: %s", symbol, e)
            return None

    # --- Utility ---------------------------------------------------------------

    def fetch_tickers(self, symbols: list) -> dict:
        """
        Fetch current ticker prices for multiple symbols in one call.

        Uses CCXT's fetch_tickers() for bulk fetching, which is more efficient
        than calling fetch_ticker() for each symbol individually.
        Falls back to individual fetch_ticker() calls if bulk fetch fails.

        NEVER returns stale or imaginary data — only real market prices.
        If both primary and fallback exchanges fail for a symbol, that symbol
        will not appear in the result dict.

        Args:
            symbols: List of journal format symbols (e.g. ["BTCUSDT", "ETHUSDT"])

        Returns:
            Dict of {symbol: {"last": float, "bid": float, "ask": float, "timestamp": int}}
            Only includes symbols for which real data was obtained.
        """
        if not symbols:
            return {}

        results = {}
        ccxt_symbols = [_to_ccxt_symbol(s) for s in symbols]
        symbol_map = {ccxt: jour for ccxt, jour in zip(ccxt_symbols, symbols)}

        # Try bulk fetch on primary exchange
        try:
            exchange = self._get_data_exchange()
            tickers = exchange.fetch_tickers(ccxt_symbols)
            for ccxt_sym, ticker in tickers.items():
                jour_sym = symbol_map.get(ccxt_sym)
                if jour_sym and ticker.get("last"):
                    results[jour_sym] = {
                        "symbol": jour_sym,
                        "last": float(ticker["last"]),
                        "bid": float(ticker.get("bid", ticker["last"])),
                        "ask": float(ticker.get("ask", ticker["last"])),
                        "timestamp": ticker.get("timestamp", 0),
                    }
        except Exception as e:
            _log.warning("Bulk ticker fetch failed: %s — trying individual fetches", e)
            # Fallback: try individual fetch_ticker() for each symbol
            # This also tries the fallback exchange per symbol
            for symbol in symbols:
                ticker = self.fetch_ticker(symbol)
                if ticker:
                    results[symbol] = ticker

        # Log any symbols we couldn't get data for
        missing = [s for s in symbols if s not in results]
        if missing:
            _log.warning("No real price data for %d symbols: %s", len(missing), missing)

        return results

    def fetch_ticker(self, symbol: str) -> Optional[dict]:
        """
        Fetch current ticker price for a symbol.

        Uses the data_source exchange (public, no auth required).
        Returns dict with: symbol, last, bid, ask, timestamp.
        Returns None on error.

        Args:
            symbol: Journal format symbol (e.g. "BTCUSDT")
        """
        ccxt_symbol = _to_ccxt_symbol(symbol)
        exchange = self._get_data_exchange()

        try:
            ticker = exchange.fetch_ticker(ccxt_symbol)
            if ticker and ticker.get("last"):
                return {
                    "symbol": symbol,
                    "last": float(ticker["last"]),
                    "bid": float(ticker.get("bid", ticker["last"])),
                    "ask": float(ticker.get("ask", ticker["last"])),
                    "timestamp": ticker.get("timestamp", 0),
                }
            _log.warning("No ticker data returned for %s", symbol)
            return None
        except Exception as e:
            _log.error("fetch_ticker failed for %s: %s", symbol, e)
            return None

    def fetch_usdt_perps(self) -> List[Dict]:
        """
        Fetch all USDT-M perpetual futures from the data source exchange.

        Returns a list of dicts, each with:
          - symbol: journal format (e.g. "BTCUSDT")
          - volume_24h_usd: 24h quote volume in USD (0.0 if unavailable)
          - open_interest_usd: open interest in USD (0.0 if unavailable)

        Uses CCXT's fetch_markets() to get the full instrument list, then
        filters for USDT-settled perpetual swaps. Volume and OI come from
        the market info dict if available (exchange-dependent).

        No authentication required — market metadata is public.
        """
        try:
            exchange = self._get_data_exchange()
            markets = exchange.fetch_markets()

            result = []
            for market in markets:
                # Filter: only USDT-settled perpetual swaps
                if market.get("type") != "swap":
                    continue
                settle = market.get("settle", "") or ""
                if settle.upper() != "USDT":
                    continue

                # Symbol in journal format
                ccxt_sym = market.get("symbol", "")
                jour_sym = _to_journal_symbol(ccxt_sym)

                # Extract volume and OI from market info (exchange-dependent fields)
                info = market.get("info", {})
                volume_24h = 0.0
                open_interest = 0.0

                # Bitget-specific fields
                if "volume24h" in info:
                    try:
                        volume_24h = float(info["volume24h"]) * float(market.get("last", 0) or 1)
                    except (ValueError, TypeError):
                        pass
                if "openInterest" in info:
                    try:
                        oi_contracts = float(info["openInterest"])
                        contract_size = float(market.get("contractSize", 1) or 1)
                        last_price = float(market.get("last", 0) or 1)
                        open_interest = oi_contracts * contract_size * last_price
                    except (ValueError, TypeError):
                        pass

                # Generic CCXT fields as fallback
                if volume_24h == 0.0 and market.get("quoteVolume"):
                    try:
                        volume_24h = float(market["quoteVolume"])
                    except (ValueError, TypeError):
                        pass

                result.append({
                    "symbol": jour_sym,
                    "volume_24h_usd": volume_24h,
                    "open_interest_usd": open_interest,
                })

            _log.info("Fetched %d USDT-M perpetual pairs from exchange", len(result))
            return result

        except Exception as e:
            _log.error("fetch_usdt_perps failed: %s", e)
            return []

    def test_connection(self) -> dict:
        """
        Test exchange connectivity.

        Tests data exchange (public) and, if not in paper mode,
        trade exchange (authenticated).

        Returns dict with: data_ok, trade_ok, primary, data_source
        """
        result = {
            "data_ok": False,
            "trade_ok": False,
            "primary": self._primary,
            "data_source": self._data_source,
            "paper_mode": self._paper_mode,
        }

        # Test data exchange
        try:
            df = self.fetch_ohlcv("BTCUSDT", "1h", limit=5)
            result["data_ok"] = df is not None and len(df) >= 3
        except Exception as e:
            _log.warning("Data exchange test failed: %s", e)

        # Test trade exchange (only if not paper mode and credentials exist)
        if not self._paper_mode:
            try:
                balance = self.fetch_balance()
                result["trade_ok"] = bool(balance)
            except Exception as e:
                _log.warning("Trade exchange test failed: %s", e)
        else:
            result["trade_ok"] = True  # Paper mode = always OK

        return result