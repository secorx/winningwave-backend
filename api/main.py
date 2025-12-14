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
# STATE (GÜNLÜK KORUMA)
# ============================================================

STATE_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "data",
    "auto_scan_state.json",
)
os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)


def load_state() -> dict:
    if not os.path.exists(STATE_PATH):
        return {}
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(st: dict) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


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
    return update_database()

@app.get("/scan_status")
def api_scan_status():
    return get_scan_status()

@app.get("/scan_result")
def api_scan_result():
    return get_scan_result()

@app.get("/live_prices")
def api_live_prices(
    symbols: str = Query(..., description="GARAN,ASELS gibi")
):
    arr = [x.strip().upper() for x in symbols.split(",") if x.strip()]
    return get_live_prices(arr)

@app.get("/load_live_prices")
def api_load_live_prices():
    return get_saved_live_prices()

@app.get("/all_symbols")
def api_all_symbols():
    return {"status": "success", "data": load_all_symbols()}

@app.get("/indexes")
def api_indexes():
    return get_indexes()


# ============================================================
# 🔒 ADMIN – GÜNLÜK TEK TARAMA (MANUEL)
# ============================================================

@app.post("/__admin/run_daily_scan")
def admin_run_daily_scan(token: str):
    """
    🔐 SADECE ADMIN
    - Günde 1 defa
    - Uzun süren tarama
    - Server uyumaz
    """

    ADMIN_TOKEN = os.getenv("ADMIN_SCAN_TOKEN")
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Yetkisiz")

    tz = ZoneInfo("Europe/Istanbul")
    today = datetime.datetime.now(tz).strftime("%Y-%m-%d")

    state = load_state()
    last_day = state.get("last_scan_day")

    if last_day == today:
        return {
            "status": "skip",
            "message": f"{today} için tarama zaten yapıldı",
        }

    # 🔒 ÖNCE YAZ → SONRA ÇALIŞTIR (DOUBLE RUN YOK)
    state["last_scan_day"] = today
    state["last_scan_ts"] = datetime.datetime.now(tz).isoformat()
    save_state(state)

    # ⚠️ BLOCKING ÇAĞRI
    start_scan_internal()

    return {
        "status": "success",
        "message": f"{today} günlük tarama tamamlandı",
    }
