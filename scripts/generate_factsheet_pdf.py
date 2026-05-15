#!/usr/bin/env python3
"""
scripts/generate_factsheet_pdf.py
2-page compact factsheet for the AI Trading Journal.
Run from project root: python3 scripts/generate_factsheet_pdf.py
"""
import os, re
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, KeepTogether
)

# ── Palette ──────────────────────────────────────────────────────────────────
BG     = colors.HexColor("#0d1117")
SURF   = colors.HexColor("#161b22")
SURF2  = colors.HexColor("#1a2030")
BORDER = colors.HexColor("#30363d")
ACCENT = colors.HexColor("#6c63ff")
TEAL   = colors.HexColor("#4fc3f7")
GREEN  = colors.HexColor("#26d96b")
YELLOW = colors.HexColor("#ffb300")
RED    = colors.HexColor("#ef5350")
TEXT   = colors.HexColor("#e6edf3")
MUTED  = colors.HexColor("#8b949e")
SONNET = colors.HexColor("#4a90d9")

CW = 18.0  # usable content width in cm

OUTPUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "docs", "trading_journal_factsheet.pdf"
)


# ── Style factory ─────────────────────────────────────────────────────────────
def sty(name, **kw):
    d = dict(fontName="Helvetica", fontSize=8, leading=11, textColor=TEXT, spaceAfter=0)
    d.update(kw)
    return ParagraphStyle(name, **d)

S = {
    "title": sty("t", fontName="Helvetica-Bold", fontSize=20, leading=24,
                 textColor=TEXT, alignment=TA_CENTER),
    "sub":   sty("s", fontSize=8, textColor=TEAL, alignment=TA_CENTER),
    "h2":    sty("h2", fontName="Helvetica-Bold", fontSize=10, leading=13,
                 textColor=ACCENT, spaceBefore=4, spaceAfter=2),
    "body":  sty("b", fontSize=8, leading=11, textColor=TEXT),
    "small": sty("sm", fontSize=7, leading=10, textColor=MUTED),
    "kv":    sty("kv", fontName="Helvetica-Bold", fontSize=16, leading=20,
                 textColor=GREEN, alignment=TA_CENTER),
    "kl":    sty("kl", fontSize=7, leading=9, textColor=MUTED, alignment=TA_CENTER),
    "bul":   sty("bul", fontSize=7.5, leading=11, textColor=TEXT, leftIndent=8),
}


def sp(h=3): return Spacer(1, h)
def rule(c=BORDER, h=4): return HRFlowable(width="100%", thickness=0.4, color=c, spaceAfter=h)

def T(data, widths, hbg=ACCENT, fs=7.5, vp=3, hp=5):
    style = [
        ("BACKGROUND",    (0, 0), (-1,  0), hbg),
        ("TEXTCOLOR",     (0, 0), (-1,  0), TEXT),
        ("FONTNAME",      (0, 0), (-1,  0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), fs),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [SURF, SURF2]),
        ("TEXTCOLOR",     (0, 1), (-1, -1), TEXT),
        ("FONTNAME",      (0, 1), (-1, -1), "Helvetica"),
        ("GRID",          (0, 0), (-1, -1), 0.2, BORDER),
        ("ALIGN",         (0, 0), (-1, -1), "LEFT"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), vp),
        ("BOTTOMPADDING", (0, 0), (-1, -1), vp),
        ("LEFTPADDING",   (0, 0), (-1, -1), hp),
        ("RIGHTPADDING",  (0, 0), (-1, -1), hp),
        ("LINEBELOW",     (0, 0), (-1,  0), 0.6, ACCENT),
    ]
    t = Table(data, colWidths=widths)
    t.setStyle(TableStyle(style))
    return t

