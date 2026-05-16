#!/usr/bin/env python3
"""
scripts/generate_architecture_pdf_id.py
Versi Bahasa Indonesia dari dokumen arsitektur PDF Jurnal Perdagangan.
Jalankan dari root proyek: python3 scripts/generate_architecture_pdf_id.py
"""

import os
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, KeepTogether
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.platypus.flowables import Flowable

C_BG      = colors.HexColor("#0d1117")
C_SURFACE = colors.HexColor("#161b22")
C_BORDER  = colors.HexColor("#30363d")
C_ACCENT  = colors.HexColor("#6c63ff")
C_ACCENT2 = colors.HexColor("#4fc3f7")
C_ACCENT3 = colors.HexColor("#26d96b")
C_YELLOW  = colors.HexColor("#ffb300")
C_RED     = colors.HexColor("#ef5350")
C_TEXT    = colors.HexColor("#e6edf3")
C_MUTED   = colors.HexColor("#8b949e")
C_SONNET  = colors.HexColor("#4a90d9")
C_HAIKU   = colors.HexColor("#7ed3a6")
C_GEMINI  = colors.HexColor("#4285F4")
C_GROK    = colors.HexColor("#1d9bf0")
C_NANSEN  = colors.HexColor("#f7931a")

OUTPUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "docs", "architecture_detailed_id.pdf")


class ColoredRule(Flowable):
    def __init__(self, color, width_pct=1.0, thickness=0.5):
        super().__init__()
        self.color = color; self.width_pct = width_pct
        self.thickness = thickness; self.height = thickness + 2
    def draw(self):
        self.canv.setStrokeColor(self.color); self.canv.setLineWidth(self.thickness)
        w = self.canv._pagesize[0] * self.width_pct; self.canv.line(0, 0, w, 0)


def make_styles():
    base = getSampleStyleSheet()
    def P(name, parent="Normal", **kw):
        return ParagraphStyle(name, parent=base[parent], **kw)
    return {
        "cover_title": P("cover_title", fontSize=32, leading=40, textColor=C_TEXT,
            fontName="Helvetica-Bold", alignment=TA_CENTER, spaceAfter=8),
        "cover_sub":  P("cover_sub",  fontSize=14, leading=20, textColor=C_ACCENT2,
            fontName="Helvetica", alignment=TA_CENTER, spaceAfter=4),
        "cover_meta": P("cover_meta", fontSize=10, leading=14, textColor=C_MUTED,
            fontName="Helvetica", alignment=TA_CENTER),
        "h1":    P("h1",   fontSize=22, leading=28, textColor=C_ACCENT,
            fontName="Helvetica-Bold", spaceBefore=18, spaceAfter=6),
        "h2":    P("h2",   fontSize=16, leading=22, textColor=C_ACCENT2,
            fontName="Helvetica-Bold", spaceBefore=14, spaceAfter=4),
        "h3":    P("h3",   fontSize=13, leading=18, textColor=C_YELLOW,
            fontName="Helvetica-Bold", spaceBefore=10, spaceAfter=3),
        "body":  P("body", fontSize=10, leading=15, textColor=C_TEXT,
            fontName="Helvetica", spaceAfter=6, alignment=TA_JUSTIFY),
        "small": P("small",fontSize=8.5,leading=13, textColor=C_MUTED,
            fontName="Helvetica", spaceAfter=3),
        "code":  P("code", fontSize=8.5,leading=13, textColor=C_ACCENT3,
            fontName="Courier", spaceAfter=4,
            backColor=colors.HexColor("#0d1117"), borderPadding=(4,6,4,6)),
        "bullet":P("bullet",fontSize=10,leading=15,textColor=C_TEXT,
            fontName="Helvetica", leftIndent=14, bulletIndent=4, spaceAfter=3),
        "tag":   P("tag",  fontSize=8, leading=11, textColor=C_BG,
            fontName="Helvetica-Bold", alignment=TA_CENTER),
    }


def make_table(data, col_widths, header_bg=C_ACCENT, row_colors=True, font_size=9):
    style = [
        ("BACKGROUND",    (0,0),(-1, 0), header_bg),
        ("TEXTCOLOR",     (0,0),(-1, 0), C_TEXT),
        ("FONTNAME",      (0,0),(-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0,0),(-1,-1), font_size),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),
            [colors.HexColor("#161b22"), colors.HexColor("#1a2030")]
            if row_colors else [C_SURFACE]),
        ("TEXTCOLOR",     (0,1),(-1,-1), C_TEXT),
        ("FONTNAME",      (0,1),(-1,-1), "Helvetica"),
        ("GRID",          (0,0),(-1,-1), 0.25, C_BORDER),
        ("ALIGN",         (0,0),(-1,-1), "LEFT"),
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
        ("TOPPADDING",    (0,0),(-1,-1), 5),
        ("BOTTOMPADDING", (0,0),(-1,-1), 5),
        ("LEFTPADDING",   (0,0),(-1,-1), 7),
        ("RIGHTPADDING",  (0,0),(-1,-1), 7),
        ("LINEBELOW",     (0,0),(-1, 0), 1.0, C_ACCENT),
    ]
    t = Table(data, colWidths=col_widths); t.setStyle(TableStyle(style)); return t


def agent_card(name, model_color, model_label, pemicu, keluaran, ringkasan, deskripsi, styles):
    tag = Table([[model_label]], colWidths=[2.5*cm])
    tag.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1),model_color),("TEXTCOLOR",(0,0),(-1,-1),C_BG),
        ("FONTNAME",(0,0),(-1,-1),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),7),
        ("ALIGN",(0,0),(-1,-1),"CENTER"),("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("TOPPADDING",(0,0),(-1,-1),2),("BOTTOMPADDING",(0,0),(-1,-1),2),
        ("LEFTPADDING",(0,0),(-1,-1),5),("RIGHTPADDING",(0,0),(-1,-1),5),
        ("BOX",(0,0),(-1,-1),0.25,C_BORDER),
    ]))
    hdr = Table([[Paragraph(f"<b>{name}</b>", ParagraphStyle("ch", fontSize=11,
        fontName="Helvetica-Bold", textColor=C_TEXT, leading=14)), tag]],
        colWidths=[12*cm, 2.8*cm])
    hdr.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1),colors.HexColor("#1a2030")),
        ("ALIGN",(0,0),(0,0),"LEFT"),("ALIGN",(1,0),(1,0),"RIGHT"),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("TOPPADDING",(0,0),(-1,-1),7),("BOTTOMPADDING",(0,0),(-1,-1),7),
        ("LEFTPADDING",(0,0),(0,0),10),("RIGHTPADDING",(1,0),(1,0),10),
        ("LINEBELOW",(0,0),(-1,0),0.5,model_color),
    ]))
    meta = Table([[
        Paragraph(f"<b>Pemicu:</b> {pemicu}", ParagraphStyle("ct", fontSize=9,
            fontName="Helvetica", textColor=C_MUTED, leading=13)),
        Paragraph(f"<b>Keluaran:</b> {keluaran}", ParagraphStyle("co", fontSize=9,
            fontName="Helvetica", textColor=C_MUTED, leading=13)),
    ]], colWidths=[7.4*cm, 7.4*cm])
    meta.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1),C_SURFACE),
        ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
        ("LEFTPADDING",(0,0),(0,0),10),("RIGHTPADDING",(1,0),(1,0),10),
        ("GRID",(0,0),(-1,-1),0.25,C_BORDER),
    ]))
    short_t = Table([[Paragraph(f"<i>{ringkasan}</i>", ParagraphStyle("ds", fontSize=9,
        fontName="Helvetica-Oblique", textColor=C_ACCENT2, leading=13))]],
        colWidths=[14.8*cm])
    short_t.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1),C_SURFACE),
        ("TOPPADDING",(0,0),(-1,-1),0),("BOTTOMPADDING",(0,0),(-1,-1),5),
        ("LEFTPADDING",(0,0),(-1,-1),10),("RIGHTPADDING",(0,0),(-1,-1),10),
    ]))
    long_t = Table([[Paragraph(deskripsi, ParagraphStyle("dl", fontSize=9,
        fontName="Helvetica", textColor=C_TEXT, leading=14, alignment=TA_JUSTIFY))]],
        colWidths=[14.8*cm])
    long_t.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1),C_SURFACE),
        ("TOPPADDING",(0,0),(-1,-1),0),("BOTTOMPADDING",(0,0),(-1,-1),8),
        ("LEFTPADDING",(0,0),(-1,-1),10),("RIGHTPADDING",(0,0),(-1,-1),10),
        ("LINEBELOW",(0,0),(-1,-1),0.5,C_BORDER),
        ("LINEBEFORE",(0,0),(0,-1),2.0,model_color),
    ]))
    return KeepTogether([hdr, meta, short_t, long_t, Spacer(1, 6)])


