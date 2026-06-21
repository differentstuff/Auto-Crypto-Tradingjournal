"""
core/exchange.py -- Unified CCXT exchange wrapper.

Provides a single interface for all exchange operations:
  - OHLCV data fetching (public, no auth required)
  - Account balance and position queries (authenticated)
  - Order placement and closing (authenticated, guarded in paper mode)
  - SL/TP order management (place-tpsl-order, modify-tpsl-order)
  - Native trailing stop (track_plan)

Credentials come from ConfigLoader (which reads exchange.yaml).
The daemon strips secrets before passing config to the substrate;
enzymes that need exchange access receive the Exchange instance directly.

Exchange-as-truth architecture:
  - fetch_positions() returns ALL fields needed for reconciliation
  - SL/TP are pushed to exchange at trade open
  - Trailing stop updates are pushed via modify-tpsl-order
  - Native trailing stop (track_plan) activates after TP1

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
      - fetch_positions(): open positions from exchange (with reconciliation fields)
      - place_order(): create a new order with preset SL/TP (paper mode guarded)
      - place_tpsl_order(): place TP/SL order (partial TP1, native trailing)
      - modify_tpsl_order(): modify existing TP/SL order (trailing stop updates)
      - place_trailing_stop(): place native trailing stop (daemon-offline backup)
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
        since: Optional[int] = None,
    ) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV candle data and return as pandas DataFrame.

        Uses the data_source exchange (public, from config).
        No authentication required — OHLCV is a public endpoint on all exchanges.

        Args:
            symbol: Journal format symbol (e.g. "BTCUSDT")
            timeframe: Candle timeframe (e.g. "4h", "1h")
            limit: Number of candles to fetch
            since: Timestamp in ms for historical queries (default: None = most recent)

        Returns:
            DataFrame with columns: ts, open, high, low, close, volume
            Index: datetime. Returns None on error.
        """
        ccxt_symbol = _to_ccxt_symbol(symbol)
        exchange = self._get_data_exchange()
        timeframe = timeframe.lower()  # Guarantee lowercase letters for API calls

        try:
            raw = exchange.fetch_ohlcv(ccxt_symbol, timeframe, since=since, limit=limit)
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
            return self._fetch_ohlcv_fallback(symbol, timeframe, limit, since=since)

    def _fetch_ohlcv_fallback(
        self,
        symbol: str,
        timeframe: str = "4h",
        limit: int = 200,
        since: Optional[int] = None,
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
            raw = exchange.fetch_ohlcv(ccxt_symbol, timeframe, since=since, limit=limit)
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

        Returns list of dicts with ALL fields needed for reconciliation:
          symbol, direction, entry_price, mark_price, size_usdt,
          unrealized_pnl, unrealized_pct, leverage,
          pos_id (exchange position ID for modify-tpsl-order),
          achieved_profits (> 0 means TP1 hit),
          sl_price (current SL on exchange),
          tp_price (current TP on exchange),
          sl_order_id (stopLossId for modify-tpsl-order),
          tp_order_id (takeProfitId for modify-tpsl-order)

        Paper mode: returns [] (no exchange positions — paper positions are runtime-only).
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

                # Raw fields from Bitget for reconciliation and order management
                info = p.get("info", {})
                pos_id = info.get("posId", "") or str(p.get("id", ""))
                achieved_profits = float(info.get("achievedProfits", 0) or 0)

                # Current SL/TP on exchange
                sl_price = float(info.get("stopLoss", 0) or 0)
                tp_price = float(info.get("takeProfit", 0) or 0)

                # Order IDs for modify-tpsl-order
                sl_order_id = info.get("stopLossId", "") or ""
                tp_order_id = info.get("takeProfitId", "") or ""

                # Position size fields
                total_contracts = float(info.get("total", contracts) or contracts)
                available_contracts = float(info.get("available", contracts) or contracts)

                result.append({
                    "symbol": symbol,
                    "direction": direction,
                    "entry_price": entry_price,
                    "mark_price": mark_price,
                    "size_usdt": round(abs(notional), 2),
                    "unrealized_pnl": round(unrealized_pnl, 4),
                    "unrealized_pct": round(unrealized_pct, 2),
                    "leverage": leverage,
                    # Reconciliation fields (exchange-as-truth)
                    "pos_id": pos_id,
                    "achieved_profits": achieved_profits,
                    "sl_price": sl_price,
                    "tp_price": tp_price,
                    "sl_order_id": sl_order_id,
                    "tp_order_id": tp_order_id,
                    "total_contracts": total_contracts,
                    "available_contracts": available_contracts,
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
        Place a market or limit order with preset SL/TP.

        In paper mode: logs the order and returns mock data.
        In live mode: calls the exchange API with presetStopLossPrice
        and presetStopSurplusPrice for SL/TP in a single call.

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

            # Build params with preset SL/TP (exchange-as-truth: SL/TP on exchange from trade open)
            params = {}
            if sl_price:
                params["stopLossPrice"] = sl_price
            if tp_price:
                params["takeProfitPrice"] = tp_price

            # Place market order with preset SL/TP
            order = exchange.create_market_order(
                symbol=ccxt_symbol,
                side=side,
                amount=size_usdt / (entry_price or 1),
                params=params if params else None,
            )

            _log.info(
                "LIVE ORDER placed: %s %s size=%.2f order_id=%s sl=%s tp=%s",
                direction, symbol, size_usdt, order.get("id", "?"),
                sl_price, tp_price,
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

    def place_tpsl_order(
        self,
        symbol: str,
        direction: str,
        trigger_price: float,
        size_pct: float = 100.0,
        size_usdt: float = 0.0,
        order_type: str = "tp",
        reduce_only: bool = True,
    ) -> Optional[dict]:
        """
        Place a TP/SL order via place-tpsl-order endpoint.

        Used for:
          - Partial TP1 exit (order_type="tp", size_pct=40, reduce_only=True)
          - Full TP2 exit (order_type="tp", size_pct=100)
          - Separate SL order (order_type="sl")

        In paper mode: logs and returns mock data.
        In live mode: calls the exchange API.

        Args:
            symbol: Journal format symbol (e.g. "BTCUSDT")
            direction: "Long" or "Short"
            trigger_price: Price at which the order triggers
            size_pct: Percentage of position to close (0-100)
            size_usdt: Position size in USDT (for computing contract amount)
            order_type: "tp" for take-profit, "sl" for stop-loss
            reduce_only: True for partial exits

        Returns dict with: order_id, symbol, status, paper
        """
        if self._paper_mode:
            _log.info(
                "PAPER TPSL ORDER: %s %s type=%s trigger=%.2f size_pct=%.1f%%",
                direction, symbol, order_type, trigger_price, size_pct,
            )
            return {
                "order_id": f"paper-tpsl-{symbol}-{order_type}",
                "symbol": symbol,
                "direction": direction,
                "order_type": order_type,
                "trigger_price": trigger_price,
                "size_pct": size_pct,
                "status": "paper_pending",
                "paper": True,
            }

        try:
            exchange = self._get_trade_exchange()
            ccxt_symbol = _to_ccxt_symbol(symbol)
            side = "sell" if direction.lower() == "long" else "buy"

            params = {
                "holdSide": direction.lower(),
                "reduceOnly": reduce_only,
            }

            if order_type == "tp":
                params["stopSurplusTriggerPrice"] = str(trigger_price)
                if size_pct < 100 and size_usdt > 0:
                    # Compute contract amount for partial close
                    # size_pct of total position
                    params["stopSurplusSize"] = str(size_pct)
            elif order_type == "sl":
                params["stopLossTriggerPrice"] = str(trigger_price)

            order = exchange.create_order(
                symbol=ccxt_symbol,
                type="market",
                side=side,
                amount=size_usdt * (size_pct / 100.0) if size_usdt > 0 else 0,
                params=params,
            )

            _log.info(
                "LIVE TPSL ORDER placed: %s %s type=%s trigger=%.2f order_id=%s",
                direction, symbol, order_type, trigger_price, order.get("id", "?"),
            )

            return {
                "order_id": order.get("id", ""),
                "symbol": symbol,
                "direction": direction,
                "order_type": order_type,
                "trigger_price": trigger_price,
                "status": order.get("status", "unknown"),
                "paper": False,
            }

        except Exception as e:
            _log.error("place_tpsl_order failed for %s: %s", symbol, e)
            return None

    def modify_tpsl_order(
        self,
        symbol: str,
        order_id: str,
        new_sl_price: float = None,
        new_tp_price: float = None,
    ) -> bool:
        """
        Modify an existing TP/SL order via modify-tpsl-order endpoint.

        Used for trailing stop updates — push new SL to exchange when
        trailing_sl actually changes.

        In paper mode: logs and returns True.
        In live mode: calls the exchange API.

        Args:
            symbol: Journal format symbol (e.g. "BTCUSDT")
            order_id: Exchange order ID of the SL/TP order to modify
            new_sl_price: New stop-loss price (None = don't change)
            new_tp_price: New take-profit price (None = don't change)

        Returns: True if successful, False otherwise.
        """
        if self._paper_mode:
            _log.info(
                "PAPER MODIFY TPSL: %s order_id=%s sl=%s tp=%s",
                symbol, order_id, new_sl_price, new_tp_price,
            )
            return True

        try:
            exchange = self._get_trade_exchange()
            ccxt_symbol = _to_ccxt_symbol(symbol)

            params = {}
            if new_sl_price is not None:
                params["stopLossPrice"] = str(new_sl_price)
            if new_tp_price is not None:
                params["stopSurplusPrice"] = str(new_tp_price)

            # Use CCXT's edit_order to modify the TP/SL order
            exchange.edit_order(
                id=order_id,
                symbol=ccxt_symbol,
                type="market",
                side="sell",  # TP/SL orders are always closing
                amount=None,
                price=None,
                params=params,
            )

            _log.info(
                "LIVE MODIFY TPSL: %s order_id=%s sl=%s tp=%s",
                symbol, order_id, new_sl_price, new_tp_price,
            )
            return True

        except Exception as e:
            _log.error("modify_tpsl_order failed for %s order_id=%s: %s", symbol, order_id, e)
            return False

    def place_trailing_stop(
        self,
        symbol: str,
        direction: str,
        trigger_price: float,
        trail_pct: float,
    ) -> Optional[dict]:
        """
        Place a native trailing stop order (daemon-offline backup).

        Activates after TP1 hit. The native trail is WIDER than the
        daemon's ATR-based trailing stop — it's a safety net, not a sniper.

        Percentage formula: (2 × ATR / current_price) × 100

        In paper mode: logs and returns mock data.
        In live mode: calls the exchange API with planType="trailing".

        Args:
            symbol: Journal format symbol (e.g. "BTCUSDT")
            direction: "Long" or "Short"
            trigger_price: Activation price (TP1 price — trail activates after TP1)
            trail_pct: Trailing percentage (e.g. 3.0 for 3%)

        Returns dict with: order_id, symbol, status, paper
        """
        if self._paper_mode:
            _log.info(
                "PAPER TRAILING STOP: %s %s trigger=%.2f trail_pct=%.2f%%",
                direction, symbol, trigger_price, trail_pct,
            )
            return {
                "order_id": f"paper-trail-{symbol}",
                "symbol": symbol,
                "direction": direction,
                "trigger_price": trigger_price,
                "trail_pct": trail_pct,
                "status": "paper_pending",
                "paper": True,
            }

        try:
            exchange = self._get_trade_exchange()
            ccxt_symbol = _to_ccxt_symbol(symbol)
            side = "sell" if direction.lower() == "long" else "buy"

            params = {
                "planType": "trailing",
                "holdSide": direction.lower(),
                "triggerPrice": str(trigger_price),
                "trailPercentage": str(trail_pct),
                "reduceOnly": True,
            }

            order = exchange.create_order(
                symbol=ccxt_symbol,
                type="market",
                side=side,
                amount=0,  # trailing stop closes entire remaining position
                params=params,
            )

            _log.info(
                "LIVE TRAILING STOP placed: %s %s trigger=%.2f trail=%.2f%% order_id=%s",
                direction, symbol, trigger_price, trail_pct, order.get("id", "?"),
            )

            return {
                "order_id": order.get("id", ""),
                "symbol": symbol,
                "direction": direction,
                "trigger_price": trigger_price,
                "trail_pct": trail_pct,
                "status": order.get("status", "unknown"),
                "paper": False,
            }

        except Exception as e:
            _log.error("place_trailing_stop failed for %s: %s", symbol, e)
            return None

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
                "cancelled": True,
            }
        except Exception as e:
            _log.error("cancel_orders failed for %s: %s", symbol, e)
            return False

    def close_position(
        self,
        symbol: str,
        direction: str = None,
        size_usdt: float = 0,
        reduce_only: bool = True,
    ) -> Optional[dict]:
        """
        Close an existing position at market.

        In paper mode: logs and returns mock data.
        In live mode: calls the exchange API.

        Returns dict with: order_id, symbol, status, paper
        """
        if self._paper_mode:
            _log.info("PAPER CLOSE: %s %s size=%.2f", direction, symbol, size_usdt)
            return {
                "order_id": f"paper-close-{symbol}",
                "symbol": symbol,
                "direction": direction,
                "status": "paper_closed",
                "paper": True,
            }

        try:
            exchange = self._get_trade_exchange()
            ccxt_symbol = _to_ccxt_symbol(symbol)
            side = "sell" if direction.lower() == "long" else "buy"

            params = {"reduceOnly": reduce_only}

            order = exchange.create_market_order(
                symbol=ccxt_symbol,
                side=side,
                amount=size_usdt,
                params=params,
            )

            _log.info("LIVE CLOSE: %s %s size=%.2f order_id=%s",
                       direction, symbol, size_usdt, order.get("id", "?"))

            return {
                "order_id": order.get("id", ""),
                "symbol": symbol,
                "direction": direction,
                "size_usdt": size_usdt,
                "status": order.get("status", "unknown"),
                "paper": False,
            }

        except Exception as e:
            _log.error("close_position failed for %s: %s", symbol, e)
            return None

    # --- Ticker Data -----------------------------------------------------------

    def fetch_tickers_bulk(self, symbols: list[str]) -> dict:
        """
        Fetch current ticker prices for multiple symbols.

        Uses the data_source exchange (public, no auth required).
        Returns dict of {journal_symbol: {symbol, last, bid, ask, timestamp}}.

        Args:
            symbols: List of journal format symbols (e.g. ["BTCUSDT", "ETHUSDT"])
        """
        if not symbols:
            return {}

        ccxt_symbols = [_to_ccxt_symbol(s) for s in symbols]
        symbol_map = {ccxt: jour for ccxt, jour in zip(ccxt_symbols, symbols)}
        results = {}

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

        Two-step process:
          1. fetch_markets() — get instrument list + OI from metadata
          2. fetch_tickers() — get real-time 24h volume from ticker endpoint

        No authentication required — both endpoints are public.
        """
        try:
            exchange = self._get_data_exchange()
            markets = exchange.fetch_markets()

            # Step 1: Build symbol list from market metadata
            usdt_perps = []
            symbol_map = {}
            for market in markets:
                if market.get("type") != "swap":
                    continue
                settle = market.get("settle", "") or ""
                if settle.upper() != "USDT":
                    continue
                ccxt_sym = market.get("symbol", "")
                jour_sym = _to_journal_symbol(ccxt_sym)
                entry = {
                    "ccxt_symbol": ccxt_sym,
                    "symbol": jour_sym,
                    "volume_24h_usd": 0.0,
                    "open_interest_usd": 0.0,
                }
                usdt_perps.append(entry)
                symbol_map[ccxt_sym] = entry

            # Step 2: Fetch real-time tickers for volume data
            n_with_volume = 0
            try:
                all_tickers = exchange.fetch_tickers()
                for p in usdt_perps:
                    ticker = all_tickers.get(p["ccxt_symbol"])
                    if not ticker:
                        continue
                    # Primary: quoteVolume (24h volume in quote currency = USD for USDT pairs)
                    quote_volume = ticker.get("quoteVolume")
                    if quote_volume is not None:
                        try:
                            p["volume_24h_usd"] = float(quote_volume)
                            n_with_volume += 1
                        except (ValueError, TypeError):
                            pass
                    # Fallback: baseVolume × last price
                    if p["volume_24h_usd"] == 0.0:
                        base_volume = ticker.get("baseVolume")
                        last_price = ticker.get("last")
                        if base_volume and last_price:
                            try:
                                p["volume_24h_usd"] = float(base_volume) * float(last_price)
                                n_with_volume += 1
                            except (ValueError, TypeError):
                                pass
            except Exception as te:
                _log.warning(
                    "fetch_tickers failed in fetch_usdt_perps: %s — volume data may be incomplete",
                    te,
                )

            # Step 3: Extract OI from market info
            for market in markets:
                if market.get("type") != "swap":
                    continue
                settle = market.get("settle", "") or ""
                if settle.upper() != "USDT":
                    continue
                ccxt_sym = market.get("symbol", "")
                if ccxt_sym not in symbol_map:
                    continue
                info = market.get("info", {})
                if "openInterest" in info:
                    try:
                        oi_contracts = float(info["openInterest"])
                        contract_size = float(market.get("contractSize", 1) or 1)
                        last_price = float(market.get("last", 0) or 1)
                        symbol_map[ccxt_sym]["open_interest_usd"] = (
                            oi_contracts * contract_size * last_price
                        )
                    except (ValueError, TypeError):
                        pass

            # Step 4: Build result list (exclude internal ccxt_symbol field)
            result = []
            for p in usdt_perps:
                result.append({
                    "symbol": p["symbol"],
                    "volume_24h_usd": p["volume_24h_usd"],
                    "open_interest_usd": p["open_interest_usd"],
                })

            _log.info(
                "Fetched %d USDT-M perpetual pairs from exchange (volume data for %d)",
                len(result), n_with_volume,
            )
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
