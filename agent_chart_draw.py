"""
agent_chart_draw.py — Annotated trade chart generator.

Pure matplotlib implementation (no mplfinance) — compatible with Python 3.13
and matplotlib ≥ 3.7. Draws candlesticks using Rectangle patches directly.

Generates a dark-themed OHLCV chart with:
  - Candlestick bodies + wicks
  - Entry (blue dashed), SL (red dashed), TP1/TP2 (green dashed) lines
  - Level legend (top-left)
  - Decision criteria text (top-right)
  - Volume bar subplot

Returns base64-encoded PNG string. Returns "" on any failure.
"""
import base64
import io

import pandas as pd


def draw(
    candles: pd.DataFrame,
    symbol: str,
    direction: str,
    entry: float,
    sl: float,
    tp1: float,
    tp2: float,
    criteria: list,
    n_candles: int = 60,
) -> str:
    """Returns base64-encoded PNG or "" on failure."""
    try:
        import matplotlib
        matplotlib.use("Agg")          # non-interactive backend — safe for threads
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        from matplotlib.patches import Rectangle
        from matplotlib.lines import Line2D
    except ImportError:
        return ""

    if candles is None or candles.empty:
        return ""

    try:
        df = candles.tail(n_candles).copy()
        df = df.reset_index()
        n  = len(df)
        xs = range(n)

        # ── Figure setup ─────────────────────────────────────────────────────
        BG      = "#0d1117"
        SURFACE = "#161b22"
        BORDER  = "#30363d"
        UP_C    = "#26a69a"
        DOWN_C  = "#ef5350"
        MUTED   = "#8b949e"
        TEXT_C  = "#e6edf3"

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
            # Body
            body_y = min(o, c)
            body_h = abs(c - o) or 1e-8
            ax_price.add_patch(Rectangle(
                (i - W_BODY / 2, body_y), W_BODY, body_h,
                facecolor=color, edgecolor=color, linewidth=0.5, zorder=3,
            ))
            # Wick
            ax_price.add_patch(Rectangle(
                (i - W_WICK / 2, l), W_WICK, h - l,
                facecolor=color, edgecolor=color, linewidth=0, zorder=2,
            ))

        ax_price.set_xlim(-0.5, n - 0.5)
        y_vals = [float(v) for col in ("low", "high") for v in df[col].dropna() if v]
        all_levels = [v for v in (entry, sl, tp1, tp2) if v]
        y_vals += all_levels
        if y_vals:
            y_lo, y_hi = min(y_vals), max(y_vals)
            pad = (y_hi - y_lo) * 0.08 or y_lo * 0.02
            ax_price.set_ylim(y_lo - pad, y_hi + pad)

        # ── Price levels ──────────────────────────────────────────────────────
        levels = [
            (entry, "#4A90D9", "--", f"Entry {entry:.4f}"),
            (sl,    "#E05555", "--", f"SL {sl:.4f}"),
            (tp1,   "#55A85A", "--", f"TP1 {tp1:.4f}"),
            (tp2,   "#55A85A", ":",  f"TP2 {tp2:.4f}"),
        ]
        legend_handles = []
        for price, color, ls, label in levels:
            if price:
                ax_price.axhline(price, color=color, linewidth=1.4,
                                  linestyle=ls, zorder=4, alpha=0.9)
                legend_handles.append(
                    Line2D([0], [0], color=color, linewidth=1.4,
                           linestyle=ls, label=label)
                )

        # ── Legend ────────────────────────────────────────────────────────────
        if legend_handles:
            leg = ax_price.legend(
                handles=legend_handles, loc="upper left", fontsize=8,
                facecolor=BG, edgecolor=BORDER, labelcolor=TEXT_C,
                framealpha=0.85,
            )

        # ── Criteria text ─────────────────────────────────────────────────────
        if criteria:
            crit = "\n".join(f"• {c}" for c in criteria[:5])
            ax_price.text(
                0.99, 0.99, crit,
                transform=ax_price.transAxes,
                fontsize=7.5, va="top", ha="right", color="#cccccc",
                bbox=dict(boxstyle="round,pad=0.35", facecolor=BG,
                          edgecolor=BORDER, alpha=0.88),
            )

        # ── Title ─────────────────────────────────────────────────────────────
        dir_arrow = "▲ LONG" if direction.lower() == "long" else "▼ SHORT"
        ax_price.set_title(
            f"{symbol}  {dir_arrow}  "
            f"Entry {entry:.4f}  SL {sl:.4f}  TP1 {tp1:.4f}  TP2 {tp2:.4f}",
            color=TEXT_C, fontsize=9, pad=6,
        )
        ax_price.set_xticks([])

        # ── Volume bars ───────────────────────────────────────────────────────
        if "volume" in df.columns:
            for i, row in df.iterrows():
                o = float(row.get("open", 0) or 0)
                c = float(row.get("close", 0) or 0)
                v = float(row.get("volume", 0) or 0)
                color = UP_C if c >= o else DOWN_C
                ax_vol.bar(i, v, width=W_BODY, color=color, alpha=0.6)
        ax_vol.set_xlim(-0.5, n - 0.5)
        ax_vol.set_ylabel("Vol", color=MUTED, fontsize=7)
        ax_vol.set_xticks([])

        # ── X-axis labels (date ticks) ────────────────────────────────────────
        date_col = df.columns[0]   # first column is the DatetimeIndex after reset
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

    except Exception:
        return ""
