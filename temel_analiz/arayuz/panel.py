# temel_analiz/arayuz/panel.py

from __future__ import annotations
import os, logging
from datetime import datetime
from typing import Dict, List

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFrame,
    QLabel, QMessageBox, QTableWidget, QTableWidgetItem,
    QLineEdit, QPushButton, QTabWidget, QApplication
)
from PyQt6.QtGui import QFont
from PyQt6.QtCore import Qt, QTimer

# ÖZEL TITLEBAR
from temel_analiz.arayuz.titlebar import CustomTitleBar

# Donut + analitik bileşenler
from temel_analiz.gorsellestirme.cizimler import DonutWidget
from temel_analiz.hesaplayicilar.puan_karti import analyze_symbols, pie_data_from_payload
from temel_analiz.arayuz.tarayici import ScannerWidget
from temel_analiz.hedef_fiyat_radar import HedefFiyatRadarWidget

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

SECTOR_TRANSLATIONS = {
    "BANK": "Bankacılık", "INSURANCE": "Sigorta", "REIT": "GYO",
    "AVIATION": "Havacılık", "HOLDING": "Holding", "TECH": "Teknoloji",
    "INDUSTRY": "Sanayi"
}

THEMES = {
    "Nebula Mavisi": {
        "accent_glow": "#00AEEF",
        "panel_bg": "rgba(25, 35, 53, 0.95)",
        "panel_border": "rgba(0, 174, 239, 0.5)",
        "text_primary": "#E5E7EB",
        "muted": "#94A3B8",
        "surface": "rgba(30, 41, 59, 0.78)",
        "stroke": "#334155",
        "bg_fallback": "#0B101A"
    }
}

class Card(QFrame):
    def __init__(self, title: str, theme: Dict[str, str]):
        super().__init__()
        self.theme = theme
        self.setObjectName("glass_panel")
        self.setStyleSheet(
            f"""
            QFrame#glass_panel {{
                background-color: {theme['panel_bg']};
                border: 1px solid {theme['panel_border']};
                border-radius: 12px;
            }}
            """
        )
        self._main = QVBoxLayout(self)
        self._main.setContentsMargins(14, 12, 14, 12)
        self._main.setSpacing(8)

        if title:
            lbl = QLabel(title)
            lbl.setStyleSheet(f"color:{theme['accent_glow']}; font-weight:600;")
            lbl.setFont(QFont("Segoe UI", 16))
            self._main.addWidget(lbl)

    def bodyLayout(self):
        if not hasattr(self, "_body"):
            self._body = QWidget()
            self._vbox = QVBoxLayout(self._body)
            self._vbox.setContentsMargins(4, 4, 4, 4)
            self._vbox.setSpacing(8)
            self._main.addWidget(self._body)
        return self._vbox


def make_badge(text: str, bg: str = "#0EA5E9") -> QFrame:
    chip = QFrame()
    chip.setObjectName("badge")
    chip.setStyleSheet(
        f"""
        QFrame#badge {{
            background-color:{bg};
            color:white;
            border-radius:12px;
            padding:6px 10px;
        }}
        QLabel#badge_text {{
            color:white;
            font-weight:600;
        }}
        """
    )
    lay = QHBoxLayout(chip)
    lay.setContentsMargins(10, 2, 10, 2)
    lbl = QLabel(text)
    lbl.setObjectName("badge_text")
    lbl.setFont(QFont("Segoe UI", 11))
    lay.addWidget(lbl)
    return chip

