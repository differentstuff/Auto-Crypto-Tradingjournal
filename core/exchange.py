"""
core/exchange.py -- Unified CCXT exchange wrapper.

Provides a single interface for all exchange operations:
  - OHLCV data fetching (public, no auth required for Binance)
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
        self._data_source: str = config_loader.get("exchange.data_source", "binance")
        self._fallback: str = config_loader.get("exchange.fallback", "bybit")
        self._paper_mode: bool = config_loader.paper_mode

        # Lazy-initialized exchange instances
        self._data_exchange = None  # For market data (Binance public)
        self._trade_exchange = None  # For trading (Bitget/Bybit authenticated)

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
        """Get or create the data source exchange (Binance public by default)."""
        if self._data_exchange is None:
            import ccxt
            exchange_id = self._data_source
            exchange_class = getattr(ccxt, exchange_id, None)
            if exchange_class is None:
                _log.warning("Unknown data exchange %s, falling back to binance", exchange_id)
                exchange_class = ccxt.binance
                exchange_id = "binance"

            kwargs = {"enableRateLimit": True}
            if exchange_id == "binance":
                kwargs["options"] = {"defaultType": "future"}

            self._data_exchange = exchange_class(kwargs)
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
                "enableRateLimit": True,
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
            if sandbox:
                kwargs["options"] = {"sandboxMode": True}

            self._trade_exchange = exchange_class(kwargs)
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

        Uses the data_source exchange (Binance public by default).
        No authentication required.

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

            kwargs = {"enableRateLimit": True}
            if fallback_id == "bybit":
                kwargs["options"] = {"defaultType": "linear"}

            exchange = exchange_class(kwargs)
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
        leverage: int = 5,
    ) -> Optional[dict]:
        """
        Place a market or limit order.

        In paper mode: logs the order and returns mock data.
        In live mode: calls the exchange API.

        Returns dict with: order_id, symbol, direction, size_usdt, status
        """
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
        leverage: int = 5,
    ) -> Optional[dict]:
        """
        Place a market order (convenience wrapper).

        Accepts both 'side' (buy/sell) and 'direction' (Long/Short) params.
        In paper mode: logs and returns mock data with paper=True.
        In live mode: calls the exchange API.

        Returns dict with: order_id, symbol, side/direction, size_usdt, status, paper
        Raises: ExchangeError on live mode failure.
        """
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