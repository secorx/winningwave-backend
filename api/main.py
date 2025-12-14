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
import datetime
from zoneinfo import ZoneInfo

# ============================================================
# REDIS (GÜNLÜK TEK TARAMA GARANTİSİ)
# ============================================================

import redis

REDIS_URL = os.getenv("REDIS_URL")
redis_client = None

if REDIS_URL:
    try:
        redis_client = redis.Redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_timeout=3,
            socket_connect_timeout=3,
        )
        redis_client.ping()
        print("REDIS: Connected")
    except Exception as e:
        print(f"REDIS: Connection failed -> {e}")
        redis_client = None
else:
    print("REDIS: REDIS_URL env yok!")

# ============================================================
# SCAN INTERNAL IMPORT
# ============================================================

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
# 🔒 BOOT-TIME DAILY SCAN (REDIS GUARANTEED)
# ============================================================

def _boot_time_daily_scan_with_redis() -> None:
    """
    ✅ GÜNDE SADECE 1 KEZ (REDIS GARANTİLİ)
    - Server kaç kere uyursa uyansın
    - Kaç process olursa olsun
    - Aynı gün 2. tarama ASLA olmaz
    """

    if redis_client is None:
        print("AUTO-SCAN: Redis yok → tarama iptal")
        return

    if start_scan_internal is None:
        print("AUTO-SCAN: start_scan_internal yok → tarama iptal")
        return

    tz = ZoneInfo("Europe/Istanbul")
    today = datetime.datetime.now(tz).strftime("%Y-%m-%d")

    redis_key = "ww:last_scan_day"

    try:
        last_day = redis_client.get(redis_key)

        if last_day == today:
            print(f"AUTO-SCAN: Skip ({today}) - bugün zaten taranmış")
            return

        # 🔐 ATOMIC SET (ÖNCE YAZ → SONRA TARAMA)
        redis_client.set(redis_key, today)

        print(f"AUTO-SCAN: 🔥 Günlük tarama başlatılıyor ({today})")
        start_scan_internal()

    except Exception as e:
        print(f"AUTO-SCAN ERROR: {e}")

# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
def _on_startup():
    _boot_time_daily_scan_with_redis()