# -----------------------------------------------------------
#    TEKLİ ANALİZ WIDGET
# -----------------------------------------------------------
class SingleAnalysisWidget(QWidget):
    def __init__(self, theme, default_symbol=None):
        super().__init__()
        self.theme = theme
        self._init_ui()

        if default_symbol:
            QTimer.singleShot(500, lambda: self.trigger_fundamental_analysis(default_symbol))

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(12)

        # ÜST BÖLÜM -----------------------------------------------------------------
        header_card = Card("Profesyonel Analiz Raporu", self.theme)
        root.addWidget(header_card)

        h_layout = QHBoxLayout()
        header_card.bodyLayout().addLayout(h_layout)

        title_layout = QVBoxLayout()
        self.title_label = QLabel("Analiz Platformu")
        self.title_label.setFont(QFont("Segoe UI", 22, QFont.Weight.Bold))
        self.title_label.setStyleSheet(f"color:{self.theme['accent_glow']}")

        # UYARI KUTUSU
        self.warning_frame = QFrame()
        self.warning_frame.setStyleSheet(
            "background-color: #450a0a; border: 1px solid #ef4444; border-radius: 6px;"
        )
        self.warning_frame.hide()

        w_lay = QHBoxLayout(self.warning_frame)
        w_lay.setContentsMargins(8, 4, 8, 4)
        self.warning_label = QLabel("")
        self.warning_label.setStyleSheet("color: #fca5a5; font-weight: bold;")
        w_lay.addWidget(self.warning_label)

        title_layout.addWidget(self.title_label)
        title_layout.addWidget(self.warning_frame)

        self.subtitle_label = QLabel("Hazır")
        self.subtitle_label.setStyleSheet(f"color:{self.theme['text_primary']}")
        self.subtitle_label.setFont(QFont("Segoe UI", 12))

        h_layout.addLayout(title_layout)
        h_layout.addStretch()
        h_layout.addWidget(self.subtitle_label, alignment=Qt.AlignmentFlag.AlignRight)

        # Arama Kutusu
        line2 = QHBoxLayout()
        line2.setContentsMargins(0, 6, 0, 0)

        self.search_input = QLineEdit(objectName="search_input")
        self.search_input.setPlaceholderText("Hisse Kodu (Örn: ASELS, THYAO)")
        self.search_input.returnPressed.connect(self._on_search)

        search_button = QPushButton("DETAYLI ANALİZ ET", objectName="search_button")
        search_button.clicked.connect(self._on_search)

        line2.addWidget(self.search_input, 1)
        line2.addWidget(search_button, 0)
        line2.addStretch()

        header_card.bodyLayout().addLayout(line2)

        # ANA GRID
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        root.addLayout(grid)

        # DONUT
        score_card = Card("Skor Kartı (0-100)", self.theme)
        self.donut = DonutWidget(self.theme)
        score_card.bodyLayout().addWidget(self.donut)
        score_card.setMinimumWidth(860)
        score_card.setMaximumWidth(1000)
        grid.addWidget(score_card, 0, 0, 1, 1)

        # SAĞ TARAF
        right_col_w = QWidget()
        right_col = QVBoxLayout(right_col_w)

        val_card = Card("Kurumsal Değerleme", self.theme)
        val_top = QHBoxLayout()
        val_top.setContentsMargins(0, 0, 0, 6)

        self.badge_score = make_badge("Skor: -", "#14B8A6")
        self.badge_target = make_badge("Hedef: -", "#0EA5E9")
        self.badge_band = make_badge("Bant: -", "#F59E0B")

        val_top.addWidget(self.badge_score)
        val_top.addWidget(self.badge_target)
        val_top.addWidget(self.badge_band)
        val_top.addStretch()
        val_card.bodyLayout().addLayout(val_top)

        self.valuation_table = self._build_table(["Metrik", "Değer"])
        val_card.bodyLayout().addWidget(self.valuation_table)

        ratios_card = Card("Kritik Rasyolar", self.theme)
        self.ratios_table = self._build_table(["Rasyo", "Değer"])
        ratios_card.bodyLayout().addWidget(self.ratios_table)

        right_col.addWidget(val_card)
        right_col.addWidget(ratios_card)

        grid.addWidget(right_col_w, 0, 1, 2, 1)

        # ALT AI
        ai_card = Card("Yapay Zeka Görüşü", self.theme)
        self.ai_comment_label = QLabel("", objectName="ai_text")
        self.ai_comment_label.setWordWrap(True)
        ai_card.bodyLayout().addWidget(self.ai_comment_label)

        grid.addWidget(ai_card, 1, 0, 1, 1)

    def _build_table(self, headers: List[str]):
        tbl = QTableWidget(0, len(headers))
        tbl.setHorizontalHeaderLabels(headers)
        tbl.verticalHeader().setVisible(False)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        tbl.horizontalHeader().setStretchLastSection(True)
        return tbl

    def _on_search(self):
        sym = self.search_input.text().strip()
        if not sym:
            QMessageBox.warning(self, "Eksik Bilgi", "Lütfen bir hisse kodu giriniz.")
            return
        self.trigger_fundamental_analysis(sym)

    def trigger_fundamental_analysis(self, symbol: str):
        clean_symbol_api = symbol.upper() + ("" if symbol.upper().endswith(".IS") else ".IS")
        display_symbol = clean_symbol_api.replace(".IS", "")

        self.title_label.setText(f"{display_symbol} — Analiz Ediliyor")
        self.subtitle_label.setText("Lütfen bekleyiniz, veri çekiliyor...")
        self.warning_frame.hide()
        self.search_input.setEnabled(False)
        QApplication.processEvents()

        try:
            payloads, errors = analyze_symbols([clean_symbol_api], save=False, sleep_sec=0.1)

            if errors and not payloads:
                raise RuntimeError(errors[0][1])
            if not payloads:
                raise ValueError("Veri boş geldi.")

            self._update_ui(payloads[0])
            self.search_input.setEnabled(True)
            self.search_input.setFocus()

        except Exception as e:
            self.search_input.setEnabled(True)
            QMessageBox.critical(self, "Analiz Başarısız", str(e))
            self.subtitle_label.setText("Hata oluştu.")

    # -----------------------------------------------------------
    #   AI YORUM METNİ
    # -----------------------------------------------------------
    def _ai_comment_text(self, p: dict, old: bool, tr_sector: str) -> str:
        if old:
            return "⚠️ <b>UYARI:</b> Bu şirkete ait finansal veriler güncel değildir (6 aydan eski)."

        score = float(p.get("score_total_0_100") or 0)
        rating = "ZAYIF"
        if score >= 80:
            rating = "MÜKEMMEL"
        elif score >= 60:
            rating = "GÜÇLÜ"
        elif score >= 45:
            rating = "ORTA"

        v = p.get("valuation", {})
        tgt = v.get("target_price")
        price = p.get("price")

        txt = f"Şirketin toplam temel analiz skoru <b>{score:.1f}</b> ve model değerlendirmesi <b>{rating}</b> seviyesindedir. "
        txt += f"Değerlendirme sektör: <b>{tr_sector}</b>. "

        if tgt and price:
            pot = ((tgt - price) / price) * 100.0
            if pot > 0:
                txt += f"Mevcut fiyat üzerinden <b>%{pot:.1f} yükseliş potansiyeli</b> bulunmaktadır."
            else:
                txt += "Hisse, belirlenen adil değerin üzerinde işlem görüyor (primli)."

        return txt
    
        # -----------------------------------------------------------
    #   ARAYÜZÜ GÜNCELLE (EKSİK OLAN FONKSİYON)
    # -----------------------------------------------------------
    def _update_ui(self, p: dict):
        raw_sec = p.get("sector", "N/A")
        price = p.get("price")
        bil = p.get("mrq_date") or "N/A"

        tr_sec = SECTOR_TRANSLATIONS.get(raw_sec, raw_sec)
        full_symbol = p.get("symbol", "")
        display_symbol = full_symbol.replace(".IS", "")

        # Eski veri kontrolü
        old_data = False
        if bil != "N/A":
            try:
                from datetime import datetime
                if (datetime.now() - datetime.strptime(bil, "%Y-%m-%d")).days > 185:
                    old_data = True
            except:
                pass

        if old_data:
            self.warning_label.setText(f"⚠️ VERİLER ESKİ! (Son Bilanço: {bil})")
            self.warning_frame.show()
            total_score = 0
        else:
            self.warning_frame.hide()
            total_score = float(p.get("score_total_0_100", 0.0))

        # Üst bar bilgisi
        right_bits = [f"<span style='color:{self.theme['muted']}'>Sektör:</span> {tr_sec}"]
        if price:
            right_bits.append(f"<span style='color:{self.theme['muted']}'>Fiyat:</span> {price:.2f} TL")
        right_bits.append(f"<span style='color:{self.theme['muted']}'>Bilanço:</span> {bil}")
        self.subtitle_label.setText("&nbsp;&nbsp; ".join(right_bits))

        # Donut grafiği
        self.donut.setData(*pie_data_from_payload(p), total_score)
        self.title_label.setText(f"{display_symbol} — Rapor")

        # Badge güncelleme
        self._set_badge_text(self.badge_score, f"Skor: {total_score:.1f}")

        v = p.get("valuation", {})
        tgt = v.get("target_price")
        band = v.get("confidence_band")

        if old_data:
            tgt = None
            band = None

        tgt_str = f"{tgt:.2f} TL" if tgt else "Hesaplanamadı"
        band_str = f"{band[0]:.2f} - {band[1]:.2f} TL" if band else "-"

        self._set_badge_text(self.badge_target, f"Hedef: {tgt_str}")
        self._set_badge_text(self.badge_band, f"Bant: {band_str}")

        # Değerleme tablosu
        dat_val = [
            ("Adil Değer (Hedef)", tgt_str),
            ("Getiri Potansiyeli", f"%{((tgt - price) / price) * 100:.1f}" if tgt and price else "-"),
            ("Kullanılan Model", v.get("method", "N/A")),
            ("Güven Aralığı", band_str),
        ]
        self._fill_table(self.valuation_table, dat_val)

        # Rasyolar
        m = p.get("metrics", {})
        raw = m.get("raw", {})
        prof = m.get("profitability", {})
        lev = m.get("leverage_liquidity", {})
        cf = m.get("cashflow_quality", {})

        def fmt(x, p=False):
            return None if x is None else (f"%{x*100:.2f}" if p else f"{x:.2f}")

        rows = [
            ("F/K", fmt(raw.get("pe"))),
            ("PD/DD", fmt(raw.get("pb"))),
            ("ROE", fmt(prof.get("roe"), True)),
            ("Net Kâr Marjı", fmt(prof.get("net_margin"), True)),
        ]

        if raw_sec not in ["BANK", "INSURANCE", "FINANCE"]:
            rows.extend([
                ("FD/FAVÖK", fmt(cf.get("ev_ebitda"))),
                ("Net Borç/FAVÖK", fmt(lev.get("net_debt_ebitda"))),
                ("Cari Oran", fmt(lev.get("current_ratio"))),
                ("SNM", fmt(cf.get("fcf_margin"), True)),
            ])
        else:
            rows.append(("ROA", fmt(prof.get("roa"), True)))

        rows = [r for r in rows if r[1] is not None]
        self._fill_table(self.ratios_table, rows)

        # AI yorum
        self.ai_comment_label.setText(
            self._ai_comment_text(p, old_data, tr_sec)
        )


    # -----------------------------------------------------------
    #   TABLO DOLDURMA
    # -----------------------------------------------------------
    def _set_badge_text(self, badge, text):
        badge.findChild(QLabel, "badge_text").setText(text)

    def _fill_table(self, tbl, rows):
        tbl.setRowCount(0)
        for r, (k, v) in enumerate(rows):
            tbl.insertRow(r)
            tbl.setItem(r, 0, QTableWidgetItem(k))
            tbl.setItem(r, 1, QTableWidgetItem(str(v)))


