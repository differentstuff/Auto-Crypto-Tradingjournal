"""
agent_chart_draw.py — Annotated trade chart generator.

Pure matplotlib implementation. Draws candlesticks with trade levels:
  - Direction badge (▲ LONG / ▼ SHORT) prominent top-left
  - Entry zone: shaded band between entry_low and entry_high
  - SL: thick red dashed line
  - TP1: bright green solid line
  - TP2: cyan solid line (clearly different from TP1)
  - Price label on right edge of every level line
  - Decision criteria text (top-right)
  - Volume bar subplot
  - S&R zones: colored by type (green=support, red=resistance), labeled, at-level highlighted

Returns base64-encoded PNG string. Returns "" on any failure.
"""
import base64
import io

import pandas as pd


def _merge_sr(levels, pct=0.003):
    """Merge S&R levels within pct of each other (by price ratio) into confluence zones."""
    if not levels:
        return []
    srt = sorted(levels, key=lambda x: x["price"])
    groups, cur = [], [srt[0]]
    for lv in srt[1:]:
        if (lv["price"] - cur[-1]["price"]) / cur[-1]["price"] <= pct:
            cur.append(lv)
        else:
            groups.append(cur); cur = [lv]
    groups.append(cur)
    out = []
    for g in groups:
        prices  = [x["price"] for x in g]
        touches = sum(x.get("touches", 1) for x in g)
        sup_cnt = sum(1 for x in g if x.get("type") == "support")
        out.append({
            "price":         sum(prices) / len(prices),
            "zone_min":      min(prices),
            "zone_max":      max(prices),
            "touches":       touches,
            "type":          "support" if sup_cnt > len(g) / 2 else "resistance",
            "is_confluence": len(g) > 1,
            "count":         len(g),
        })
    return out


