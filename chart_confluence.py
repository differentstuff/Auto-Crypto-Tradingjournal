"""
chart_confluence.py — Multi-timeframe confluence scoring engine.
Single public function: confluence_score().
All _*_weight helpers are private to this module.
Extracted from chart_context.py.
"""
import threading as _threading
import time as _time
from ccxt_client import get_binance_price, get_binance_ticker_change

# VIX cache: TTL-based (5 minutes) to avoid hammering yfinance during scans.
_vix_cache: dict = {"value": None, "ts": 0.0}
_vix_lock = _threading.Lock()
_VIX_TTL = 300  # 5 minutes

def _get_vix_multiplier() -> float:
    """
    Returns a regime multiplier based on VIX level.
    Cached for 5 minutes to avoid hammering yfinance during scans.
    - VIX > 30 (high fear / risk-off): 0.80 — suppress bullish confluence
    - VIX ≤ 30 (normal / risk-on):    1.00 — no suppression
    Returns 1.0 on any error or if yfinance unavailable.
    """
    global _vix_cache
    now = _time.time()
    with _vix_lock:
        if now - _vix_cache["ts"] < _VIX_TTL and _vix_cache["value"] is not None:
            return _vix_cache["value"]
    try:
        from market_context import get_macro_regime
        regime = get_macro_regime()
        vix = regime.get("vix")
        if vix is None:
            multiplier = 1.0
        elif vix > 30:
            multiplier = 0.80
        else:
            multiplier = 1.0
        with _vix_lock:
            _vix_cache = {"value": multiplier, "ts": now}
        return multiplier
    except Exception:
        return 1.0


# Correlated pairs where cross-exchange divergence is meaningful.
# All must be liquid USDT-M perpetuals available on both Bitget and Binance.
SMT_SYMBOLS = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"}

# For each symbol, its correlated counterpart to compare direction against.
# Both must be available via get_binance_ticker_change() below.
SMT_PAIRS = {
    "BTCUSDT": "ETHUSDT",
    "ETHUSDT": "BTCUSDT",
    "SOLUSDT": "ETHUSDT",
    "BNBUSDT": "BTCUSDT",
    "XRPUSDT": "BTCUSDT",
}


def _rsi_weight(rsi_val: float) -> float:
    """RSI contribution: ±1 at extremes, 0 at 50. Dead-band ±5 around 50."""
    if rsi_val > 55:   return min((rsi_val - 50) / 30.0,  1.0)
    if rsi_val < 45:   return max((rsi_val - 50) / 30.0, -1.0)
    return 0.0


def _macd_weight(macd: dict) -> float:
    """MACD contribution: full ±1 when aligned + growing, ±0.5 when aligned but fading."""
    trend    = macd.get("trend", "")
    hist_dir = macd.get("histogram_trend", "")
    if trend == "bullish":
        return 1.0 if hist_dir == "growing" else 0.5
    if trend == "bearish":
        return -1.0 if hist_dir == "growing" else -0.5
    return 0.0


def _ema_weight(ema: dict) -> float:
    """EMA contribution: ±1 fully aligned stack + price, ±0.5 partial."""
    al = ema.get("alignment", "")
    sk = ema.get("stack", "")
    if "fully bullish" in al and "bullish" in sk: return  1.0
    if "fully bearish" in al and "bearish" in sk: return -1.0
    if "bullish" in sk or "fully bullish" in al:  return  0.5
    if "bearish" in sk or "fully bearish" in al:  return -0.5
    return 0.0


def _adx_weight(adx: dict) -> float:
    """ADX contribution: direction × trend strength (ADX value / 50, capped at 1)."""
    direction = adx.get("direction", "")
    adx_val   = adx.get("value", 0)
    strength  = min(adx_val / 50.0, 1.0)
    if "bullish" in direction:  return  strength
    if "bearish" in direction:  return -strength
    return 0.0


