import traceback

from flask import Blueprint, request

from database import db_conn
from helpers import _ok, _err
import ai_live_trade
import bitget_client
import blofin_client


_SECTOR_MAP: dict[str, str] = {
    "BTCUSDT": "BTC",
    "ETHUSDT": "ETH", "LDOUSDT": "ETH", "STRKUSDT": "ETH", "ENSUSDT": "ETH",
    "SOLUSDT": "L1",  "BNBUSDT": "L1",  "XRPUSDT": "L1",  "ADAUSDT": "L1",
    "AVAXUSDT": "L1", "DOTUSDT": "L1",  "ATOMUSDT": "L1", "NEARUSDT": "L1",
    "TRXUSDT": "L1",  "XLMUSDT": "L1",  "TONUSDT": "L1",  "FTMUSDT": "L1",
    "ALGOUSDT": "L1", "EGLDUSDT": "L1", "SUIUSDT": "L1",  "APTUSDT": "L1",
    "INJUSDT": "L1",  "SEIUSDT": "L1",  "ICPUSDT": "L1",  "HBARUSDT": "L1",
    "KASUSDT": "L1",  "LTCUSDT": "L1",  "BCHUSDT": "L1",  "MINAUSDT": "L1",
    "TIAUSDT": "L1",  "STXUSDT": "L1",
    "MATICUSDT": "L2", "ARBUSDT": "L2", "OPUSDT": "L2",   "ZKUSDT": "L2",
    "METISUSDT": "L2",
    "UNIUSDT": "DeFi", "AAVEUSDT": "DeFi", "LINKUSDT": "DeFi", "CRVUSDT": "DeFi",
    "MKRUSDT": "DeFi", "SNXUSDT": "DeFi",  "COMPUSDT": "DeFi", "DYDXUSDT": "DeFi",
    "CAKEUSDT": "DeFi","GMXUSDT": "DeFi",  "PENDLEUSDT": "DeFi","JUPUSDT": "DeFi",
    "SUSHIUSDT": "DeFi","RUNEUSDT": "DeFi",
    "FETUSDT": "AI",   "RENDERUSDT": "AI", "WLDUSDT": "AI",  "TAOUSDT": "AI",
    "GRTUSDT": "AI",   "AGIXUSDT": "AI",   "OCEANUSDT": "AI","ARKMUSDT": "AI",
    "DOGEUSDT": "Meme","SHIBUSDT": "Meme", "PEPEUSDT": "Meme","WIFUSDT": "Meme",
    "BONKUSDT": "Meme","BOMEUSDT": "Meme", "FLOKIUSDT": "Meme","MOGUSDT": "Meme",
    "POPCATUSDT": "Meme","TURBOUSDT": "Meme",
    "ORDIUSDT": "BTC Eco","SATSUSDT": "BTC Eco",
    "SANDUSDT": "Gaming","AXSUSDT": "Gaming","GALAUSDT": "Gaming","IMXUSDT": "Gaming",
    "MANAUSDT": "Gaming","APEUSDT": "Gaming",
}


def _classify_sector(symbol: str) -> str:
    return _SECTOR_MAP.get(symbol.upper(), "Other")


def _compute_portfolio_risk(positions: list, equity: float) -> dict:
    total_long   = sum(p.get("size_usdt", 0) for p in positions if p.get("direction") == "Long")
    total_short  = sum(p.get("size_usdt", 0) for p in positions if p.get("direction") == "Short")
    total_margin = sum(float(p.get("margin_usdt") or 0) for p in positions)
    margin_pct   = round(total_margin / equity * 100, 1) if equity else 0.0

    sector_usd: dict[str, float] = {}
    for p in positions:
        sec = _classify_sector(p.get("symbol", ""))
        sector_usd[sec] = sector_usd.get(sec, 0) + float(p.get("size_usdt") or 0)

    by_sector = sorted(
        [{"sector": k, "usd": round(v, 2)} for k, v in sector_usd.items()],
        key=lambda x: x["usd"], reverse=True,
    )
    total_notional = total_long + total_short
    top_sector_pct = round(by_sector[0]["usd"] / total_notional * 100, 1) if total_notional and by_sector else 0.0

    return {
        "total_long_usd":   round(total_long, 2),
        "total_short_usd":  round(total_short, 2),
        "net_exposure_usd": round(total_long - total_short, 2),
        "total_margin_usd": round(total_margin, 2),
        "margin_used_pct":  margin_pct,
        "top_sector_pct":   top_sector_pct,
        "by_sector":        by_sector,
        "position_count":   len(positions),
    }


