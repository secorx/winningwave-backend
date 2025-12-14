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

import os
import json
import datetime
from zoneinfo import ZoneInfo

try:
    from .services import start_scan_internal
except Exception:
    start_scan_internal = None


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="WinningWave SENTEZ AI API",
    version="1.0",
)

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

@app.get("/analyze")
def api_analyze(symbol: str):
    return analyze_single(symbol)

@app.get("/scanner")
def api_scanner():
    return get_scanner()

@app.get("/hedef_fiyat_radar")
@app.get("/radar")
def api_radar():
    return get_radar()

@app.get("/update_database")
@app.post("/update_database")
def api_update_database():
    return update_database()

@app.get("/scan_status")
def api_scan_status():
    return get_scan_status()

@app.get("/scan_result")
def api_scan_result():
    return get_scan_result()

@app.get("/live_prices")
def api_live_prices(
    symbols: str = Query(
        ...,
        description="Virgülle ayrılmış BIST sembolleri (GARAN,ASELS,THYAO gibi)"
    )
):
    arr = [x.strip().upper() for x in symbols.split(",") if x.strip()]
    return get_live_prices(arr)

@app.get("/load_live_prices")
def api_load_live_prices():
    return get_saved_live_prices()

@app.get("/save_live_prices")
def api_save_live_prices():
    return {
        "status": "success",
        "message": "Canlı fiyatlar otomatik kaydedilir.",
    }

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
# BOOT-TIME DAILY SCAN CHECK (TEK VE GARANTİLİ)
# ============================================================

def _load_piyasa_data() -> list:
    """
    services.py içindeki piyasa_verisi.json'u okur.
    Dosya yoksa veya boşsa [] döner.
    """
    try:
        from .services import load_json
        return load_json() or []
    except Exception:
        return []


def _already_scanned_today(today: str) -> bool:
    """
    Bugün tarama yapıldı mı?
    → piyasa_verisi.json içindeki herhangi bir kayıtta
      last_check_time == today ise EVET.
    """
    data = _load_piyasa_data()
    for x in data:
        if x.get("last_check_time") == today:
            return True
    return False


def _boot_time_daily_check_and_start_if_needed() -> None:
    """
    🔒 GARANTİLİ MODEL:
    - Server her ayağa kalktığında çalışır
    - Bugün tarama varsa → ASLA tekrar başlatmaz
    - Yoksa → 1 kez başlatır
    """
    if start_scan_internal is None:
        print("AUTO-SCAN: start_scan_internal bulunamadı.")
        return

    tz = ZoneInfo("Europe/Istanbul")
    today = datetime.datetime.now(tz).strftime("%Y-%m-%d")

    if _already_scanned_today(today):
        print(f"AUTO-SCAN: Skip ({today}) – bugün tarama zaten yapılmış.")
        return

    print("AUTO-SCAN: Boot-time günlük TEK tarama başlatılıyor.")
    try:
        start_scan_internal()
    except Exception as e:
        print(f"AUTO-SCAN ERROR: {e}")


@app.on_event("startup")
def _on_startup():
    _boot_time_daily_check_and_start_if_needed()