def _wt_weight(wt: dict) -> float:
    """
    WaveTrend contribution (Cipher A/B).
    Crossover signals in OB/OS zones are the strongest inputs (±1.0).
    Gold signal (extreme oversold cross) = max bullish (1.0).
    Position-only (no cross) scales WT1 value like RSI: ±0.5 max.
    """
    if not wt:
        return 0.0
    signal = wt.get("signal")
    if signal == "gold_buy":   return  1.0
    if signal == "buy":        return  0.85
    if signal == "sell":       return -0.85
    # No fresh cross — use WT1 position scaled to ±0.5
    wt1 = wt.get("wt1", 0.0)
    return max(-0.5, min(0.5, wt1 / 60.0))


def _volume_weight(inds: dict, directional_score: float) -> float:
    """
    Volume confirms the dominant direction.
    High volume (>1.5×) amplifies consensus by ±0.5.
    Low volume (<0.7×) dampens consensus by ∓0.25.
    Direction taken from the four other signals' net score.
    """
    ratio = inds.get("volume", {}).get("ratio", 1.0)
    sign  = 1 if directional_score > 0 else (-1 if directional_score < 0 else 0)
    if ratio > 1.5:
        return  0.5 * sign
    if ratio < 0.7:
        return -0.25 * sign
    return 0.0


def _cvd_weight(cvd: dict) -> float:
    """CVD rising = bullish signal (+0.4), falling = bearish (-0.4), flat = 0."""
    trend = cvd.get("trend", "flat")
    return 0.4 if trend == "rising" else (-0.4 if trend == "falling" else 0.0)


def _smt_weight(inds: dict, symbol: str) -> float:
    """
    Cross-exchange divergence check (SMT-inspired).
    Returns +0.15 when Bitget vs Binance prices diverge >= 0.5%
    (price dislocation at this level = potential SMT signal).
    Returns 0.0 when prices agree or data unavailable.
    """
    if symbol not in SMT_SYMBOLS:
        return 0.0
    bitget_price = (inds.get("ema") or {}).get("current_price")
    if not bitget_price:
        return 0.0
    try:
        binance_price = get_binance_price(symbol)
    except Exception:
        return 0.0
    if binance_price is None:
        return 0.0
    delta_pct = abs(bitget_price - binance_price) / bitget_price
    return 0.15 if delta_pct >= 0.005 else 0.0


def _smt_direction_weight(inds: dict, symbol: str) -> float:
    """
    True SMT divergence: compare 24h direction of symbol vs its correlated pair.

    Returns +0.15 when the symbol is going UP while its pair goes DOWN
    (pair fails to confirm the move — bullish SMT divergence at lows).
    Returns -0.15 when the symbol is going DOWN while its pair goes UP
    (pair fails to confirm — bearish SMT divergence at highs).
    Returns 0.0 when both move in the same direction or data unavailable.

    Threshold: divergence only counts when the directions differ by >= 1%.
    """
    pair = SMT_PAIRS.get(symbol)
    if not pair:
        return 0.0
    try:
        sym_chg  = get_binance_ticker_change(symbol)
        pair_chg = get_binance_ticker_change(pair)
    except Exception:
        return 0.0
    if sym_chg is None or pair_chg is None:
        return 0.0
    # Same direction = no divergence
    if sym_chg * pair_chg > 0:
        return 0.0
    # Directions differ — check magnitude
    if abs(sym_chg - pair_chg) < 1.0:
        return 0.0
    # Symbol up, pair down → bullish SMT
    if sym_chg > 0 and pair_chg < 0:
        return 0.15
    # Symbol down, pair up → bearish SMT
    if sym_chg < 0 and pair_chg > 0:
        return -0.15
    return 0.0


def _mfi_weight(wt: dict) -> float:
    """
    MFI (Money Flow) contribution from WaveTrend data.
    MFI > 10 = capital inflow (bullish +0.3), MFI < -10 = outflow (bearish -0.3).
    Dead-band ±10 avoids noise near zero.
    """
    mfi = wt.get("mfi", 0.0) if wt else 0.0
    if mfi > 10:   return  0.3
    if mfi < -10:  return -0.3
    return 0.0


