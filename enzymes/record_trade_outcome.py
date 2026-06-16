"""
enzymes/record_trade_outcome.py -- Synthase enzyme: record trades to learning DB.

Records trade entries and exits to the trade_learning table.
This is a Synthase concern (building learning data from substrate state),
not a Transporter concern (moving data between systems).

Activates when:
  - decisions.action == 'trade_open' → records entry
  - decisions.action == 'trade_closed' → records exit outcome

Runs AFTER ExecuteTrade/ExecuteExit (lower priority = runs later in pipeline).

Writes to: trade_learning table (side-effect only, no substrate changes)

Enzyme class: Synthase
Priority: -1 (runs after all Transporters at priority 0)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from core.enzyme import Enzyme, EnzymeClass, register_enzyme
from core.substrate import Substrate

_log = logging.getLogger(__name__)


def _now_iso() -> str:
    """Return current UTC time in ISO format.

    Kept for backward compatibility. New code should use substrate.now_iso()
    which respects the virtual clock in replay mode.
    """
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Per-indicator signal extraction
# ---------------------------------------------------------------------------
# Each function takes the raw indicator result dict (as stored in
# substrate.market.indicators[symbol][timeframe][indicator_name]) and
# returns a normalised dict with at least a "signal" key containing
# "bullish", "bearish", or "neutral".  Additional fields are preserved
# so the learning engine can do richer analysis later.
#
# These extractors mirror the directional logic in ScoreConfluence so
# that the learning engine's accuracy tracking is consistent with the
# scoring that actually triggered the trade.

def _extract_rsi_signal(rsi: dict, rsi_high: float, rsi_low: float) -> dict:
    """RSI directional signal: overbought → bearish, oversold → bullish.
    Thresholds read from scoring.rsi_signal_high/low in config.
    All parameters are required — they must come from substrate.cfg()."""
    value = rsi.get("value", 50)
    level = rsi.get("level", "neutral")
    if value > rsi_high:
        signal = "bullish"
    elif value < rsi_low:
        signal = "bearish"
    else:
        signal = "neutral"
    return {"signal": signal, "value": value, "level": level}


def _extract_macd_signal(macd: dict) -> dict:
    """MACD directional signal from bias + histogram growth."""
    bias = macd.get("bias", "")
    histogram_growing = macd.get("histogram_growing", False)
    crossover = macd.get("crossover", False)
    crossunder = macd.get("crossunder", False)
    if "bullish" in bias:
        signal = "bullish"
    elif "bearish" in bias:
        signal = "bearish"
    else:
        signal = "neutral"
    return {
        "signal": signal,
        "bias": bias,
        "histogram_growing": histogram_growing,
        "crossover": crossover,
        "crossunder": crossunder,
    }


def _extract_ema_signal(ema: dict) -> dict:
    """EMA stack directional signal from alignment + stack."""
    alignment = ema.get("alignment", "")
    stack = ema.get("stack", "")
    if "bullish" in alignment and "bullish" in stack:
        signal = "bullish"
    elif "bearish" in alignment and "bearish" in stack:
        signal = "bearish"
    elif "bullish" in alignment or "bullish" in stack:
        signal = "bullish"
    elif "bearish" in alignment or "bearish" in stack:
        signal = "bearish"
    else:
        signal = "neutral"
    return {"signal": signal, "alignment": alignment, "stack": stack}


def _extract_adx_signal(adx: dict) -> dict:
    """ADX directional signal from direction field."""
    direction = adx.get("direction", "")
    value = adx.get("value", 0)
    trend_strength = adx.get("trend_strength", "weak")
    if "bullish" in direction:
        signal = "bullish"
    elif "bearish" in direction:
        signal = "bearish"
    else:
        signal = "neutral"
    return {"signal": signal, "value": value, "trend_strength": trend_strength}


def _extract_wavetrend_signal(wt: dict) -> dict:
    """WaveTrend directional signal from crossover/zone."""
    if not wt or not isinstance(wt, dict):
        return {"signal": "neutral"}
    signal_raw = wt.get("signal")
    zone = wt.get("zone", "")
    cross = wt.get("cross", "")
    if signal_raw == "gold_buy":
        return {"signal": "bullish", "zone": zone, "cross": cross}
    if signal_raw == "buy":
        return {"signal": "bullish", "zone": zone, "cross": cross}
    if signal_raw == "sell":
        return {"signal": "bearish", "zone": zone, "cross": cross}
    # Fallback: use zone
    if "overbought" in zone:
        return {"signal": "bearish", "zone": zone, "cross": cross}
    if "oversold" in zone:
        return {"signal": "bullish", "zone": zone, "cross": cross}
    # Fallback: use wt1 position
    wt1 = wt.get("wt1", 0)
    if wt1 > 20:
        return {"signal": "bullish", "zone": zone, "cross": cross, "wt1": wt1}
    if wt1 < -20:
        return {"signal": "bearish", "zone": zone, "cross": cross, "wt1": wt1}
    return {"signal": "neutral", "zone": zone, "cross": cross}


def _extract_stochrsi_signal(srsi: dict) -> dict:
    """Stochastic RSI directional signal from K/D crossover and zone."""
    if not srsi or not isinstance(srsi, dict):
        return {"signal": "neutral"}
    k = srsi.get("k", 50)
    d = srsi.get("d", 50)
    signal_text = srsi.get("signal", "")
    if "overbought" in signal_text.lower():
        return {"signal": "bearish", "k": k, "d": d}
    if "oversold" in signal_text.lower():
        return {"signal": "bullish", "k": k, "d": d}
    # K crossing above D → bullish momentum
    if k > d:
        return {"signal": "bullish", "k": k, "d": d}
    if k < d:
        return {"signal": "bearish", "k": k, "d": d}
    return {"signal": "neutral", "k": k, "d": d}


def _extract_cvd_signal(cvd: dict) -> dict:
    """CVD directional signal from trend direction."""
    if not cvd or not isinstance(cvd, dict):
        return {"signal": "neutral"}
    trend = cvd.get("trend", "flat")
    value = cvd.get("value", 0)
    if trend == "rising":
        return {"signal": "bullish", "trend": trend, "value": value}
    if trend == "falling":
        return {"signal": "bearish", "trend": trend, "value": value}
    return {"signal": "neutral", "trend": trend, "value": value}


def _extract_order_flow_signal(of: dict) -> dict:
    """Order flow directional signal from delta direction."""
    if not of or not isinstance(of, dict):
        return {"signal": "neutral"}
    sig = of.get("signal", "neutral")
    delta = of.get("delta", 0)
    divergence = of.get("divergence", False)
    if "buying" in sig:
        return {"signal": "bullish", "delta": delta, "divergence": divergence}
    if "selling" in sig:
        return {"signal": "bearish", "delta": delta, "divergence": divergence}
    return {"signal": "neutral", "delta": delta, "divergence": divergence}


def _extract_volume_signal(vol: dict) -> dict:
    """Volume signal — not directional, but confirms/rejects direction.

    Stored as "neutral" because volume alone doesn't have a bullish/bearish
    stance; it amplifies whatever direction the other indicators show.
    The learning engine skips neutral signals, but the ratio and raw signal
    text are preserved for future analysis.
    """
    if not vol or not isinstance(vol, dict):
        return {"signal": "neutral"}
    ratio = vol.get("ratio", 1.0)
    raw_signal = vol.get("signal", "")
    current = vol.get("current", 0)
    avg = vol.get("avg_20", 0)
    return {
        "signal": "neutral",
        "ratio": ratio,
        "current": current,
        "avg_20": avg,
        "raw_signal": raw_signal,
    }


def _extract_bollinger_signal(boll: dict) -> dict:
    """Bollinger Bands directional signal from position within bands."""
    if not boll or not isinstance(boll, dict):
        return {"signal": "neutral"}
    position_pct = boll.get("position_pct", 50)
    band_width = boll.get("band_width")
    raw_signal = boll.get("signal", "")
    # Upper band area → bearish (overbought), lower band area → bullish (oversold)
    if position_pct > 80:
        return {"signal": "bearish", "position_pct": position_pct, "band_width": band_width}
    if position_pct < 20:
        return {"signal": "bullish", "position_pct": position_pct, "band_width": band_width}
    return {"signal": "neutral", "position_pct": position_pct, "band_width": band_width}


def _extract_atr_signal(atr: dict) -> dict:
    """ATR — volatility context, not directional. Stored for SL sizing reference."""
    if not atr or not isinstance(atr, dict):
        return {"signal": "neutral"}
    return {
        "signal": "neutral",
        "value": atr.get("value", 0),
        "pct": atr.get("pct", 0),
    }


# Registry: indicator name → extraction function
# Only indicators with a meaningful directional signal are tracked
# by the learning engine for accuracy.  Non-directional indicators
# (ATR, volume) are included for context but have signal="neutral".
_INDICATOR_EXTRACTORS = {
    "rsi": _extract_rsi_signal,
    "macd": _extract_macd_signal,
    "ema_stack": _extract_ema_signal,
    "adx": _extract_adx_signal,
    "wavetrend": _extract_wavetrend_signal,
    "stoch_rsi": _extract_stochrsi_signal,
    "cvd": _extract_cvd_signal,
    "order_flow": _extract_order_flow_signal,
    "volume": _extract_volume_signal,
    "bollinger": _extract_bollinger_signal,
    "atr": _extract_atr_signal,
}


def _extract_indicator_signals(
    indicator_data: dict,
    symbol: str,
    timeframe: str = "",
    indicator_configs: list | None = None,
) -> dict:
    """
    Extract per-indicator directional signals from substrate.market.indicators.

    Takes the raw indicator data structure produced by CollectOHLCV:
        {symbol: {timeframe: {indicator_name: result_dict, ...}, ...}, ...}

    Returns a flat dict of normalised signals:
        {indicator_name: {signal: "bullish"|"bearish"|"neutral", ...}, ...}

    The "signal" field is what the learning engine (analyzer.py, combination.py)
    uses to track per-indicator accuracy and compute adjusted weights.

    Each indicator's signal direction is determined by the same logic that
    ScoreConfluence uses for scoring, ensuring consistency between what
    triggered the trade and what the learning engine evaluates.

    Config-driven filtering:
        If indicator_configs is provided (from strategy YAML), only indicators
        with weight > 0 are extracted. This ensures the learning engine tracks
        only indicators that actually influenced the trade decision — not noise
        from indicators the strategy doesn't use.

        If indicator_configs is None, all known extractors are used (backward
        compat for callers that don't pass config).

    Args:
        indicator_data: substrate.market.indicators — nested dict by symbol/timeframe.
        symbol: Trade symbol (e.g. "BTCUSDT").
        timeframe: Primary timeframe (e.g. "4H"). If empty, uses first available.
        indicator_configs: List of indicator config dicts from strategy YAML.
            Each dict has "name" and "weight" keys. If None, all known
            indicators are extracted. If empty list, no indicators are extracted.

    Returns:
        Dict of {indicator_name: {signal: str, ...relevant_fields}}.
        Empty dict if no data found for the symbol/timeframe.
    """
    if not indicator_data or not symbol:
        return {}

    # Determine which indicators the strategy actually uses.
    # Only extract signals for indicators with weight > 0 — these are the
    # ones that influenced the trade decision via ScoreConfluence.
    # This keeps the learning loop tight: accuracy is only tracked for
    # indicators that contributed to the confluence score.
    if indicator_configs is not None:
        active_indicators = {
            cfg.get("name", "")
            for cfg in indicator_configs
            if cfg.get("weight", 0) > 0
        }
        if not active_indicators:
            # No indicators configured → nothing to extract
            return {}
    else:
        # Backward compat: extract all known indicators
        active_indicators = set(_INDICATOR_EXTRACTORS.keys())

    # Navigate to the symbol's indicator data
    sym_data = indicator_data.get(symbol)
    if not sym_data or not isinstance(sym_data, dict):
        return {}

    # Pick the timeframe
    if timeframe and timeframe in sym_data:
        tf_data = sym_data[timeframe]
    else:
        # Fall back to first available timeframe with valid data
        for tf_key in sym_data:
            tf_data = sym_data[tf_key]
            if isinstance(tf_data, dict) and tf_data.get("ok"):
                timeframe = tf_key
                break
        else:
            return {}

    if not isinstance(tf_data, dict) or not tf_data.get("ok"):
        return {}

    # Extract signals only for active (strategy-configured) indicators
    signals = {}
    for ind_name in active_indicators:
        if ind_name not in _INDICATOR_EXTRACTORS:
            continue
        extractor = _INDICATOR_EXTRACTORS[ind_name]
        raw = tf_data.get(ind_name)
        if raw is None:
            continue
        if not isinstance(raw, dict):
            # Some indicators return lists (sr_levels, trendlines) — skip
            continue
        try:
            extracted = extractor(raw)
            if extracted and isinstance(extracted, dict):
                signals[ind_name] = extracted
        except Exception:
            _log.debug("Failed to extract signal for %s", ind_name, exc_info=True)

    return signals


# ---------------------------------------------------------------------------
# DB recording functions
# ---------------------------------------------------------------------------

def _record_trade_entry(trade_approved: dict, strategy_name: str,
                        strategy_uid: str = "legacy",
                        signal_states: dict = None,
                        trajectory_data: dict = None,
                        indicator_data: dict = None,
                        indicator_configs: list | None = None,
                        now_iso: str | None = None) -> None:
    """
    Record a new trade entry in the trade_learning table.

    Called in both paper and live modes so the learning engine
    always has data to work with.

    Args:
        trade_approved: Dict with trade details (symbol, direction, score, etc.)
        strategy_name: Strategy name for DB grouping.
        strategy_uid: Stable strategy UID for learning data isolation.
        signal_states: Dict of {symbol: label} from substrate.analysis.signal_states.
        trajectory_data: Dict of trajectory info from substrate.market.pre_trade_context.
        indicator_data: Dict of {symbol: {tf: {indicator: result}}} from substrate.market.indicators.
        indicator_configs: List of indicator config dicts from strategy YAML.
            Each dict has "name" and "weight" keys. Only indicators with
            weight > 0 are extracted, keeping the learning loop tight.
    """
    try:
        from core.database import db_conn
        import json as _json

        # Build signals_at_entry_json from per-indicator signal data
        # This provides the learning engine with precise, objective data about
        # what each indicator was showing at trade entry time — not just a
        # subjective label, but actual values and directional signals.
        #
        # Format: {rsi: {signal: "bullish", value: 65.3}, macd: {signal: "bullish", bias: "bullish_growing"}, ...}
        # This is what the learning engine needs to determine which indicators
        # were actually correct predictors of the trade outcome.
        signals_json = ""
        if trade_approved:
            symbol = trade_approved.get("symbol", "")
            timeframe = trade_approved.get("timeframe", "")
            # Extract per-indicator signals from current market data.
            # Only extract indicators the strategy actually uses (weight > 0)
            # so the learning engine tracks accuracy of relevant indicators only.
            indicator_signals = _extract_indicator_signals(
                indicator_data, symbol, timeframe,
                indicator_configs=indicator_configs,
            )
            # Also include the confluence label for backward compatibility
            if signal_states and symbol:
                label = signal_states.get(symbol, "")
                if label:
                    indicator_signals["_confluence_label"] = label
            if indicator_signals:
                signals_json = _json.dumps(indicator_signals)

        # Extract trajectory data from pre_trade_context
        # This provides the learning engine with trajectory pattern and coincidence risk
        trajectory_pattern = ""
        coincidence_risk = ""
        if trajectory_data and trade_approved:
            symbol = trade_approved.get("symbol", "")
            traj = trajectory_data.get(symbol, {})
            if isinstance(traj, dict):
                trajectory_pattern = traj.get("trajectory_type", "")
                coincidence_risk = traj.get("coincidence_risk", "")

        # Extract LLM tracking fields from trade_approved
        # These are recorded in trade_learning for analysis:
        # "Does LLM validation actually improve trade outcomes?"
        llm_verdict = trade_approved.get("llm_verdict") if trade_approved else None
        llm_reason = trade_approved.get("llm_reason") if trade_approved else None
        llm_model = trade_approved.get("llm_model") if trade_approved else None
        llm_enabled = trade_approved.get("llm_enabled", False) if trade_approved else False
        llm_override = trade_approved.get("llm_override", False) if trade_approved else False

        with db_conn() as conn:
            conn.execute(
                """INSERT INTO trade_learning
                   (strategy_name, strategy_uid, symbol, direction, entry_time,
                    confluence_score_at_entry, signals_at_entry_json,
                    pre_trade_trajectory_pattern, pre_trade_coincidence_risk,
                    llm_verdict, llm_reason, llm_model, llm_enabled, llm_override)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    strategy_name,
                    strategy_uid,
                    trade_approved.get("symbol", ""),
                    trade_approved.get("direction", ""),
                    now_iso or _now_iso(),
                    trade_approved.get("score", 0),
                    signals_json,
                    trajectory_pattern,
                    coincidence_risk,
                    llm_verdict,
                    llm_reason,
                    llm_model,
                    1 if llm_enabled else 0,
                    1 if llm_override else 0,
                ),
            )
    except Exception as e:
        _log.warning("Failed to record trade entry in DB: %s", e)