bp = Blueprint("live", __name__)


@bp.route("/api/live/positions")
def api_live_positions():
    try:
        positions   = []
        total_eq    = 0.0
        total_avail = 0.0
        raw_equity  = {}

        try:
            positions  = bitget_client.get_open_positions()
            raw_equity = bitget_client.get_account_equity()
            total_eq    += float(raw_equity.get("accountEquity") or raw_equity.get("equity") or 0)
            total_avail += float(raw_equity.get("available") or 0)
        except Exception:
            pass
        try:
            if blofin_client.is_configured():
                positions += blofin_client.get_open_positions()
                bl_eq      = blofin_client.get_account_equity()
                total_eq    += float(bl_eq.get("equity") or 0)
                total_avail += float(bl_eq.get("available") or 0)
        except Exception:
            pass

        # Normalize to a consistent shape the frontend always expects
        equity = {
            **raw_equity,
            "accountEquity": str(round(total_eq,    8)),
            "available":     str(round(total_avail, 8)),
        }
        return _ok({"positions": positions, "equity": equity})
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/live/portfolio-risk")
def api_portfolio_risk():
    """GET /api/live/portfolio-risk — aggregated book risk metrics."""
    try:
        positions  = []
        total_eq   = 0.0
        try:
            positions  = bitget_client.get_open_positions()
            eq         = bitget_client.get_account_equity()
            total_eq  += float(eq.get("accountEquity") or 0)
        except Exception:
            pass
        try:
            if blofin_client.is_configured():
                positions += blofin_client.get_open_positions()
                bl_eq      = blofin_client.get_account_equity()
                total_eq  += float(bl_eq.get("equity") or 0)
        except Exception:
            pass
        return _ok(_compute_portfolio_risk(positions, equity=total_eq))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)


@bp.route("/api/live/pending-orders")
def api_live_pending_orders():
    try:
        orders = bitget_client.get_pending_orders()
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)

    with db_conn() as conn:
        tracked = [r[0] for r in conn.execute(
            "SELECT bitget_order_id FROM pending_limits WHERE bitget_order_id IS NOT NULL"
        ).fetchall()]

        # Backfill missing SL/TP on journal limits from Bitget preset values
        for order in orders.get("entry", []):
            oid     = order.get("order_id")
            preset_sl = order.get("preset_sl")
            preset_tp = order.get("preset_tp")
            if not oid or (not preset_sl and not preset_tp):
                continue
            sets, vals = [], []
            if preset_sl:
                sets.append("sl_price = ?");  vals.append(preset_sl)
            if preset_tp:
                sets.append("tp1_price = ?"); vals.append(preset_tp)
            if sets:
                vals.append(oid)
                conn.execute(
                    f"UPDATE pending_limits SET {', '.join(sets)} WHERE bitget_order_id=? AND sl_price IS NULL",
                    vals
                )
        conn.commit()

    return _ok({"bitget_orders": orders, "tracked_ids": tracked})


@bp.route("/api/live/analyze", methods=["POST"])
def api_live_analyze():
    try:
        position = request.get_json(force=True)
        if not position or not position.get("symbol"):
            return _err("position data with symbol required")
        return _ok(ai_live_trade.analyze_position(position))
    except Exception:
        traceback.print_exc()
        return _err("Internal server error", 500)
