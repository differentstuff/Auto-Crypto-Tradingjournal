"""
agent_chart_draw.py — Annotated trade chart generator.

Generates a candlestick chart (4H candles, last 60 periods) with:
  - Entry price (blue dashed line)
  - Stop loss (red dashed line)
  - TP1 and TP2 (green dashed lines)
  - Key criteria as text annotations in the top-right corner
  - Volume subplot below

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
    """
    Returns base64-encoded PNG or "" on failure.
    criteria: list of short strings explaining why the trade was taken.
    """
    try:
        import mplfinance as mpf
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
    except ImportError:
        return ""

    if candles is None or candles.empty:
        return ""

    try:
        df = candles.tail(n_candles).copy()
        df.index = pd.DatetimeIndex(df.index)

        # Horizontal level lines as addplot series
        addplots = []
        levels = [
            (entry, "#4A90D9", 1.5, "--"),   # entry blue
            (sl,    "#E05555", 1.5, "--"),   # SL red
            (tp1,   "#55A85A", 1.2, "--"),   # TP1 green
            (tp2,   "#55A85A", 1.2, ":"),    # TP2 green dotted
        ]
        for price, color, lw, ls in levels:
            if price:
                series = pd.Series(price, index=df.index)
                addplots.append(mpf.make_addplot(series, color=color, width=lw, linestyle=ls))

        title_dir = "▲ LONG" if direction.lower() == "long" else "▼ SHORT"
        title = f"{symbol}  {title_dir}  Entry {entry:.4f}  SL {sl:.4f}  TP1 {tp1:.4f}  TP2 {tp2:.4f}"

        mc = mpf.make_marketcolors(up="#26a69a", down="#ef5350",
                                   wick="inherit", edge="inherit",
                                   volume={"up": "#26a69a44", "down": "#ef535044"})
        style = mpf.make_mpf_style(marketcolors=mc, base_mpf_style="nightclouds",
                                   gridstyle=":", gridcolor="#2a2a2a",
                                   facecolor="#1a1a2e", edgecolor="#2a2a2e",
                                   figcolor="#1a1a2e", y_on_right=False)

        fig, axes = mpf.plot(
            df, type="candle", style=style,
            addplot=addplots,
            volume=True,
            title=title,
            returnfig=True,
            figsize=(14, 8),
        )

        fig.tight_layout()

        ax = axes[0]

        # Level legend
        patches = [
            mpatches.Patch(color="#4A90D9", label=f"Entry {entry:.4f}"),
            mpatches.Patch(color="#E05555", label=f"SL {sl:.4f}"),
            mpatches.Patch(color="#55A85A", label=f"TP1 {tp1:.4f}"),
            mpatches.Patch(color="#55A85A", alpha=0.5, label=f"TP2 {tp2:.4f}"),
        ]
        ax.legend(handles=patches, loc="upper left", fontsize=8,
                  facecolor="#1a1a2e", edgecolor="#444", labelcolor="white")

        # Criteria annotations top-right
        if criteria:
            crit_text = "\n".join(f"• {c}" for c in criteria[:5])
            ax.text(0.99, 0.99, crit_text, transform=ax.transAxes,
                    fontsize=7, verticalalignment="top", horizontalalignment="right",
                    color="#cccccc",
                    bbox=dict(boxstyle="round,pad=0.3",
                              facecolor="#1a1a2e", edgecolor="#444", alpha=0.85))

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=120, bbox_inches="tight",
                    facecolor="#1a1a2e", edgecolor="none")
        plt.close(fig)
        buf.seek(0)
        return base64.b64encode(buf.read()).decode("utf-8")

    except Exception:
        return ""
