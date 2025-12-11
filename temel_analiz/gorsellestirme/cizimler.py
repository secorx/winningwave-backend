# coding: utf-8
# ai/temel_analiz/gorsellestirme/cizimler.py

from __future__ import annotations
from typing import List
from PyQt6.QtWidgets import QWidget, QSizePolicy
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QFont
from PyQt6.QtCore import Qt, QRectF, QSize, QPointF

DONUT_COLORS = ["#10B981", "#F59E0B", "#F43F5E", "#00AEEF"]

class DonutWidget(QWidget):
    """Skor dilim yazıları KALDIRILMIŞ donut bileşeni."""
    def __init__(self, theme: dict):
        super().__init__()
        self.theme = theme
        self.labels: List[str] = []; self.values: List[float] = []; self.total_score: float = 0.0
        self.colors = [QColor(c) for c in DONUT_COLORS]
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(420); self.setMinimumWidth(820); self.setMaximumWidth(1000)

    def sizeHint(self) -> QSize: return QSize(860, 440)

    def setData(self, labels: List[str], values: List[float], total_score: float):
        self.labels = labels
        self.values = [float(v or 0.0) for v in values]
        self.total_score = float(total_score or 0.0)
        self.update()

    def paintEvent(self, event):
        import math
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if not self.values or sum(self.values) <= 0:
            p.setPen(QColor(self.theme["muted"])); p.setFont(QFont("Segoe UI", 16))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Analiz bekleniyor..."); return

        w, h = self.width(), self.height()
        margin, legend_w = 12, 220
        work_w, work_h = w - legend_w - 3*margin, h - 2*margin
        size = min(work_w, work_h)
        cx, cy = margin + size/2, margin + size/2
        outer_r = size/2
        ring_thick = outer_r * 0.38
        inner_r = outer_r - ring_thick

        total = sum(self.values)
        current_angle_deg = 90.0

        for idx, v in enumerate(self.values):
            if v <= 0: continue
            span_angle_deg = (v / total) * 360.0
            p.setPen(QPen(QColor("#0F172A"), 1.3))
            p.setBrush(QBrush(self.colors[idx]))
            rect = QRectF(cx-outer_r, cy-outer_r, outer_r*2, outer_r*2)
            p.drawPie(rect, int(current_angle_deg * 16), int(span_angle_deg * 16))
            current_angle_deg += span_angle_deg

        # İç boşluk
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(QColor(self.theme["panel_bg"])))
        p.drawEllipse(QPointF(cx, cy), inner_r, inner_r)

        # Skor metni (merkez)
        grade = "ZAYIF" if self.total_score < 40 else ("ORTA" if self.total_score < 60 else ("GÜÇLÜ" if self.total_score < 80 else "MÜKEMMEL"))
        lines = [
            (f"{self.total_score:.1f}", QFont("Segoe UI", 28, QFont.Weight.Bold), QColor("#FFFFFF")),
            ("SKOR", QFont("Segoe UI", 16, QFont.Weight.Bold), QColor("#FFFFFF")),
            (f"({grade})", QFont("Segoe UI", 12, QFont.Weight.DemiBold), QColor(self.theme["muted"])),
        ]
        total_h = 0; heights = []
        for _, font, _ in lines:
            p.setFont(font); fm = p.fontMetrics(); h_line = fm.height(); heights.append(h_line); total_h += h_line
        y = cy - total_h / 2.0
        for (text, font, color), h_line in zip(lines, heights):
            p.setFont(font); p.setPen(color)
            rect = QRectF(cx - inner_r, y, inner_r * 2, h_line)
            p.drawText(rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, text)
            y += h_line

        # Lejant
        legend_x = int(w - legend_w + margin)
        legend_y = int(margin + 6)
        p.setFont(QFont("Segoe UI", 12, QFont.Weight.DemiBold))
        p.setPen(QColor(self.theme["text_primary"]))
        p.drawText(legend_x, legend_y, "Skor Dilimleri")

        p.setFont(QFont("Segoe UI", 11)); fm = p.fontMetrics()
        total = sum(self.values); y = legend_y + fm.height() + 10
        for i, (lbl, val) in enumerate(zip(self.labels, self.values)):
            pct = (val / total * 100.0) if total > 0 else 0.0
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(self.colors[i])); p.drawRect(legend_x, int(y - 10), 12, 12)
            p.setPen(QColor(self.theme["text_primary"]))
            p.drawText(legend_x + 16, int(y), f"{lbl} — {pct:.1f}%")
            y += fm.height() + 8
