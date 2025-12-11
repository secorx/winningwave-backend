# temel_analiz/arayuz/titlebar.py

from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QFont

class CustomTitleBar(QWidget):
    def __init__(self, parent, title="WinningWave SenTeZ AI", bg="#0B101A", fg="#E5E7EB"):
        super().__init__()
        self.parent = parent
        self.setFixedHeight(38)
        self.bg = bg
        self.fg = fg

        self.setStyleSheet(f"""
            background-color: {self.bg};
            color: {self.fg};
        """)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 12, 0)

        # Sol - Title
        self.title = QLabel(title)
        self.title.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        lay.addWidget(self.title)

        lay.addStretch()

        # Minimize
        self.btn_min = QPushButton("–")
        self.btn_min.setFixedSize(32, 24)
        self.btn_min.setStyleSheet(self._btn_style())
        self.btn_min.clicked.connect(self.parent.showMinimized)
        lay.addWidget(self.btn_min)

        # Maximize / Restore
        self.btn_max = QPushButton("□")
        self.btn_max.setFixedSize(32, 24)
        self.btn_max.setStyleSheet(self._btn_style())
        self.btn_max.clicked.connect(self._toggle_max)
        lay.addWidget(self.btn_max)

        # Close
        self.btn_close = QPushButton("✕")
        self.btn_close.setFixedSize(32, 24)
        self.btn_close.setStyleSheet(self._btn_style("red"))
        self.btn_close.clicked.connect(self.parent.close)
        lay.addWidget(self.btn_close)

        self.drag_pos = QPoint()

    def _btn_style(self, hover_color="#333"):
        return f"""
            QPushButton {{
                background-color: transparent;
                color: {self.fg};
                border: none;
            }}
            QPushButton:hover {{
                background-color: {hover_color};
            }}
        """

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_pos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event):
        if not self.drag_pos.isNull():
            delta = event.globalPosition().toPoint() - self.drag_pos
            self.parent.move(self.parent.pos() + delta)
            self.drag_pos = event.globalPosition().toPoint()

    def mouseReleaseEvent(self, event):
        self.drag_pos = QPoint()

    def _toggle_max(self):
        if self.parent.isMaximized():
            self.parent.showNormal()
        else:
            self.parent.showMaximized()
