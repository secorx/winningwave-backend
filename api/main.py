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
# GÜNLÜK TEK TARAMA – TARİH BAZLI (GARANTİLİ)
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


@app.get("/__internal_daily_scan_check")
def internal_daily_scan_check():
    """
    🔒 GÜNDE SADECE 1 TARAMA

    - Saat önemli değil
    - Server her uyandığında çalışsa bile
      aynı gün ikinci tarama ASLA olmaz
    """

    tz = ZoneInfo("Europe/Istanbul")
    today = datetime.datetime.now(tz).strftime("%Y-%m-%d")

    st = _load_state()
    last_day = st.get("last_scan_day")

    if last_day == today:
        return {
            "status": "skip",
            "message": f"{today} için tarama zaten yapılmış",
        }

    # Önce state yaz (double trigger engeli)
    st["last_scan_day"] = today
    st["last_scan_ts"] = datetime.datetime.now(tz).isoformat()
    _save_state(st)

    if start_scan_internal is not None:
        start_scan_internal()
        return {
            "status": "started",
            "message": f"{today} için günlük tarama başlatıldı",
        }

    return {
        "status": "error",
        "message": "start_scan_internal bulunamadı",
    }
