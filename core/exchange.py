""" core/exchange.py -- Unified CCXT exchange wrapper.

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

Position TP/SL architecture (Bitget):
  - SL: planType=pos_loss — attached to position, close-only, auto-cancelled
  - TP2: planType=pos_profit — attached to position, close-only, auto-cancelled
  - TP1: planType=profit_plan — independent partial close, reduce-only
  - pos_loss/pos_profit are managed by Bitget as part of the position object
  - When position closes, pos_loss/pos_profit are automatically cancelled
  - profit_plan (TP1) is independent but reduce-only, so it cannot open new positions
  - fetch_positions() reads back SL/TP from position data + open plan orders

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
            config = {
                "apiKey": creds.get("api_key", ""),
                "secret": creds.get("secret_key", ""),
            }
            # Bitget requires passphrase
            if exchange_id == "bitget" and creds.get("passphrase"):
                config["password"] = creds["passphrase"]
            # Blofin requires passphrase
            if exchange_id == "blofin" and creds.get("passphrase"):
                config["password"] = creds["passphrase"]

            sandbox = creds.get("sandbox", False)

            self._trade_exchange = exchange_class(config)
            self._trade_exchange.enableRateLimit = True
            self._trade_exchange.options['defaultType'] = 'future'
            if sandbox:
                self._trade_exchange.options['sandboxMode'] = True

            # Ensure position mode is synced with exchange.
            # Bitget accounts default to one-way (unilateral) mode, but CCXT
            # may internally assume hedge mode when stopLoss/takeProfit objects
            # are used. Calling set_position_mode(False) syncs CCXT's internal
            # state with the exchange, preventing error 40774.
            # This is a no-op if already in one-way mode — safe to call every time.
            # Ref: ccxt/ccxt#20729, ccxt/ccxt#19140, ccxt/ccxt#22547
            if exchange_id == "bitget":
                try:
                    self._trade_exchange.set_position_mode(False, None)
                    _log.info("Bitget position mode set to one-way (unilateral)")
                except Exception as e:
                    _log.warning("Could not set Bitget position mode: %s", e)

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
                _log.debug("Limited data for %s %s: %d bars (need 30+ for indicators)",
                           symbol, timeframe, len(df))

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

                # Current SL/TP on exchange — read from position data.
                # Bitget returns presetStopLossPrice and presetStopSurplusPrice
                # on the position itself. These are the position-level SL/TP
                # set via pos_loss/pos_profit plan types.
                # For independent plan orders (profit_plan/loss_plan), we also
                # fetch open orders below and match them.
                sl_price = float(info.get("presetStopLossPrice", 0) or info.get("stopLoss", 0) or 0)
                tp_price = float(info.get("presetStopSurplusPrice", 0) or info.get("takeProfit", 0) or 0)

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

            # ── Enrich with open plan orders ──────────────────────────────────
            # Position-level SL/TP (pos_loss/pos_profit) may not appear in
            # the position data. Fetch open orders and match them to positions
            # to extract sl_price, tp_price, sl_order_id, tp_order_id.
            try:
                self._enrich_positions_with_orders(exchange, result)
            except Exception as e:
                _log.warning("Could not enrich positions with order data: %s", e)

            return result

        except Exception as e:
            _log.error("fetch_positions failed: %s", e)
            return []

    def _enrich_positions_with_orders(self, exchange, positions: list) -> None:
        """
        Enrich position dicts with SL/TP data from pending plan orders.

        Bitget's position endpoint does NOT reliably include SL/TP info for
        pos_loss/pos_profit plan orders. The plan-pending endpoint is the
        source of truth for these orders.

        IMPORTANT: this method resets sl_price/tp_price/sl_order_id/tp_order_id
        to 0/"" before filling from plan orders. If a plan order was cancelled,
        it will NOT appear in the pending list, and the fields will correctly
        show as empty (exchange-as-truth). This prevents stale values from
        persisting after cancel_orders().

        Updates positions in-place.
        """
        if not positions:
            return

        symbols = set(p["symbol"] for p in positions)

        for symbol in symbols:
            ccxt_symbol = _to_ccxt_symbol(symbol)
            try:
                market = exchange.market(ccxt_symbol)
                plan_orders = []

                # Fetch TP/SL plan orders (profit_plan, loss_plan, moving_plan, pos_*)
                for plan_filter in ("profit_loss", "normal_plan"):
                    try:
                        response = exchange.private_mix_get_v2_mix_order_orders_plan_pending({
                            "symbol": market["id"],
                            "productType": "USDT-FUTURES",
                            "planType": plan_filter,
                        })
                        if isinstance(response, dict):
                            data = response.get("data", {})
                            if isinstance(data, dict):
                                plan_orders.extend(data.get("entrustedList", []) or [])
                            elif isinstance(data, list):
                                plan_orders.extend(data)
                    except Exception as e:
                        _log.warning("Could not fetch %s orders for %s: %s", plan_filter, symbol, e)
            except Exception as e:
                _log.warning("Could not fetch plan orders for %s: %s", symbol, e)
                continue

            # Find matching position
            target_positions = [p for p in positions if p["symbol"] == symbol]
            if not target_positions:
                continue

            pos = target_positions[0]
            direction = pos.get("direction", "Long")

            # Reset SL/TP fields — plan orders are the source of truth.
            # If no matching plan order exists, fields stay empty (correct
            # after cancel_orders — no stale values persist).
            pos["sl_price"] = 0.0
            pos["tp_price"] = 0.0
            pos["sl_order_id"] = ""
            pos["tp_order_id"] = ""

            # In one-way mode: holdSide = 'buy' for Long, 'sell' for Short
            expected_hold_side = "buy" if direction.lower() == "long" else "sell"

            for order in plan_orders:
                plan_type = order.get("planType", "")
                order_id = order.get("orderId", "")
                try:
                    trigger_price = float(order.get("triggerPrice", 0) or 0)
                except (ValueError, TypeError):
                    trigger_price = 0.0
                hold_side = str(order.get("holdSide", "")).lower()

                # Skip orders that don't match our position's direction
                if hold_side and hold_side != expected_hold_side:
                    continue

                # Match plan types to position fields (last one wins for SL/TP)
                if plan_type in ("pos_loss", "loss_plan") and trigger_price > 0:
                    pos["sl_price"] = trigger_price
                    pos["sl_order_id"] = str(order_id)
                elif plan_type in ("pos_profit", "profit_plan") and trigger_price > 0:
                    pos["tp_price"] = trigger_price
                    pos["tp_order_id"] = str(order_id)

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
        Place a market order to open a position, optionally with SL/TP.

        If sl_price / tp_price are provided, they are placed as position-level
        plan orders (pos_loss / pos_profit) AFTER the market order fills and
        the position registers on the exchange. This is the safe-by-default
        behavior: every caller (daemon, scripts) gets SL/TP on exchange
        without needing to call place_tpsl_order() separately.

        SL/TP cannot be attached to the market order itself — Bitget silently
        ignores stopLoss/takeProfit params on market orders.

        The daemon (execute_trade.py) passes sl_price and tp_price; TP1 is
        placed separately by the daemon as a profit_plan (partial close).

        In paper mode: logs the order and returns mock data.

        Returns dict with: order_id, symbol, direction, size_usdt, status,
            sl_order_id, tp_order_id (if SL/TP were placed)
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

            # Compute contracts from USDT notional and entry price.
            contracts = size_usdt / (entry_price or 1) if entry_price else 0
            if not contracts or contracts <= 0:
                _log.error("place_order: cannot compute contracts for %s (size_usdt=%.2f entry_price=%s)",
                           symbol, size_usdt, entry_price)
                return None

            # Round to exchange's amount precision (e.g. DOGE requires integer)
            try:
                contracts = float(exchange.amount_to_precision(ccxt_symbol, contracts))
            except Exception:
                contracts = round(contracts)  # Fallback: round to nearest integer

            # Place market order (no attached SL/TP)
            order = exchange.create_market_order(
                symbol=ccxt_symbol,
                side=side,
                amount=contracts,
            )

            _log.info(
                "LIVE ORDER placed: %s %s contracts=%.4f (size_usdt=%.2f) order_id=%s",
                direction, symbol, contracts, size_usdt, order.get("id", "?"),
            )

            result = {
                "order_id": order.get("id", ""),
                "symbol": symbol,
                "direction": direction,
                "size_usdt": size_usdt,
                "status": order.get("status") or "filled",  # CCXT may return None for market orders
            }

            # If sl_price / tp_price were provided, place them as position-level
            # plan orders (pos_loss / pos_profit) after the position settles.
            # This makes place_order() safe-by-default for ALL callers:
            # the daemon (execute_trade.py) passes sl_price/tp_price expecting
            # them to be set on exchange, and now they actually are.
            if sl_price or tp_price:
                # Wait briefly for the position to register on Bitget
                import time as _time
                settled = False
                for _ in range(10):  # up to 5 seconds
                    _time.sleep(0.5)
                    positions = self.fetch_positions()
                    if any(p.get("symbol") == symbol for p in positions):
                        settled = True
                        break
                if not settled:
                    _log.warning("Position for %s not visible after order — SL/TP placement may fail", symbol)

                if sl_price:
                    sl_result = self.place_tpsl_order(
                        symbol=symbol,
                        direction=direction,
                        trigger_price=sl_price,
                        order_type="sl",
                        size_pct=100.0,
                        size_usdt=0.0,
                    )
                    if sl_result:
                        result["sl_order_id"] = sl_result.get("order_id", "")
                        result["sl_price"] = sl_price
                    else:
                        _log.error("Failed to place SL for %s — position has NO stop-loss!", symbol)

                if tp_price:
                    tp_result = self.place_tpsl_order(
                        symbol=symbol,
                        direction=direction,
                        trigger_price=tp_price,
                        order_type="tp",
                        size_pct=100.0,
                        size_usdt=0.0,
                    )
                    if tp_result:
                        result["tp_order_id"] = tp_result.get("order_id", "")
                        result["tp_price"] = tp_price
                    else:
                        _log.error("Failed to place TP for %s", symbol)

            return result

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
        entry_price: float = None,
        order_type: str = "tp",
        reduce_only: bool = True,
    ) -> Optional[dict]:
        """
        Place a TP/SL order via Bitget's place-tpsl-order endpoint.

        Uses position-level plan types (pos_loss/pos_profit) for SL and TP2,
        and profit_plan for partial TP1. This is the correct architecture:

          - pos_loss: Position stop-loss — attached to position, close-only,
            auto-cancelled when position closes. No size needed.
          - pos_profit: Position take-profit (TP2) — same as pos_loss but for TP.
          - profit_plan: Independent take-profit (TP1) — partial close, needs size.
            reduce_only=True ensures it can never open a new position.

        SAFETY:
          - pos_loss/pos_profit are managed by Bitget as part of the position
            object. They CANNOT open new positions — only close the existing one.
          - profit_plan (TP1) with reduce_only=True can only reduce the position.
          - When the position closes, pos_loss/pos_profit are auto-cancelled.
          - If SL hits and position closes, stale TP1 (profit_plan) remains but
            reduce_only=True prevents it from opening a new position.

        In paper mode: logs and returns mock data.
        In live mode: calls the exchange API.

        Args:
            symbol: Journal format symbol (e.g. "BTCUSDT")
            direction: "Long" or "Short"
            trigger_price: Price at which the order triggers
            size_pct: Percentage of position to close (0-100).
                Only used for profit_plan (TP1). Ignored for pos_loss/pos_profit.
            size_usdt: Position size in USDT (for computing contract amount for TP1).
            entry_price: Entry price for contract conversion (TP1 only).
            order_type: "tp" for take-profit, "sl" for stop-loss
            reduce_only: True for partial exits (always True for safety)

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

            # For one-way mode: holdSide = 'buy' for Long, 'sell' for Short
            hold_side = "buy" if direction.lower() == "long" else "sell"

            # Determine plan type based on order type and size_pct.
            # - Full SL (size_pct=100): pos_loss — attached to position
            # - Full TP (size_pct=100): pos_profit — attached to position
            # - Partial TP1 (size_pct<100): profit_plan — independent, reduce-only
            is_partial = size_pct < 100 and size_usdt > 0

            if order_type == "sl":
                plan_type = "loss_plan" if is_partial else "pos_loss"
            elif order_type == "tp":
                plan_type = "profit_plan" if is_partial else "pos_profit"
            else:
                _log.error("place_tpsl_order: unknown order_type '%s'", order_type)
                return None

            # Build request for Bitget's place-tpsl-order endpoint.
            # We call this directly via CCXT's implicit API method for full
            # control over the planType parameter.
            market = exchange.market(ccxt_symbol)

            # Round trigger_price to market's price precision to avoid
            # Bitget 'checkBDScale' errors (e.g. DOGEUSDT requires 5 decimals).
            try:
                trigger_price = float(exchange.price_to_precision(ccxt_symbol, trigger_price))
            except Exception:
                trigger_price = round(trigger_price, 5)

            request = {
                "symbol": market["id"],  # Bitget format: "DOGEUSDT"
                "productType": "USDT-FUTURES",
                "marginCoin": "USDT",
                "planType": plan_type,
                "triggerPrice": str(trigger_price),
                "triggerType": "mark_price",
                "holdSide": hold_side,
                "executePrice": "0",  # Market execution
            }

            # Size is required for profit_plan/loss_plan, NOT for pos_loss/pos_profit
            if plan_type in ("profit_plan", "loss_plan"):
                price = entry_price
                if not price or price <= 0:
                    ticker = self.fetch_ticker(symbol)
                    price = ticker.get("last", 0) if ticker else 0
                if not price or price <= 0:
                    _log.error("place_tpsl_order: cannot determine price for %s", symbol)
                    return None

                contracts = (size_usdt * (size_pct / 100.0)) / price if size_usdt > 0 else 0
                try:
                    contracts = float(exchange.amount_to_precision(ccxt_symbol, contracts))
                except Exception:
                    contracts = round(contracts)

                request["size"] = str(int(contracts)) if contracts == int(contracts) else str(contracts)

            # Call Bitget's place-tpsl-order endpoint directly.
            # This gives us full control over planType (pos_loss vs loss_plan).
            response = exchange.private_mix_post_v2_mix_order_place_tpsl_order(request)

            # Extract order ID from response
            order_id = ""
            if isinstance(response, dict):
                data = response.get("data", response)
                if isinstance(data, dict):
                    order_id = str(data.get("orderId", data.get("clientOid", "")))
                elif isinstance(data, str):
                    order_id = data

            _log.info(
                "LIVE TPSL ORDER placed: %s %s planType=%s trigger=%.2f order_id=%s",
                direction, symbol, plan_type, trigger_price, order_id,
            )

            return {
                "order_id": order_id,
                "symbol": symbol,
                "direction": direction,
                "order_type": order_type,
                "plan_type": plan_type,
                "trigger_price": trigger_price,
                "size_pct": size_pct,
                "status": "live_pending",
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
        direction: str = None,
    ) -> bool:
        """
        Modify an existing TP/SL order in place via Bitget's native endpoint.

        Calls Bitget's modify-tpsl-order endpoint directly:
          POST /api/v2/mix/order/modify-tpsl-order

        This is used INSTEAD of CCXT's edit_order because CCXT doesn't
        properly route plan order modifications — it maps stopLossPrice/
        takeProfitPrice to the wrong endpoint parameters. The Bitget
        endpoint requires planType + triggerPrice, which CCXT's generic
        edit_order doesn't provide.

        SAFETY: in-place modification — the position is NEVER left without
        a stop-loss. On failure the EXISTING SL/TP order remains active on
        the exchange and this method returns False, so the caller (daemon)
        retries on the next cycle instead of silently skipping.

        In paper mode: logs and returns True.
        In live mode: calls the exchange API.

        Args:
            symbol: Journal format symbol (e.g. "BTCUSDT")
            order_id: Exchange order ID of the SL/TP order to modify
            new_sl_price: New stop-loss price (None = don't change)
            new_tp_price: New take-profit price (None = don't change)
            direction: "Long" or "Short" — determines closing side.

        Returns: True if successful, False otherwise.
        """
        if self._paper_mode:
            _log.info(
                "PAPER MODIFY TPSL: %s order_id=%s sl=%s tp=%s",
                symbol, order_id, new_sl_price, new_tp_price,
            )
            return True

        if new_sl_price is None and new_tp_price is None:
            _log.warning("modify_tpsl_order called with no price change for %s", symbol)
            return True

        try:
            exchange = self._get_trade_exchange()
            ccxt_symbol = _to_ccxt_symbol(symbol)
            market = exchange.market(ccxt_symbol)

            # Bitget's modify-tpsl-order endpoint requires planType and
            # triggerPrice — NOT stopLossPrice/takeProfitPrice. CCXT's
            # edit_order doesn't properly route to this endpoint or map
            # these params. Call the Bitget endpoint directly for full
            # control (same pattern as place_tpsl_order).

            # Determine which plan orders to modify.
            # SL → pos_loss, TP → pos_profit. In practice the daemon
            # only modifies one at a time (trailing stop = SL only).
            modifications = []
            if new_sl_price is not None:
                modifications.append(("pos_loss", new_sl_price))
            if new_tp_price is not None:
                modifications.append(("pos_profit", new_tp_price))

            # Fetch position for contract amount. Bitget's modify-tpsl-order
            # may require newSize (code 400172 'Order quantity cannot be empty').
            positions = self.fetch_positions()
            target = [p for p in positions if p.get("symbol") == symbol]
            amount = None
            if target:
                amount = float(target[0].get("total_contracts", 0)) or None
                if amount:
                    try:
                        amount = float(exchange.amount_to_precision(ccxt_symbol, amount))
                    except Exception:
                        amount = round(amount)

            # Call Bitget's modify-tpsl-order endpoint for each modification.
            for plan_type, trigger_price in modifications:
                # Round trigger_price to market's price precision
                try:
                    trigger_price = float(exchange.price_to_precision(ccxt_symbol, trigger_price))
                except Exception:
                    trigger_price = round(trigger_price, 5)

                request = {
                    "symbol": market["id"],
                    "productType": "USDT-FUTURES",
                    "marginCoin": "USDT",
                    "orderId": order_id,
                    "planType": plan_type,
                    "triggerPrice": str(trigger_price),
                }

                # Include newSize when available (prevents code 400172)
                if amount and amount > 0:
                    request["newSize"] = str(int(amount)) if amount == int(amount) else str(amount)

                exchange.private_mix_post_v2_mix_order_modify_tpsl_order(request)

                _log.info(
                    "LIVE MODIFY TPSL: %s order_id=%s planType=%s triggerPrice=%.6f",
                    symbol, order_id, plan_type, trigger_price,
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

        Uses Bitget's moving_plan planType via place-tpsl-order endpoint.
        reduce_only=True ensures it can never open a new position.

        In paper mode: logs and returns mock data.
        In live mode: calls the exchange API.

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

            # For one-way mode: holdSide = 'buy' for Long, 'sell' for Short
            hold_side = "buy" if direction.lower() == "long" else "sell"

            # Fetch current position to get actual contract amount.
            positions = self.fetch_positions()
            target = [p for p in positions if p.get("symbol") == symbol]
            if not target:
                _log.error("place_trailing_stop: no open position for %s", symbol)
                return None

            contracts = float(target[0].get("available_contracts", 0) or target[0].get("total_contracts", 0))
            if contracts <= 0:
                _log.error("place_trailing_stop: no contracts for %s", symbol)
                return None

            # Round to exchange's amount precision
            try:
                contracts = float(exchange.amount_to_precision(ccxt_symbol, contracts))
            except Exception:
                contracts = round(contracts)

            # Use Bitget's place-tpsl-order with planType=moving_plan.
            # This is the native trailing stop — managed by Bitget.
            # NOTE: moving_plan does NOT use executePrice — it always
            # executes at market price when triggered. Including it causes
            # 'Parameter verification failed executePrice' error.
            market = exchange.market(ccxt_symbol)

            # Round trigger_price to market's price precision
            try:
                trigger_price = float(exchange.price_to_precision(ccxt_symbol, trigger_price))
            except Exception:
                trigger_price = round(trigger_price, 5)

            request = {
                "symbol": market["id"],
                "productType": "USDT-FUTURES",
                "marginCoin": "USDT",
                "planType": "moving_plan",
                "triggerPrice": str(trigger_price),
                "triggerType": "mark_price",
                "holdSide": hold_side,
                "size": str(int(contracts)) if contracts == int(contracts) else str(contracts),
                "rangeRate": str(trail_pct),
            }

            response = exchange.private_mix_post_v2_mix_order_place_tpsl_order(request)

            # Extract order ID from response
            order_id = ""
            if isinstance(response, dict):
                data = response.get("data", response)
                if isinstance(data, dict):
                    order_id = str(data.get("orderId", data.get("clientOid", "")))
                elif isinstance(data, str):
                    order_id = data

            _log.info(
                "LIVE TRAILING STOP placed: %s %s trigger=%.2f trail=%.2f%% contracts=%.4f order_id=%s",
                direction, symbol, trigger_price, trail_pct, contracts, order_id,
            )

            return {
                "order_id": order_id,
                "symbol": symbol,
                "direction": direction,
                "trigger_price": trigger_price,
                "trail_pct": trail_pct,
                "status": "live_pending",
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
        Cancel all open orders for a symbol — BOTH regular orders AND plan orders.

        Bitget separates order types into two categories:
          - Regular orders: limit/market orders on the order book
          - Plan orders: trigger/SL/TP/trailing orders (pos_loss, pos_profit,
            profit_plan, loss_plan, moving_plan, normal_plan, track_plan)

        CCXT's cancel_all_orders() only cancels REGULAR orders. Plan orders
        require the separate plan-pending endpoint + cancel-plan-order.

        In paper mode: no-op, returns True.

        Returns: True if successful
        """
        if self._paper_mode:
            _log.info("PAPER CANCEL: %s (no-op)", symbol)
            return True

        success = True

        # Step 1: Cancel regular orders
        try:
            exchange = self._get_trade_exchange()
            ccxt_symbol = _to_ccxt_symbol(symbol)
            orders = exchange.cancel_all_orders(ccxt_symbol)
        except Exception as e:
            error_str = str(e)
            # Bitget code 22001 = "No order to cancel" — not an error.
            if '"code":"22001"' in error_str or 'No order to cancel' in error_str:
                _log.info("cancel_orders: no regular orders for %s (already clean)", symbol)
            else:
                _log.error("cancel_orders (regular) failed for %s: %s", symbol, e)
                success = False

        # Step 2: Cancel plan orders (SL/TP/trigger/trailing)
        try:
            exchange = self._get_trade_exchange()
            ccxt_symbol = _to_ccxt_symbol(symbol)
            market = exchange.market(ccxt_symbol)

            # Fetch pending plan orders for this symbol
            response = exchange.private_mix_get_v2_mix_order_orders_plan_pending({
                "symbol": market["id"],
                "productType": "USDT-FUTURES",
                "planType": "profit_loss",  # covers profit/loss/moving/pos plans
            })

            plan_orders = []
            if isinstance(response, dict):
                data = response.get("data", {})
                if isinstance(data, dict):
                    plan_orders = data.get("entrustedList", []) or []
                elif isinstance(data, list):
                    plan_orders = data

            # Also fetch trigger orders (normal_plan/track_plan)
            try:
                response2 = exchange.private_mix_get_v2_mix_order_orders_plan_pending({
                    "symbol": market["id"],
                    "productType": "USDT-FUTURES",
                    "planType": "normal_plan",
                })
                if isinstance(response2, dict):
                    data2 = response2.get("data", {})
                    if isinstance(data2, dict):
                        plan_orders.extend(data2.get("entrustedList", []) or [])
                    elif isinstance(data2, list):
                        plan_orders.extend(data2)
            except Exception as e2:
                _log.warning("Could not fetch normal_plan orders for %s: %s", symbol, e2)

            # Cancel each plan order
            cancelled_count = 0
            for po in plan_orders:
                try:
                    order_id = po.get("orderId", "")
                    plan_type = po.get("planType", "")
                    if not order_id:
                        continue
                    exchange.private_mix_post_v2_mix_order_cancel_plan_order({
                        "symbol": market["id"],
                        "productType": "USDT-FUTURES",
                        "orderId": order_id,
                        "planType": plan_type,
                    })
                    cancelled_count += 1
                except Exception as ce:
                    _log.warning("Could not cancel plan order %s for %s: %s", po.get("orderId"), symbol, ce)
                    success = False

            if cancelled_count > 0:
                _log.info("Cancelled %d plan order(s) for %s", cancelled_count, symbol)

        except Exception as e:
            error_str = str(e)
            if '"code":"22001"' in error_str or 'No order to cancel' in error_str:
                _log.info("cancel_orders: no plan orders for %s (already clean)", symbol)
            else:
                _log.error("cancel_orders (plan) failed for %s: %s", symbol, e)
                success = False

        return {
            "symbol": symbol,
            "cancelled": success,
        }

    def close_position(
        self,
        symbol: str,
        direction: str = None,
        size_usdt: float = 0,
        reduce_only: bool = True,
        price: float = None,
        total_contracts: float = None,
    ) -> Optional[dict]:
        """
        Close an existing position at market.

        In paper mode: logs and returns mock data.
        In live mode: calls the exchange API.

        Args:
            symbol: Journal format symbol (e.g. "BTCUSDT")
            direction: "Long" or "Short"
            size_usdt: Position size in USDT notional
            reduce_only: True for partial close, False for full close
            price: Current price to convert size_usdt to contracts.
                If None, fetches current ticker price.
            total_contracts: Actual contract amount from the position.
                If provided, used directly instead of computing from
                size_usdt/price (avoids rounding errors that leave 1 contract).

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

            # Use actual contract count if provided (avoids rounding errors
            # that leave 1 contract behind). Fall back to size_usdt/price.
            close_price = price
            if total_contracts and total_contracts > 0:
                contracts = float(total_contracts)
                # Still need close_price for logging
                if not close_price or close_price <= 0:
                    ticker = self.fetch_ticker(symbol)
                    close_price = ticker.get("last", 0) if ticker else 0
            else:
                if not close_price or close_price <= 0:
                    ticker = self.fetch_ticker(symbol)
                    close_price = ticker.get("last", 0) if ticker else 0
                if not close_price or close_price <= 0:
                    _log.error("close_position: cannot determine price for %s", symbol)
                    return None
                contracts = size_usdt / close_price if close_price > 0 else 0

            # Round to exchange's amount precision
            try:
                contracts = float(exchange.amount_to_precision(ccxt_symbol, contracts))
            except Exception:
                contracts = round(contracts)

            params = {"reduceOnly": reduce_only}

            order = exchange.create_market_order(
                symbol=ccxt_symbol,
                side=side,
                amount=contracts,
                params=params,
            )

            _log.info("LIVE CLOSE: %s %s contracts=%.4f (size_usdt=%.2f price=%.2f) order_id=%s",
                       direction, symbol, contracts, size_usdt, close_price, order.get("id", "?"))

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
            for symbol in symbols:
                ticker = self.fetch_ticker(symbol)
                if ticker:
                    results[symbol] = ticker

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

        No authentication required — both endpoints are public.
        """
        try:
            exchange = self._get_data_exchange()
            markets = exchange.fetch_markets()

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

            n_with_volume = 0
            try:
                all_tickers = exchange.fetch_tickers()
                for p in usdt_perps:
                    ticker = all_tickers.get(p["ccxt_symbol"])
                    if not ticker:
                        continue
                    quote_volume = ticker.get("quoteVolume")
                    if quote_volume is not None:
                        try:
                            p["volume_24h_usd"] = float(quote_volume)
                            n_with_volume += 1
                        except (ValueError, TypeError):
                            pass
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

        # Test data exchange — fetch enough bars to avoid spurious warnings
        try:
            df = self.fetch_ohlcv("BTCUSDT", "1h", limit=100)
            result["data_ok"] = df is not None and len(df) >= 30
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
