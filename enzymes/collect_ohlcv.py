"""
enzymes/collect_ohlcv.py -- Sensor enzyme: fetch OHLCV and compute indicators.

Fetches candle data for all watched symbols, computes only the strategy-enabled
indicators (from config), and writes results to substrate.market.indicators.

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
    """

    name = "CollectOHLCV"
    enzyme_class = EnzymeClass.SENSOR
    priority = 5

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config=config)
        self._exchange = None

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

        # Get exchange instance (lazy init)
        if self._exchange is None:
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

        n_symbols = len(all_indicators)
        n_indicators = len(compute_configs)
        self._log.info(
            "Collected OHLCV: %d symbols, %d indicators, timeframes=%s",
            n_symbols, n_indicators, timeframes,
        )

        return substrate

    def flux_score(self, substrate: Substrate) -> float:
        """High priority when indicators are empty (data is foundational)."""
        if self.can_activate(substrate):
            return 2.0  # Sensors get high flux when data is missing
        return 0.0