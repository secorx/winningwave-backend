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


app = FastAPI(title="WinningWave SENTEZ AI API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# STATE (GÜNLÜK KORUMA)
# ============================================================

STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "auto_scan_state.json")
os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)


def load_state() -> dict:
    if not os.path.exists(STATE_PATH):
        return {}
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def save_state(st: dict) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


# ============================================================
# NORMAL ROUTES
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

@app.get("/radar")
def api_radar():
    return get_radar()

@app.get("/scan_status")
def api_scan_status():
    return get_scan_status()

@app.get("/scan_result")
def api_scan_result():
    return get_scan_result()

@app.get("/all_symbols")
def api_all_symbols():
    return {"status": "success", "data": load_all_symbols()}

@app.get("/indexes")
def api_indexes():
    return get_indexes()


# ============================================================
# 🔒 ADMIN – GÜNLÜK TEK TARAMA (GET + POST)
# ============================================================

@app.api_route("/__admin/run_daily_scan", methods=["GET", "POST"])
def admin_run_daily_scan(token: str = Query(...)):
    ADMIN_TOKEN = os.getenv("ADMIN_SCAN_TOKEN")
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Yetkisiz")

    tz = ZoneInfo("Europe/Istanbul")
    today = datetime.datetime.now(tz).strftime("%Y-%m-%d")

    state = load_state()
    if state.get("last_scan_day") == today:
        return {
            "status": "skip",
            "message": f"{today} için tarama zaten yapıldı",
        }

    # önce state yaz → sonra başlat (double run yok)
    state["last_scan_day"] = today
    state["last_scan_ts"] = datetime.datetime.now(tz).isoformat()
    save_state(state)

    # BLOCKING (Render uyumaz, yarım kalmaz)
    start_scan_internal()

    return {
        "status": "success",
        "message": f"{today} günlük tarama başlatıldı",
    }
