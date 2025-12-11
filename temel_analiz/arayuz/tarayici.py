# temel_analiz/arayuz/tarayici.py

import json
import os
import time
import shutil
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
                             QTableWidget, QTableWidgetItem, QHeaderView, 
                             QLabel, QProgressBar, QMessageBox, QAbstractItemView)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont

from temel_analiz.veri_saglayicilar.sektor_verisi import BIST_SECTOR_MAP
from temel_analiz.veri_saglayicilar.veri_saglayici import fetch_company
from temel_analiz.hesaplayicilar.puan_karti import build_payload

CACHE_FILE = "piyasa_verisi.json"
BACKUP_FILE = "piyasa_verisi.bak"

class ScannerWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(list)
    
    def __init__(self, force_refresh=False):
        super().__init__()
        self.force_refresh = force_refresh
        self.existing_data = {}

    def _load_data_safe(self):
        data = []
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except:
                if os.path.exists(BACKUP_FILE):
                    try:
                        with open(BACKUP_FILE, "r", encoding="utf-8") as f:
                            data = json.load(f)
                    except: pass
        return data

    def run(self):
        symbols = list(BIST_SECTOR_MAP.keys())
        total = len(symbols)
        
        old_list = self._load_data_safe()
        for x in old_list:
            self.existing_data[x["symbol"]] = x

        with ThreadPoolExecutor(max_workers=2) as executor:
            future_to_sym = {executor.submit(self._process_single, sym): sym for sym in symbols}
            
            processed_count = 0
            for future in as_completed(future_to_sym):
                sym = future_to_sym[future]
                processed_count += 1
                try:
                    data = future.result()
                    if data and data.get("status") != "skipped":
                        self.existing_data[sym] = data
                except Exception:
                    pass
                
                self.progress.emit(processed_count, total, sym)
                
                if processed_count % 10 == 0:
                     self._save_intermediate()

        final_display_list = [
            x for x in self.existing_data.values() 
            if x.get("status") == "success" and x.get("score", 0) >= 50
        ]
        
        # --- SABİT SIRALAMA ---
        # 1. Tarih (Yeniden Eskiye)
        # 2. Skor (Yüksekten Düşüğe)
        # 3. Sembol (Alfabetik - Eşitlik durumunda oynamasın diye)
        final_display_list.sort(key=lambda x: (x.get("date_sortABLE", 0), x.get("score", 0), x.get("symbol", "")), reverse=True)
        
        self._save_intermediate() 
        self.finished.emit(final_display_list)

    def _save_intermediate(self):
        try:
            data = list(self.existing_data.values())
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            shutil.copy2(CACHE_FILE, BACKUP_FILE)
        except: pass

    def _process_single(self, sym):
        current_time = datetime.now()
        
        if not self.force_refresh and sym in self.existing_data:
            cached = self.existing_data[sym]
            last_check_str = cached.get("last_check_time", "2000-01-01")
            status = cached.get("status", "unknown")
            
            try:
                last_check = datetime.strptime(last_check_str, "%Y-%m-%d")
                seconds_diff = (current_time - last_check).total_seconds()
                
                # HATA: 24 saat bekleme (Hızlandırma)
                if status == "failed" and seconds_diff < 86400:
                    return {"symbol": sym, "status": "skipped"} 
                
                # BAŞARILI: 60 gün bekleme
                if status == "success":
                    balance_date_str = cached.get("date_str", "1900-01-01")
                    try:
                        balance_date = datetime.strptime(balance_date_str, "%Y-%m-%d")
                        days_since_balance = (current_time - balance_date).days
                        if days_since_balance < 60:
                            return cached
                    except: pass
            except: pass

        full_sym = sym + ".IS"
        time.sleep(1.0) 
        
        try:
            c = fetch_company(full_sym)
            if c and c.periods:
                payload = build_payload(c)
                date_str = c.most_recent_quarter_date()
                try:
                    dt = datetime.strptime(date_str, "%Y-%m-%d")
                    date_sortable = int(dt.strftime("%Y%m%d"))
                except:
                    date_sortable = 19000101
                
                score = payload.get("score_total_0_100", 0)
                
                return {
                    "symbol": sym,
                    "status": "success",
                    "last_check_time": current_time.strftime("%Y-%m-%d"),
                    "date_str": date_str,
                    "date_sortABLE": date_sortable, 
                    "score": score,
                    "target": payload.get("valuation", {}).get("target_price"),
                    "band": payload.get("valuation", {}).get("confidence_band"),
                    "price": c.last_price
                }
            else:
                return {"symbol": sym, "status": "failed", "last_check_time": current_time.strftime("%Y-%m-%d")}
        except Exception:
            return {"symbol": sym, "status": "failed", "last_check_time": current_time.strftime("%Y-%m-%d")}