def two(left_items, right_items, lw=9.0):
    rw = CW - lw - 0.2
    lcell = [[item] for item in left_items]
    rcell = [[item] for item in right_items]
    lt = Table(lcell, colWidths=[lw * cm])
    lt.setStyle(TableStyle([("PADDING", (0,0), (-1,-1), 0),
                             ("VALIGN",  (0,0), (-1,-1), "TOP")]))
    rt = Table(rcell, colWidths=[rw * cm])
    rt.setStyle(TableStyle([("PADDING", (0,0), (-1,-1), 0),
                             ("VALIGN",  (0,0), (-1,-1), "TOP")]))
    row = Table([[lt, rt]], colWidths=[lw * cm, rw * cm])
    row.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "TOP"),
                              ("PADDING",(0,0), (-1,-1), 0)]))
    return row


# ── PAGE 1 ────────────────────────────────────────────────────────────────────
def build_page1():
    story = []

    # Header
    hdr = Table([[Paragraph("📈  AI Crypto Trading Journal", S["title"])]],
                colWidths=[CW * cm])
    hdr.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,-1), colors.HexColor("#0d1520")),
        ("TOPPADDING",    (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ("LINEBELOW",     (0,0), (-1,-1), 2, ACCENT),
    ]))
    story += [hdr, sp(3),
              Paragraph("Self-hosted · Raspberry Pi 5 · Flask 3.1 · Claude Sonnet 4.6 + Haiku 4.5 · "
                        "Bitget + Blofin · SQLite WAL · 14 live data sources", S["sub"]),
              sp(6)]

    # KPI strip (fake demo numbers)
    kw = CW / 5 * cm
    kpi = Table([
        [Paragraph("842",     S["kv"]), Paragraph("67.3%",   S["kv"]),
         Paragraph("+$12,840",S["kv"]), Paragraph("1.49",    S["kv"]),
         Paragraph("3.82",    S["kv"])],
        [Paragraph("Positions", S["kl"]), Paragraph("Win rate (Long)", S["kl"]),
         Paragraph("P&amp;L 90d USDT", S["kl"]), Paragraph("Sharpe", S["kl"]),
         Paragraph("Calmar", S["kl"])],
    ], colWidths=[kw]*5)
    kpi.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,-1), SURF),
        ("GRID",          (0,0), (-1,-1), 0.2, BORDER),
        ("TOPPADDING",    (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("ALIGN",         (0,0), (-1,-1), "CENTER"),
        ("LINEABOVE",     (0,0), (-1,0),  1.0, ACCENT),
        ("LINEBELOW",     (0,1), (-1,1),  0.8, BORDER),
    ]))
    story += [kpi,
              Paragraph("* Illustrative numbers only — not real results", S["small"]),
              sp(7)]

    # Two-column: pipeline + model routing
    pipe = T([
        ["Stage",       "Agent",                   "Model / Source"],
        ["1 Collect",   "agent_data_collector",    "14 parallel workers"],
        ["2 Candles",   "chart_context",           "CCXT · Binance Futures"],
        ["3 Interpret", "chart_confluence / S&R",  "Pure Python indicators"],
        ["4 Sentiment", "market_context + Nansen", "REST APIs"],
        ["5 Review",    "backtest_context",        "SQLite trade history"],
        ["6 Score",     "prompt_builder → Claude", "Sonnet 4.6"],
        ["7 Consensus", "agent_orchestrator",      "Gemini Flash"],
        ["8 Risk",      "agent_risk_mgmt",         "Kelly criterion + ATR"],
        ["9 Chart",     "agent_chart_draw",        "mplfinance PNG"],
        ["10 Alert",    "telegram_notify",         "Telegram Bot API"],
    ], [2.2*cm, 3.7*cm, 3.0*cm], fs=7.5, vp=2)

    route = T([
        ["Task",            "Model",   "Reason"],
        ["call_analyzer",   "Sonnet",  "15-field JSON + chain-of-thought"],
        ["scanner_batch",   "Sonnet",  "12 setups: entry/SL/TP reasoning"],
        ["advisor",         "Sonnet",  "Full portfolio coaching"],
        ["rulebook",        "Sonnet",  "Synthesises 800+ trade history"],
        ["pattern_detect",  "Sonnet",  "Cross-pattern compounding"],
        ["scanner_quick",   "Haiku",   "Score 0-10, runs 100× per scan"],
        ["hindsight",       "Haiku",   "Binary ENTER/SKIP classification"],
        ["live_trade",      "Haiku",   "Hold/Adjust/Close — latency key"],
        ["trade_grader",    "Haiku",   "A–D grade, simple rubric"],
    ], [2.8*cm, 1.6*cm, 4.5*cm], hbg=SONNET, fs=7.5, vp=2)

    story.append(two(
        [Paragraph("AI Pipeline (per trade call)", S["h2"]), pipe],
        [Paragraph("Model Routing", S["h2"]), route],
    ))
    story.append(sp(7))

    # Consensus table
    story.append(Paragraph("Claude + Gemini Consensus Scoring", S["h2"]))
    story.append(T([
        ["|Δ| Claude − Gemini", "Confidence", "Score",            "Flag",        "Action"],
        ["0 – 1 pt",            "High",        "Average",          "✓ Confirmed", "Trade normally"],
        ["2 pt",                "Medium",      "Average",          "~ Aligned",   "Trade, watch"],
        ["3 pt",                "Low",         "Claude 60% + G 40%","⚠ Divergent","Reduce size"],
        ["> 3 pt",              "Very Low",    "Claude kept",      "⚡ REVIEW",   "Do not trade — investigate"],
    ], [3.0*cm, 2.0*cm, 3.2*cm, 2.5*cm, 7.3*cm],
       hbg=colors.HexColor("#4a2080"), fs=7.5, vp=2))

    return story