def _record_trade_exit(symbol: str, position: dict, exit_reason: str,
                       pnl: dict, strategy_name: str,
                       strategy_uid: str = "legacy",
                       now_iso: str | None = None) -> None:
    """
    Update trade_learning table with exit data.

    Uses a subquery to find the most recent open trade for this symbol,
    because SQLite does not support ORDER BY / LIMIT in UPDATE statements.
    """
    try:
        from core.database import db_conn

        with db_conn() as conn:
            # SQLite-safe: subquery finds the single row to update
            conn.execute(
                """UPDATE trade_learning
                   SET exit_time = ?,
                       outcome = ?,
                       pnl_pct = ?,
                       pnl_usdt = ?,
                       exit_reason = ?,
                       sl_hit = ?,
                       trailing_stop_hit = ?
                   WHERE id = (
                       SELECT id FROM trade_learning
                       WHERE symbol = ?
                         AND exit_time IS NULL
                         AND strategy_name = ?
                         AND strategy_uid = ?
                       ORDER BY entry_time DESC
                       LIMIT 1
                   )""",
                (
                    now_iso or datetime.now(timezone.utc).isoformat(),
                    "win" if pnl["pnl_usdt"] >= 0 else "loss",
                    pnl["pnl_pct"],
                    pnl["pnl_usdt"],
                    exit_reason,
                    1 if "sl" in exit_reason.lower() else 0,
                    1 if "trailing" in exit_reason.lower() else 0,
                    symbol,
                    strategy_name,
                    strategy_uid,
                ),
            )
    except Exception as e:
        _log.warning("Failed to record trade exit in DB: %s", e)


