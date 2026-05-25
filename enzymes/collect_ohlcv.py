"""
enzymes/collect_ohlcv.py -- Sensor enzyme: fetch OHLCV and compute indicators.

Fetches candle data for all watched symbols, computes only the strategy-enabled
indicators (from config), and writes results to substrate.market.indicators.

Also maintains:
  - indicator_history: rolling window of indicator snapshots for trajectory analysis

On cold start (empty indicator_history), bootstraps history from the last N
bars of OHLCV data so that CollectPreTradeContext has real trajectory data
immediately, avoiding the 12-cycle warmup delay.

Enzyme class: Sensor
Activates when: market.indicators is empty or stale

Port of: agent_data_collector.py (data fetching), chart_indicators.py (computation)
"""

from __future__ import annotations

import logging
from typing import Optional

from core.enzyme import Enzyme, EnzymeClass, register_enzyme
from core.substrate import Substrate

_log = logging.getLogger(__name__)


@register_enzyme
class CollectOHLCV(Enzyme):
    """
    Sensor enzyme: fetch OHLCV data and compute enabled indicators.

    Reads the symbol list from substrate.market.symbols_watched.
    Computes only indicators with weight > 0 (scoring indicators) plus
    indicators needed for infrastructure (atr for SL sizing, sr_levels
    for entry zone validation).

    Writes to substrate.market.indicators as:
        {symbol: {timeframe: {indicator_name: result, ...}, ...}, ...}

    Also maintains:
        substrate.market.indicator_history: rolling window of snapshots
    """

    name = "CollectOHLCV"
    enzyme_class = EnzymeClass.SENSOR
    priority = 5

    def __init__(self, config: Optional[dict] = None, exchange=None):
        """
        Initialize CollectOHLCV.

        Args:
            config: Strategy config dict (same as all enzymes).
            exchange: core.exchange.Exchange instance for OHLCV fetching.
                      Injected from main.py — avoids creating duplicate
                      ConfigLoader/Exchange instances.
        """
        super().__init__(config=config)
        self._exchange = exchange

    def requires(self) -> list[str]:
        return ["strategy.name is set"]

    def prohibits(self) -> list[str]:
        return []

    def can_activate(self, substrate: Substrate) -> bool:
        """Activate if indicators are empty or stale."""
        indicators = substrate.market.get("indicators", {})
        if not indicators:
            return True
        # Check freshness: if last_scan_at is empty, we need data
        last_scan = substrate.market.get("last_scan_at", "")
        if not last_scan:
            return True
        return False

    def transform(self, substrate: Substrate) -> Substrate:
        """Fetch OHLCV and compute indicators for all watched symbols."""
        from core.exchange import Exchange
        from indicators.registry import compute_indicator

        symbols = substrate.market.get("symbols_watched", [])
        if not symbols:
            self._log.info("No symbols to watch, skipping")
            substrate.market["indicators"] = {}
            substrate.market["last_scan_at"] = substrate._now_iso()
            return substrate

        # Get indicator config from substrate's config reference
        indicator_configs = substrate.cfg("indicators", [])
        timeframe = substrate.strategy.get("timeframe", "4H")
        confirmation_tf = substrate.strategy.get("confirmation_tf", "1H")
        timeframes = [timeframe]
        if confirmation_tf and confirmation_tf != timeframe:
            timeframes.append(confirmation_tf)

        # Determine which indicators to compute:
        # - weight > 0: scoring indicators (rsi, macd, ema_stack, adx)
        # - weight == 0 but needed: infrastructure (atr, sr_levels)
        compute_configs = []
        for ind_cfg in indicator_configs:
            name = ind_cfg.get("name", "")
            weight = ind_cfg.get("weight", 0)
            if weight > 0 or name in ("atr", "sr_levels"):
                compute_configs.append(ind_cfg)

        # Exchange instance should be injected from main.py.
        # If missing (e.g. tests), create one as fallback with a warning.
        if self._exchange is None:
            self._log.warning(
                "No Exchange instance injected — creating fallback. "
                "This should be fixed by passing exchange= to the constructor."
            )
            from core.config_loader import ConfigLoader
            config_loader = ConfigLoader(
                strategy_name=substrate.strategy.get("name", "momentum_rising")
            )
            self._exchange = Exchange(config_loader)

        # Fetch and compute for each symbol
        all_indicators = {}

        for symbol in symbols:
            sym_indicators = {}

            for tf in timeframes:
                df = self._exchange.fetch_ohlcv(symbol, timeframe=tf, limit=200)
                if df is None or df.empty or len(df) < 30:
                    self._log.warning(
                        "Insufficient data for %s %s (%d bars)",
                        symbol, tf, len(df) if df is not None else 0,
                    )
                    continue

                tf_indicators = {"ok": True, "candles_used": len(df)}
                for ind_cfg in compute_configs:
                    ind_name = ind_cfg.get("name", "")
                    ind_params = ind_cfg.get("params", {})
                    try:
                        result = compute_indicator(ind_name, df, **ind_params)
                        if result is not None:
                            tf_indicators[ind_name] = result
                    except Exception as e:
                        self._log.warning(
                            "Indicator %s failed for %s %s: %s",
                            ind_name, symbol, tf, e,
                        )

                sym_indicators[tf] = tf_indicators

            if sym_indicators:
                all_indicators[symbol] = sym_indicators

        # Write to substrate
        substrate.market["indicators"] = all_indicators
        substrate.market["last_scan_at"] = substrate._now_iso()

        # --- Indicator history (rolling window) ---
        # Append current indicator snapshot to the history for each symbol.
        # This provides real trajectory data for CollectPreTradeContext instead
        # of the old heuristic estimate. History survives reset_cycle() and is
        # trimmed to the configured max length.
        #
        # After restart, history starts empty. The first N cycles will have
        # insufficient data, causing CollectPreTradeContext to set
        # coincidence_risk='high' and block trades via ISC-007. This is
        # intentional: no trades until sufficient trajectory data exists.
        lookback = substrate.cfg("learning.trajectory_lookback_bars", 12)
        # Shallow-copy safe: create new history dict so we don't mutate the
        # shared reference from the original substrate.
        old_history = substrate.market.get("indicator_history", {})
        history = {sym: list(snapshots) for sym, snapshots in old_history.items()}

        # Cold start bootstrap: if indicator_history is empty, compute
        # historical snapshots from the OHLCV data we just fetched.
        # This eliminates the 12-cycle warmup delay after restart.
        cold_start = not history
        if cold_start:
            self._bootstrap_indicator_history(
                substrate, symbols, timeframes, compute_configs, lookback
            )
            # Re-read history after bootstrap
            history = substrate.market.get("indicator_history", {})

        for symbol, sym_data in all_indicators.items():
            # Build a snapshot with directional signals for trajectory classification
            primary_tf = list(sym_data.keys())[0] if sym_data else None
            if not primary_tf:
                continue

            tf_inds = sym_data.get(primary_tf, {})
            if not isinstance(tf_inds, dict) or not tf_inds.get("ok"):
                continue

            snapshot = {
                "timestamp": substrate._now_iso(),
                "indicators": tf_inds,
                "signal": self._compute_signal_direction(tf_inds),
            }

            if symbol not in history:
                history[symbol] = []
            history[symbol].append(snapshot)

            # Trim to max length
            if len(history[symbol]) > lookback:
                history[symbol] = history[symbol][-lookback:]

        substrate.market["indicator_history"] = history

        n_symbols = len(all_indicators)
        n_indicators = len(compute_configs)
        self._log.info(
            "Collected OHLCV: %d symbols, %d indicators, timeframes=%s%s",
            n_symbols, n_indicators, timeframes,
            " (cold start bootstrap)" if cold_start else "",
        )

        return substrate

    def _bootstrap_indicator_history(
        self,
        substrate: Substrate,
        symbols: list,
        timeframes: list,
        compute_configs: list,
        lookback: int,
    ) -> None:
        """
        Bootstrap indicator history from historical OHLCV data on cold start.

        When indicator_history is empty (first startup after restart), this method
        computes indicators for the last N bars using the same OHLCV data we already
        fetched. This eliminates the warmup delay where trades would be blocked by
        ISC-007 due to insufficient trajectory data.

        This is safe because:
        - We use the same real OHLCV data (no imaginary values)
        - We compute the same indicators with the same config
        - The only difference is we compute them for historical bars, not just the latest

        The bootstrap runs once, on the first cycle after restart when
        indicator_history is empty. Subsequent cycles only append the current snapshot.
        """
        from indicators.registry import compute_indicator
        import pandas as pd

        # Shallow-copy safe: create new history dict
        old_history = substrate.market.get("indicator_history", {})
        history = {sym: list(snapshots) for sym, snapshots in old_history.items()}
        timeframe = substrate.strategy.get("timeframe", "4H")
        # Use more bars for bootstrap to get enough historical data
        bootstrap_bars = substrate.cfg("learning.trajectory_bootstrap_bars", 48)

        self._log.info(
            "Cold start: bootstrapping indicator history with %d bars per symbol",
            bootstrap_bars,
        )

        for symbol in symbols:
            # Fetch extended historical data for bootstrap
            df = self._exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=200)
            if df is None or df.empty or len(df) < bootstrap_bars:
                self._log.warning(
                    "Insufficient data for bootstrap of %s (%d bars needed, %d available)",
                    symbol, bootstrap_bars, len(df) if df is not None else 0,
                )
                continue

            # Compute indicators at evenly-spaced historical points
            # We divide the available data into `lookback` evenly-spaced slices
            # and compute indicators for each slice's endpoint bar.
            # This gives us a representative trajectory without computing
            # indicators for all 200 bars (which would be slow).
            step = max(1, (len(df) - bootstrap_bars) // lookback)
            start_idx = len(df) - bootstrap_bars

            symbol_history = []
            for i in range(lookback):
                idx = start_idx + i * step
                if idx >= len(df):
                    idx = len(df) - 1

                # Slice the DataFrame up to this point
                slice_df = df.iloc[:idx + 1]

                # Skip if not enough bars for indicator computation
                if len(slice_df) < 30:
                    continue

                # Compute indicators for this historical slice
                tf_indicators = {"ok": True, "candles_used": len(slice_df)}
                for ind_cfg in compute_configs:
                    ind_name = ind_cfg.get("name", "")
                    ind_params = ind_cfg.get("params", {})
                    try:
                        result = compute_indicator(ind_name, slice_df, **ind_params)
                        if result is not None:
                            tf_indicators[ind_name] = result
                    except Exception:
                        pass  # Skip failed indicators in bootstrap

                if not tf_indicators.get("ok"):
                    continue

                snapshot = {
                    "timestamp": slice_df.index[-1].isoformat() if hasattr(slice_df.index[-1], 'isoformat') else str(slice_df.index[-1]),
                    "indicators": tf_indicators,
                    "signal": self._compute_signal_direction(tf_indicators),
                }
                symbol_history.append(snapshot)

            if symbol_history:
                history[symbol] = symbol_history
                self._log.info(
                    "Bootstrapped %d history entries for %s",
                    len(symbol_history), symbol,
                )

        substrate.market["indicator_history"] = history

    @staticmethod
    def _compute_signal_direction(tf_inds: dict) -> str:
        """
        Compute the overall signal direction from indicator values.

        Returns 'bullish', 'bearish', or 'neutral' based on the weighted
        direction of scoring indicators. Used for trajectory classification.
        """
        score = 0.0
        count = 0

        # RSI
        rsi = tf_inds.get("rsi", {})
        if isinstance(rsi, dict) and "value" in rsi:
            val = rsi["value"]
            if val > 55:
                score += 1
            elif val < 45:
                score -= 1
            count += 1

        # MACD
        macd = tf_inds.get("macd", {})
        if isinstance(macd, dict) and "bias" in macd:
            bias = macd["bias"]
            if "bullish" in bias:
                score += 1
            elif "bearish" in bias:
                score -= 1
            count += 1

        # EMA stack
        ema = tf_inds.get("ema_stack", {})
        if isinstance(ema, dict) and "alignment" in ema:
            alignment = ema["alignment"]
            if "bullish" in alignment:
                score += 1
            elif "bearish" in alignment:
                score -= 1
            count += 1

        # ADX
        adx = tf_inds.get("adx", {})
        if isinstance(adx, dict) and "direction" in adx:
            direction = adx["direction"]
            if "bullish" in direction:
                score += 1
            elif "bearish" in direction:
                score -= 1
            count += 1

        if count == 0:
            return "neutral"
        if score > 0:
            return "bullish"
        if score < 0:
            return "bearish"
        return "neutral"

    def flux_score(self, substrate: Substrate) -> float:
        """Dynamic flux: highest priority when indicators are empty (foundational data)."""
        if not self.can_activate(substrate):
            return 0.0
        # Indicators are foundational — without them, no other enzyme can work.
        # Higher flux when we have positions that need current mark prices.
        positions = substrate.portfolio.get("open_positions", [])
        if positions:
            return 3.0  # Positions exist — need fresh data for risk management
        return 2.0  # No positions — still important but slightly less urgent