def draw(
    candles: pd.DataFrame,
    symbol: str,
    direction: str,
    entry: float,
    sl: float,
    tp1: float,
    tp2: float,
    criteria: list = None,
    n_candles: int = 60,
    entry_high: float = None,
    sr_levels: list = None,
) -> str:
    """Returns base64-encoded PNG or '' on failure."""
    try:
        import matplotlib
        import matplotlib.pyplot as plt
        plt.switch_backend("Agg")
        import matplotlib.patches as mpatches
        from matplotlib.patches import Rectangle, FancyBboxPatch
        from matplotlib.lines import Line2D
    except ImportError:
        return ""

    if candles is None or candles.empty:
        return ""

    try:
        df = candles.tail(n_candles).copy().reset_index()
        n  = len(df)

        # ── Palette ──────────────────────────────────────────────────────────
        BG      = "#0d1117"
        SURFACE = "#161b22"
        BORDER  = "#30363d"
        UP_C    = "#26a69a"
        DOWN_C  = "#ef5350"
        MUTED   = "#8b949e"
        TEXT_C  = "#e6edf3"
        ENTRY_C = "#4A90D9"   # blue
        SL_C    = "#E05555"   # red
        TP1_C   = "#26D96B"   # bright green
        TP2_C   = "#4FC3F7"   # cyan — clearly different from TP1
        is_long = direction.lower() == "long"
        DIR_C   = TP1_C if is_long else SL_C   # badge color matches bias

        fig, (ax_price, ax_vol) = plt.subplots(
            2, 1, figsize=(14, 8),
            gridspec_kw={"height_ratios": [3, 1], "hspace": 0.04},
            facecolor=BG,
        )
        for ax in (ax_price, ax_vol):
            ax.set_facecolor(SURFACE)
            ax.tick_params(colors=MUTED, labelsize=8)
            for spine in ax.spines.values():
                spine.set_edgecolor(BORDER)
            ax.grid(color=BORDER, linewidth=0.4, alpha=0.5)

        # ── Candlesticks ─────────────────────────────────────────────────────
        W_BODY = 0.6
        W_WICK = 0.08
        for i, row in df.iterrows():
            o, h, l, c = (float(row.get(k, 0) or 0)
                          for k in ("open", "high", "low", "close"))
            color = UP_C if c >= o else DOWN_C
            ax_price.add_patch(Rectangle(
                (i - W_BODY / 2, min(o, c)), W_BODY, abs(c - o) or 1e-8,
                facecolor=color, edgecolor=color, linewidth=0.5, zorder=3,
            ))
            ax_price.add_patch(Rectangle(
                (i - W_WICK / 2, l), W_WICK, h - l,
                facecolor=color, edgecolor=color, linewidth=0, zorder=2,
            ))

        ax_price.set_xlim(-0.5, n - 0.5)

        # ── Y-axis range (include all levels) ────────────────────────────────
        y_vals = [float(v) for col in ("low", "high") for v in df[col].dropna() if v]
        for lv in (entry, entry_high, sl, tp1, tp2):
            if lv:
                y_vals.append(lv)
        if y_vals:
            y_lo, y_hi = min(y_vals), max(y_vals)
            pad = (y_hi - y_lo) * 0.10 or y_lo * 0.02
            ax_price.set_ylim(y_lo - pad, y_hi + pad)

        # ── Entry zone band ───────────────────────────────────────────────────
        ez_lo = entry or 0
        ez_hi = entry_high if (entry_high and entry_high != entry) else entry
        if ez_lo and ez_hi and ez_hi != ez_lo:
            ax_price.axhspan(ez_lo, ez_hi, alpha=0.13, color=ENTRY_C, zorder=1)

        # ── S&R zones (A+B+C+D+E) ────────────────────────────────────────────
        last_close = float(df["close"].iloc[-1]) if "close" in df.columns else 0
        merged_sr  = _merge_sr(sr_levels or [])
        for lvl in merged_sr[:6]:   # cap at 6 to avoid clutter
            is_sup = lvl["type"] == "support"
            col_hex = "#26D96B" if is_sup else "#EF5350"
            at_level = last_close and abs(lvl["price"] - last_close) / last_close <= 0.005

            # Zone band
            lo = lvl.get("zone_min", lvl["price"] * 0.997)
            hi = lvl.get("zone_max", lvl["price"] * 1.003)
            base_a = min(0.07 + (lvl["touches"] - 1) * 0.04, 0.38)
            alpha  = min(base_a + 0.18, 0.55) if at_level else base_a
            ax_price.axhspan(lo, hi, alpha=alpha, color=col_hex, zorder=1)

            # At-level: extra border line
            if at_level:
                ax_price.axhline(lvl["price"], color=col_hex, linewidth=0.9,
                                 linestyle="-", zorder=2, alpha=0.6)

            # Right-side label
            conf_tag = f" x{lvl['count']}" if lvl.get("is_confluence") else ""
            label = f"{'S' if is_sup else 'R'} {lvl['price']:.5g} x{lvl['touches']}{conf_tag}"
            if at_level:
                label += " !"
            ax_price.text(
                n - 0.5, lvl["price"], f"  {label}",
                color=col_hex, fontsize=7.5, fontweight="bold" if at_level else "normal",
                va="center", ha="left", zorder=5,
                bbox=dict(boxstyle="round,pad=0.18", facecolor=BG,
                          edgecolor=col_hex, alpha=0.7, linewidth=0.7),
            )

        # ── Level lines + right-side price labels ────────────────────────────
        def _draw_level(price, color, lw, ls, label):
            if not price:
                return None
            ax_price.axhline(price, color=color, linewidth=lw,
                             linestyle=ls, zorder=4, alpha=0.92)
            # Price label anchored to right edge of chart
            ax_price.text(
                n - 0.6, price, f"  {label}  {price:.5g}",
                color=color, fontsize=8, fontweight="bold",
                va="center", ha="left", zorder=6,
                bbox=dict(boxstyle="round,pad=0.2", facecolor=BG,
                          edgecolor=color, alpha=0.75, linewidth=0.8),
            )
            return Line2D([0], [0], color=color, linewidth=lw,
                          linestyle=ls, label=f"{label} {price:.5g}")

        legend_handles = []
        for handle in [
            _draw_level(entry, ENTRY_C, 1.8, "--", "Entry"),
            _draw_level(sl,    SL_C,    1.8, "--", "SL"),
            _draw_level(tp1,   TP1_C,   1.6, "-",  "TP1"),
            _draw_level(tp2,   TP2_C,   1.6, "-",  "TP2"),
        ]:
            if handle:
                legend_handles.append(handle)

        # ── Legend ────────────────────────────────────────────────────────────
        if legend_handles:
            ax_price.legend(
                handles=legend_handles, loc="upper left", fontsize=8,
                facecolor=BG, edgecolor=BORDER, labelcolor=TEXT_C,
                framealpha=0.85,
            )

        # ── Direction badge (top-left, above legend) ──────────────────────────
        dir_arrow = "▲  LONG" if is_long else "▼  SHORT"
        ax_price.text(
            0.01, 0.99, f" {dir_arrow} ",
            transform=ax_price.transAxes,
            fontsize=13, fontweight="bold", va="top", ha="left",
            color=DIR_C,
            bbox=dict(boxstyle="round,pad=0.4", facecolor=BG,
                      edgecolor=DIR_C, alpha=0.92, linewidth=1.5),
            zorder=10,
        )

        # ── Criteria text (top-right) ──────────────────────────────────────────
        crit_list = criteria or []
        if crit_list:
            crit = "\n".join(f"• {c}" for c in crit_list[:5])
            ax_price.text(
                0.99, 0.99, crit,
                transform=ax_price.transAxes,
                fontsize=7.5, va="top", ha="right", color="#cccccc",
                bbox=dict(boxstyle="round,pad=0.35", facecolor=BG,
                          edgecolor=BORDER, alpha=0.88),
            )

        # ── Title ─────────────────────────────────────────────────────────────
        parts = [symbol]
        if entry: parts.append(f"Entry {entry:.5g}")
        if sl:    parts.append(f"SL {sl:.5g}")
        if tp1:   parts.append(f"TP1 {tp1:.5g}")
        if tp2:   parts.append(f"TP2 {tp2:.5g}")
        ax_price.set_title("  ·  ".join(parts), color=MUTED, fontsize=8, pad=5)
        ax_price.set_xticks([])

        # ── Volume bars ───────────────────────────────────────────────────────
        if "volume" in df.columns:
            for i, row in df.iterrows():
                o = float(row.get("open", 0) or 0)
                c = float(row.get("close", 0) or 0)
                v = float(row.get("volume", 0) or 0)
                ax_vol.bar(i, v, width=W_BODY,
                           color=UP_C if c >= o else DOWN_C, alpha=0.6)
        ax_vol.set_xlim(-0.5, n - 0.5)
        ax_vol.set_ylabel("Vol", color=MUTED, fontsize=7)
        ax_vol.set_xticks([])

        # ── X-axis date labels ────────────────────────────────────────────────
        date_col = df.columns[0]
        step = max(1, n // 8)
        tick_xs = list(range(0, n, step))
        tick_labels = []
        for ix in tick_xs:
            ts = df[date_col].iloc[ix]
            try:
                tick_labels.append(pd.Timestamp(ts).strftime("%m/%d %H:%M"))
            except Exception:
                tick_labels.append(str(ix))
        ax_vol.set_xticks(tick_xs)
        ax_vol.set_xticklabels(tick_labels, rotation=30, ha="right",
                                fontsize=7, color=MUTED)

        # ── Encode ────────────────────────────────────────────────────────────
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=110, bbox_inches="tight",
                    facecolor=BG, edgecolor="none")
        plt.close(fig)
        buf.seek(0)
        return base64.b64encode(buf.read()).decode("utf-8")

    except Exception as e:
        import traceback
        print(f"[ChartDraw] draw() failed: {e}", flush=True)
        traceback.print_exc()
        return ""
