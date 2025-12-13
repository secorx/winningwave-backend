# api/main.py

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from .services import (
    analyze_single,
    get_scanner,
    get_radar,
    update_database,
    get_scan_status,
    get_scan_result,
    get_live_prices,
    get_saved_live_prices,
    get_indexes,
)
from temel_analiz.veri_saglayicilar.yerel_csv import load_all_symbols


app = FastAPI(
    title="WinningWave SENTEZ AI API",
    version="1.0",
)

# ----------------------------------------------------------
# CORS
# ----------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# ROUTES
# ============================================================

@app.get("/")
def home():
    return {"status": "ok", "message": "API çalışıyor"}


# -----------------------------------------------------------
# TEKLİ ANALİZ
# -----------------------------------------------------------
@app.get("/analyze")
def api_analyze(symbol: str):
    return analyze_single(symbol)


# -----------------------------------------------------------
# TARAMA (scanner)
# -----------------------------------------------------------
@app.get("/scanner")
def api_scanner():
    return get_scanner()


# -----------------------------------------------------------
# HEDEF FİYAT RADARI
# -----------------------------------------------------------
@app.get("/hedef_fiyat_radar")
@app.get("/radar")
def api_radar():
    return get_radar()


# -----------------------------------------------------------
# TARAMA BAŞLAT
# -----------------------------------------------------------
@app.get("/update_database")
@app.post("/update_database")
def api_update_database():
    return update_database()


# -----------------------------------------------------------
# TARAYICI DURUMU
# -----------------------------------------------------------
@app.get("/scan_status")
def api_scan_status():
    return get_scan_status()


# -----------------------------------------------------------
# TARAYICI SONUCU (piyasa_verisi.json içeriği)
# -----------------------------------------------------------
@app.get("/scan_result")
def api_scan_result():
    return get_scan_result()


# -----------------------------------------------------------
# CANLI FİYATLAR
# -----------------------------------------------------------
@app.get("/live_prices")
def api_live_prices(
    symbols: str = Query(
        ...,
        description="Virgülle ayrılmış BIST sembolleri (GARAN,ASELS,THYAO gibi; .IS EKLEME)."
    )
):
    # "GARAN, ASELS" → ["GARAN", "ASELS"]
    arr = [x.strip().upper() for x in symbols.split(",") if x.strip()]
    return get_live_prices(arr)


# -----------------------------------------------------------
# SON KAYITLI CANLI FİYATLAR (program açılışında)
# -----------------------------------------------------------
@app.get("/load_live_prices")
def api_load_live_prices():
    return get_saved_live_prices()


# -----------------------------------------------------------
# /save_live_prices → Flutter tarafı 404 görmesin
# (gerçek kayıt zaten get_live_prices içinde yapılıyor)
# -----------------------------------------------------------
@app.get("/save_live_prices")
def api_save_live_prices():
    return {
        "status": "success",
        "message": "Canlı fiyatlar /live_prices çağrılırken otomatik kaydediliyor.",
    }


# ============================================================
# PC İLE AYNI OLSUN DİYE EK ENDPOINTLER
# ============================================================

# -----------------------------------------------------------
# TÜM SEMBOLLER – load_all_symbols()
# -----------------------------------------------------------
@app.get("/all_symbols")
def api_all_symbols():
    try:
        symbols = load_all_symbols()
        return {"status": "success", "data": symbols}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# -----------------------------------------------------------
# ENDEKS VERİLERİ – XU100 / XU030 (PC mantığı)
# -----------------------------------------------------------
@app.get("/indexes")
def api_indexes():
    return get_indexes()

# ============================================================
# ARKA PLANDA 03:00'DA TARAMAYI OTOMATİK ÇALIŞTIRAN SCHEDULER
# ============================================================

import threading
import datetime
import time
import os
import json
from .services import update_database, SCAN_STATE

# Sunucunun bir önceki taramayı ne zaman yaptığını kalıcı tutalım
STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "auto_scan_state.json")
os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)


def _load_state() -> dict:
    if not os.path.exists(STATE_PATH):
        return {}
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(st: dict) -> None:
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(st, f)
    except Exception:
        pass


def auto_daily_scan_loop():
    while True:
        now = datetime.datetime.now()
        today = now.strftime("%Y-%m-%d")

        st = _load_state()
        last_day = st.get("last_scan_day")

        # Saat 03:00 oldu mu? (03:00–03:05 pencere güvenli)
        in_window = (now.hour == 3 and now.minute <= 5)

        # Eğer tarama kaçırıldıysa (Render uykudaydı), ilk uyanmada telafi et:
        missed = (last_day != today and now.hour > 3)

        if (in_window or missed) and last_day != today:
            print("AUTO-SCAN: Otomatik günlük tarama başlatılıyor.")
            update_database()
            st["last_scan_day"] = today
            _save_state(st)

        time.sleep(30)  # Her 30 saniyede bir kontrol


# Thread başlatılır
t = threading.Thread(target=auto_daily_scan_loop)
t.daemon = True
t.start()

