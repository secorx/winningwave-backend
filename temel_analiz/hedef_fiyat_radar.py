# coding: utf-8
"""
WinningWave SenTez AI - Hedef Fiyat Radarı (Nebula UI, Yahoo, Thread'siz)
Bu dosya panelde HedefFiyatRadarWidget olarak kullanılır.
İstersen tek başına da çalıştırabilirsin.
"""

from __future__ import annotations

import os
import json
import time
import random
from typing import List, Dict, Optional, Tuple

import yfinance as yf

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QPen, QFont, QBrush
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QTableWidget,
    QTableWidgetItem,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)

# -------------------------------------------------------------
# Sabitler
# -------------------------------------------------------------

INDEX_SYMBOLS = ["XU100.IS", "XU030.IS"]


# -------------------------------------------------------------
# Yardımcı: piyasa_verisi.json yolunu bul
# -------------------------------------------------------------

def find_piyasa_json() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    candidate1 = os.path.join(here, "piyasa_verisi.json")
    if os.path.exists(candidate1):
        return candidate1

    candidate2 = os.path.join(os.getcwd(), "piyasa_verisi.json")
    if os.path.exists(candidate2):
        return candidate2

    return candidate1


# -------------------------------------------------------------
# Seçimi boşluğa tıklayınca temizleyen tablo
# -------------------------------------------------------------

class ClickableTable(QTableWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        # Seçim arka planını iptal et
        self.setStyleSheet("""
        QTableView::item:selected {
            background: transparent !important;
        }
        """)

    def mousePressEvent(self, event):
        index = self.indexAt(event.position().toPoint())

        if not index.isValid():
            sel_model = self.selectionModel()
            if sel_model:
                sel_model.clearSelection()
            self.clearFocus()
            return

        super().mousePressEvent(event)

        sel_model = self.selectionModel()
        if sel_model:
            sel_model.clearSelection()


# -------------------------------------------------------------
# % Potansiyel sütunu için badge delegate
# -------------------------------------------------------------

class BadgeDelegate(QStyledItemDelegate):
    def paint(self, painter, option: QStyleOptionViewItem, index):
        text = index.data(Qt.ItemDataRole.DisplayRole)
        border_color = index.data(Qt.ItemDataRole.UserRole)
        fg_brush = index.data(Qt.ItemDataRole.ForegroundRole)

        painter.save()

        # Arka plan
        painter.fillRect(option.rect, QColor("#020617"))

        # İnce border
        if border_color:
            pen = QPen(QColor(border_color))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.drawRect(option.rect.adjusted(1, 1, -1, -1))

        # Metin rengi
        if isinstance(fg_brush, QBrush):
            painter.setPen(fg_brush.color())
        elif isinstance(fg_brush, QColor):
            painter.setPen(fg_brush)
        else:
            painter.setPen(QColor("#E5ECF7"))

        painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, text)

        painter.restore()


# -------------------------------------------------------------
# Ana Widget (panelin kullanacağı)
# -------------------------------------------------------------

