from fastapi import FastAPI, Query, HTTPException
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
    start_scan_internal,
)

from temel_analiz.veri_saglayicilar.yerel_csv import load_all_symbols

import os
import json
import time
import datetime
from zoneinfo import ZoneInfo


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
# STATE (GÜNLÜK KORUMA) - TEK DOSYA
# ============================================================

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


def _today_tr() -> str:
    tz = ZoneInfo("Europe/Istanbul")
    return datetime.datetime.now(tz).strftime("%Y-%m-%d")


def _now_iso_tr() -> str:
    tz = ZoneInfo("Europe/Istanbul")
    return datetime.datetime.now(tz).isoformat()


# ============================================================
# ROUTES (NORMAL)
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
    # Mobil/HTTP tetiklemeyi zaten services.py engelliyor (bilgi mesajı döndürür)
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
        description="Virgülle ayrılmış BIST sembolleri (GARAN,ASELS,THYAO gibi)",
    )
):
    arr = [x.strip().upper() for x in symbols.split(",") if x.strip()]
    return get_live_prices(arr)


@app.get("/load_live_prices")
def api_load_live_prices():
    return get_saved_live_prices()


@app.get("/save_live_prices")
def api_save_live_prices():
    # Flutter tarafı bunu çağırıyorsa bozulmasın diye koruyoruz
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
# 🔒 ADMIN – GÜNLÜK TEK TARAMA (GET + POST)
# ============================================================
# Kullanım:
# 1) Render env'e ADMIN_SCAN_TOKEN ekle (mesela 852456 değil, uzun bir şey yap)
# 2) Tarama başlat:
#    https://<domain>/__admin/run_daily_scan?token=YOURTOKEN
#
# Bu endpoint:
# - Aynı gün 2. kez çalışmaz (skip döner)
# - start_scan_internal() ile taramayı başlatır
# - Tarama bitene kadar request'i açık tutar (blocking wait)
#   => Render idle sleep riski minimuma iner.
# ============================================================

@app.api_route("/__admin/run_daily_scan", methods=["GET", "POST"])
def admin_run_daily_scan(
    token: str = Query(..., description="ADMIN_SCAN_TOKEN ile aynı olmalı"),
    force: int = Query(0, description="1 olursa günlük kilidi bypass eder (dikkat)"),
):
    ADMIN_TOKEN = os.getenv("ADMIN_SCAN_TOKEN")
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Yetkisiz")

    today = _today_tr()
    st = _load_state()

    if not force:
        if st.get("last_scan_day") == today:
            return {
                "status": "skip",
                "message": f"{today} için tarama zaten yapılmış.",
                "state": st,
            }

    # Önce state yaz (double-run engeli)
    st["last_scan_day"] = today
    st["last_scan_ts"] = _now_iso_tr()
    st["last_scan_by"] = "admin"
    _save_state(st)

    # Tarama başlat (services içinde thread)
    start_resp = start_scan_internal()

    # Tarama durumunu poll ederek request'i açık tut
    t0 = time.time()
    max_wait_sec = int(os.getenv("ADMIN_SCAN_MAX_WAIT_SEC", "5400"))  # default 90 dk

    while True:
        ss = get_scan_status() or {}
        # services.py formatı: SCAN_STATE dict
        running = bool(ss.get("running"))
        finished = bool(ss.get("finished"))
        percent = ss.get("percent", 0)
        msg = ss.get("message", "")

        if finished and not running:
            break

        # timeout
        if (time.time() - t0) > max_wait_sec:
            return {
                "status": "timeout",
                "message": "Tarama hâlâ sürüyor. Request timeout’a düştü ama tarama thread’i devam ediyor olabilir.",
                "scan_status": ss,
                "started": start_resp,
            }

        # 2 saniye aralıkla bekle (Render request’i canlı tutar)
        time.sleep(2)

    return {
        "status": "success",
        "message": f"{today} günlük tarama tamamlandı.",
        "duration_sec": int(time.time() - t0),
        "scan_status": get_scan_status(),
        "started": start_resp,
        "state": _load_state(),
    }


# ============================================================
# STARTUP
# ============================================================
# ÖNEMLİ: Boot-time auto scan yok!
# Server uyanınca tarama başlatmayacak.
# ============================================================

@app.on_event("startup")
def _on_startup():
    print("STARTUP: API hazır. (Auto-scan kapalı, admin kontrollü)")
