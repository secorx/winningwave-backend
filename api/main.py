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
# TARAMA (scanner) – SADECE OKUMA
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
# TARAMA BAŞLAT (MOBİL ASLA TETİKLEYEMEZ)
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
# TARAYICI SONUCU
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
    arr = [x.strip().upper() for x in symbols.split(",") if x.strip()]
    return get_live_prices(arr)

# -----------------------------------------------------------
# SON KAYITLI CANLI FİYATLAR
# -----------------------------------------------------------
@app.get("/load_live_prices")
def api_load_live_prices():
    return get_saved_live_prices()

# -----------------------------------------------------------
# /save_live_prices – dummy endpoint
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

@app.get("/all_symbols")
def api_all_symbols():
    try:
        symbols = load_all_symbols()
        return {"status": "success", "data": symbols}
    except Exception as e:
        return {"status": "error", "error": str(e)}

@app.get("/indexes")
def api_indexes():
    return get_indexes()

# ============================================================
# GÜNLÜK OTOMATİK TARAMA SCHEDULER
#
# ✔ Günde SADECE 1 kez
# ✔ 03:00 öncelikli
# ✔ Server 03:00’te uykudaysa → ilk uyanışta 1 kez
# ✘ Aynı gün ikinci tarama YOK
# ✘ Mobil / HTTP tetiklemesi YOK
# ============================================================

import os
import json
import time
import threading
import datetime
from zoneinfo import ZoneInfo

try:
    from .services import start_scan_internal
except Exception:
    start_scan_internal = None

_SCHED_STARTED = False

STATE_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "data",
    "auto_scan_state.json",
)
os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)


def _load_state() -> dict:
    if not os.path.exists(STATE_PATH):
        return {}
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_state(st: dict) -> None:
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def auto_daily_scan_loop():
    tz = ZoneInfo("Europe/Istanbul")

    while True:
        now = datetime.datetime.now(tz)
        today = now.strftime("%Y-%m-%d")

        st = _load_state()
        last_day = st.get("last_scan_day")

        # BUGÜN ZATEN TARAMA YAPILDIYSA → ASLA TEKRARLAMA
        if last_day == today:
            time.sleep(30)
            continue

        # 03:00–03:05 ideal pencere
        in_ideal_window = (now.hour == 3 and now.minute <= 5)

        # 03:00 kaçırıldı ama server şimdi ayakta → telafi
        missed_but_awake = (now.hour > 3)

        if in_ideal_window or missed_but_awake:
            print("AUTO-SCAN: Günlük tek tarama başlatılıyor.")

            # Önce state yaz → double trigger önlenir
            st["last_scan_day"] = today
            st["last_scan_ts"] = now.isoformat()
            _save_state(st)

            try:
                if start_scan_internal is not None:
                    start_scan_internal()
                else:
                    update_database()
            except Exception as e:
                print(f"AUTO-SCAN ERROR: {e}")

        time.sleep(30)


@app.on_event("startup")
def _start_scheduler_once():
    global _SCHED_STARTED

    if os.getenv("DISABLE_AUTO_SCAN", "0") == "1":
        print("AUTO-SCAN: DISABLE_AUTO_SCAN=1 → scheduler kapalı.")
        return

    if _SCHED_STARTED:
        return

    _SCHED_STARTED = True
    t = threading.Thread(target=auto_daily_scan_loop, daemon=True)
    t.start()
    print("AUTO-SCAN: Scheduler başlatıldı (günde 1 kez, 03:00 öncelikli).")