def build_story(styles):
    S = styles
    story = []

    # ── HALAMAN SAMPUL ────────────────────────────────────────────────────────
    story.append(Spacer(1, 3*cm))
    story.append(Paragraph("Jurnal Perdagangan", S["cover_title"]))
    story.append(Paragraph("Arsitektur Agen AI", S["cover_sub"]))
    story.append(Spacer(1, 0.4*cm))
    story.append(ColoredRule(C_ACCENT, 0.6))
    story.append(Spacer(1, 0.4*cm))
    story.append(Paragraph("Kerangka Kecerdasan Multi-Model — v1.5.0", S["cover_meta"]))
    story.append(Paragraph(
        "Claude Sonnet · Claude Haiku · Google Gemini · xAI Grok · Nansen", S["cover_meta"]))
    story.append(Paragraph(
        "CoinGecko · Coinalyze · Finnhub · Deribit · DefiLlama · blockchain.com · yfinance · CCXT",
        S["cover_meta"]))
    story.append(Spacer(1, 1*cm))
    story.append(make_table([
        ["Apa yang dibahas dokumen ini"],
        ["Dokumen ini menjelaskan setiap agen AI, agen data, dan agen otomatisasi yang "
         "menjalankan jurnal perdagangan kripto futures berbasis mandiri. Setiap bagian "
         "menjelaskan apa yang dilakukan agen, mengapa dirancang demikian, model apa yang "
         "digunakan, kapan dijalankan, dan bagaimana keterkaitannya dengan agen lain. "
         "Cocok untuk pemula dan ahli."],
    ], [15*cm], header_bg=C_ACCENT))
    story.append(PageBreak())

    # ── BAGIAN 1 ──────────────────────────────────────────────────────────────
    story.append(Paragraph("1. Gambaran Umum Sistem", S["h1"]))
    story.append(ColoredRule(C_ACCENT)); story.append(Spacer(1, 4))
    story.append(Paragraph(
        "Jurnal perdagangan adalah aplikasi berbasis mandiri yang berjalan di Raspberry Pi 5. "
        "Terhubung ke bursa kripto futures (Bitget, Blofin), melacak riwayat perdagangan Anda, "
        "dan menggunakan jaringan agen AI khusus untuk membantu membuat keputusan perdagangan "
        "yang lebih baik. Setiap agen memiliki satu tanggung jawab yang spesifik dan terfokus — "
        "tidak ada yang mencoba melakukan segalanya.", S["body"]))
    story.append(Paragraph(
        "Untuk <b>pemula:</b> bayangkan ini sebagai tim analis, masing-masing ahli di satu bidang. "
        "Satu analis membaca grafik, yang lain memeriksa media sosial, yang ketiga memberi skor "
        "ide perdagangan Anda, dan yang lain meninjau riwayat Anda. Orkestrator adalah pemimpin "
        "tim yang memutuskan siapa yang berbicara dan seberapa besar bobot yang diberikan pada "
        "setiap pendapat.", S["body"]))
    story.append(Paragraph(
        "Untuk <b>ahli:</b> arsitektur ini menggunakan pemisahan konteks stabil/dinamis yang "
        "sadar akan caching prompt, lapisan penilaian konsensus dari dua LLM independen dengan "
        "deteksi divergensi, kecerdasan eksternal berbobot-MC (Grok), dan loop umpan balik "
        "backtest yang menyuntikkan pola tingkat kemenangan historis langsung ke setiap "
        "prompt penilaian.", S["body"]))
    story.append(Spacer(1, 6))
    story.append(Paragraph("Tiga jenis agen", S["h2"]))
    story.append(make_table([
        ["Jenis", "Artinya", "Contoh"],
        ["Agen Analisis",
         "Menerima masukan (panggilan perdagangan, posisi, data historis) dan mengembalikan "
         "penilaian terstruktur berbasis AI. Dipanggil sesuai permintaan pengguna.",
         "call_analyzer, advisor, hindsight, live_trade"],
        ["Agen Otomatisasi",
         "Berjalan terjadwal di latar belakang tanpa tindakan pengguna. Mengambil data, "
         "mendeteksi setup, mengirim peringatan, dan menyinkronkan posisi dari bursa.",
         "scanner_scheduler, bitget_sync, blofin_sync"],
        ["Agen Data",
         "Mengambil dan menyimpan data eksternal (tanpa AI). Dipanggil oleh agen analisis "
         "untuk memperkaya prompt dengan konteks pasar, sinyal on-chain, atau kecerdasan sosial.",
         "nansen_client, grok_client, gemini_client, market_context, chart_context"],
    ], [3.5*cm, 7*cm, 4.3*cm], header_bg=C_ACCENT))
    story.append(PageBreak())

    # ── BAGIAN 2 ──────────────────────────────────────────────────────────────
    story.append(Paragraph("2. Orkestrator Utama", S["h1"]))
    story.append(ColoredRule(C_ACCENT)); story.append(Spacer(1, 4))
    story.append(Paragraph(
        "Orkestrator (<b>agent_orchestrator.py</b>) tidak memanggil model AI sendiri. "
        "Tugasnya adalah mengoordinasikan hasil dari beberapa model dan membuat keputusan "
        "routing. Ia menjawab dua pertanyaan: <i>model mana yang harus menangani tugas ini?</i> "
        "dan <i>apakah Claude dan Gemini sepakat tentang sinyal perdagangan ini?</i>", S["body"]))
    story.append(Paragraph("2.1 Pengarah Model", S["h2"]))
    story.append(Paragraph(
        "Setiap tugas AI dalam jurnal diklasifikasikan sebagai <b>tugas penalaran</b> "
        "(membutuhkan model paling mampu) atau <b>tugas klasifikasi</b> (dapat dilakukan "
        "model yang lebih cepat dan murah). Ini penting karena Claude Sonnet biayanya sekitar "
        "10× lebih mahal per token daripada Claude Haiku — routing yang tepat mengurangi biaya "
        "tanpa mengorbankan akurasi.", S["body"]))
    story.append(make_table([
        ["Tugas", "Model", "Alasan pemilihan"],
        ["call_analyzer",    "Sonnet 4.6", "Keluaran JSON terstruktur kompleks 15+ bidang, penilaian chain-of-thought"],
        ["scanner_batch",    "Sonnet 4.6", "Mengevaluasi 12 simbol sekaligus, butuh penalaran masuk/SL/TP mendalam"],
        ["advisor",          "Sonnet 4.6", "Pelatihan portofolio penuh dari 800+ perdagangan, rekomendasi panjang"],
        ["rulebook",         "Sonnet 4.6", "Menyintesis seluruh riwayat perdagangan menjadi aturan perdagangan personal"],
        ["limit_analyzer",   "Sonnet 4.6", "Keputusan risiko pada order tertunda — akurasi sangat penting"],
        ["pattern_detector", "Sonnet 4.6", "Analisis penggabungan lintas-pola — membutuhkan penalaran nyata"],
        ["scanner_quick",    "Haiku 4.5",  "Skor 0-10 + satu kalimat — klasifikasi murni, berjalan 100× per pemindaian"],
        ["live_trade",       "Haiku 4.5",  "Tindakan Tahan/Tutup/Sesuaikan — rubrik sederhana, latensi penting"],
        ["hindsight",        "Haiku 4.5",  "Vonis MASUK/LEWATI retroaktif — tugas klasifikasi biner"],
        ["trade_grader",     "Haiku 4.5",  "Nilai eksekusi A/B/C/D — rubrik sederhana, berjalan sekali per perdagangan ditutup"],
    ], [3.5*cm, 3*cm, 8.3*cm], header_bg=C_ACCENT, font_size=8.5))
    story.append(Spacer(1, 10))
    story.append(Paragraph("2.2 Algoritma Penilaian Konsensus", S["h2"]))
    story.append(Paragraph(
        "Saat pengguna menganalisis panggilan perdagangan, Claude dan Google Gemini memberi "
        "skor setup secara independen. Claude menerima konteks penuh (rulebook, data grafik, "
        "kondisi pasar, perdagangan historis serupa). Gemini menerima <i>hanya teks panggilan "
        "mentah</i> — tanpa konteks tambahan. Ini disengaja: dua penilai dengan kumpulan "
        "informasi berbeda menghasilkan persetujuan atau ketidaksetujuan yang lebih bermakna "
        "daripada dua salinan prompt yang sama.", S["body"]))
    story.append(Paragraph(
        "Saat mereka sepakat (|Δ| ≤ 1), kepercayaan tinggi — sinyal setup kuat. Saat sangat "
        "tidak sepakat (|Δ| > 3), perdagangan ditandai untuk ditinjau manual sebelum bertindak. "
        "Rata-rata berbobot (Claude 60%, Gemini 40% pada ketidaksetujuan ringan) mencerminkan "
        "bahwa konteks penuh Claude umumnya menghasilkan skor lebih akurat untuk setup "
        "teknikal terstruktur.", S["body"]))
    story.append(make_table([
        ["Delta (|Claude − Gemini|)", "Kepercayaan", "Skor digunakan", "Tanda UI", "Tindakan yang disarankan"],
        ["0 – 1 poin",   "Tinggi",        "Rata-rata sederhana",    "✓ Dikonfirmasi", "Perdagangan dengan risiko normal"],
        ["2 poin",       "Sedang",        "Rata-rata sederhana",    "~ Selaras",      "Perdagangan, pantau ketat"],
        ["3 poin",       "Rendah",        "Claude 60% + Gemini 40%","⚠ Divergen",     "Kurangi ukuran atau tunggu konfirmasi"],
        ["> 3 poin",     "Sangat Rendah", "Skor Claude dipakai",    "⚡ TINJAU",      "Jangan perdagangan — selidiki ketidaksetujuan"],
    ], [3.5*cm, 2.2*cm, 3.2*cm, 2.5*cm, 3.4*cm],
       header_bg=colors.HexColor("#4a2080"), font_size=8.5))
    story.append(PageBreak())

    # ── BAGIAN 3 ──────────────────────────────────────────────────────────────
    story.append(Paragraph("3. Agen Analisis", S["h1"]))
    story.append(ColoredRule(C_ACCENT)); story.append(Spacer(1, 4))
    story.append(Paragraph(
        "Agen analisis dipicu oleh tindakan pengguna (mengklik tombol, meminta analisis). "
        "Setiap agen menerima masukan terfokus, membangun prompt teroptimasi dari sistem "
        "konteks bersama, memanggil AI, dan mengembalikan JSON terstruktur.", S["body"]))

    for args in [
        ("📊 Penganalisis Panggilan  —  ai_call.py", C_SONNET, "Sonnet 4.6",
         "Pengguna menempel teks panggilan analis (Telegram, Twitter, manual)",
         "Skor 1-10, masuk/SL/TP, ukuran posisi, peringatan pola, R:R, konsensus Gemini",
         "Agen kecerdasan utama untuk mengevaluasi panggilan perdagangan dari analis eksternal.",
         "Saat pengguna menerima panggilan perdagangan dari analis di Telegram, mereka menempelkannya "
         "di sini. Agen secara otomatis mengekstrak simbol, arah, harga masuk, stop loss, dan target "
         "profit menggunakan regex. Kemudian menjalankan tiga hal secara paralel: (1) pemeriksaan "
         "kualitas stop loss berbasis ATR menggunakan data candle 1H, (2) konteks pasar live (tingkat "
         "pendanaan, Fear &amp; Greed), dan (3) skor pra-konfirmasi independen dari Gemini. Claude "
         "kemudian menerima konteks penuh — rulebook personal, umpan balik kalibrasi, indikator grafik "
         "untuk 4H dan 1D, sinyal smart money Nansen, sentimen sosial Grok (dibobot berdasarkan "
         "kapitalisasi pasar), perdagangan historis serupa — dan menghasilkan analisis terstruktur "
         "15 bidang. Analisis sebelumnya untuk simbol yang sama disuntikkan sebagai loop pembelajaran "
         "(penggunaan ulang CoT), memungkinkan Claude mendeteksi apakah kondisi setup telah berubah. "
         "Skor konsensus (Claude vs Gemini) disimpan ke database bersama analisis lengkap."),
        ("📈 Pemindai  —  ai_scanner.py (3 tahap)", C_SONNET, "Sonnet + Haiku",
         "Setiap 30 menit (otomatis) atau dijalankan manual",
         "Hingga 12 setup dengan skor: zona_masuk, sl, tp1, tp2, rasio_rr, urgensi",
         "Secara proaktif menemukan setup perdagangan dari 100+ simbol tanpa menunggu panggilan analis.",
         "Tahap 0 (Lapisan Makro, sekali per scan): VIX, Fear &amp; Greed, kalender ekonomi Finnhub, "
         "dan dominasi BTC diambil sekali. Batas skor dihitung: VIX &gt; 35 batas 6.0, VIX 25-35 "
         "batas 7.5, acara makro berdampak tinggi dalam 24 jam batas 7.0. Batas ini diterapkan pada "
         "setiap skor Tahap 3 sebelum pemeriksaan ambang — setup dengan skor 9 di lingkungan VIX "
         "&gt; 35 dibatasi menjadi 6.0. Tahap 1 (Filter Konfluensi, tanpa AI): mengambil candle 4H "
         "dan 1D untuk semua simbol secara paralel dan menghitung RSI, MACD, EMA, ADX, WaveTrend, "
         "CVD, dan skor konfluensi 9 sinyal termasuk divergensi SMT. Biasanya memotong 100+ simbol "
         "menjadi ~25-30 dengan nol biaya API. Tahap 2 (Gerbang Kualitas, tanpa AI): menerapkan "
         "aturan teknikal — menolak RSI yang terlalu jauh, struktur S/R yang hilang, ADX datar, "
         "tingkat pendanaan sangat tinggi. Memotong menjadi ~10-15 finalis. Tahap 3a (Skor Cepat "
         "Haiku): Haiku memberi skor setiap finalis dengan prompt minimal (maks 120 token keluaran). "
         "Tahap 3b (Batch Sonnet + batas makro): semua finalis yang tersisa diberi skor dalam SATU "
         "panggilan Sonnet. Batas makro diterapkan pada setiap skor sebelum ambang. Tahap 3c "
         "(Konsensus Gemini): 5 finalis teratas menerima skor Gemini independen. Setup yang diberi "
         "peringatan otomatis disimpan ke analyzed_calls untuk penautaan posisi tanpa intervensi manual."),
        ("🧠 Penasihat AI  —  ai_advisor.py", C_SONNET, "Sonnet 4.6",
         "Pengguna mengklik 'Dapatkan Saran AI' di Edge Lab",
         "Kekuatan portofolio, kelemahan, rekomendasi spesifik, wawasan simbol",
         "Pelatihan portofolio tingkat tinggi berdasarkan riwayat perdagangan lengkap dan pasar terkini.",
         "Penasihat menerima statistik gabungan dari seluruh riwayat perdagangan Anda: tingkat "
         "kemenangan, faktor keuntungan, performa per simbol, per hari dalam seminggu, per jam, "
         "per jenis setup, per durasi. Penasihat juga menerima konteks pasar saat ini (dominasi BTC, "
         "Fear &amp; Greed, tingkat pendanaan). Rulebook dan data kalibrasi di-cache sebagai prefiks "
         "stabil. Claude mengidentifikasi pola di semua dimensi dan menghasilkan laporan pelatihan "
         "terstruktur: apa yang Anda lakukan dengan baik (kekuatan), di mana Anda kehilangan uang "
         "(kelemahan), dan 3-5 rekomendasi spesifik yang dapat ditindaklanjuti dengan data pendukung. "
         "Contoh: 'Perdagangan LONG Anda pada Jumat sore memiliki tingkat kemenangan 43% vs 71% di "
         "hari lain — pertimbangkan menghindari posisi baru setelah pukul 15:00 UTC pada hari Jumat.'"),
        ("🔮 Penganalisis Tinjauan Balik  —  ai_hindsight.py", C_HAIKU, "Haiku 4.5",
         "Pengguna menjalankan batch tinjauan balik (N perdagangan terakhir)",
         "Vonis MASUK/LEWATI per perdagangan, akurasi TP/FP/TN/FN, perbandingan P&L",
         "Penilaian buta retroaktif: apa yang akan dikatakan AI jika melihat setup sebelum hasilnya?",
         "Ini adalah alat backtesting dengan disiplin utama: Claude ditunjukkan gambaran teknikal "
         "sebagaimana tampilnya PADA WAKTU MASUK — bukan grafik saat ini. Agen mengambil candle OHLCV "
         "historis yang berakhir pada tepat waktu masuk perdagangan dan merekonstruksi keadaan "
         "indikator. Kemudian memberi skor setup tanpa mengetahui bagaimana perdagangan berakhir. "
         "Hasilnya menunjukkan: (1) seberapa sering panggilan MASUK Claude sebenarnya menang (tingkat "
         "True Positive), (2) seberapa sering panggilan LEWATI-nya akan benar, dan (3) P&amp;L "
         "hipotetis jika Anda hanya mengambil perdagangan yang Claude beri nilai ≥6. Loop kalibrasi "
         "ini membantu mengidentifikasi apakah penilaian AI bersifat prediktif terhadap hasil aktual "
         "— fondasi untuk target akurasi 85%."),
        ("👁 Pemeriksa Perdagangan Live  —  ai_live_trade.py", C_HAIKU, "Haiku 4.5",
         "Pengguna mengklik '🤖 Analisis AI' pada kartu posisi live",
         "Tindakan (Tahan/Tutup/Sesuaikan SL), peringkat risiko 1-10, saran TP/SL",
         "Pemeriksaan kesehatan cepat per posisi untuk posisi futures terbuka.",
         "Menerima data posisi lengkap (masuk, mark, SL, TP, durasi, margin, P&amp;L belum "
         "terealisasi, tingkat pendanaan) ditambah indikator grafik 4H saat ini. Haiku mengevaluasi "
         "apakah tesis perdagangan masih valid, apakah stop berisiko, dan apakah posisi telah terbuka "
         "terlalu lama. Mengembalikan rekomendasi terstruktur dalam waktu kurang dari 2 detik. Haiku "
         "digunakan di sini secara khusus karena pengguna sedang melihat portofolio live mereka dan "
         "latensi penting — menunggu 5 detik untuk setiap kartu akan membuat frustrasi."),
        ("⏳ Penganalisis Limit  —  ai_limit.py", C_SONNET, "Sonnet 4.6",
         "Pengguna mengklik 'Analisis' pada order limit tertunda",
         "Skor kualitas masuk, penilaian risiko, validasi ATR",
         "Mengevaluasi order limit sebelum terpicu — menangkap setup buruk sebelum terisi.",
         "Order limit mewakili perdagangan yang direncanakan namun belum dieksekusi. Penganalisis "
         "limit memeriksa apakah harga masuk yang direncanakan berada di level yang secara struktural "
         "kuat, apakah stop loss berada di luar lantai noise ATR, dan bagaimana limit ini sesuai "
         "dengan posisi terbuka atau tertunda lainnya. Menggunakan Sonnet karena ini adalah keputusan "
         "yang konsekuensial (uang nyata berisiko saat limit terisi) dan penilaian kualitas masuk "
         "yang bernuansa mendapat manfaat dari model yang lebih mampu."),
        ("🏆 Pemberi Nilai Perdagangan  —  ai_trade_grader.py", C_HAIKU, "Haiku 4.5",
         "Pengguna mengklik '⚡ Nilai' pada perdagangan yang sudah ditutup",
         "Nilai A/B/C/D dengan penjelasan tertulis tentang kualitas eksekusi",
         "Loop umpan balik kualitas eksekusi — apakah masuk/keluar benar-benar dilaksanakan dengan baik?",
         "Terpisah dari apakah perdagangan menguntungkan, pemberi nilai mengevaluasi "
         "<i>seberapa baik</i> perdagangan dieksekusi: apakah masuk terjadi dekat zona ideal, apakah "
         "stop diatur dengan benar relatif terhadap struktur, apakah keluar terlalu awal atau "
         "terlambat, apakah ukuran risiko sesuai? Perdagangan bisa mendapat nilai eksekusi B+ namun "
         "tetap rugi (proses benar, nasib buruk) atau eksekusi D yang menang secara kebetulan. "
         "Melacak nilai eksekusi dari waktu ke waktu mengidentifikasi apakah kerugian berasal dari "
         "setup yang buruk atau eksekusi yang buruk — dua masalah berbeda yang memerlukan solusi berbeda."),
        ("📖 Generator Buku Aturan  —  ai_rulebook.py", C_SONNET, "Sonnet 4.6",
         "Regenerasi otomatis mingguan (jika 5+ perdagangan baru) atau pembaruan manual",
         "10 aturan personal dengan tingkat kepercayaan dan anotasi kadaluarsa",
         "Buku aturan perdagangan yang diperbarui sendiri, disintesis dari riwayat perdagangan Anda.",
         "Buku aturan adalah agen yang paling berdampak untuk peningkatan akurasi jangka panjang. "
         "Claude membaca statistik perdagangan lengkap Anda — performa per jenis setup, simbol, sesi, "
         "hari dalam seminggu, jam, periode penahanan, dan arah — dan menyintesis 5-10 aturan personal. "
         "Ini bukan saran umum melainkan aturan yang diturunkan dari DATA ANDA yang spesifik. Contoh: "
         "'Breakout Jumat pagi: 3 perdagangan, 0 menang, rata-rata -$166 — bukti kuat untuk "
         "menghindari.' Aturan yang lebih dari 30 hari diberi anotasi [kadaluarsa] sehingga Claude "
         "mendiskonnya dalam prompt analisis. Penjaga regenerasi mencegah regenerasi jika kurang dari "
         "5 perdagangan baru ada sejak pembaruan terakhir. Semua 10 aturan disuntikkan ke setiap "
         "prompt analisis sebagai prefiks stabil yang di-cache — Claude selalu memiliki konteks "
         "personal Anda tanpa membayarnya setiap panggilan."),
    ]:
        story.append(agent_card(*args, styles))
    story.append(PageBreak())

    # ── BAGIAN 4 ──────────────────────────────────────────────────────────────
    story.append(Paragraph("4. Penyedia AI Eksternal", S["h1"]))
    story.append(ColoredRule(C_ACCENT)); story.append(Spacer(1, 4))
    story.append(Paragraph(
        "Dua penyedia AI eksternal diintegrasikan bersama Claude untuk memberikan sinyal "
        "independen yang tidak dapat diperoleh dari analisis teknikal saja.", S["body"]))
    story.append(Paragraph("4.1  Google Gemini — Pemberi Skor Pra-Konfirmasi Independen", S["h2"]))
    story.append(Paragraph(
        "<b>Mengapa Gemini?</b> Memiliki model AI kedua yang memberi skor panggilan perdagangan "
        "yang sama secara independen menciptakan sinyal validasi silang. Gemini hanya melihat "
        "teks panggilan mentah — tanpa rulebook, tanpa konteks grafik. Asimetri informasi ini "
        "disengaja. Jika dua model dengan data pelatihan dan konteks yang berbeda mencapai skor "
        "yang sama, sinyalnya lebih kuat. Jika mereka sangat tidak setuju, ketidaksetujuan itu "
        "sendiri informatif.", S["body"]))
    story.append(Paragraph(
        "<b>Untuk pemula:</b> bayangkan memiliki dua dokter yang memberikan pendapat independen "
        "pada X-ray yang sama tanpa memberitahu salah satunya apa yang dikatakan yang lain. Jika "
        "keduanya mengatakan 'ini terlihat baik,' Anda percaya diri. Jika satu mengatakan 'ini "
        "serius' dan yang lain 'tidak ada yang perlu dikhawatirkan,' Anda tahu harus mendapatkan "
        "pendapat ketiga.", S["body"]))
    story.append(Paragraph(
        "<b>Detail teknis:</b> menggunakan Gemini 2.0 Flash (cepat, murah) melalui Google "
        "Generative Language API dengan <code>responseMimeType: application/json</code> untuk "
        "memaksa keluaran terstruktur. Berjalan paralel dengan pemeriksaan ATR dan pengambilan "
        "konteks pasar — tanpa tambahan waktu dinding. Di-cache 30 menit per pasangan (simbol, "
        "arah). Hasil disimpan di kolom <code>analyzed_calls.gemini_score</code> dan "
        "<code>consensus_score</code> untuk backtesting.", S["body"]))
    story.append(Spacer(1, 8))
    story.append(Paragraph("4.2  xAI Grok — Kecerdasan Sosial (X/Twitter)", S["h2"]))
    story.append(Paragraph(
        "<b>Mengapa Grok?</b> Grok memiliki akses real-time ke data X (Twitter). Untuk aset kripto "
        "berkapitalisasi kecil dan mikro, harga sering lebih didorong oleh narasi sosial dan "
        "sentimen komunitas daripada oleh fundamental on-chain atau pola teknikal. Grok adalah "
        "satu-satunya AI dalam tumpukan yang dapat melihat apa yang dikatakan orang tentang koin "
        "tertentu saat ini.", S["body"]))
    story.append(Paragraph(
        "<b>Pembobotan kapitalisasi pasar:</b> untuk koin berkapitalisasi besar seperti Bitcoin atau "
        "Ethereum, kebisingan media sosial jauh melebihi sinyal — perdagangan institusional mendominasi, "
        "dan tweet viral jarang menggerakkan harga secara bermakna. Untuk koin berkapitalisasi mikro "
        "($200M ke bawah), satu postingan berpengaruh dapat menggerakkan harga 30%. Rumus bobot "
        "mencerminkan realitas ini:", S["body"]))
    story.append(make_table([
        ["Kapitalisasi Pasar", "Bobot Grok", "Alasan"],
        ["> $5 miliar",         "0%  — dilewati", "Kapitalisasi besar: kebisingan sosial > sinyal. Aliran institusional mendominasi."],
        ["$1 M – $5 M",         "15% bobot",      "Menengah-besar: hanya konteks tambahan."],
        ["$200 jt – $1 M",      "40% bobot",      "Kecil: sentimen sosial adalah pendorong harga yang berarti."],
        ["< $200 jt",           "80% bobot",      "Mikro: narasi sosial sering menjadi pendorong UTAMA."],
        ["Kap. tidak diketahui","60% bobot",      "Diperlakukan sebagai kecil hingga CoinGecko mengkonfirmasi sebaliknya."],
    ], [3.5*cm, 2.5*cm, 8.8*cm], header_bg=C_GROK, font_size=8.5))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "<b>Apa yang disediakan Grok:</b> ringkasan 100-130 kata yang mencakup sentimen X/Twitter "
        "(bullish/bearish/campuran dan narasi dominan), berita atau perkembangan terbaru (7 hari "
        "terakhir), penilaian kualitas sosial (analisis organik vs. hype terkoordinasi), dan risiko "
        "sosial/berita terbesar terhadap arah perdagangan saat ini. Tanda bahaya secara eksplisit "
        "ditandai dengan ⚠. Ringkasan disuntikkan ke konteks prompt dengan label yang menunjukkan "
        "bobot sehingga Claude tahu seberapa banyak mengandalkannya relatif terhadap indikator "
        "teknikal.", S["body"]))
    story.append(PageBreak())

    # ── BAGIAN 5 ──────────────────────────────────────────────────────────────
    story.append(Paragraph("5. Agen Data", S["h1"]))
    story.append(ColoredRule(C_ACCENT)); story.append(Spacer(1, 4))
    story.append(Paragraph(
        "Agen data mengambil, menyimpan, dan memformat data eksternal. Mereka tidak memanggil "
        "model AI — mereka adalah pipeline data murni yang keluarannya memperkaya prompt AI.", S["body"]))
    story.append(Paragraph(
        "Semua sumber dimasukkan ke satu TypedDict <b>CollectorResult</b> melalui "
        "<b>data_sources.py</b> (lapisan adapter tipis). Menambah sumber baru = satu fungsi "
        "di data_sources.py + satu bidang di CollectorResult — tidak ada file lain yang perlu "
        "diubah. Kolektor menjalankan 12 pekerja secara paralel.", S["body"]))
    story.append(Spacer(1, 4))
    story.append(Paragraph("Lapisan 1 — Makro Global  (diambil sekali, bukan per simbol)", S["h3"]))
    story.append(make_table([
        ["Sumber", "Penyedia", "Auth", "Data / Bidang", "Digunakan di"],
        ["VIX", "CBOE · yfinance", "Gratis", "Level VIX saat ini; cache 5 menit",
         "Batas scanner (>35→6.0, >25→7.5) · Konfluensi ×0.80 saat >30"],
        ["DXY", "ICE · yfinance", "Gratis", "Level DXY; label rezim USD",
         "Blok rezim makro · Header scanner Tahap 3"],
        ["Fear & Greed", "alternative.me", "Gratis", "Skor 0-100; label (Ketakutan Ekstrem … Keserakahan Ekstrem)",
         "Logika batas scanner · Prompt sentimen · Pulsa dasbor"],
        ["Kalender Ekonomi", "Finnhub API", "Kunci", "Acara FOMC/CPI/NFP; jam_hingga; tanda macro_risk",
         "Batas scanner 7.0 saat acara berdampak tinggi dalam 24 jam · Blok risiko prompt"],
        ["Dominasi BTC + Kap Pasar", "CoinGecko (gratis)", "Gratis", "btc_dominance_pct; total_market_cap_usd; market_regime",
         "Header makro scanner · Konteks pasar Call Analyzer"],
    ], [2.0*cm, 2.8*cm, 1.0*cm, 4.6*cm, 4.4*cm], header_bg=C_RED, font_size=8))
    story.append(Spacer(1, 6))
    story.append(Paragraph("Lapisan 2 — Struktur Pasar  (seluruh kripto, bukan per simbol)", S["h3"]))
    story.append(make_table([
        ["Sumber", "Penyedia", "Auth", "Data / Bidang", "Digunakan di"],
        ["Skew Opsi (PCR/IV)", "Deribit (gratis)", "Gratis",
         "put_call_ratio; iv_skew; near_term_iv (diurutkan berdasarkan kedaluwarsa); label sentimen",
         "BTC/ETH saja — bias put/call institusional dalam prompt sentimen"],
        ["Mempool BTC", "blockchain.com", "Gratis",
         "mempool_bytes; n_transactions; avg_fee_usd; label kemacetan",
         "Konteks kemacetan on-chain disuntikkan ke prompt Call Analyzer"],
        ["Koin Trending (top 10)", "CoinGecko (gratis)", "Gratis",
         "10 simbol koin trending teratas dalam 24 jam terakhir",
         "Apakah koin yang dianalisis sedang trending? Disuntikkan ke blok konteks prompt"],
    ], [2.8*cm, 2.5*cm, 1.0*cm, 4.6*cm, 3.9*cm], header_bg=C_YELLOW, font_size=8))
    story.append(Spacer(1, 6))
    story.append(Paragraph("Lapisan 3 — Tingkat Simbol  (per koin yang dianalisis)", S["h3"]))
    story.append(make_table([
        ["Sumber", "Penyedia", "Auth", "Data / Bidang", "Digunakan di"],
        ["Rasio L/S Multi-Bursa", "Binance+Bybit+OKX · CCXT", "Gratis",
         "Rasio L/S per bursa; arah konsensus; divergensi ritel vs smart money",
         "Blok posisi massa · tanda contra-signal (>65% vs arah perdagangan)"],
        ["OI · Pendanaan · Likuidasi", "Coinalyze API", "Kunci",
         "OI gabungan (multi-bursa); tren likuidasi 24h; tingkat pendanaan; spread pendanaan per-bursa",
         "Blok derivatif dalam prompt · bias pendanaan di agen sentimen"],
        ["Peringkat kap · tier · volume", "CoinGecko (gratis)", "Gratis",
         "market_cap_rank; cap_tier (mega/besar/menengah/kecil/mikro); volume_24h_usd",
         "Konteks koin dalam prompt · skala bobot Grok berdasarkan tier kap"],
        ["TVL DeFi + perubahan 7h", "DefiLlama (gratis)", "Gratis",
         "protokol; tvl_usd; tvl_7d_change_pct (mengembalikan {} untuk non-DeFi)",
         "Token DeFi saja — konteks kesehatan protokol dalam prompt"],
        ["Candle OHLCV (4H + 1D)", "Binance Futures · CCXT", "Gratis",
         "~200 bar DataFrame OHLCV per timeframe; memunculkan exception saat gagal",
         "Semua indikator · deteksi S/R · divergensi SMT · Backtest · Grafik"],
    ], [2.8*cm, 2.8*cm, 1.0*cm, 4.6*cm, 3.6*cm], header_bg=C_ACCENT2, font_size=8))
    story.append(Spacer(1, 6))
    story.append(Paragraph("Lapisan 4 — Kecerdasan Perdagangan  (paling spesifik untuk perdagangan yang dianalisis)", S["h3"]))
    story.append(make_table([
        ["Sumber", "Penyedia", "Auth", "Data / Bidang", "Digunakan di"],
        ["Aliran Dompet Smart Money", "Nansen API", "Berbayar",
         "sinyal; label; smart_money_bias; arah akumulasi/distribusi (🟢/🔴)",
         "Prompt agen sentimen — perilaku dompet institusional"],
        ["Konteks Sosial & Berita", "xAI Grok API", "Kunci",
         "teks (narasi); bobot 0.0–0.8 (disekalakan berdasarkan tier kap)",
         "Blok terakhir dalam prompt Call Analyzer · prioritas anggaran prompt terendah"],
    ], [2.8*cm, 2.0*cm, 1.0*cm, 5.0*cm, 4.0*cm], header_bg=C_NANSEN, font_size=8))
    story.append(Spacer(1, 8))
    story.append(Paragraph("5.1  Arsitektur Konteks Grafik", S["h2"]))
    story.append(Paragraph("Pipeline grafik dibagi menjadi beberapa modul murni untuk kemudahan pengujian dan perawatan:", S["body"]))
    story.append(make_table([
        ["Modul", "Tanggung jawab", "Fungsi utama"],
        ["chart_indicators.py", "Komputasi indikator murni — tanpa panggilan API, tanpa efek samping",
         "compute_rsi, compute_macd, compute_ema_alignment, compute_adx, compute_wavetrend "
         "(VMC Cipher A/B, n1=10/n2=21), compute_cvd (rumus MFM), compute_all_indicators"],
        ["chart_sr.py", "Deteksi S/R dengan toleransi relatif-ATR dan pembobotan kebaruan",
         "detect_support_resistance (pengelompokan ATR, peluruhan eksponensial pada kebaruan sentuhan), nearest_levels"],
        ["chart_candles.py", "Pengambilan OHLCV + cache 10 menit", "get_candles (Binance via CCXT), get_candles_for_chart"],
        ["chart_patterns.py", "Garis tren + retracement Fibonacci", "detect_trendlines, detect_fibonacci"],
        ["chart_confluence.py", "Pemberi skor konfluensi 9 sinyal + divergensi SMT + pengali VIX",
         "_smt_weight (lintas-bursa ±0.5%), _smt_direction_weight (pasangan berkorelasi 24h ±1%), "
         "VIX ×0.80 saat >30 (cache 5 menit); max_val=6.50/TF"],
        ["chart_context.py", "Fasad tipis — mengekspor ulang dari 4 modul di atas",
         "get_candles, compute_indicators, confluence_score, get_candles_for_chart"],
    ], [3.2*cm, 4.5*cm, 7.1*cm], header_bg=C_ACCENT2, font_size=8.5))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "<b>Sistem konfluensi 9 sinyal:</b> RSI, MACD, EMA, ADX, WaveTrend (VMC Cipher A/B, "
        "n1=10/n2=21), MFI, CVD (Pengali Aliran Uang v×(2c−l−h)/(h−l)), anomali volume, ditambah "
        "2 varian SMT. Skor maksimum 6.50/timeframe. Pengali VIX ×0.80 diterapkan pada skor akhir "
        "saat VIX > 30 — tekanan makro secara otomatis mengurangi keyakinan konfluensi.", S["body"]))
    story.append(PageBreak())

    # ── BAGIAN 6 ──────────────────────────────────────────────────────────────
    story.append(Paragraph("6. Agen Otomatisasi", S["h1"]))
    story.append(ColoredRule(C_ACCENT)); story.append(Spacer(1, 4))
    story.append(Paragraph(
        "Agen otomatisasi berjalan terus-menerus di thread latar belakang. Tidak memerlukan "
        "interaksi pengguna — mereka memantau pasar, menyinkronkan posisi, dan mengirimkan "
        "peringatan secara otomatis.", S["body"]))
    story.append(Paragraph("6.1  Penjadwal Pemindai  —  scanner_scheduler.py", S["h2"]))
    story.append(Paragraph(
        "Menjalankan pipeline pemindai 3 tahap penuh setiap 30 menit. Pemindaian pertama dijalankan "
        "5 menit setelah aplikasi dimulai (memberi waktu sinkronisasi bursa selesai). Setelah setiap "
        "run yang menghasilkan hasil di atas ambang skor:", S["body"]))
    for l in [
        "1. Mengirimkan peringatan HTML Telegram dengan simbol, arah, skor, zona masuk, SL, TP1, TP2, R:R, dan urgensi.",
        "2. Menyimpan setiap setup yang diberi peringatan ke <code>analyzed_calls</code> dengan analyst='scanner'. "
           "Ini penting untuk penautaan posisi otomatis — saat posisi yang diberi peringatan pemindai terbuka di bursa, "
           "<code>check-matches</code> otomatis mengkonfirmasi tautan tanpa tindakan pengguna.",
        "3. Mendeduplikasi berdasarkan (simbol, arah) dalam jendela 4 jam untuk mencegah spam pada scan berturutan.",
    ]:
        story.append(Paragraph(l, S["bullet"]))
    story.append(Spacer(1, 6))
    story.append(Paragraph("6.2  Sinkronisasi Bursa  —  bitget_sync.py / blofin_sync.py", S["h2"]))
    story.append(Paragraph(
        "Berjalan setiap 5 menit di thread latar belakang. Menggunakan paginasi berbasis kursor "
        "untuk menangkap semua posisi tertutup tanpa memandang durasi. Perilaku utama:", S["body"]))
    story.append(make_table([
        ["Fitur", "Apa yang dilakukan"],
        ["Tutup panggilan otomatis",
         "Saat posisi ditutup, menemukan analyzed_call yang ditautkan dan menandainya ditutup. "
         "Mencatat TP atau SL mana yang terpukul berdasarkan harga tutup vs level."],
        ["Penandaan rezim pasar",
         "Menandai setiap posisi bull/bear/range pada waktu masuk menggunakan persilangan EMA50/200 BTC "
         "(get_btc_regime). Memungkinkan pemfilteran analitik berdasarkan kondisi pasar."],
        ["Pelacakan MFE/MAE",
         "Mencatat Maximum Favourable Excursion dan Maximum Adverse Excursion untuk setiap perdagangan. "
         "Digunakan untuk analitik 'apakah Anda keluar terlalu awal?'"],
        ["Deduplikasi",
         "Idempoten — aman dijalankan setiap 5 menit. Menggunakan ID order bursa untuk mencegah entri "
         "duplikat tanpa memandang seberapa sering sinkronisasi berjalan."],
        ["Jendela kejar-ketinggalan",
         "Saat startup, mengambil perdagangan dari 48 jam terakhir untuk memulihkan yang terlewat "
         "selama downtime (restart Pi, gangguan jaringan, dll.)"],
    ], [3.8*cm, 11*cm], header_bg=colors.HexColor("#2a5090"), font_size=8.5))
    story.append(PageBreak())

    # ── BAGIAN 7 ──────────────────────────────────────────────────────────────
    story.append(Paragraph("7. Backtest Tertanam & Optimizer", S["h1"]))
    story.append(ColoredRule(C_ACCENT)); story.append(Spacer(1, 4))
    story.append(Paragraph(
        "Backtest tertanam berjalan sepenuhnya pada posisi historis yang tersimpan di database "
        "SQLite lokal — tidak memerlukan panggilan API eksternal. Menggunakan logika indikator "
        "yang sama dengan pipeline live (WaveTrend n1=10/n2=21, rumus CVD MFM) sehingga hasil "
        "backtest sebanding dengan kualitas sinyal live.", S["body"]))
    story.append(Paragraph("7.1  Mesin Backtest  —  backtest_engine.py", S["h2"]))
    story.append(make_table([
        ["Komponen", "Apa yang dilakukan"],
        ["run_backtest(simbol, tf, hari, params, end_offset_days)",
         "Mengambil OHLCV via CCXT, menerapkan logika sinyal tervektor, mensimulasikan perdagangan, "
         "mengembalikan BacktestResult dengan Sharpe, Sortino, drawdown maksimum, faktor keuntungan, "
         "tingkat kemenangan, rata-rata menang/kalah. end_offset_days menggeser jendela pengambilan "
         "ke belakang dalam waktu."],
        ["backtest_metrics.py",
         "Fungsi metrik murni: sharpe_ratio (varians sampel N−1, disetahunkan √365), sortino_ratio "
         "(hanya std downside), max_drawdown (fraksi puncak-ke-lembah), profit_factor (keuntungan "
         "bruto / kerugian bruto). Atribusi GPL-3.0."],
        ["Parameter yang dapat dikonfigurasi",
         "rsi_oversold (25–45), rsi_overbought (55–75), ema_short/long (10–50/50–250), adx_min "
         "(15–35), atr_sl_mult (1.0–3.0), min_confluence (1–4). Semua 7 parameter dapat dicari "
         "oleh optimizer Bayesian."],
    ], [4.5*cm, 10.3*cm], header_bg=colors.HexColor("#2a4020"), font_size=8.5))
    story.append(Spacer(1, 8))
    story.append(Paragraph("7.2  Optimizer Bayesian  —  backtest_optimizer.py", S["h2"]))
    story.append(Paragraph(
        "Menggunakan Optuna (sampler TPE) untuk memaksimalkan rasio Sharpe dari 7 parameter. "
        "Berjalan di thread daemon latar belakang agar UI tetap responsif. Setiap run disimpan "
        "di tabel <b>optimizer_runs</b> — tab Analisis menampilkan 5 run terakhir dengan Sharpe, "
        "tingkat kemenangan, dan parameter terbaik.", S["body"]))
    story.append(Paragraph("7.3  Uji Walk-Forward  —  Tanpa Kebocoran Data", S["h2"]))
    story.append(Paragraph(
        "Uji walk-forward membagi rentang tanggal posisi nyata menjadi 70% pelatihan / 30% uji. "
        "Detail implementasi kritis: <b>end_offset_days</b> diteruskan melalui run_backtest → "
        "_fetch_ohlcv sehingga jendela pelatihan berakhir di <i>sekarang − hari_uji</i> "
        "(bukan di <i>sekarang</i>). Tanpa ini, kedua jendela berlabuh ke masa kini — set uji "
        "adalah subset set pelatihan, membuat semua hasil walk-forward tidak berarti.", S["body"]))
    story.append(make_table([
        ["Jendela", "Rentang pengambilan", "Sumber parameter", "Tujuan"],
        ["Pelatihan (70%)", "sekarang − (hari_uji+hari_latih) → sekarang − hari_uji",
         "Optimizer memaksimalkan Sharpe di sini", "Temukan parameter terbaik"],
        ["Uji (30%)", "sekarang − hari_uji → sekarang",
         "Parameter terbaik pelatihan diterapkan beku", "Ukur Sharpe di luar sampel"],
        ["Sinyal overfitting", "train_sharpe >> test_sharpe", "—",
         "Selisih > 0.5 menunjukkan curve-fitting; gunakan parameter lebih sederhana"],
    ], [2.5*cm, 4.5*cm, 3.5*cm, 4.3*cm], header_bg=C_ACCENT, font_size=8.5))
    story.append(Spacer(1, 8))
    story.append(Paragraph("7.4  Metrik Dasbor  —  analytics.py", S["h2"]))
    story.append(Paragraph(
        "Sharpe dan Calmar dihitung dari <b>wallet_snapshots</b> (riwayat saldo bergulir yang "
        "diimpor dari bursa). Invariant rumus utama:", S["body"]))
    story.append(make_table([
        ["Metrik", "Rumus", "Catatan"],
        ["Rasio Sharpe", "mean(ret_harian) × 365 / (std(ret_harian, N−1) × √365)",
         "Varians sampel (penyebut N−1). Filter dompet: saldo > $1 USDT."],
        ["Rasio Calmar", "ann_return_pct / max_dd_pct",
         "max_dd_pct diukur sebagai % dari puncak bergulir SAAT LEMBAH — bukan ATH akhir."],
        ["Volatilitas tahunan", "std(ret_harian, N−1) × √365 × 100",
         "Ditampilkan sebagai % di bawah Sharpe pada dasbor."],
    ], [2.5*cm, 6.5*cm, 5.8*cm], header_bg=colors.HexColor("#2a1a40"), font_size=8.5))
    story.append(PageBreak())

    # ── BAGIAN 8 ──────────────────────────────────────────────────────────────
    story.append(Paragraph("8. Arsitektur Prompt & Caching", S["h1"]))
    story.append(ColoredRule(C_ACCENT)); story.append(Spacer(1, 4))
    story.append(Paragraph(
        "Cara prompt dibangun dan di-cache adalah salah satu keputusan arsitektur terpenting "
        "dalam sistem — secara langsung memengaruhi biaya dan akurasi.", S["body"]))
    story.append(Paragraph("8.1  Pemisahan Stabil / Dinamis", S["h2"]))
    story.append(Paragraph(
        "Caching prompt Anthropic bekerja dengan menyimpan prefiks yang identik secara byte-per-byte "
        "antar panggilan. Saat prefiks ter-cache digunakan kembali, Anthropic menagih sekitar 10% "
        "dari harga token input normal untuk bagian yang ter-cache. Namun, jika data live (tingkat "
        "pendanaan, indikator grafik, konteks pasar) disertakan dalam blok ter-cache, kunci cache "
        "berubah setiap beberapa menit dan cache hit tidak pernah terjadi — Anda membayar harga "
        "penuh setiap panggilan.", S["body"]))
    story.append(Paragraph(
        "Solusinya: <b>build_stable_prefix()</b> mengembalikan hanya konten yang berubah paling "
        "banyak setiap minggu (rulebook + kalibrasi + kekuatan pola). <b>build_context()</b> "
        "mengembalikan konten dinamis yang berubah per panggilan (wawasan backtest, data pasar, "
        "indikator grafik, Nansen, Grok, perdagangan serupa). Prefiks stabil mendapat "
        "<code>cache_control: ephemeral</code> — konteks dinamis tidak.", S["body"]))
    story.append(make_table([
        ["Blok", "Isi", "Cache?", "Berubah seberapa sering"],
        ["Prefiks stabil (Blok 1)",
         "Rulebook (10 aturan), umpan balik kalibrasi, kekuatan pola teratas-3",
         "✓ YA\ncache_control:\nephemeral", "Mingguan (saat 5+ perdagangan baru)"],
        ["Konteks dinamis (Blok 2)",
         "Wawasan backtest, konteks pasar live, indikator grafik, sinyal Nansen, ringkasan sosial "
         "Grok, perdagangan historis serupa",
         "✗ TIDAK", "Setiap panggilan (data pasar setiap 5 menit)"],
        ["Prompt variabel (Blok 3)",
         "Teks panggilan, ukuran posisi, CoT dari analisis simbol yang sama sebelumnya",
         "✗ TIDAK", "Setiap panggilan"],
    ], [3.5*cm, 6.5*cm, 2.0*cm, 2.8*cm], header_bg=C_ACCENT, font_size=8.5))
    story.append(Spacer(1, 8))
    story.append(Paragraph("8.2  Loop Umpan Balik Backtest", S["h2"]))
    story.append(Paragraph(
        "Setiap prompt analisis Claude dimulai dengan blok performa historis ringkas yang disuntikkan "
        "oleh <b>get_backtest_context()</b> di analytics.py. Ini memberi Claude konteks numerik "
        "spesifik tentang pola perdagangan ANDA sebelum memberi skor panggilan baru:", S["body"]))
    story.append(Paragraph("<i>Contoh konteks backtest yang disuntikkan ke prompt:</i>", S["small"]))
    story.append(Paragraph(
        "WAWASAN BACKTEST:\n"
        "  Forma terkini: 72% TK 20 terakhir · streak MMKMM · rata2 +$8.40\n"
        "  Setup breakout: 100% TK (6 perdagangan) rata2 +$7.00\n"
        "  BTCUSDT Long: 75% TK (12 perdagangan) rata2 +$12.50\n"
        "  ⚠ Rabu: hati-hati (57% TK, -$355 total P&L)\n"
        "  ⚠ 21:00 UTC: jam lemah (70% TK, -$1831 total)", S["code"]))
    story.append(Paragraph(
        "Ini bukan saran umum — ini diturunkan dari riwayat perdagangan aktual pengguna secara "
        "real time. Claude melihat sinyal peluang (setup teknikal) dan konteks historis (apakah "
        "trader ini benar-benar untung dari jenis setup ini pada jam ini?) sebelum memberikan skor. "
        "Ini adalah mekanisme utama untuk meningkatkan akurasi seiring bertambahnya riwayat "
        "perdagangan.", S["body"]))
    story.append(Spacer(1, 8))
    story.append(Paragraph("8.3  Loop Pembelajaran CoT", S["h2"]))
    story.append(Paragraph(
        "Saat Claude menganalisis panggilan, penalaran langkah-demi-langkah (bidang 'thinking' "
        "dalam respons JSON) disimpan sebagai <code>cot_reasoning</code> di database. Saat simbol "
        "yang sama dianalisis berikutnya, penalaran sebelumnya disuntikkan ke prompt sebagai "
        "konteks ANALISIS SEBELUMNYA. Claude kemudian dapat secara eksplisit membandingkan: "
        "'Terakhir menganalisis ARKMUSDT, saya menandai SL terlalu dekat ke lantai noise 4H. "
        "Apakah itu berubah?' Ini memungkinkan deteksi kesalahan berulang dan penyempurnaan "
        "berkelanjutan tanpa pelatihan ulang apa pun.", S["body"]))
    story.append(PageBreak())

    # ── BAGIAN 9 ──────────────────────────────────────────────────────────────
    story.append(Paragraph("9. Sistem Tautan Posisi Otomatis", S["h1"]))
    story.append(ColoredRule(C_ACCENT)); story.append(Spacer(1, 4))
    story.append(Paragraph(
        "Fitur utama adalah perdagangan yang diperingatkan pemindai dan panggilan yang dianalisis "
        "manual secara otomatis ditautkan ke posisi live yang sesuai — tanpa memerlukan pengguna "
        "mengkonfirmasi setiap kecocokan secara manual.", S["body"]))
    story.append(Paragraph("9.1  Cara tautan dibuat", S["h2"]))
    story.append(make_table([
        ["Skenario", "Perilaku tautan otomatis"],
        ["Pemindai mengirim peringatan Telegram untuk ARKMUSDT Long",
         "scanner_scheduler._persist_setups() menyimpan setup ke analyzed_calls "
         "(analyst='scanner', status='saved'). Saat ARKMUSDT Long muncul di posisi live, "
         "check-matches otomatis mengkonfirmasinya dan mengatur status='matched'. "
         "Tidak diperlukan klik pengguna."],
        ["Pengguna menjalankan call analyzer untuk NOTUSDT Long, posisi ditutup, lalu dibuka lagi",
         "Panggilan ditutup otomatis saat posisi ditutup. Saat NOTUSDT Long baru terbuka, "
         "check-matches mendeteksi panggilan tertutup + posisi yang cocok dan otomatis "
         "mengaktifkannya kembali (status='matched'). Banner 'Sebelumnya ditautkan' muncul di UI."],
        ["Perdagangan datang dari Telegram tetapi tidak ada panggilan yang pernah dianalisis",
         "Kartu posisi live menampilkan tombol kuning '📝 Analisis Dulu'. Mengkliknya navigasi "
         "ke penganalisis panggilan dengan simbol sudah terisi. Setelah menjalankan dan menyimpan "
         "analisis, tautan dibuat secara otomatis."],
        ["Sinyal pemindai tidak tersimpan (skor di bawah ambang) tetapi posisi dibuka",
         "Entri panggilan minimal dapat dibuat langsung dari data posisi live dengan "
         "analyst='scanner'. check-matches otomatis mengkonfirmasinya pada siklus berikutnya."],
    ], [4.5*cm, 10.3*cm], header_bg=C_ACCENT3, font_size=8.5))
    story.append(Spacer(1, 8))
    story.append(Paragraph("9.2  Yang muncul di Perdagangan Live saat ditautkan", S["h2"]))
    story.append(Paragraph(
        "Setelah posisi ditautkan ke panggilan, kartu perdagangan live menampilkan "
        "<b>Panel Target Panggilan</b> dengan: jarak dari harga mark ke SL, TP1, TP2, dan "
        "rata-rata masuk panggilan. Peringatan TP1-tercapai muncul saat harga mark melewati TP1, "
        "dengan saran stop break-even otomatis. Posisi dengan panggilan tertaut juga menampilkan "
        "skor setup, jenis perdagangan, dan rasio R:R dari analisis asli.", S["body"]))
    story.append(PageBreak())

    # ── BAGIAN 10 ─────────────────────────────────────────────────────────────
    story.append(Paragraph("10. Pengukuran Akurasi & Target 85%", S["h1"]))
    story.append(ColoredRule(C_ACCENT)); story.append(Spacer(1, 4))
    story.append(Paragraph(
        "Target akurasi ≥85% berarti: saat skor konsensus ≥6 dan kepercayaan 'tinggi' (Claude dan "
        "Gemini sepakat dalam 1 poin), perdagangan seharusnya menguntungkan setidaknya 85% dari "
        "waktu. Ini diukur oleh <b>scripts/backtest_consensus.py</b>.", S["body"]))
    story.append(Paragraph("10.1  Tiga hipotesis yang diuji", S["h2"]))
    story.append(make_table([
        ["Hipotesis", "Klaim", "Cara diukur"],
        ["H1: Claude saja",
         "Skor ≥6 dari Claude saja memprediksi perdagangan menguntungkan",
         "outcome_is_win() untuk semua panggilan dengan setup_score ≥ N"],
        ["H2: Konsensus",
         "Kesepakatan antara Claude dan Gemini (|Δ|≤1) meningkatkan akurasi vs H1",
         "outcome_is_win() untuk panggilan dengan consensus_score ≥ N DAN confidence='high'"],
        ["H3: Penghindaran divergensi",
         "Panggilan dengan |Δ|>2 (tanda TINJAU) memiliki tingkat kemenangan lebih rendah dari rata-rata",
         "outcome_is_win() untuk panggilan dengan |claude_score - gemini_score| > 2"],
    ], [2.5*cm, 5.5*cm, 6.8*cm], header_bg=C_ACCENT, font_size=8.5))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "<b>Status saat ini:</b> sistem sedang mengumpulkan bukti — setiap panggilan baru yang "
        "disimpan menyimpan gemini_score dan consensus_score, dan setiap hasil yang dicatat "
        "memperbaiki konteks backtest yang disuntikkan ke prompt masa depan. Target 85% dapat "
        "diukur setelah ~15-20 panggilan dengan hasil lebih.", S["body"]))
    story.append(Paragraph(
        "Jalankan backtest kapan saja: <code>python3 scripts/backtest_consensus.py "
        "--host &lt;ip-pi&gt;:8082</code>. Tambahkan <code>--live</code> untuk memberi skor ulang "
        "20 panggilan terakhir dengan Gemini live (menggunakan kredit API).", S["body"]))
    story.append(PageBreak())

    # ── BAGIAN 11 ─────────────────────────────────────────────────────────────
    story.append(Paragraph("11. Pipeline Agen Khusus (v1.5.0)", S["h1"]))
    story.append(ColoredRule(C_ACCENT)); story.append(Spacer(1, 4))
    story.append(Paragraph(
        "Pipeline AI direfaktor menjadi agen khusus dengan kontrak masukan/keluaran bertipe "
        "(TypedDict). Setiap agen memiliki satu tanggung jawab yang jelas, dapat diuji secara "
        "terpisah, dan berkomunikasi hanya melalui nilai kembalian — tidak ada state yang "
        "dibagikan. Semua TypedDict berada di <code>agent_types.py</code>.", S["body"]))
    story.append(Spacer(1, 6))
    story.append(Paragraph("11.1  Alur Pipeline", S["h2"]))
    story.append(Paragraph(
        "<pre>DataCollector → [DataInterpreter + MarketSentiment (paralel)] → DataReviewer\n"
        "    → TradePrep (Claude + Gemini) → RiskMgmt → AnalysisResult\n\n"
        "Setelah posisi terbuka:\n"
        "    TradeMonitor (latar belakang, setiap 10 menit) menjalankan:\n"
        "    DataCollector → DataInterpreter → MarketSentiment → vonis Haiku\n"
        "    Saat risk_rating >= 7 atau tindakan != Tahan: kirim peringatan Telegram + atur lencana UI</pre>",
        ParagraphStyle("blok_kode", fontName="Courier", fontSize=8,
                       textColor=C_ACCENT2, backColor=C_SURFACE, borderPadding=6, leading=12)))
    story.append(Spacer(1, 6))
    story.append(Paragraph("11.2  Kontrak Agen", S["h2"]))
    story.append(make_table([
        ["Agen", "Masukan", "Keluaran", "Panggilan AI?", "Akses DB?"],
        ["DataCollector",       "CollectorInput",    "CollectorResult",   "Tidak",        "Tidak"],
        ["DataInterpreter",     "CollectorResult",   "InterpreterResult", "Tidak",        "Tidak"],
        ["MarketSentiment",     "CollectorResult",   "SentimentResult",   "Tidak",        "Tidak"],
        ["DataReviewer",        "InterpreterResult", "ReviewerResult",    "Tidak",        "Hanya baca"],
        ["RiskManagement",      "TradePrepResult",   "RiskResult",        "Tidak",        "Tidak"],
        ["TradePreparation",    "Semua 4 di atas",   "TradePrepResult",   "Sonnet+Gemini","Baca (prefiks stabil)"],
        ["TradeMonitor",        "Posisi + Interp",   "MonitorResult",     "Haiku",        "Baca"],
        ["ChartDraw",           "Candle + level",    "PNG (base64)",      "Tidak",        "Tidak"],
    ], [3.2*cm, 3.0*cm, 3.0*cm, 2.5*cm, 2.5*cm], header_bg=C_ACCENT, font_size=8.5))
    story.append(Spacer(1, 8))
    story.append(Paragraph("11.3  Kemampuan Baru", S["h2"]))
    C_CHART = colors.HexColor("#f39c12")
    for args in [
        ("📊 Grafik Perdagangan Beranotasi  —  agent_chart_draw.py", C_CHART, "v1.1.0",
         "Otomatis — dipicu oleh TradePrep atau thread monitor", "Lihat deskripsi", "",
         "Saat TradePrep menghasilkan rekomendasi perdagangan, agent_chart_draw.py menghasilkan "
         "grafik candlestick mplfinance bertema gelap yang dianotasi dengan garis Masuk (biru "
         "putus-putus), Stop Loss (merah putus-putus), TP1 dan TP2 (hijau), legenda level, dan "
         "kriteria keputusan sebagai teks yang ditumpangkan di kanan atas. PNG dikodekan base64 "
         "dan disimpan di analyzed_calls.chart_png_b64. Peringatan pemindai Telegram melampirkan "
         "grafik ini sebagai foto — Anda melihat setup perdagangan secara visual, bukan hanya "
         "sebagai angka."),
        ("⚖️ Ukuran Posisi Kriteria Kelly  —  agent_risk_mgmt.py", C_ACCENT3, "v1.1.0",
         "Otomatis — dipicu oleh TradePrep atau thread monitor", "Lihat deskripsi", "",
         "Ukuran posisi kini mencakup fraksi kriteria Kelly (0.05–0.25) yang diturunkan dari "
         "setup_score sebagai proksi keunggulan. Kelly memetakan skor 1-10 ke estimasi tingkat "
         "kemenangan 0.35–0.75, kemudian menghitung f = (TK×R − (1−TK)) / R di mana R = 2.0 "
         "(baseline R:R konservatif 2:1). Dibatasi maksimum 0.25 untuk mencegah pertaruhan "
         "berlebihan. sizing_breakdown dan kelly_fraction disimpan di analyzed_calls.risk_verdict_json "
         "untuk ditinjau."),
        ("🔍 Monitor Posisi Proaktif  —  monitor_scheduler.py", C_SONNET, "v1.1.0",
         "Otomatis — dipicu oleh TradePrep atau thread monitor", "Lihat deskripsi", "",
         "Thread latar belakang memantau semua posisi terbuka setiap 10 menit. Posisi di mana "
         "unrealized_pct < -5% ATAU durasi > 4 jam diperiksa dengan rantai DataCollector → "
         "DataInterpreter → MarketSentiment → Haiku yang ringan. Saat risk_rating ≥ 7 atau "
         "tindakan ≠ Tahan, sistem mengirim peringatan Telegram dan mengatur monitor_alert=1 di "
         "analyzed_calls untuk lencana UI — tanpa mengeksekusi perdagangan apa pun (hanya rekomendasi)."),
        ("🔀 Deteksi Sinyal Kontra  —  agent_market_sentiment.py", C_ACCENT2, "v1.1.0",
         "Otomatis — dipicu oleh TradePrep atau thread monitor", "Lihat deskripsi", "",
         "Agen MarketSentiment menghitung tanda contra_signal: True saat massa memposisikan diri "
         "dengan berat berlawanan arah perdagangan yang diusulkan (>65% akun long saat Anda akan "
         "Long). Kesadaran kontrarian disuntikkan ke setiap prompt TradePrep, dan TradeMonitor "
         "menggunakannya untuk meningkatkan peringkat risiko posisi yang ada yang berenang "
         "melawan arus massa."),
    ]:
        story.append(agent_card(*args, styles))
    story.append(PageBreak())

    # ── BAGIAN 12 ─────────────────────────────────────────────────────────────
    story.append(Paragraph("12. Referensi Agen Lengkap", S["h1"]))
    story.append(ColoredRule(C_ACCENT)); story.append(Spacer(1, 4))
    story.append(make_table([
        ["Agen / Modul", "Jenis", "Model", "Pemicu", "Anggaran token"],
        ["call_analyzer",        "Analisis",    "Sonnet 4.6",    "Sesuai permintaan",       "~4.000 masuk / 4.096 keluar"],
        ["scanner (batch)",      "Analisis",    "Sonnet 4.6",    "Otomatis 30 menit",       "~5.500 masuk / 14.400 keluar"],
        ["scanner (cepat)",      "Analisis",    "Haiku 4.5",     "Per finalis",             "~1.200 masuk / 120 keluar"],
        ["advisor",              "Analisis",    "Sonnet 4.6",    "Sesuai permintaan",       "~4.000 masuk / 4.096 keluar"],
        ["rulebook",             "Analisis",    "Sonnet 4.6",    "Mingguan / manual",       "~3.000 masuk / 2.048 keluar"],
        ["hindsight",            "Analisis",    "Haiku 4.5",     "Batch sesuai permintaan", "~800 masuk / 512 keluar"],
        ["live_trade",           "Analisis",    "Haiku 4.5",     "Per klik",                "~600 masuk / 768 keluar"],
        ["trade_grader",         "Analisis",    "Haiku 4.5",     "Per perdagangan ditutup", "~700 masuk / 350 keluar"],
        ["limit_analyzer",       "Analisis",    "Sonnet 4.6",    "Per order limit",         "~2.000 masuk / 768 keluar"],
        ["pattern_detector",     "Analisis",    "Sonnet 4.6",    "Melalui advisor",         "~2.500 masuk / 1.200 keluar"],
        ["Gemini 2.0 Flash",     "AI Eksternal","Gemini 2.0",    "Paralel dengan panggilan","~300 masuk / 200 keluar"],
        ["xAI Grok 3 Fast",      "AI Eksternal","Grok 3",        "Paralel dengan panggilan","~250 masuk / 130 keluar"],
        ["Nansen screener",      "Data",        "—",             "Per pemindaian / panggilan","1 kredit API per run"],
        ["chart_context",        "Data",        "—",             "Per analisis",            "Binance REST (ter-cache)"],
        ["market_context",       "Data",        "—",             "Per analisis",            "4 bursa + 2 API"],
        ["scanner_scheduler",    "Otomatisasi", "—",             "Setiap 30 menit",         "Menjalankan pemindai + Telegram"],
        ["monitor_scheduler",    "Otomatisasi", "—",             "Setiap 10 menit",         "Haiku per posisi"],
        ["bitget_sync",          "Otomatisasi", "—",             "Setiap 5 menit",          "Kursor Bitget REST"],
        ["blofin_sync",          "Otomatisasi", "—",             "Setiap 5 menit",          "Kursor Blofin REST"],
        ["agent_data_collector", "Agen",        "—",             "Per panggilan pipeline",  "Paralel: 12 sumber (4 lapisan)"],
        ["agent_data_interpreter","Agen",       "—",             "Per panggilan pipeline",  "Murni: indikator"],
        ["agent_market_sentiment","Agen",       "—",             "Per panggilan pipeline",  "Murni: bias makro"],
        ["agent_data_reviewer",  "Agen",        "—",             "Per panggilan pipeline",  "Baca DB: KPI"],
        ["agent_risk_mgmt",      "Agen",        "—",             "Per panggilan pipeline",  "Matematika murni: Kelly"],
        ["agent_trade_prep",     "Agen",        "Sonnet+Gemini", "Per panggilan pipeline",  "Panggilan AI utama"],
        ["agent_trade_monitor",  "Agen",        "Haiku 4.5",     "Per pass monitor",        "~800 masuk / 300 keluar"],
        ["agent_chart_draw",     "Agen",        "—",             "Per TradePrep",           "PNG mplfinance"],
    ], [3.8*cm, 2.3*cm, 2.5*cm, 2.5*cm, 3.7*cm], header_bg=C_ACCENT, font_size=8))

    story.append(Spacer(1, 10))
    story.append(ColoredRule(C_MUTED)); story.append(Spacer(1, 4))
    story.append(Paragraph(
        "Jurnal Perdagangan v1.5.0 · Berbasis mandiri di Raspberry Pi 5 · "
        "Dibuat dengan Claude Code · github.com/anvilfilbert/Auto-Crypto-Tradingjournal",
        ParagraphStyle("catatan_kaki", fontSize=8, textColor=C_MUTED,
                       fontName="Helvetica", alignment=TA_CENTER)))
    return story