class HedefFiyatRadarWidget(QWidget):
    """
    Panel.py içinden direkt bu sınıf kullanılıyor.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        self._records: List[Dict] = []
        self._refresh_queue: List[Tuple[str, str]] = []
        self._refresh_in_progress: bool = False

        self._init_ui()
        self._load_data()
        self._rebuild_tables()

        # İlk fiyat yenilemesi
        self.start_refresh()

        # 15 dakikada bir otomatik yenileme
        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(15 * 60 * 1000)
        self._auto_timer.timeout.connect(self.start_refresh)
        self._auto_timer.start()

    # ---------------------------------------------------------
    # UI Kurulumu
    # ---------------------------------------------------------

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # Arka plan
        self.setObjectName("radar_root")
        self.setStyleSheet("""
        QWidget#radar_root {
            background-color: #05091A;
            color: #E5E7EB;
            font-family: 'Segoe UI';
        }
        """)

        # ---------- Üst Bar ----------
        top_layout = QHBoxLayout()
        root.addLayout(top_layout)

        title_label = QLabel("Hedef Fiyat Radarı")
        title_label.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        title_label.setStyleSheet("QLabel { color:#38BDF8; }")
        top_layout.addWidget(title_label)
        top_layout.addStretch(1)

        # Endeks etiketleri
        self.lbl_xu100 = QLabel("XU100: -")
        self.lbl_xu030 = QLabel("XU030: -")

        for lbl in (self.lbl_xu100, self.lbl_xu030):
            lbl.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
            lbl.setStyleSheet("""
            QLabel {
                color:#93C5FD;
                padding:3px 8px;
                border-radius:6px;
                background-color:rgba(15,23,42,0.9);
            }
            """)

        idx_layout = QHBoxLayout()
        idx_layout.setSpacing(8)
        idx_layout.addWidget(self.lbl_xu100)
        idx_layout.addWidget(self.lbl_xu030)
        top_layout.addLayout(idx_layout)

        # Güncelle butonu
        self.btn_refresh = QPushButton("Fiyatları Güncelle")
        self.btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_refresh.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        self.btn_refresh.setStyleSheet("""
        QPushButton {
            background-color:#2563EB;
            color:#F9FAFB;
            font-size:12px;
            font-weight:600;
            border-radius:6px;
            padding:6px 12px;
            border:1px solid #1D4ED8;
        }
        QPushButton:hover {
            background-color:#1D4ED8;
        }
        QPushButton:disabled {
            background-color:#4B5563;
            color:#9CA3AF;
            border-color:#374151;
        }
        """)
        self.btn_refresh.clicked.connect(self.start_refresh)
        top_layout.addWidget(self.btn_refresh)

        # ---------- Ana Çerçeve ----------
        outer_frame = QFrame()
        outer_frame.setStyleSheet("""
        QFrame {
            background-color:#020617;
            border-radius:14px;
            border:1px solid #1F2937;
        }
        """)
        root.addWidget(outer_frame, 1)

        outer_layout = QHBoxLayout(outer_frame)
        outer_layout.setContentsMargins(14, 14, 14, 14)
        outer_layout.setSpacing(14)

        # ---------- Sol Panel: Tüm Hisseler ----------
        left_frame = QFrame()
        left_frame.setStyleSheet("""
        QFrame {
            background-color:#020617;
            border-radius:10px;
            border:1px solid #1E293B;
        }
        """)
        left_layout = QVBoxLayout(left_frame)
        left_layout.setContentsMargins(10, 10, 10, 10)
        outer_layout.addWidget(left_frame, 1)

        left_title = QLabel("Tüm Hisseler (Alfabetik)")
        left_title.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
        left_title.setStyleSheet("QLabel { color:#93C5FD; }")
        left_layout.addWidget(left_title)

        self.table_all = ClickableTable()
        self.table_all.setColumnCount(3)
        self.table_all.setHorizontalHeaderLabels(["Sembol", "Fiyat", "Günlük %"])
        self._style_table(self.table_all)
        left_layout.addWidget(self.table_all)

        # ---------- Sağ Panel: Hedef Fiyat Potansiyeli ----------
        right_frame = QFrame()
        right_frame.setStyleSheet("""
        QFrame {
            background-color:#020617;
            border-radius:10px;
            border:1px solid #1E293B;
        }
        """)
        right_layout = QVBoxLayout(right_frame)
        right_layout.setContentsMargins(10, 10, 10, 10)
        outer_layout.addWidget(right_frame, 3)

        right_title = QLabel("Hedef Fiyat Yükseliş Potansiyeli")
        right_title.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
        right_title.setStyleSheet("QLabel { color:#93C5FD; }")
        right_layout.addWidget(right_title)

        self.table_upside = ClickableTable()
        self.table_upside.setColumnCount(6)
        self.table_upside.setHorizontalHeaderLabels(
            ["Sembol", "Fiyat", "Hedef Fiyat", "% Potansiyel", "Skor", "Bilanço"]
        )
        self._style_table(self.table_upside)
        # % Potansiyel sütununa badge delegate
        self.table_upside.setItemDelegateForColumn(3, BadgeDelegate(self.table_upside))
        right_layout.addWidget(self.table_upside)

    # ---------------------------------------------------------
    # Tablo stil ayarları
    # ---------------------------------------------------------

    def _style_table(self, table: QTableWidget):
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)

        table.setStyleSheet("""
        QTableWidget {
            background-color:transparent;
            alternate-background-color:#0b1120;
            color:#E5E7EB;
            gridline-color:#111827;
            font-size:12px;
        }
        QHeaderView::section {
            background-color:#020617;
            color:#9CA3AF;
            font-size:11px;
            font-weight:600;
            padding:6px 4px;
            border:0px;
            border-bottom:1px solid #1F2937;
        }
        QTableView::item:selected {
            background-color:transparent;
        }
        """)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setDefaultSectionSize(110)

    # ---------------------------------------------------------
    # Veri yükleme (piyasa_verisi.json)
    # ---------------------------------------------------------

    def _load_data(self):
        path = find_piyasa_json()
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            raw = []

        records: List[Dict] = []
        for d in raw:
            if d.get("status") != "success":
                continue

            symbol = d.get("symbol", "").upper()
            price = float(d.get("price") or 0)
            target = float(d.get("target") or 0)
            score = float(d.get("score") or 0)
            date = d.get("date_str") or d.get("last_check_time") or ""

            if price <= 0 or target <= 0:
                continue

            rec = {
                "symbol": symbol,
                "live_price": price,
                "daily": 0.0,
                "target": target,
                "score": score,
                "date": date,
            }
            rec["upside"] = (target - price) / price * 100.0
            records.append(rec)

        self._records = records

    # ---------------------------------------------------------
    # Yardımcı format
    # ---------------------------------------------------------

    @staticmethod
    def fmt(value: float, ndigits: int = 2, sign: bool = False) -> str:
        if sign:
            return f"{value:+.{ndigits}f}"
        return f"{value:.{ndigits}f}"

    # ---------------------------------------------------------
    # Yahoo: fiyat + günlük %
    # ---------------------------------------------------------

    def _fetch_price(self, yahoo_symbol: str) -> Tuple[Optional[float], Optional[float]]:
        try:
            ticker = yf.Ticker(yahoo_symbol)

            fi = getattr(ticker, "fast_info", None)
            last = getattr(fi, "last_price", None) if fi else None
            if last is None:
                last = getattr(fi, "last_close", None) if fi else None

            prev_close = getattr(fi, "previous_close", None) if fi else None

            if last is None or prev_close is None:
                try:
                    hist = ticker.history(period="2d")
                    if not hist.empty:
                        closes = hist["Close"].tolist()
                        if len(closes) == 1:
                            last = closes[0]
                        elif len(closes) >= 2:
                            prev_close = closes[-2]
                            last = closes[-1]
                except Exception:
                    pass

            if last is None:
                return None, None

            price = float(last)
            daily = None
            if prev_close not in (None, 0):
                prev = float(prev_close)
                if prev > 0:
                    daily = (price - prev) / prev * 100.0

            return price, daily
        except Exception:
            return None, None

    # ---------------------------------------------------------
    # Tabloları yeniden kur
    # ---------------------------------------------------------

    def _rebuild_tables(self):
        # Sol tablo: alfabetik
        sorted_all = sorted(self._records, key=lambda r: r["symbol"])
        self.table_all.setRowCount(len(sorted_all))

        for row, rec in enumerate(sorted_all):
            sym_item = QTableWidgetItem(rec["symbol"])
            sym_item.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
            price_item = QTableWidgetItem(self.fmt(rec["live_price"]))
            daily_item = QTableWidgetItem(self.fmt(rec["daily"], 2, True))

            sym_item.setForeground(QColor("#E5E7EB"))

            # Günlük % renklendirme
            if rec["daily"] > 0:
                color = QColor("#22C55E")
            elif rec["daily"] < 0:
                color = QColor("#F97373")
            else:
                color = QColor("#9CA3AF")

            price_item.setForeground(color)
            daily_item.setForeground(color)

            price_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            daily_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            sym_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

            self.table_all.setItem(row, 0, sym_item)
            self.table_all.setItem(row, 1, price_item)
            self.table_all.setItem(row, 2, daily_item)

        self.table_all.resizeColumnsToContents()

        # Sağ tablo: potansiyele göre büyükten küçüğe
        sorted_up = sorted(self._records, key=lambda r: r["upside"], reverse=True)
        self.table_upside.setRowCount(len(sorted_up))

        for row, rec in enumerate(sorted_up):
            sym_item = QTableWidgetItem(rec["symbol"])
            sym_item.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
            sym_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

            price_item = QTableWidgetItem(self.fmt(rec["live_price"]))
            target_item = QTableWidgetItem(self.fmt(rec["target"]))
            upside_item = QTableWidgetItem(self.fmt(rec["upside"], 1, True))
            score_item = QTableWidgetItem(self.fmt(rec["score"], 1))
            date_item = QTableWidgetItem(rec["date"])

            # Skor renklendirme (piyasa tarayıcısı ile uyumlu)
            score_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            score_item.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
            if rec["score"] >= 80:
                score_item.setForeground(QColor("#4ade80"))
            elif rec["score"] >= 50:
                score_item.setForeground(QColor("#fbbf24"))
            else:
                score_item.setForeground(QColor("#f87171"))

            # Bilanço tarihi turuncu
            date_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            date_item.setForeground(QColor("#facc15"))
            date_item.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))

            # % Potansiyel badge rengi
            up = rec["upside"]
            if up >= 200:
                border = "#22C55E"   # canlı yeşil
            elif up >= 100:
                border = "#f97316"   # turuncu
            elif up >= 0:
                border = "#22D3EE"   # mavi
            else:
                border = "#F97373"   # kırmızı

            upside_item.setData(Qt.ItemDataRole.UserRole, border)
            upside_item.setForeground(QColor(border))

            # Hizalamalar
            for item in (price_item, target_item, upside_item):
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            self.table_upside.setItem(row, 0, sym_item)
            self.table_upside.setItem(row, 1, price_item)
            self.table_upside.setItem(row, 2, target_item)
            self.table_upside.setItem(row, 3, upside_item)
            self.table_upside.setItem(row, 4, score_item)
            self.table_upside.setItem(row, 5, date_item)

        self.table_upside.resizeColumnsToContents()

    # ---------------------------------------------------------
    # Fiyat yenileme (threadsiz, adım adım)
    # ---------------------------------------------------------

    def start_refresh(self):
        # Zaten çalışıyorsa tekrar başlatma
        if self._refresh_in_progress:
            return

        if not self._records:
            self._load_data()
            if not self._records:
                return

        # Kuyruğu hazırla: önce endeks, sonra hisseler
        self._refresh_queue = [("index", s) for s in INDEX_SYMBOLS] + [
            ("stock", r["symbol"]) for r in self._records
        ]

        self._refresh_in_progress = True
        self.btn_refresh.setEnabled(False)
        self.btn_refresh.setText("Güncelleniyor...")

        # İlk adımı başlat
        QTimer.singleShot(10, self._process_refresh_step)

    def _process_refresh_step(self):
        if not self._refresh_queue:
            # Bitti
            self._refresh_in_progress = False
            self.btn_refresh.setEnabled(True)
            self.btn_refresh.setText("Fiyatları Güncelle")
            self._rebuild_tables()
            return

        kind, sym = self._refresh_queue.pop(0)

        if kind == "index":
            price, daily = self._fetch_price(sym)
            if price is not None:
                self._update_index(sym, price, daily if daily is not None else 0.0)
        else:
            yahoo_sym = f"{sym}.IS"
            price, daily = self._fetch_price(yahoo_sym)
            if price is not None:
                self._update_stock(sym, price, daily if daily is not None else 0.0)

        # Bir sonrakini planla (çok küçük delay)
        QTimer.singleShot(50 + int(random.uniform(0, 50)), self._process_refresh_step)

    # ---------------------------------------------------------
    # Güncelleme yardımcıları
    # ---------------------------------------------------------

    def _update_stock(self, symbol: str, price: float, daily: float):
        for rec in self._records:
            if rec["symbol"] == symbol:
                rec["live_price"] = price
                rec["daily"] = daily
                if price > 0:
                    rec["upside"] = (rec["target"] - price) / price * 100.0
                break

    def _update_index(self, idx_symbol: str, price: float, daily: float):
        pct_str = self.fmt(daily, 2, True)
        if daily > 0:
            color = "#22C55E"
        elif daily < 0:
            color = "#F97373"
        else:
            color = "#9CA3AF"

        html = f"{self.fmt(price, 2)}  <span style='color:{color}'>{pct_str}%</span>"

        if idx_symbol.startswith("XU100"):
            self.lbl_xu100.setText(f"XU100: {html}")
        else:
            self.lbl_xu030.setText(f"XU030: {html}")


# -------------------------------------------------------------
# Tek başına çalıştırmak için pencere sarmalayıcı
# -------------------------------------------------------------

class HedefFiyatRadarWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("WinningWave SenTeZ AI - Hedef Fiyat Radarı")
        self.resize(1500, 850)

        self._widget = HedefFiyatRadarWidget(self)
        self.setCentralWidget(self._widget)


# -------------------------------------------------------------
# Çalıştırma
# -------------------------------------------------------------

def main():
    import sys
    app = QApplication(sys.argv)
    win = HedefFiyatRadarWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