# ── PAGE 2 ────────────────────────────────────────────────────────────────────
def build_page2():
    story = [PageBreak()]

    # Data sources table
    story.append(Paragraph("Data Sources — Macro → Micro", S["h2"]))
    story.append(rule(ACCENT, 3))

    src_rows = [
        ["Layer",             "Source",                    "Provider",         "Auth", "Used in Pipeline"],
        ["L1 Global Macro",   "VIX Volatility Index",      "CBOE · yfinance",  "Free", "Scanner cap (>35→6.0, >25→7.5) · Confluence ×0.80 when >30"],
        ["L1 Global Macro",   "DXY US Dollar Index",       "ICE · yfinance",   "Free", "Macro regime label · Stage 3 scanner header"],
        ["L1 Global Macro",   "Fear & Greed Index",        "alternative.me",   "Free", "Scanner macro cap · Sentiment prompt · Dashboard strip"],
        ["L1 Global Macro",   "Economic Calendar",         "Finnhub API",      "Key",  "FOMC/CPI/NFP → cap 7.0 when high-impact event in 24h"],
        ["L1 Global Macro",   "BTC Dominance + Mkt Cap",   "CoinGecko (free)", "Free", "Scanner macro header · Call Analyzer market context"],
        ["L2 Market Struct.", "Options Skew (PCR / IV)",   "Deribit (free)",   "Free", "BTC/ETH only — put/call bias, near-term IV, sentiment label"],
        ["L2 Market Struct.", "BTC Mempool",               "blockchain.com",   "Free", "On-chain congestion context injected into prompt"],
        ["L2 Market Struct.", "Trending Coins (top 10)",   "CoinGecko (free)", "Free", "Is the analyzed coin trending? Prompt context block"],
        ["L3 Symbol-Level",   "Multi-Exchange L/S Ratio",  "Binance+Bybit+OKX","Free", "Crowd positioning · contra-signal when >65% vs trade dir."],
        ["L3 Symbol-Level",   "OI · Funding · Liquidations","Coinalyze API",   "Key",  "Derivatives block · funding bias · per-exchange spread"],
        ["L3 Symbol-Level",   "Cap rank · tier · volume",  "CoinGecko (free)", "Free", "Coin context · scales Grok weight (mega 30% → micro 80%)"],
        ["L3 Symbol-Level",   "DeFi TVL + 7d change",      "DefiLlama (free)", "Free", "DeFi tokens only — protocol health context"],
        ["L3 Symbol-Level",   "OHLCV Candles (4H + 1D)",   "Binance · CCXT",   "Free", "All indicators · S/R detection · SMT · Backtester · Charts"],
        ["L4 Trade Intel.",   "Smart Money Wallet Flows",  "Nansen API",       "Paid", "Accumulating/distributing 🟢/🔴 in sentiment agent prompt"],
        ["L4 Trade Intel.",   "Social & News Context",     "xAI Grok API",     "Key",  "Direction-weighted; last prompt block · lowest priority"],
    ]

    layer_bg = {
        "L1 Global Macro":   colors.HexColor("#200f15"),
        "L2 Market Struct.": colors.HexColor("#1c1a0f"),
        "L3 Symbol-Level":   colors.HexColor("#0f1822"),
        "L4 Trade Intel.":   colors.HexColor("#0f1e14"),
    }
    layer_tc = {
        "L1 Global Macro":   RED,
        "L2 Market Struct.": YELLOW,
        "L3 Symbol-Level":   TEAL,
        "L4 Trade Intel.":   GREEN,
    }

    src_style = [
        ("BACKGROUND",    (0,0), (-1, 0), ACCENT),
        ("TEXTCOLOR",     (0,0), (-1, 0), TEXT),
        ("FONTNAME",      (0,0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0,0), (-1,-1), 7.5),
        ("TEXTCOLOR",     (0,1), (-1,-1), TEXT),
        ("FONTNAME",      (0,1), (-1,-1), "Helvetica"),
        ("GRID",          (0,0), (-1,-1), 0.2, BORDER),
        ("ALIGN",         (0,0), (-1,-1), "LEFT"),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING",    (0,0), (-1,-1), 2),
        ("BOTTOMPADDING", (0,0), (-1,-1), 2),
        ("LEFTPADDING",   (0,0), (-1,-1), 5),
        ("RIGHTPADDING",  (0,0), (-1,-1), 5),
        ("LINEBELOW",     (0,0), (-1, 0), 0.6, ACCENT),
    ]
    layer_rows = {
        "L1 Global Macro":   [1,2,3,4,5],
        "L2 Market Struct.": [6,7,8],
        "L3 Symbol-Level":   [9,10,11,12,13],
        "L4 Trade Intel.":   [14,15],
    }
    for layer, rows in layer_rows.items():
        for r in rows:
            src_style += [
                ("BACKGROUND", (0,r), (-1,r), layer_bg[layer]),
                ("TEXTCOLOR",  (0,r), (0, r), layer_tc[layer]),
                ("FONTNAME",   (0,r), (0, r), "Helvetica-Bold"),
            ]

    src_tbl = Table(src_rows, colWidths=[2.6*cm, 3.4*cm, 2.8*cm, 1.0*cm, 8.2*cm])
    src_tbl.setStyle(TableStyle(src_style))
    story += [src_tbl, sp(8)]

    # Two-column: New features | Stack + Budget
    feats = [
        ("Walk-forward backtester",
         "70/30 temporal split on real positions; end_offset_days prevents train/test leakage"),
        ("Optimizer history",
         "Last 5 Bayesian runs stored with Sharpe + best params in Analysis tab"),
        ("Scanner macro layer",
         "VIX / F&G / FOMC fetched once per scan; score caps applied before Stage 3 Sonnet"),
        ("SMT Direction weight",
         "+0.15 when symbol moves opposite its correlated pair (BTC↔ETH, SOL→ETH, BNB→BTC)"),
        ("Calmar &amp; Sharpe fixed",
         "Max DD vs running peak (not ATH); sample variance N−1; wallet filter &gt;$1 USDT"),
        ("14 data sources",
         "CoinGecko, Deribit, Finnhub, Coinalyze, DefiLlama, Blockchain.com, Nansen, Grok all wired"),
        ("Data Sources page",
         "Tools → 🗂️ shows all 14 sources grouped macro→micro with auth, inputs, pipeline usage"),
        ("Scanner macro UI",
         "Live VIX / F&G / cap + macro warnings visible per scan; Gemini consensus on top-5"),
    ]
    feat_items = [Paragraph("What's New — v1.5.0", S["h2"])]
    for title, detail in feats:
        feat_items.append(
            Paragraph(f"<b>{title}</b> — {detail}", S["bul"]))
        feat_items.append(sp(2))

    stack = T([
        ["Layer",        "Technology"],
        ["Runtime",      "Python 3.13 · Flask 3.1"],
        ["Database",     "SQLite WAL · 7-day auto backup"],
        ["AI Primary",   "Claude Sonnet 4.6 · Haiku 4.5"],
        ["AI Consensus", "Google Gemini 2.0 Flash"],
        ["AI Social",    "xAI Grok 3 Fast"],
        ["Exchange",     "CCXT · Binance · Bybit · OKX"],
        ["Charts",       "mplfinance · lightweight-charts"],
        ["Hardware",     "Raspberry Pi 5 · port 8082"],
        ["Tests",        "pytest · 351 passing"],
    ], [2.5*cm, 5.4*cm], hbg=colors.HexColor("#1a3040"), fs=7.5, vp=2)

    budget = T([
        ["Priority", "Block",               "Protected until"],
        ["1st",      "Backtest context",    "Always — most trade-relevant"],
        ["2nd",      "Market data blocks",  "Always (Coinalyze, F&G, L/S, options…)"],
        ["3rd",      "Rulebook",            "Remaining < 100 chars"],
        ["4th",      "Chart context",       "Remaining < 100 chars"],
        ["5th",      "Calibration",         "Remaining < 100 chars"],
        ["6th",      "Grok social",         "Remaining < 150 chars · weight 0–80%"],
    ], [1.0*cm, 3.0*cm, 3.9*cm], hbg=colors.HexColor("#2a1a40"), fs=7.5, vp=2)

    right_items = [
        Paragraph("Technical Stack", S["h2"]), stack, sp(4),
        Paragraph("Prompt Budget Order", S["h2"]), budget,
    ]

    story.append(two(feat_items, right_items, lw=9.2))
    story.append(sp(8))

    # Footer
    story.append(rule(BORDER, 3))
    ft = Table([[
        Paragraph("github.com/anvilfilbert/Auto-Crypto-Tradingjournal", S["small"]),
        Paragraph("v1.5.0 · 2026-05-15  ·  All numbers shown are illustrative only",
                  ParagraphStyle("fc", fontSize=7, leading=10,
                                 textColor=MUTED, alignment=TA_CENTER)),
        Paragraph("192.168.1.21:8082  ·  Raspberry Pi 5", S["small"]),
    ]], colWidths=[6*cm, 6*cm, 6*cm])
    ft.setStyle(TableStyle([
        ("ALIGN",    (0,0), (0,0),  "LEFT"),
        ("ALIGN",    (1,0), (1,0),  "CENTER"),
        ("ALIGN",    (2,0), (2,0),  "RIGHT"),
        ("VALIGN",   (0,0), (-1,-1),"MIDDLE"),
        ("PADDING",  (0,0), (-1,-1), 0),
        ("TEXTCOLOR",(0,0), (-1,-1), MUTED),
    ]))
    story.append(ft)
    return story


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    doc = SimpleDocTemplate(
        OUTPUT, pagesize=A4,
        leftMargin=1.5*cm, rightMargin=1.5*cm,
        topMargin=1.3*cm,  bottomMargin=1.0*cm,
        title="AI Trading Journal — Factsheet v1.5.0",
        author="Auto Crypto Trading Journal",
    )
    doc.build(build_page1() + build_page2())
    print(f"Generated: {OUTPUT}")


if __name__ == "__main__":
    main()