def _get_tf_weights(ctx: dict, tf: str, symbol: str = "") -> list:
    """Return signal weights for a single timeframe."""
    inds = ctx.get(tf, {}).get("indicators", {})
    if not inds.get("ok"):
        return []
    base = [
        _rsi_weight(inds.get("rsi",  {}).get("value", 50)),
        _macd_weight(inds.get("macd", {})),
        _ema_weight(inds.get("ema",   {})),
        _adx_weight(inds.get("adx",   {})),
        _wt_weight(inds.get("wavetrend", {})),
        _mfi_weight(inds.get("wavetrend", {})),
        _cvd_weight(inds.get("cvd", {})),
        _smt_weight(inds, symbol),
        _smt_direction_weight(inds, symbol),
    ]
    base.append(_volume_weight(inds, sum(base)))
    return base


def confluence_score(symbol: str, timeframes: list = None, ctx: dict = None) -> dict:
    """
    Aggregate RSI/MACD/EMA/ADX direction signals across timeframes with
    magnitude weighting — strong signals contribute more than weak ones.
    Returns {score, max, bullish, bearish, label, details}.
    Pass ctx to reuse an already-computed get_chart_context() result.
    """
    tfs = timeframes or ["4H", "1D"]
    if ctx is None:
        from chart_context import get_chart_context  # lazy to avoid circular import
        ctx = get_chart_context(symbol, tfs)

    total_score = 0.0
    details     = []

    for tf in tfs:
        inds = ctx.get(tf, {}).get("indicators", {})
        if not inds.get("ok"):
            continue

        rsi_w  = _rsi_weight(inds.get("rsi",  {}).get("value", 50))
        macd_w = _macd_weight(inds.get("macd", {}))
        ema_w  = _ema_weight(inds.get("ema",   {}))
        adx_w  = _adx_weight(inds.get("adx",   {}))
        wt_w   = _wt_weight(inds.get("wavetrend", {}))
        mfi_w  = _mfi_weight(inds.get("wavetrend", {}))
        cvd_w  = _cvd_weight(inds.get("cvd", {}))
        smt_w     = _smt_weight(inds, symbol)
        smt_dir_w = _smt_direction_weight(inds, symbol)
        base_score = rsi_w + macd_w + ema_w + adx_w + wt_w + mfi_w + cvd_w + smt_w + smt_dir_w
        vol_w  = _volume_weight(inds, base_score)

        tf_score = base_score + vol_w
        total_score += tf_score

        pos = round(sum(w for w in (rsi_w, macd_w, ema_w, adx_w, wt_w, mfi_w, cvd_w, smt_w, smt_dir_w, vol_w) if w > 0), 1)
        neg = round(sum(w for w in (rsi_w, macd_w, ema_w, adx_w, wt_w, mfi_w, cvd_w, smt_w, smt_dir_w, vol_w) if w < 0), 1)
        details.append(f"{tf}: +{pos}/{neg}")

    # Apply macro regime multiplier (VIX-based, cached 5 min)
    vix_mult = _get_vix_multiplier()
    if vix_mult != 1.0:
        total_score = round(total_score * vix_mult, 2)

    max_val = float(len(tfs) * 6.50)  # cross-exchange SMT +0.15 + direction SMT +0.15 per TF
    pct     = total_score / max_val if max_val else 0.0

    # Thresholds: ±0.33 ≈ net 1/3 of max weight aligned; ±0.60 = strong consensus
    if pct >= 0.60:
        label = "Strong Bullish"
    elif pct >= 0.33:
        label = "Bullish"
    elif pct <= -0.60:
        label = "Strong Bearish"
    elif pct <= -0.33:
        label = "Bearish"
    else:
        label = "Neutral"

    bull_total = round(sum(w for tf in tfs
                           for inds_w in [_get_tf_weights(ctx, tf, symbol)]
                           for w in inds_w if w > 0), 1)
    bear_total = round(abs(sum(w for tf in tfs
                               for inds_w in [_get_tf_weights(ctx, tf, symbol)]
                               for w in inds_w if w < 0)), 1)

    return {
        "score":   round(total_score, 2),
        "max":     max_val,
        "bullish": bull_total,
        "bearish": bear_total,
        "label":   label,
        "details": details,
        "vix_regime_active": vix_mult != 1.0,
    }
