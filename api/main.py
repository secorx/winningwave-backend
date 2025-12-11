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