def on_page(canvas, doc):
    canvas.saveState()
    w, h = A4
    canvas.setFillColor(C_BG); canvas.rect(0, 0, w, h, fill=1, stroke=0)
    canvas.setFillColor(C_ACCENT); canvas.rect(0, h - 6*mm, w, 6*mm, fill=1, stroke=0)
    canvas.setFillColor(C_SURFACE); canvas.rect(0, 0, w, 10*mm, fill=1, stroke=0)
    canvas.setFont("Helvetica", 8); canvas.setFillColor(C_MUTED)
    canvas.drawCentredString(w / 2, 3.5*mm, f"Halaman {doc.page}")
    canvas.restoreState()


def main():
    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    print(f"Membuat PDF → {OUTPUT}")
    doc = SimpleDocTemplate(
        OUTPUT, pagesize=A4,
        leftMargin=1.8*cm, rightMargin=1.8*cm,
        topMargin=1.6*cm,  bottomMargin=1.6*cm,
        title="Jurnal Perdagangan — Arsitektur Agen AI",
        author="Jurnal Perdagangan v1.5.0",
        subject="Kerangka Kecerdasan Multi-Model v1.5.0",
    )
    styles = make_styles()
    story  = build_story(styles)
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    size_mb = os.path.getsize(OUTPUT) / 1_000_000
    print(f"Selesai — {size_mb:.1f} MB  →  {OUTPUT}")


if __name__ == "__main__":
    main()
