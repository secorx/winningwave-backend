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
# Hedef: Günde SADECE 1 tarama
# - Öncelik: 03:00 (Europe/Istanbul)
# - 03:00 kaçırıldıysa: Server 03:00 sonrası ilk açıldığında 1 kez
# - Aynı gün ikinci tarama: ASLA
# ============================================================

import os
import json
import time
import threading
import datetime
from zoneinfo import ZoneInfo

# services.py içinde BU fonksiyon olmalı (sende var):
# def start_scan_internal(): ... (thread başlatıyor)
try:
    from .services import start_scan_internal
except Exception:
    start_scan_internal = None  # çok kritik: yoksa tarama başlatamayız

_SCHED_STARTED = False
_SCHED_LOCK = threading.Lock()

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


def _today_str(tz: ZoneInfo) -> str:
    return datetime.datetime.now(tz).strftime("%Y-%m-%d")


def _build_03_target(now: datetime.datetime) -> datetime.datetime:
    # Aynı gün 03:00 hedefi
    return now.replace(hour=3, minute=0, second=0, microsecond=0)


def _seconds_until(dt: datetime.datetime, now: datetime.datetime) -> float:
    return max(0.0, (dt - now).total_seconds())


def _run_daily_scan_once(tz: ZoneInfo) -> None:
    """
    Bugün için taramayı 1 kez çalıştırır (state yazıp başlatır).
    Aynı anda iki thread tetiklemesin diye lock var.
    """
    with _SCHED_LOCK:
        now = datetime.datetime.now(tz)
        today = now.strftime("%Y-%m-%d")

        st = _load_state()
        last_day = st.get("last_scan_day")

        # Bugün zaten yapıldıysa çık
        if last_day == today:
            return

        # Önce state yaz (double trigger'ı engelle)
        st["last_scan_day"] = today
        st["last_scan_ts"] = now.isoformat()
        _save_state(st)

        print(f"AUTO-SCAN: {today} için günlük tarama başlatılıyor (now={now.isoformat()}).")

        try:
            if start_scan_internal is not None:
                start_scan_internal()
            else:
                # start_scan_internal yoksa: burada tarama başlatamayız.
                # update_database zaten mobil tetiklemeyi engelliyor ve sadece mesaj döndürüyor.
                print("AUTO-SCAN WARNING: start_scan_internal bulunamadı. Tarama başlatılamadı.")
        except Exception as e:
            print(f"AUTO-SCAN ERROR: {e}")


def auto_daily_scan_loop():
    """
    Strateji:
    - Eğer saat 03:00'tan ÖNCE ise: 03:00'ı bekle, o an taramayı başlat.
    - Eğer saat 03:00'tan SONRA ise ve bugün tarama yapılmadıysa:
        server ilk açıldığı anda (startup sonrası) hemen 1 kez tarama başlat.
    - Bugün yapıldıysa: yarın 03:00'ı bekle.
    """
    tz = ZoneInfo("Europe/Istanbul")

    while True:
        now = datetime.datetime.now(tz)
        today = now.strftime("%Y-%m-%d")

        st = _load_state()
        last_day = st.get("last_scan_day")

        # Bugün zaten tarandıysa → yarın 03:00'ı bekle
        if last_day == today:
            tomorrow = (now + datetime.timedelta(days=1)).replace(hour=3, minute=0, second=0, microsecond=0)
            sleep_s = _seconds_until(tomorrow, now)
            time.sleep(min(sleep_s, 3600))  # max 1 saatlik uyku parçaları (log/robust)
            continue

        # Bugün taranmadı:
        target_today_03 = _build_03_target(now)

        if now < target_today_03:
            # 03:00 gelmedi → 03:00'ı bekle
            sleep_s = _seconds_until(target_today_03, now)
            time.sleep(min(sleep_s, 3600))
            continue

        # now >= 03:00 ve bugün taranmadı → catch-up: ilk uyanışta 1 kez
        _run_daily_scan_once(tz)

        # Tarama başlatıldıktan sonra kısa uyku (loop gereksiz dönmesin)
        time.sleep(60)


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
    print("AUTO-SCAN: Scheduler başlatıldı (günde 1 kez, 03:00 öncelikli, kaçırılırsa ilk uyanışta).")