def _compute_pnl(position: dict) -> dict:
    """Compute gross PnL for a closing position.

    Delegates to core.position_sizing.compute_pnl for the actual calculation.
    This wrapper extracts position dict fields and maps mark_price to exit_price.

    Returns gross P&L (no fees). Live trading uses broker fills which
    already include actual fees — never apply compute_net_pnl to live data.
    """
    from core.position_sizing import compute_pnl

    return compute_pnl(
        entry_price=position.get("entry_price", 0),
        exit_price=position.get("mark_price", 0),
        direction=position.get("direction", "Long"),
        size_usdt=position.get("size_usdt", 0),
    )


@register_enzyme
class RecordTradeOutcome(Enzyme):
    """
    Synthase enzyme: record trade entries and exits to the learning database.

    This enzyme separates the learning/recording concern from the
    execution concern. ExecuteTrade and ExecuteExit handle order
    placement and portfolio updates; RecordTradeOutcome handles
    database recording for the learning engine.

    Activates when:
      - action == 'trade_open' AND trade_approved is set (entry recording)
      - action == 'trade_closed' AND exit_approved is set (exit recording)

    Does NOT modify the substrate — side-effect only (DB writes).
    """

    name = "RecordTradeOutcome"
    enzyme_class = EnzymeClass.SYNTHASE
    priority = -1  # Runs after Transporters (priority 0)

    def requires(self) -> list[str]:
        return ["decisions.action in ('trade_open', 'trade_closed')"]

    def prohibits(self) -> list[str]:
        return []

    def can_activate(self, substrate: Substrate) -> bool:
        action = substrate.decisions.get("action", "wait")
        return action in ("trade_open", "trade_closed")

    def transform(self, substrate: Substrate) -> Substrate:
        """Record trade entry or exit to the learning database."""
        action = substrate.decisions.get("action", "wait")
        strategy_name = substrate.strategy.get("name", "")
        strategy_uid = substrate.strategy.get("uid", "legacy")
        now_iso = substrate.now_iso()

        if action == "trade_open":
            trade_approved = substrate.decisions.get("trade_approved")
            if trade_approved:
                # Pass indicator data so _extract_indicator_signals can build
                # per-indicator signals for the learning engine.
                # This is the critical link: without it, signals_at_entry_json
                # is empty and the learning feedback loop is dormant.
                signal_states = substrate.analysis.get("signal_states", {})
                trajectory_data = substrate.market.get("pre_trade_context", {})
                indicator_data = substrate.market.get("indicators", {})
                indicator_configs = substrate.cfg("indicators")
                _record_trade_entry(
                    trade_approved, strategy_name, strategy_uid,
                    signal_states=signal_states,
                    trajectory_data=trajectory_data,
                    indicator_data=indicator_data,
                    indicator_configs=indicator_configs,
                    now_iso=now_iso,
                )
                self._log.info(
                    "Recorded trade entry: %s %s",
                    trade_approved.get("direction", "?"),
                    trade_approved.get("symbol", "?"),
                )

        elif action == "trade_closed":
            exit_approved = substrate.decisions.get("exit_approved")
            if exit_approved:
                symbol = exit_approved.get("symbol", "?")
                exit_reason = exit_approved.get("reason", "unknown")

                # Find the position that was just closed
                # (it may already be removed from open_positions by ExecuteExit,
                #  so we compute PnL from exit_approved data if available)
                pnl = {"pnl_pct": 0.0, "pnl_usdt": 0.0}

                # Try to find position in portfolio (may still be there briefly)
                for pos in substrate.portfolio.get("open_positions", []):
                    if pos.get("symbol") == symbol:
                        pnl = _compute_pnl(pos)
                        break

                # Override with exit_approved PnL if available
                if "pnl_pct" in exit_approved:
                    pnl = {
                        "pnl_pct": exit_approved.get("pnl_pct", 0.0),
                        "pnl_usdt": exit_approved.get("pnl_usdt", 0.0),
                    }

                _record_trade_exit(symbol, exit_approved, exit_reason, pnl,
                                   strategy_name, strategy_uid, now_iso=now_iso)
                self._log.info(
                    "Recorded trade exit: %s reason=%s pnl=%.2f%%",
                    symbol, exit_reason, pnl["pnl_pct"],
                )

        return substrate

    def flux_score(self, substrate: Substrate) -> float:
        """
        Dynamic flux: high when a trade just happened (must record),
        low otherwise.
        """
        if not self.can_activate(substrate):
            return 0.0
        action = substrate.decisions.get("action", "wait")
        # Trade just opened or closed — record immediately
        if action == "trade_open":
            return 4.0
        if action == "trade_closed":
            return 4.0
        return 0.5