# -----------------------------------------------------------
#                   ANA PANEL (NEBULA UI)
# -----------------------------------------------------------
class NebulaPanel(QMainWindow):
    def __init__(self, default_symbol: str = "GARAN.IS"):
        super().__init__()
        self.theme = THEMES["Nebula Mavisi"]

        # FULLSCREEN AÇ + Windows TITLEBAR rengini koyulaştır
        self.setWindowTitle("WinningWave SenTeZ AI - Kurumsal Analiz Platformu")
        self.setGeometry(100, 100, 1300, 750)

        # Windows Titlebar koyulaştırma
        try:
            import ctypes
            hwnd = self.winId().__int__()
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd,
                DWMWA_USE_IMMERSIVE_DARK_MODE,
                ctypes.byref(ctypes.c_int(1)),
                ctypes.sizeof(ctypes.c_int(1)),
            )
        except Exception:
            pass  # Windows 10/11 çalışıyorsa sorun çıkmaz

        self._init_ui(default_symbol)

        # ARKA PLAN
        bg_path = "nebula_bg.jpg"
        if os.path.exists(bg_path):
            clean_path = bg_path.replace("\\", "/")
            bg_style = f"border-image: url({clean_path});"
        else:
            bg_style = f"background-color: {self.theme['bg_fallback']};"

        # Genel tema
        self.setStyleSheet(f"""
        QLabel#ai_text {{
            color: white;
            font-size: 13px;
        }}
                           
            QMainWindow, QWidget#central_widget {{
                {bg_style}
            }}

            QTabWidget::pane {{
                border: 0;
            }}

            QTabBar::tab {{
                background: rgba(30, 41, 59, 0.6);
                color: {self.theme['text_primary']};
                padding: 10px 20px;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                margin-right: 5px;
                font-weight: bold;
                font-size: 14px;
            }}

            QTabBar::tab:selected {{
                background: {self.theme['accent_glow']};
                color: white;
            }}

            QTableWidget {{
                background-color: transparent;
                color: {self.theme['text_primary']};
                gridline-color: {self.theme['stroke']};
            }}

            QHeaderView::section {{
                background-color: rgba(30, 41, 59, 0.8);
                color: {self.theme['text_primary']};
                border: 1px solid {self.theme['stroke']};
                padding: 6px;
                font-weight: bold;
            }}

            QLineEdit#search_input {{
                background-color: {self.theme['surface']};
                color: {self.theme['text_primary']};
                border: 1px solid {self.theme['stroke']};
                border-radius: 8px;
                padding: 8px;
            }}

            QPushButton#search_button {{
                background-color: {self.theme['accent_glow']};
                color: white;
                border: none;
                border-radius: 8px;
                padding: 8px 14px;
                font-weight: bold;
            }}
        """)

    # -----------------------------------------------------------
    #            SAYFALARIN OLUŞTURULMASI
    # -----------------------------------------------------------
    def _init_ui(self, default_symbol: str):
        central = QWidget(objectName="central_widget")
        self.setCentralWidget(central)

        main = QVBoxLayout(central)

        self.tabs = QTabWidget()
        main.addWidget(self.tabs)

        # TEKLİ ANALİZ
        self.single_analysis_page = SingleAnalysisWidget(self.theme, default_symbol)
        self.tabs.addTab(self.single_analysis_page, "TEKLİ ANALİZ")

        # TARAYICI
        self.scanner_page = ScannerWidget(self.theme)
        self.tabs.addTab(self.scanner_page, "PİYASA TARAYICISI")

        # HEDEF FİYAT RADARI
        self.radar_page = HedefFiyatRadarWidget()
        self.tabs.addTab(self.radar_page, "HEDEF FİYAT RADARI")
