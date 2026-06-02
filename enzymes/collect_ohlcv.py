"""
enzymes/collect_ohlcv.py -- Sensor enzyme: fetch OHLCV and compute indicators.

Fetches candle data for all watched symbols, computes only the strategy-enabled
indicators (from config), and writes results to substrate.market.indicators.

P7 (Smart OHLCV Activation):
  Only fetches and computes when a new candle has closed for a symbol/timeframe.
  Between candle closes, indicators persist on the substrate — they represent
  the last completed candle's data, which is still valid. This eliminates
  redundant API calls (e.g., ~48 calls/day/symbol → ~6 for 4H candles).

P2 (Per-Candle-Close History):
  Appends to indicator_history only when a new candle has closed, not every
  cycle. History entries represent real closed candles, not cycle snapshots.
  Config uses trajectory_lookback_hours (time-based) instead of
  trajectory_lookback_bars (count-based).

On cold start (empty indicator_history), bootstraps history from the last N
bars of OHLCV data so that CollectPreTradeContext has real trajectory data
immediately, avoiding the warmup delay.

Enzyme class: Sensor
Activates when: indicators empty (cold start) OR new candle closed
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from core.enzyme import Enzyme, EnzymeClass, register_enzyme
from core.substrate import Substrate

_log = logging.getLogger(__name__)


# ── Timeframe helpers (module-level for testability) ─────────────────────────

def timeframe_to_minutes(timeframe: str) -> int:
    """
    Convert a timeframe string (e.g., '4H', '1h', '15m', '1D') to minutes.

    Handles both uppercase and lowercase. Returns 60 (1H) for unknown formats.
    """
    tf = timeframe.strip().upper()
    if tf.endswith("H"):
        return int(tf[:-1]) * 60
    if tf.endswith("M"):
        return int(tf[:-1])
    if tf.endswith("D"):
        return int(tf[:-1]) * 1440
    if tf.endswith("W"):
        return int(tf[:-1]) * 10080
    _log.warning("Unknown timeframe format '%s', defaulting to 60 min", timeframe)
    return 60


def candle_floor(ts: datetime, timeframe: str) -> datetime:
    """
    Round a timestamp down to the start of the current candle for the given timeframe.

    For example, with timeframe='4H' and ts=14:37 UTC, returns 12:00 UTC.
    """
    tf_minutes = timeframe_to_minutes(timeframe)
    total_minutes = ts.hour * 60 + ts.minute
    floored_minutes = (total_minutes // tf_minutes) * tf_minutes
    return ts.replace(
        hour=floored_minutes // 60,
        minute=floored_minutes % 60,
        second=0,
        microsecond=0,
    )


def should_refresh_ohlcv(timeframe: str, last_close_ts: str, now: datetime) -> bool:
    """
    Determine whether OHLCV data should be refreshed for a given timeframe.

    Returns True if a new candle has closed since the last recorded close timestamp.
    Returns True if last_close_ts is empty (cold start or first run).

    Args:
        timeframe: Timeframe string (e.g., '4H', '1h').
        last_close_ts: ISO timestamp of the last candle close we processed.
        now: Current UTC datetime.
    """
    if not last_close_ts:
        return True  # cold start — no record yet
    try:
        last_dt = datetime.fromisoformat(last_close_ts)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return True  # invalid timestamp — refresh

    current_floor = candle_floor(now, timeframe)
    last_floor = candle_floor(last_dt, timeframe)
    return current_floor > last_floor


@register_enzyme
class CollectOHLCV(Enzyme):
    """
    Sensor enzyme: fetch OHLCV data and compute enabled indicators.

    Reads the symbol list from substrate.market.symbols_watched.
    Computes only indicators with weight > 0 (scoring indicators) plus
    indicators needed for infrastructure (atr for SL sizing, sr_levels
    for entry zone validation).

    P7: Only fetches data when a new candle has closed. Between closes,
    indicators persist on the substrate — they're still valid.

    P2: Only appends to indicator_history when a new candle has closed.

    Writes to substrate.market.indicators as:
        {symbol: {timeframe: {indicator_name: result, ...}, ...}, ...}

    Also maintains:
        substrate.market.indicator_history: rolling window of snapshots
        substrate.market.last_candle_close_ts: {symbol_tf: ISO_timestamp}
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
        self.exchange = exchange

    def requires(self) -> list[str]:
        return ["strategy.name is set"]

    def prohibits(self) -> list[str]:
        return []

    def can_activate(self, substrate: Substrate) -> bool:
        """
        Activate if indicators are empty (cold start) OR any symbol/timeframe
        has a new candle closed since last recorded close.

        P7: Between candle closes, this returns False, saving API calls.
        """
        indicators = substrate.market.get("indicators", {})
        if not indicators:
            return True  # cold start — need initial data

        # Check if any symbol/timeframe has a new candle
        last_candle_close_ts = substrate.market.get("last_candle_close_ts", {})
        symbols = substrate.market.get("symbols_watched", [])
        timeframe = substrate.strategy.get("timeframe", "4H")
        confirmation_tf = substrate.strategy.get("confirmation_tf", "1H")
        timeframes = [timeframe]
        if confirmation_tf and confirmation_tf != timeframe:
            timeframes.append(confirmation_tf)

        now = datetime.now(timezone.utc)
        for symbol in symbols:
            for tf in timeframes:
                key = f"{symbol}_{tf}"
                last_ts = last_candle_close_ts.get(key, "")
                if not last_ts:
                    return True  # no record yet for this symbol/tf
                if should_refresh_ohlcv(tf, last_ts, now):
                    return True  # new candle closed

        return False  # no new candle for any symbol/timeframe

    def transform(self, substrate: Substrate) -> Substrate:
        """Fetch OHLCV and compute indicators for symbols with new candles."""
        from core.exchange import Exchange
        from indicators.registry import compute_indicator

        symbols = substrate.market.get("symbols_watched", [])
        if not symbols:
            self._log.info("No symbols to watch, skipping")
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
        # - weight == 0 but needed: infrastructure (atr, sr_levels, momentum_quality)
        compute_configs = []
        for ind_cfg in indicator_configs:
            name = ind_cfg.get("name", "")
            weight = ind_cfg.get("weight", 0)
            if weight > 0 or name in ("atr", "sr_levels", "momentum_quality"):
                compute_configs.append(ind_cfg)

        # Exchange instance should be injected from main.py.
        if self.exchange is None:
            self._log.warning(
                "No Exchange instance injected — creating fallback. "
                "This should be fixed by passing exchange= to the constructor."
            )
            from core.config_loader import ConfigLoader
            config_loader = ConfigLoader(
                strategy_name=substrate.strategy.get("name", "momentum_rising")
            )
            self.exchange = Exchange(config_loader)

        # P7: Check which symbols/timeframes need refresh
        now = datetime.now(timezone.utc)
        existing_indicators = dict(substrate.market.get("indicators", {}))
        last_candle_close_ts = dict(substrate.market.get("last_candle_close_ts", {}))

        # Track which symbols had new candles (for history append)
        symbols_with_new_candle = set()

        # Fetch and compute for each symbol/timeframe that needs refresh
        all_indicators = {}  # rebuilt from existing + refreshed

        for symbol in symbols:
            sym_indicators = dict(existing_indicators.get(symbol, {}))

            for tf in timeframes:
                key = f"{symbol}_{tf}"
                last_ts = last_candle_close_ts.get(key, "")

                # P7: Skip if no new candle has closed
                if last_ts and not should_refresh_ohlcv(tf, last_ts, now):
                    self._log.debug(
                        "No new candle for %s %s, preserving indicators", symbol, tf
                    )
                    continue

                # New candle — fetch and compute
                ohlcv_limit = substrate.cfg("exchange.ohlcv_limit")
                df = self.exchange.fetch_ohlcv(symbol, timeframe=tf, limit=ohlcv_limit)
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
                # P7: Record the candle close timestamp
                last_candle_close_ts[key] = candle_floor(now, tf).isoformat()
                symbols_with_new_candle.add(symbol)

            if sym_indicators:
                all_indicators[symbol] = sym_indicators

        # Preserve indicators for symbols that had no new candle
        for symbol in existing_indicators:
            if symbol not in all_indicators:
                all_indicators[symbol] = existing_indicators[symbol]

        # Write to substrate
        substrate.market["indicators"] = all_indicators
        substrate.market["last_scan_at"] = substrate._now_iso()
        substrate.market["last_candle_close_ts"] = last_candle_close_ts

        # --- Indicator history (P2: per-candle-close only) ---
        # Only append to history when a new candle has closed for the symbol.
        # This ensures history entries represent real closed candles, not
        # cycle snapshots of the same forming candle.
        lookback_hours = substrate.cfg("learning.trajectory_lookback_hours")
        # Shallow-copy safe: create new history dict
        old_history = substrate.market.get("indicator_history", {})
        history = {sym: list(snapshots) for sym, snapshots in old_history.items()}

        # Cold start bootstrap: if indicator_history is empty, compute
        # historical snapshots from the OHLCV data we just fetched.
        cold_start = not history
        if cold_start:
            self._bootstrap_indicator_history(
                substrate, symbols, timeframes, compute_configs, lookback_hours
            )
            # Re-read history after bootstrap
            history = {sym: list(snapshots) for sym, snapshots in
                       substrate.market.get("indicator_history", {}).items()}

        # P2: Only append snapshots for symbols with new candles
        for symbol in symbols_with_new_candle:
            sym_data = all_indicators.get(symbol, {})
            if not sym_data:
                continue

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

            # P2: Trim history by time span, not count
            self._trim_history_by_time(history, symbol, lookback_hours)

        substrate.market["indicator_history"] = history

        n_symbols = len(all_indicators)
        n_indicators = len(compute_configs)
        n_refreshed = len(symbols_with_new_candle)
        self._log.info(
            "Collected OHLCV: %d symbols (%d refreshed), %d indicators, timeframes=%s%s",
            n_symbols, n_refreshed, n_indicators, timeframes,
            " (cold start bootstrap)" if cold_start else "",
        )

        return substrate

    @staticmethod
    def _trim_history_by_time(
        history: dict, symbol: str, lookback_hours: float
    ) -> None:
        """
        Trim indicator history to the configured time span.

        P2: History is trimmed by time (trajectory_lookback_hours), not by count.
        This ensures that trajectory analysis covers a consistent time window
        regardless of cycle frequency.
        """
        if symbol not in history or not history[symbol]:
            return

        snapshots = history[symbol]
        if len(snapshots) < 2:
            return

        # Parse the oldest timestamp and trim from the front
        now = datetime.now(timezone.utc)
        cutoff = now.timestamp() - (lookback_hours * 3600)

        # Find the first snapshot within the lookback window
        trimmed = []
        for snap in snapshots:
            ts_str = snap.get("timestamp", "")
            if not ts_str:
                trimmed.append(snap)  # keep snapshots without timestamps
                continue
            try:
                ts = datetime.fromisoformat(ts_str)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts.timestamp() >= cutoff:
                    trimmed.append(snap)
            except (ValueError, TypeError):
                trimmed.append(snap)  # keep unparseable timestamps

        # Always keep at least 2 snapshots for trajectory classification
        if len(trimmed) < 2 and len(snapshots) >= 2:
            trimmed = snapshots[-2:]

        history[symbol] = trimmed

    def _bootstrap_indicator_history(
        self,
        substrate: Substrate,
        symbols: list,
        timeframes: list,
        compute_configs: list,
        lookback_hours: float,
    ) -> None:
        """
        Bootstrap indicator history from historical OHLCV data on cold start.

        When indicator_history is empty (first startup after restart), this method
        computes indicators for the last N bars using the same OHLCV data we already
        fetched. This eliminates the warmup delay where trades would be blocked by
        ISC-007 due to insufficient trajectory data.

        P2: Uses trajectory_lookback_hours (time-based) to determine how many
        historical candles to compute.
        """
        from indicators.registry import compute_indicator

        # Shallow-copy safe: create new history dict
        old_history = substrate.market.get("indicator_history", {})
        history = {sym: list(snapshots) for sym, snapshots in old_history.items()}
        timeframe = substrate.strategy.get("timeframe", "4H")

        # Calculate how many candles we need based on lookback_hours
        tf_minutes = timeframe_to_minutes(timeframe)
        bootstrap_bars = max(12, int(lookback_hours * 60 / tf_minutes))
        # Cap at ohlcv_limit (max OHLCV fetch)
        ohlcv_limit = substrate.cfg("exchange.ohlcv_limit")
        bootstrap_bars = min(bootstrap_bars, ohlcv_limit)

        self._log.info(
            "Cold start: bootstrapping indicator history with %d bars per symbol "
            "(lookback_hours=%.1f, tf=%s)",
            bootstrap_bars, lookback_hours, timeframe,
        )

        for symbol in symbols:
            # Fetch extended historical data for bootstrap
            df = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=ohlcv_limit)
            if df is None or df.empty or len(df) < bootstrap_bars:
                self._log.warning(
                    "Insufficient data for bootstrap of %s (%d bars needed, %d available)",
                    symbol, bootstrap_bars, len(df) if df is not None else 0,
                )
                continue

            # Compute indicators at evenly-spaced historical points
            # We divide the available data into `bootstrap_bars` evenly-spaced slices
            # and compute indicators for each slice's endpoint bar.
            step = max(1, (len(df) - bootstrap_bars) // bootstrap_bars)
            start_idx = len(df) - bootstrap_bars

            symbol_history = []
            for i in range(bootstrap_bars):
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