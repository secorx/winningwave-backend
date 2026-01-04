from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
import os
import json
import datetime
import threading
from zoneinfo import ZoneInfo
import pymongo # <--- EKLENDİ

# ============================================================
# TEMEL ANALİZ SERVİSLERİ (DOKUNULMADI)
# ============================================================
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
    start_scan_internal,
)

from .fundamental_scan_auto import (
    maybe_start_daily_scan_after_0300,
    start_admin_scan,
    get_scanner_state,
)

from .live_prices_auto import (
    maybe_start_daily_live_prices_after_0330,
    get_live_prices_state,
)

# ============================================================
# FUNDS ROUTER
# ============================================================
from .funds_routes import router as funds_router

# ============================================================
# TECHNICAL ROUTER (NEW)
# ============================================================
from .technical_routes import router as technical_router

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
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# FUNDS ROUTER REGISTER
# ============================================================
app.include_router(
    funds_router,
    prefix="/funds",
    tags=["funds"],
)

# ============================================================
# TECHNICAL ROUTER REGISTER (NEW)
# ============================================================
app.include_router(
    technical_router,
    prefix="/technical",
    tags=["technical"],
)

# ============================================================
# STATE (GÜNLÜK TARAMA) - MONGODB GÜNCELLEMESİ YAPILDI
# ============================================================
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STATE_DIR = os.path.join(BASE_DIR, "state")
STATE_PATH = os.path.join(STATE_DIR, "scan_state.json")
os.makedirs(STATE_DIR, exist_ok=True)

# --- MONGODB BAĞLANTISI ---
MONGO_URI_STATE = "mongodb+srv://secorx:852456Rocco@borsaapp.dhrfqjg.mongodb.net/?retryWrites=true&w=majority&appName=BorsaApp"
col_state = None

try:
    client_state = pymongo.MongoClient(MONGO_URI_STATE)
    db_state = client_state["borsa_db"]
    col_state = db_state["app_state"]
    print("✅ MongoDB Bağlantısı Başarılı (main.py)")
except Exception as e:
    print(f"❌ MongoDB State Bağlantı Hatası: {e}")


def load_state() -> dict:
    # 1. Önce MongoDB'ye bak
    if col_state is not None:
        try:
            doc = col_state.find_one({"_id": "daily_scan_state"})
            if doc:
                return doc
        except:
            pass

    # 2. Yedek olarak dosyaya bak (Eski sistem)
    try:
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}
    except Exception:
        return {}


def save_state(state: dict):
    # 1. MongoDB'ye kaydet
    if col_state is not None:
        try:
            state["_id"] = "daily_scan_state"
            col_state.replace_one({"_id": "daily_scan_state"}, state, upsert=True)
        except:
            pass

    # 2. Dosyaya da kaydet (Yedek)
    try:
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_PATH)
    except Exception:
        pass

# ============================================================
# ROUTES (TEMEL ANALİZ)
# ============================================================
@app.get("/")
def root():
    return {"status": "ok", "service": "WinningWave SENTEZ AI API"}

@app.get("/analyze")
def api_analyze(symbol: str = Query(...)):
    return analyze_single(symbol)

@app.get("/scanner")
def api_scanner(readOnly: bool = Query(True)):
    """
    readOnly=True -> sadece sonuç okur, ASLA tarama başlatmaz
    readOnly=False -> (isteğe bağlı) günlük taramayı tetikler (kilitli)
    """
    if not readOnly:
        pass
    return get_scanner()

@app.get("/scan/auto-trigger")
def api_scan_auto_trigger():
    """
    Temel Analiz ekranına girildiğinde çağrılır.
    03:00 sonrası, günde 1 defa otomatik taramayı başlatır.
    """
    return maybe_start_daily_scan_after_0300(
        scan_runner=start_scan_internal
    )

@app.get("/live_prices/state")
def api_live_prices_state():
    """
    Canlı fiyat refresh state + snapshot (herkes için ortak)
    """
    return get_live_prices_state()


@app.get("/radar")
def api_radar():
    # ✅ 03:30 sonrası ilk radar girişinde canlı fiyat refresh'i arka planda başlat
    def _runner():
        # full refresh: scanner datasındaki tüm hisseler
        r = get_live_prices(None)
        # snapshot için özet dönelim
        try:
            cnt = len((r or {}).get("data") or [])
        except Exception:
            cnt = 0
        return {"status": "success", "count": cnt}

    maybe_start_daily_live_prices_after_0330(runner=_runner, mode="auto")
    return get_radar()


@app.get("/update_db")
def api_update_db():
    return update_database()

@app.get("/scan/status")
def api_scan_status():
    return get_scan_status()

@app.get("/scan/result")
def api_scan_result():
    return get_scan_result()

@app.get("/live_prices")
def api_live_prices(symbols: Optional[str] = Query(None)):
    # symbols verilirse: sadece o semboller
    if symbols:
        symbols_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
        return get_live_prices(symbols_list)
    # symbols yoksa: services.py kendi içinden "tüm hisseleri" çeker
    return get_live_prices(None)

@app.get("/live_prices/auto-trigger")
def api_live_prices_auto_trigger():
    """
    03:30 sonrası ilk çağrıda canlı fiyatları otomatik yeniler.
    Günde 1 defa çalışır, snapshot + state yazar.
    """
    def _runner():
        # Tüm hisseler için canlı fiyatları çek + diske yaz
        return get_live_prices(None)

    return maybe_start_daily_live_prices_after_0330(
        runner=_runner,
        mode="auto"
    )


@app.get("/live_prices/saved")
def api_live_prices_saved():
    return get_saved_live_prices()

@app.get("/indexes")
def api_indexes():
    return get_indexes()

# ============================================================
# ADMIN – GÜNLÜK TARAMA
# ============================================================
@app.api_route("/__admin/run_daily_scan", methods=["GET", "POST"])
def admin_run_daily_scan(token: str = Query(...)):
    
    ADMIN_TOKEN = os.getenv("ADMIN_SCAN_TOKEN")
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Yetkisiz")
    
    tz = ZoneInfo("Europe/Istanbul")
    today = datetime.datetime.now(tz).strftime("%Y-%m-%d")
    
    # ARTIK MONGODB'DEN KONTROL EDİYOR
    state = load_state()
    
    if state.get("last_scan_day") == today:
        return {"status": "skip", "message": "Bugün zaten çalıştı"}
    
    state["last_scan_day"] = today
    state["last_scan_ts"] = datetime.datetime.now(tz).isoformat()
    
    # ARTIK MONGODB'YE KAYDEDİYOR
    save_state(state)
    
    threading.Thread(
        target=start_scan_internal,
        daemon=True
    ).start()
    
    return {"status": "success", "message": "Günlük tarama başlatıldı"}

@app.post("/scan/admin-run")
def api_admin_scan_run(token: str = Query(...)):
    ADMIN_TOKEN = os.getenv("ADMIN_SCAN_TOKEN")
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Yetkisiz")

    return start_admin_scan(
        scan_runner=start_scan_internal
    )



# ============================================================
# 🔁 BACKWARD COMPATIBILITY (MOBILE SUPPORT)
# Flutter eski endpoint isimlerini kullanıyor
# ============================================================
@app.get("/scan_status")
def api_scan_status_compat():
    return get_scan_status()

@app.get("/hedef_fiyat_radar")
def api_hedef_fiyat_radar_compat():
    return get_radar()
