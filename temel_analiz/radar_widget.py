# coding: utf-8
"""
Hedef Fiyat Radarı - TAB uyumlu Widget
Panel.py içerisine sekme olarak eklenir.
"""

from __future__ import annotations

import os
import json
import time
import random
from typing import List, Dict, Optional, Tuple

import yfinance as yf

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QPen
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QTableWidget, QTableWidgetItem,
    QStyledItemDelegate, QStyleOptionViewItem
)


# -------------------------------------------------------------
# JSON dosyası bulucu
# -------------------------------------------------------------
def find_piyasa_json() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    c1 = os.path.join(here, "..", "piyasa_verisi.json")
    c2 = os.path.join(here, "piyasa_verisi.json")

    if os.path.exists(c1):
        return c1
    if os.path.exists(c2):
        return c2
    return c1


# -------------------------------------------------------------
# Clickable Table (highlight disabled)
# -------------------------------------------------------------
class ClickableTable(QTableWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        # Seçili satır arka planını tamamen kaldır
        self.setStyleSheet("""
        QTableView::item:selected {
            background: transparent !important;
        }
        """)

    def mousePressEvent(self, event):
        index = self.indexAt(event.position().toPoint())

        if not index.isValid():
            sel = self.selectionModel()
            if sel:
                sel.clearSelection()
            self.clearFocus()
            if self.parent():
                self.parent().setFocus(Qt.FocusReason.OtherFocusReason)
            return

        super().mousePressEvent(event)
        sel = self.selectionModel()
        if sel:
            sel.clearSelection()


# -------------------------------------------------------------
# % Potansiyel için ince badge
# -------------------------------------------------------------
class BadgeDelegate(QStyledItemDelegate):
    def paint(self, painter, option: QStyleOptionViewItem, index):
        text = index.data(Qt.ItemDataRole.DisplayRole)
        border = index.data(Qt.ItemDataRole.UserRole)

        painter.save()
        painter.fillRect(option.rect, QColor("#020617"))

        if border:
            pen = QPen(QColor(border))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.drawRect(option.rect.adjusted(1, 1, -1, -1))

        painter.setPen(QColor("#E5ECF7"))
        painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()


# -------------------------------------------------------------
# Yahoo fiyat işçisi
# -------------------------------------------------------------
class PriceWorker(QThread):
    priceFetched = pyqtSignal(str, float, float)
    indexFetched = pyqtSignal(str, float, float)
    finished = pyqtSignal()

    def __init__(self, symbols: List[str], index_syms: List[str], parent=None):
        super().__init__(parent)
        self.symbols = symbols
        self.index_syms = index_syms

    def _fetch(self, ysym: str) -> Tuple[Optional[float], Optional[float]]:
        try:
            t = yf.Ticker(ysym)
            fi = getattr(t, "fast_info", None)

            last = getattr(fi, "last_price", None) if fi else None
            prev = getattr(fi, "previous_close", None) if fi else None

            if last is None or prev is None:
                hist = t.history(period="2d")
                if not hist.empty:
                    closes = hist["Close"].tolist()
                    if len(closes) == 1:
                        last = closes[0]
                    elif len(closes) >= 2:
                        prev = closes[-2]
                        last = closes[-1]

            if last is None:
                return None, None

            daily = None
            if prev:
                daily = (last - prev) / prev * 100.0

            return float(last), daily
        except:
            return None, None

    def run(self):
        # Endeksler
        for idx in self.index_syms:
            time.sleep(random.uniform(0.3, 0.45))
            p, d = self._fetch(idx)
            if p is not None:
                self.indexFetched.emit(idx, p, d or 0)

        # Hisseler
        for s in self.symbols:
            time.sleep(random.uniform(0.10, 0.20))
            p, d = self._fetch(f"{s}.IS")
            if p is not None:
                self.priceFetched.emit(s, p, d or 0)

        self.finished.emit()


# -------------------------------------------------------------
# Ana TAB İÇERİĞİ
# -------------------------------------------------------------
class HedefFiyatRadarWidget(QWidget):

    INDEXES = ["XU100.IS", "XU030.IS"]

    def __init__(self, parent=None):
        super().__init__(parent)

        self.records: List[Dict] = []
        self.worker: Optional[PriceWorker] = None

        self._init_ui()
        self._load()
        self._fill()

        self._start_refresh()

        self.timer = QTimer(self)
        self.timer.setInterval(15 * 60 * 1000)
        self.timer.timeout.connect(self._start_refresh)
        self.timer.start()

    # -----------------------------------------------------------------
    # UI
    # -----------------------------------------------------------------
    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel("Hedef Fiyat Radarı")
        title.setStyleSheet("font-size:22px; font-weight:700; color:#00E5DA;")
        layout.addWidget(title)

        # Üst bar
        h = QHBoxLayout()
        layout.addLayout(h)

        self.lbl100 = QLabel("XU100: -")
        self.lbl030 = QLabel("XU030: -")

        for lbl in [self.lbl100, self.lbl030]:
            lbl.setStyleSheet("""
            QLabel {
                font-size:13px; font-weight:600;
                color:#9CA3FF;
                padding:4px 12px;
                background-color:#0F172A;
                border-radius:6px;
            }
            """)

        h.addStretch(1)
        h.addWidget(self.lbl100)
        h.addWidget(self.lbl030)

        self.btn = QPushButton("Fiyatları Güncelle")
        self.btn.clicked.connect(self._start_refresh)
        self.btn.setStyleSheet("""
        QPushButton {
            background-color:#2563EB; color:white;
            border-radius:6px; padding:6px 14px;
        }
        QPushButton:hover { background-color:#1D4ED8; }
        """)
        h.addWidget(self.btn)

        # Ana çerçeve
        frame = QFrame()
        frame.setStyleSheet("""
        QFrame {
            background-color:#070F26;
            border-radius:12px;
            border:1px solid #1E293B;
        }
        """)
        layout.addWidget(frame, 1)

        fx = QHBoxLayout(frame)
        fx.setContentsMargins(14, 14, 14, 14)

        # SOL TABLO
        self.t_left = ClickableTable()
        self.t_left.setColumnCount(3)
        self.t_left.setHorizontalHeaderLabels(["Sembol", "Fiyat", "Günlük %"])
        self._style(self.t_left)
        fx.addWidget(self.t_left, 1)

        # SAĞ TABLO
        self.t_right = ClickableTable()
        self.t_right.setColumnCount(6)
        self.t_right.setHorizontalHeaderLabels(
            ["Sembol", "Fiyat", "Hedef Fiyat", "% Potansiyel", "Skor", "Bilanço"]
        )
        self._style(self.t_right)
        self.t_right.setItemDelegateForColumn(3, BadgeDelegate(self))
        fx.addWidget(self.t_right, 2)

    # -----------------------------------------------------------------
    def _style(self, t: QTableWidget):
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        t.setShowGrid(False)
        t.horizontalHeader().setStretchLastSection(True)

        t.setStyleSheet("""
        QTableWidget {
            background-color:#020617;
            color:#E5ECF7;
            font-size:11px;
        }
        QHeaderView::section {
            background-color:#020617;
            color:#9CA3AF;
            padding:6px;
            border:0;
            border-bottom:1px solid #1F2937;
        }
        """)

    # -----------------------------------------------------------------
    def _load(self):
        path = find_piyasa_json()
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except:
            raw = []

        recs = []
        for d in raw:
            if d.get("status") != "success":
                continue

            price = float(d.get("price") or 0)
            target = float(d.get("target") or 0)
            if price <= 0 or target <= 0:
                continue

            s = d["symbol"].upper()
            score = float(d.get("score") or 0)
            date = d.get("date_str") or d.get("last_check_time") or ""

            up = (target - price) / price * 100

            recs.append({
                "symbol": s,
                "price": price,
                "daily": 0,
                "target": target,
                "score": score,
                "date": date,
                "up": up
            })

        self.records = recs

    # -----------------------------------------------------------------
    def _fill(self):
        # Sol taraf
        left = sorted(self.records, key=lambda r: r["symbol"])
        self.t_left.setRowCount(len(left))

        for i, r in enumerate(left):
            sym = QTableWidgetItem(r["symbol"])
            px = QTableWidgetItem(f"{r['price']:.2f}")
            dy = QTableWidgetItem(f"{r['daily']:+.2f}")

            color = QColor("#9CA3AF")
            if r["daily"] > 0:
                color = QColor("#22C55E")
            elif r["daily"] < 0:
                color = QColor("#F97373")

            px.setForeground(color)
            dy.setForeground(color)

            px.setTextAlignment(Qt.AlignmentFlag.AlignRight)
            dy.setTextAlignment(Qt.AlignmentFlag.AlignRight)

            self.t_left.setItem(i, 0, sym)
            self.t_left.setItem(i, 1, px)
            self.t_left.setItem(i, 2, dy)

        # Sağ taraf
        right = sorted(self.records, key=lambda r: r["up"], reverse=True)
        self.t_right.setRowCount(len(right))

        for i, r in enumerate(right):
            up = r["up"]
            border = "#22D3EE" if up > 0 else "#F97373"

            items = [
                QTableWidgetItem(r["symbol"]),
                QTableWidgetItem(f"{r['price']:.2f}"),
                QTableWidgetItem(f"{r['target']:.2f}"),
                QTableWidgetItem(f"{up:+.1f}"),
                QTableWidgetItem(f"{r['score']:.1f}"),
                QTableWidgetItem(r["date"])
            ]

            items[1].setTextAlignment(Qt.AlignmentFlag.AlignRight)
            items[2].setTextAlignment(Qt.AlignmentFlag.AlignRight)
            items[3].setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            items[4].setTextAlignment(Qt.AlignmentFlag.AlignRight)
            items[5].setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            items[3].setData(Qt.ItemDataRole.UserRole, border)

            for c, it in enumerate(items):
                self.t_right.setItem(i, c, it)

    # -----------------------------------------------------------------
    def _start_refresh(self):
        if not self.records:
            return

        syms = [r["symbol"] for r in self.records]

        self.btn.setText("Güncelleniyor...")
        self.btn.setEnabled(False)

        self.worker = PriceWorker(syms, self.INDEXES)
        self.worker.priceFetched.connect(self._on_price)
        self.worker.indexFetched.connect(self._on_index)
        self.worker.finished.connect(self._finish)
        self.worker.start()

    # -----------------------------------------------------------------
    def _on_price(self, s: str, p: float, d: float):
        for r in self.records:
            if r["symbol"] == s:
                r["price"] = p
                r["daily"] = d
                r["up"] = (r["target"] - p) / p * 100
                break

    def _on_index(self, s: str, p: float, d: float):
        pct = f"{d:+.2f}%"
        color = "#22C55E" if d > 0 else "#F97373"
        txt = f"{p:.2f}  <span style='color:{color}'>{pct}</span>"

        if "100" in s:
            self.lbl100.setText(f"XU100: {txt}")
        else:
            self.lbl030.setText(f"XU030: {txt}")

    # -----------------------------------------------------------------
    def _finish(self):
        self._fill()
        self.btn.setText("Fiyatları Güncelle")
        self.btn.setEnabled(True)