class ScannerWidget(QWidget):
    def __init__(self, theme):
        super().__init__()
        self.theme = theme
        self.init_ui()
        self.load_from_cache()
        
    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)
        
        title = QLabel("Yapay Zeka Destekli BIST Tarayıcısı")
        title.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {self.theme['accent_glow']}")
        layout.addWidget(title)
        
        info = QLabel("Sıralama: <b>En Yeni Bilanço</b> > <b>En Yüksek Skor</b> (50+ Puan).<br>Hatalı hisseler 24 saat boyunca tekrar taranmaz (Akıllı Hız).")
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {self.theme['text_primary']}; font-size: 14px;")
        layout.addWidget(info)
        
        btn_layout = QHBoxLayout()
        self.btn_scan = QPushButton("VERİTABANINI GÜNCELLE (AKILLI TARAMA)")
        self.btn_scan.setMinimumHeight(45)
        self.btn_scan.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_scan.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.theme['surface']};
                color: {self.theme['accent_glow']};
                border: 1px solid {self.theme['accent_glow']};
                font-weight: bold;
                font-size: 14px;
                border-radius: 8px;
            }}
            QPushButton:hover {{
                background-color: {self.theme['accent_glow']};
                color: white;
            }}
            QPushButton:disabled {{
                background-color: {self.theme['stroke']};
                color: {self.theme['muted']};
                border: 1px solid {self.theme['stroke']};
            }}
        """)
        self.btn_scan.clicked.connect(self.start_smart_scan)
        btn_layout.addWidget(self.btn_scan)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)
        
        self.progress_label = QLabel("Hazır")
        self.progress_label.setStyleSheet(f"color: {self.theme['muted']}")
        layout.addWidget(self.progress_label)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                border: 1px solid {self.theme['stroke']};
                border-radius: 5px;
                text-align: center;
                color: white;
                background-color: {self.theme['surface']};
            }}
            QProgressBar::chunk {{
                background-color: {self.theme['accent_glow']};
                width: 10px;
            }}
        """)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)
        
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Hisse", "Bilanço Tarihi", "Yapay Zeka Skoru", "Hedef Fiyat", "Güven Bandı"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        
        # --- SEÇİMİ VE MAVİ IŞIĞI TAMAMEN KAPATMA ---
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        
        self.table.setStyleSheet(f"""
            QTableWidget {{
                background-color: transparent;
                alternate-background-color: #1e293b;
                gridline-color: {self.theme['stroke']};
                color: {self.theme['text_primary']};
                font-size: 14px;
                selection-background-color: transparent;
                outline: none;
            }}
            QHeaderView::section {{
                background-color: {self.theme['surface']};
                color: {self.theme['accent_glow']};
                font-weight: bold;
                padding: 8px;
                border: 1px solid {self.theme['stroke']};
            }}
            QTableWidget::item {{
                padding: 6px;
                border: none;
            }}
            QTableWidget::item:selected {{
                background-color: transparent;
                color: {self.theme['text_primary']};
            }}
        """)
        layout.addWidget(self.table)
        
    def load_from_cache(self):
        data = []
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except:
                if os.path.exists(BACKUP_FILE):
                    try:
                        with open(BACKUP_FILE, "r", encoding="utf-8") as f:
                            data = json.load(f)
                    except: pass
        
        if data:
            filtered = [
                x for x in data 
                if x.get("status") == "success" and x.get("score", 0) >= 50
            ]
            # --- SABİT SIRALAMA (Yükleme sırasında da aynı mantık) ---
            filtered.sort(key=lambda x: (x.get("date_sortABLE", 0), x.get("score", 0), x.get("symbol", "")), reverse=True)
            self.update_table(filtered)
            self.progress_label.setText(f"Kayıtlı veriler yüklendi. ({len(filtered)} güçlü hisse)")

    def start_smart_scan(self):
        self.btn_scan.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_label.setText("Akıllı Tarama Başlatılıyor... (Hatalı hisseler ve günceller atlanacak)")
        
        self.worker = ScannerWorker(force_refresh=False)
        self.worker.progress.connect(self.update_progress)
        self.worker.finished.connect(self.scan_finished)
        self.worker.start()
        
    def update_progress(self, current, total, symbol):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        if current % 5 == 0:
            self.progress_label.setText(f"Kontrol Ediliyor: {symbol} ({current}/{total})")
        
    def scan_finished(self, results):
        self.btn_scan.setEnabled(True)
        self.progress_label.setText(f"Güncelleme Tamamlandı. Toplam {len(results)} güçlü hisse listeleniyor.")
        self.update_table(results)
        
    def update_table(self, results):
        self.table.setRowCount(len(results))
        for row, data in enumerate(results):
            sym_item = QTableWidgetItem(data['symbol'])
            sym_item.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
            sym_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 0, sym_item)
            
            date_item = QTableWidgetItem(data['date_str'])
            date_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            date_item.setForeground(QColor("#facc15"))
            date_item.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
            self.table.setItem(row, 1, date_item)
            
            score = data['score']
            score_item = QTableWidgetItem(f"{score:.1f}")
            score_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            score_item.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
            if score >= 80: score_item.setForeground(QColor("#4ade80"))
            elif score >= 50: score_item.setForeground(QColor("#fbbf24"))
            else: score_item.setForeground(QColor("#f87171"))
            self.table.setItem(row, 2, score_item)
            
            tgt = data['target']
            tgt_text = f"{tgt:.2f} TL" if tgt else "-"
            tgt_item = QTableWidgetItem(tgt_text)
            tgt_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 3, tgt_item)
            
            band = data['band']
            band_text = f"{band[0]:.2f} - {band[1]:.2f}" if band else "-"
            band_item = QTableWidgetItem(band_text)
            band_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 4, band_item)