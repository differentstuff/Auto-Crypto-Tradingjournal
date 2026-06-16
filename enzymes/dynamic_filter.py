"""
enzymes/dynamic_filter.py -- Sensor enzyme: dynamic symbol selection.

Fetches all USDT-M perpetual pairs from the exchange, filters by
volume/OI/R² floor, ranks by momentum_quality, and returns top-N
candidates merged with the always_watch list.

Two modes (config-driven, no hardcoded values):
  - "static":   Returns only always_watch symbols (current behavior).
  - "combined": Returns always_watch + top-N from exchange universe.

Pipeline (combined mode):
  1. Fetch universe (all USDT-M perps from exchange API)
  2. Filter by min_volume_24h_usd
  3. Filter by min_open_interest_usd
  4. Compute momentum_quality for remaining symbols (needs OHLCV)
  5. Filter by min_r_squared (symbols with no trend are excluded)
  6. Rank by momentum_quality score descending, take top-N
  7. Merge with always_watch (union, deduped — always_watch always wins)
  8. Subtract never_trade (last — hard override, even if top-1)

The exchange universe is cached for refresh_interval_hours to avoid
API spam. The enzyme only re-fetches when the interval has elapsed.

Enzyme class: Sensor
Priority: 6 (higher than CollectOHLCV's 5, so symbols are set before OHLCV fetch)
Activates when: mode is "combined" AND (no symbols set OR refresh_interval elapsed)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from core.enzyme import Enzyme, EnzymeClass, register_enzyme
from core.substrate import Substrate

_log = logging.getLogger(__name__)


def _hours_since(iso_ts: str, now: datetime | None = None) -> float:
    """
    Calculate hours elapsed since an ISO timestamp.

    Returns float hours. Returns float('inf') if timestamp is empty
    or unparseable (treats "never run" as infinitely long ago).

    Args:
        iso_ts: ISO timestamp string.
        now: Current datetime. If None, uses real UTC time (backward compat).
    """
    if not iso_ts:
        return float("inf")
    try:
        dt = datetime.fromisoformat(iso_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if now is None:
            now = datetime.now(timezone.utc)
        elapsed = (now - dt).total_seconds() / 3600
        return max(0.0, elapsed)
    except (ValueError, TypeError):
        return float("inf")


@register_enzyme
class DynamicFilter(Enzyme):
    """
    Sensor enzyme: dynamic symbol selection based on momentum_quality ranking.

    Reads config from substrate.cfg("symbols.*") and writes the final
    symbol list to substrate.market["symbols_watched"].

    All thresholds, limits, and lists come from config — no hardcoded values.
    """

    name = "DynamicFilter"
    enzyme_class = EnzymeClass.SENSOR
    priority = 6  # Must run before CollectOHLCV (priority 5)

    def __init__(self, config: Optional[dict] = None, exchange=None):
        """
        Initialize DynamicFilter.

        Args:
            config: Strategy config dict (same as all enzymes).
            exchange: core.exchange.Exchange instance for API calls.
                      Injected from main.py — avoids creating duplicate instances.
        """
        super().__init__(config=config)
        self.exchange = exchange

    def requires(self) -> list[str]:
        return ["strategy.name is set"]

    def prohibits(self) -> list[str]:
        return []

    def can_activate(self, substrate: Substrate) -> bool:
        """
        Activate when:
          - mode is "combined" AND
          - symbols_watched is empty (cold start) OR refresh_interval has elapsed

        In "static" mode, this enzyme never activates — the substrate
        already has always_watch from initialization.
        """
        mode = substrate.cfg("symbols.mode", "static")
        if mode != "combined":
            return False

        # Cold start: no symbols set yet
        symbols = substrate.market.get("symbols_watched", [])
        if not symbols:
            return True

        # Check refresh interval
        refresh_hours = substrate.cfg("symbols.dynamic_filter.refresh_interval_hours", 4)
        last_run = substrate.market.get("last_dynamic_filter_at", "")
        hours_since_run = _hours_since(last_run, now=substrate.now_as_datetime())

        return hours_since_run >= refresh_hours

    def transform(self, substrate: Substrate) -> Substrate:
        """
        Execute the dynamic filter pipeline and update symbols_watched.

        Only runs in "combined" mode (can_activate enforces this).
        In static mode, this method is never called by the daemon.
        """
        mode = substrate.cfg("symbols.mode", "static")
        if mode != "combined":
            # Defensive: should never be called in static mode (can_activate
            # returns False), but if it is, return substrate unchanged.
            self._log.warning("DynamicFilter.transform() called in static mode — no-op")
            return substrate

        # --- Combined mode: full pipeline ---
        always_watch = substrate.cfg("symbols.always_watch", [])
        never_trade = substrate.cfg("symbols.never_trade", [])
        df_config = substrate.cfg("symbols.dynamic_filter", {})
        limit = df_config.get("limit", 15)
        min_volume = df_config.get("min_volume_24h_usd", 0)
        min_oi = df_config.get("min_open_interest_usd", 0)
        min_r_squared = df_config.get("min_r_squared", 0.15)

        # Step 1: Fetch universe from exchange
        universe = self._fetch_universe(substrate, df_config)
        if not universe:
            self._log.warning(
                "No universe data from exchange — falling back to always_watch only"
            )
            substrate.market["symbols_watched"] = [
                s for s in always_watch if s not in never_trade
            ]
            return substrate

        n_universe = len(universe)
        self._log.info("Universe: %d USDT-M perps fetched", n_universe)

        # Step 2: Filter by min_volume_24h_usd
        after_volume = [
            s for s in universe
            if s["volume_24h_usd"] >= min_volume
        ]
        n_filtered_volume = n_universe - len(after_volume)
        self._log.info(
            "Volume filter (>= %d USD): %d passed, %d filtered",
            min_volume, len(after_volume), n_filtered_volume,
        )

        # Step 3: Filter by min_open_interest_usd
        after_oi = [
            s for s in after_volume
            if s["open_interest_usd"] >= min_oi
        ]
        n_filtered_oi = len(after_volume) - len(after_oi)
        self._log.info(
            "OI filter (>= %d USD): %d passed, %d filtered",
            min_oi, len(after_oi), n_filtered_oi,
        )

        # Step 4: Compute momentum_quality for remaining symbols
        ranked = self._rank_by_momentum(substrate, after_oi, min_r_squared)

        # Step 5: Take top-N
        top_n = ranked[:limit]
        top_n_symbols = [item["symbol"] for item in top_n]
        self._log.info(
            "Ranking: %d symbols scored, top-%d selected: %s",
            len(ranked), limit, top_n_symbols,
        )

        # Step 6: Merge with always_watch (union, deduped)
        # always_watch symbols are ALWAYS included regardless of ranking
        merged = list(dict.fromkeys(always_watch + top_n_symbols))

        # Step 7: Subtract never_trade (LAST — hard override)
        # Even if a symbol is top-1, if it's in never_trade, it's excluded
        final = [s for s in merged if s not in never_trade]

        # Log any never_trade exclusions that actually hit
        excluded = [s for s in merged if s in never_trade]
        if excluded:
            self._log.info(
                "never_trade excluded %d symbols: %s", len(excluded), excluded,
            )

        # Write to substrate
        substrate.market["symbols_watched"] = final
        substrate.market["last_dynamic_filter_at"] = substrate._now_iso()

        self._log.info(
            "Dynamic filter complete: %d symbols watched "
            "(always_watch=%d, dynamic=%d, never_trade_excluded=%d)",
            len(final), len(always_watch), len(top_n_symbols), len(excluded),
        )

        return substrate

    def _fetch_universe(self, substrate: Substrate, df_config: dict) -> list[dict]:
        """
        Fetch the universe of tradeable symbols from the exchange.

        Uses the universe_source config key to determine where to get
        the symbol list. Currently only "exchange" is supported.

        Returns list of dicts: [{"symbol": str, "volume_24h_usd": float, "open_interest_usd": float}]
        """
        universe_source = df_config.get("universe_source", "exchange")

        if universe_source == "exchange":
            return self._fetch_from_exchange(substrate)

        self._log.warning(
            "Unknown universe_source '%s' — only 'exchange' is supported", universe_source,
        )
        return []

    def _fetch_from_exchange(self, substrate: Substrate) -> list[dict]:
        """
        Fetch all USDT-M perps from the exchange via fetch_usdt_perps().

        Returns list of dicts with symbol, volume, and OI data.
        """
        if self.exchange is None:
            self._log.warning(
                "No Exchange instance injected — cannot fetch universe. "
                "Pass exchange= to the DynamicFilter constructor."
            )
            return []

        try:
            return self.exchange.fetch_usdt_perps()
        except Exception as e:
            self._log.error("Failed to fetch USDT perps from exchange: %s", e)
            return []

    def _rank_by_momentum(
        self, substrate: Substrate, candidates: list[dict], min_r_squared: float
    ) -> list[dict]:
        """
        Compute momentum_quality for each candidate symbol and rank by score.

        For each symbol:
          1. Fetch OHLCV data
          2. Compute momentum_quality indicator
          3. If score is None or filtered=True (R² < floor), exclude
          4. Otherwise, include with score

        The min_r_squared floor comes from symbols.dynamic_filter.min_r_squared
        (the dynamic filter's config key), NOT from the indicator's own params.
        This override ensures the filter's floor is authoritative for ranking.

        Returns list of dicts sorted by score descending:
            [{"symbol": str, "score": float, "r_squared": float}, ...]
        """
        if self.exchange is None:
            self._log.warning("No Exchange instance — cannot rank by momentum_quality")
            return []

        from indicators.momentum_quality import compute_momentum_quality

        timeframe = substrate.strategy.get("timeframe", "4h")
        ohlcv_limit = substrate.cfg("exchange.ohlcv_limit", 200)

        # Get momentum_quality params from indicator config, then OVERRIDE
        # min_r_squared with the dynamic filter's authoritative floor value.
        # The indicator's own min_r_squared is for scoring; the filter's
        # min_r_squared is the hard floor for inclusion in the symbol list.
        mq_params = self._get_momentum_quality_params(substrate)
        mq_params["min_r_squared"] = min_r_squared

        ranked = []
        for candidate in candidates:
            symbol = candidate["symbol"]

            try:
                df = self.exchange.fetch_ohlcv(
                    symbol, timeframe=timeframe, limit=ohlcv_limit
                )
                if df is None or len(df) < 30:
                    self._log.debug(
                        "Insufficient OHLCV for %s (%d bars) — skipping",
                        symbol, len(df) if df is not None else 0,
                    )
                    continue

                result = compute_momentum_quality(df, **mq_params)

                if result is None:
                    self._log.debug("momentum_quality returned None for %s — skipping", symbol)
                    continue

                if result.get("filtered", False):
                    self._log.debug(
                        "%s filtered: R²=%.4f < min_r_squared=%.4f",
                        symbol, result.get("r_squared", 0), min_r_squared,
                    )
                    continue

                score = result.get("score")
                if score is None:
                    continue

                ranked.append({
                    "symbol": symbol,
                    "score": score,
                    "r_squared": result.get("r_squared", 0),
                    "direction": result.get("direction", "neutral"),
                })

            except Exception as e:
                self._log.warning(
                    "momentum_quality failed for %s: %s — skipping", symbol, e,
                )
                continue

        # Sort by score descending (highest momentum quality first)
        ranked.sort(key=lambda x: x["score"], reverse=True)
        return ranked

    def _get_momentum_quality_params(self, substrate: Substrate) -> dict:
        """
        Extract momentum_quality indicator params from config.

        Reads from indicators list where name == "momentum_quality".
        Returns dict of params suitable for compute_momentum_quality().
        """
        indicator_configs = substrate.cfg("indicators", [])
        for ind_cfg in indicator_configs:
            if ind_cfg.get("name") == "momentum_quality":
                return dict(ind_cfg.get("params", {}))

        # Fallback: no config found, return empty dict (function uses its own defaults)
        self._log.warning(
            "No momentum_quality indicator config found — using default params"
        )
        return {}

    def flux_score(self, substrate: Substrate) -> float:
        """
        Dynamic flux: highest priority when symbols are empty (foundational data).

        DynamicFilter must run before CollectOHLCV so the symbol list is
        set before OHLCV fetching begins. Higher flux when no symbols
        are set yet (cold start).
        """
        if not self.can_activate(substrate):
            return 0.0

        symbols = substrate.market.get("symbols_watched", [])
        if not symbols:
            return 4.0  # Cold start — must run before anything else

        return 1.5  # Refresh — important but not urgent