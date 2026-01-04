# SENTEZ_AI_TEMEL_ANALIZ_M/api/services.py

import os
import json
import threading
import time
import random
from typing import Dict, Any, List, Optional, Tuple

import requests
import yfinance as yf
import pymongo # <--- EKLENDİ: MongoDB Kütüphanesi

from pathlib import Path

# ============================================================
# MONGODB BAĞLANTISI (YENİ EKLENEN KISIM)
# ============================================================
# Eski sistem dosyaya yazıyordu, şimdi buraya yazacak.
MONGO_URI = "mongodb+srv://secorx:852456Rocco@borsaapp.dhrfqjg.mongodb.net/?retryWrites=true&w=majority&appName=BorsaApp"

# Global değişkenler (Bağlantı koparsa kod çökmesin diye None ile başlatıyoruz)
col_scanner = None
col_live = None
col_radar = None

try:
    client = pymongo.MongoClient(MONGO_URI)
    db = client["borsa_db"]
    
    # Koleksiyonlar (Tablolar)
    col_scanner = db["scanner_data"]
    col_live = db["live_prices"]
    col_radar = db["radar_cache"]
    print("✅ MongoDB Bağlantısı Başarılı (services.py)")
except Exception as e:
    print(f"❌ MongoDB Bağlantı Hatası: {e}")

# ============================================================
# PATHS (TEK KAYNAK: api/data) - HİÇBİRİ SİLİNMEDİ
# ============================================================

BASE_DIR = Path(__file__).resolve().parent              # .../api
DATA_DIR = BASE_DIR / "data"                            # .../api/data
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Temel analiz JSON'ları (eski app / temel menüler bunu kullanıyor)
PIYASA_PATH       = DATA_DIR / "piyasa_verisi.json"
LIVE_PRICES_PATH  = DATA_DIR / "live_prices.json"
RADAR_PATH        = DATA_DIR / "radar_cache.json"

# (Yeni modüller için de burada dursun; bu dosya sadece temel servisleri yönetir)
FUNDS_MASTER_PATH = DATA_DIR / "funds_master.json"
TECH_SYMBOLS_PATH = DATA_DIR / "technical_symbols.json"
TEFAS_PATH        = DATA_DIR / "tefas_dump.json"
TEFAS_EME_PATH    = DATA_DIR / "tefas_dump_EME.json"


# ============================================================
# CORE IMPORTS (DOKUNULMADI - mevcut sistemin)
# ============================================================

from temel_analiz.hesaplayicilar.puan_karti import analyze_symbols, build_payload
from temel_analiz.veri_saglayicilar.veri_saglayici import fetch_company
from temel_analiz.veri_saglayicilar.yerel_csv import load_all_symbols


# ============================================================
# SAFE JSON IO (ATOMİK YAZMA) - SİLİNMEDİ (Yedek olarak duruyor)
# ============================================================

def _atomic_write_json(path: Path, obj: Any) -> None:
    """
    Yarım/bozuk JSON oluşmasını engeller:
    tmp -> replace
    """
    try:
        tmp = Path(str(path) + ".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        # API çökmesin
        pass


def _safe_read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


# ============================================================
# PIYASA (SCANNER DATA) OKU / YAZ - GÜNCELLENDİ (MongoDB)
# ============================================================

def load_json() -> List[Dict[str, Any]]:
    # Önce MongoDB'den okumayı dene
    if col_scanner is not None:
        try:
            # _id:0 diyerek MongoDB'nin özel ID'sini siliyoruz, yoksa Flutter bozulur
            return list(col_scanner.find({}, {"_id": 0}))
        except:
            pass
    
    # MongoDB çalışmazsa eski sistemden devam et (Yedek)
    data = _safe_read_json(PIYASA_PATH, [])
    return data if isinstance(data, list) else []


def save_json(data: List[Dict[str, Any]]) -> None:
    # MongoDB'ye yaz
    if col_scanner is not None and data:
        try:
            col_scanner.delete_many({}) # Eskiyi sil
            col_scanner.insert_many(data) # Yeniyi yaz
        except Exception as e:
            print(f"MongoDB Yazma Hatası: {e}")

    # Dosyaya da yaz (Yedek olsun, garanti olsun)
    _atomic_write_json(PIYASA_PATH, data)


# ============================================================
# LIVE PRICES CACHE - GÜNCELLENDİ (MongoDB)
# ============================================================

def save_live_price_json(data: List[Dict[str, Any]]) -> None:
    # MongoDB
    if col_live is not None and data:
        try:
            col_live.delete_many({})
            col_live.insert_many(data)
        except:
            pass
            
    # Dosya (Yedek)
    _atomic_write_json(LIVE_PRICES_PATH, data)


def load_live_price_json() -> List[Dict[str, Any]]:
    # MongoDB
    if col_live is not None:
        try:
            return list(col_live.find({}, {"_id": 0}))
        except:
            pass
            
    # Dosya (Yedek)
    data = _safe_read_json(LIVE_PRICES_PATH, [])
    return data if isinstance(data, list) else []


# ============================================================
# RADAR CACHE - GÜNCELLENDİ (MongoDB)
# ============================================================

def save_radar_cache(data: List[Dict[str, Any]]) -> None:
    # MongoDB
    if col_radar is not None and data:
        try:
            col_radar.delete_many({})
            col_radar.insert_many(data)
        except:
            pass
            
    # Dosya (Yedek)
    _atomic_write_json(RADAR_PATH, data)


def load_radar_cache() -> List[Dict[str, Any]]:
    # MongoDB
    if col_radar is not None:
        try:
            return list(col_radar.find({}, {"_id": 0}))
        except:
            pass

    # Dosya (Yedek)
    data = _safe_read_json(RADAR_PATH, [])
    return data if isinstance(data, list) else []


# ============================================================
# YAHOO PRICE HELPER (Hisse + Endeks) - DOKUNULMADI
# ============================================================

def _yahoo_price(yahoo_symbol: str) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Dönüş: (last_price, prev_close, daily_pct)
    """
    try:
        ticker = yf.Ticker(yahoo_symbol)

        fi = getattr(ticker, "fast_info", None)
        last = getattr(fi, "last_price", None) if fi else None
        if last is None:
            last = getattr(fi, "last_close", None) if fi else None

        prev_close = getattr(fi, "previous_close", None) if fi else None

        # fast_info eksikse -> history fallback
        if last is None or prev_close is None:
            try:
                hist = ticker.history(period="2d")
                if not hist.empty:
                    closes = hist["Close"].tolist()
                    if len(closes) == 1:
                        last = closes[0]
                    elif len(closes) >= 2:
                        prev_close = closes[-2]
                        last = closes[-1]
            except Exception:
                pass

        if last is None:
            return None, None, None

        price = float(last)
        prev = float(prev_close) if prev_close not in (None, 0) else None

        daily = None
        if prev not in (None, 0):
            daily = (price - prev) / prev * 100.0

        return price, prev, daily
    except Exception:
        return None, None, None


# ============================================================
# TEKLİ ANALİZ - DOKUNULMADI
# ============================================================

def analyze_single(symbol: str) -> Dict[str, Any]:
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return {"status": "error", "error": "symbol boş"}

    if not symbol.endswith(".IS"):
        symbol += ".IS"

    result, errors = analyze_symbols([symbol], save=False, sleep_sec=0.05)
    if errors and not result:
        return {"status": "error", "error": errors[0][1]}

    return {"status": "success", "data": result[0]}


# ============================================================
# SCANNER (OKUMA) - DOKUNULMADI
# ============================================================

def get_scanner() -> Dict[str, Any]:
    data = load_json() # Artık önce MongoDB'ye bakar
    out: List[Dict[str, Any]] = []

    for x in data:
        if x.get("status") != "success":
            continue
        score = float(x.get("score") or 0)
        if score < 50:
            continue
        out.append(x)

    out.sort(
        key=lambda x: (x.get("date_sortable", 0), x.get("score", 0)),
        reverse=True,
    )
    return {"status": "success", "data": out}


# ============================================================
# LIVE PRICE (BIST) - DOKUNULMADI
# ============================================================

def fetch_live_price_single(symbol: str) -> Optional[Dict[str, Any]]:
    """
    1) borsa.doviz.com
    2) Yahoo
    3) piyasa_verisi.json fallback
    """
    sym = (symbol or "").upper().strip()
    if not sym:
        return None

    short = sym.replace(".IS", "")
    yahoo_symbol = short + ".IS"

    # 1) borsa.doviz.com
    try:
        r = requests.get(
            f"https://borsa.doviz.com/api/v1/stocks/{short}",
            timeout=3,
        )
        if r.ok:
            js = r.json()
            if isinstance(js, dict) and "last" in js:
                price = float(js["last"])
                prev = float(js.get("previousClose", price) or price)
                pct = (price - prev) / prev * 100 if prev else 0.0
                return {
                    "symbol": short,
                    "price": round(price, 2),
                    "prev": round(prev, 2),
                    "chgPct": round(pct, 2),
                }
    except Exception:
        pass

    # 2) Yahoo
    price, prev, daily = _yahoo_price(yahoo_symbol)
    if price is not None:
        if prev is None:
            prev = price
        if daily is None:
            daily = 0.0
        return {
            "symbol": short,
            "price": round(float(price), 2),
            "prev": round(float(prev), 2),
            "chgPct": round(float(daily), 2),
        }

    # 3) Local piyasa JSON fallback (Veritabanından bakar)
    try:
        all_data = load_json() # MongoDB
        for x in all_data:
            if (x.get("symbol", "") or "").upper() == short:
                price_f = float(x.get("price") or 0)
                if price_f > 0:
                    # Scannerdaki fiyatı fallback olarak dön
                    return {
                        "symbol": short,
                        "price": round(price_f, 2),
                        "prev": round(price_f, 2),
                        "chgPct": 0.0,
                    }
    except Exception:
        pass

    return None


def fetch_live_prices(symbols: List[str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    uniq_syms = sorted(set([s for s in symbols if s]))

    for s in uniq_syms:
        d = fetch_live_price_single(s)
        if d:
            out.append(d)
        time.sleep(random.uniform(0.08, 0.16))

    out.sort(key=lambda x: x["symbol"])
    return out


# ============================================================
# GET LIVE PRICES - AKILLI MOD (DÜZELTİLDİ)
# ============================================================

def get_live_prices(symbols: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    BURASI KRİTİK DEĞİŞİKLİK:
    1. Eğer symbols Yoksa (Tüm Liste isteniyorsa) -> MongoDB'den hazır listeyi oku (Hızlı, Veri Silmez).
    2. Eğer symbols Varsa (Özel Liste/Tekil) -> Taze çek, ama veritabanını SİLME.
    """
    
    # 1. TAM LİSTE İSTEĞİ (Telefondaki ana liste)
    if not symbols:
        # Render Free Tier 500 hisseyi anlık çekemez, zaman aşımı olur.
        # O yüzden gece 03:00'te kaydettiğimiz hazineyi (MongoDB) sunuyoruz.
        cached_data = load_live_price_json()
        return {"status": "success", "data": cached_data}
    
    # 2. ÖZEL LİSTE / REFRESH İSTEĞİ (Az sayıda hisse)
    data = fetch_live_prices(symbols)
    
    # Buradaki kritik nokta: save_live_price_json() çağırmıyoruz!
    # Çünkü çağırırsak tüm veritabanını silip sadece bu 3 hisseyi yazar.
    # Bu yüzden sadece sonucu kullanıcıya dönüyoruz.
    
    return {"status": "success", "data": data}


def get_saved_live_prices() -> Dict[str, Any]:
    return {"status": "success", "data": load_live_price_json()}


# ============================================================
# RADAR (cache + background refresh) - DOKUNULMADI
# ============================================================

RADAR_STATE: Dict[str, Any] = {
    "refresh_running": False,
    "last_refresh_ts": 0.0,
}


def _build_radar_from_local_only() -> List[Dict[str, Any]]:
    data = load_json() # MongoDB
    radar: List[Dict[str, Any]] = []

    for x in data:
        if x.get("status") != "success":
            continue

        score = float(x.get("score") or 0)
        if score < 50:
            continue

        target = x.get("target")
        if target is None:
            continue

        try:
            price_f = float(x.get("price") or 0)
            target_f = float(target)
        except Exception:
            continue

        if price_f <= 0 or target_f <= 0:
            continue

        potential = (target_f - price_f) / price_f * 100

        band_raw = x.get("band") or [0, 0]
        try:
            bmin = float(band_raw[0] or 0)
            bmax = float(band_raw[1] or 0)
        except Exception:
            bmin = 0.0
            bmax = 0.0

        radar.append({
            "symbol": x.get("symbol", ""),
            "date": x.get("date_str", ""),
            "price": round(price_f, 2),
            "target": round(target_f, 2),
            "score": score,
            "potential": round(potential, 2),
            "band": [bmin, bmax],
            "band_min": bmin,
            "band_max": bmax,
        })

    radar.sort(key=lambda x: x["potential"], reverse=True)
    return radar


def _radar_refresh_thread() -> None:
    global RADAR_STATE

    RADAR_STATE["refresh_running"] = True
    try:
        data = load_json() # MongoDB
        radar: List[Dict[str, Any]] = []

        for x in data:
            if x.get("status") != "success":
                continue

            score = float(x.get("score") or 0)
            if score < 50:
                continue

            target = x.get("target")
            if target is None:
                continue

            symbol = (x.get("symbol") or "").strip()
            if not symbol:
                continue

            # ✅ Önce disk cache'teki live_prices.json'ı kullan (herkes aynı veriyi görsün)
            cached_live_list = load_live_price_json() # MongoDB
            cached_map = {str(it.get("symbol") or "").upper(): it for it in cached_live_list if isinstance(it, dict)}

            cl = cached_map.get(symbol.upper())
            if cl and cl.get("price") is not None:
                try:
                    price_f = float(cl["price"])
                except Exception:
                    price_f = 0.0
            else:
                # Cache'te yoksa eski davranışa fallback (en güvenlisi)
                live = fetch_live_price_single(symbol)
                if live:
                    price_f = float(live["price"])
                else:
                    try:
                        price_f = float(x.get("price") or 0)
                    except Exception:
                        continue


            if price_f <= 0:
                continue

            try:
                target_f = float(target)
            except Exception:
                continue

            potential = (target_f - price_f) / price_f * 100

            band_raw = x.get("band") or [0, 0]
            try:
                bmin = float(band_raw[0] or 0)
                bmax = float(band_raw[1] or 0)
            except Exception:
                bmin = 0.0
                bmax = 0.0

            radar.append({
                "symbol": symbol,
                "date": x.get("date_str", ""),
                "price": round(price_f, 2),
                "target": round(target_f, 2),
                "score": score,
                "potential": round(potential, 2),
                "band": [bmin, bmax],
                "band_min": bmin,
                "band_max": bmax,
            })

            time.sleep(random.uniform(0.08, 0.16))

        radar.sort(key=lambda x: x["potential"], reverse=True)
        save_radar_cache(radar) # MongoDB
        RADAR_STATE["last_refresh_ts"] = time.time()

    finally:
        RADAR_STATE["refresh_running"] = False


def get_radar() -> Dict[str, Any]:
    cached = load_radar_cache() # MongoDB
    if cached:
        data = cached
    else:
        data = _build_radar_from_local_only()
        save_radar_cache(data)

    if not RADAR_STATE.get("refresh_running", False):
        th = threading.Thread(target=_radar_refresh_thread, daemon=True)
        th.start()

    return {"status": "success", "data": data}


# ============================================================
# INDEXES (XU100 / XU030) - DOKUNULMADI
# ============================================================

INDEX_CACHE: Dict[str, Dict[str, Optional[float]]] = {
    "XU100": {"value": None, "chg": None},
    "XU030": {"value": None, "chg": None},
}


def get_indexes() -> Dict[str, Any]:
    global INDEX_CACHE

    out = {
        "XU100": {"value": None, "chg": None},
        "XU030": {"value": None, "chg": None},
    }

    mapping = {
        "XU100": "XU100.IS",
        "XU030": "XU030.IS",
    }

    for key, ysym in mapping.items():
        price, prev, daily = _yahoo_price(ysym)

        if price is None:
            cached = INDEX_CACHE.get(key) or {}
            if cached.get("value") is not None:
                out[key]["value"] = cached["value"]
                out[key]["chg"] = cached.get("chg", 0.0)
            continue

        chg = daily if daily is not None else 0.0
        val = round(float(price), 2)
        chg_r = round(float(chg), 2)

        out[key]["value"] = val
        out[key]["chg"] = chg_r

        INDEX_CACHE[key]["value"] = val
        INDEX_CACHE[key]["chg"] = chg_r

    return {"status": "success", "data": out}


# ============================================================
# SCAN ENGINE (server internal) - BURASI ÖNEMLİ
# ============================================================

SCAN_STATE: Dict[str, Any] = {
    "running": False,
    "current": "",
    "completed": 0,
    "total": 0,
    "percent": 0,
    "message": "",
    "finished": False,
}


def _scan_thread() -> None:
    global SCAN_STATE

    symbols = load_all_symbols()
    total = len(symbols)

    SCAN_STATE.update({
        "running": True,
        "finished": False,
        "completed": 0,
        "total": total,
        "percent": 0,
        "message": "Tarama başladı",
    })

    old = load_json() # MongoDB
    old_map: Dict[str, Dict[str, Any]] = {
        x["symbol"]: x for x in old if isinstance(x, dict) and "symbol" in x
    }

    for i, sym in enumerate(symbols):
        if not SCAN_STATE["running"]:
            break

        SCAN_STATE["current"] = sym
        SCAN_STATE["completed"] = i + 1
        SCAN_STATE["percent"] = int((i + 1) / total * 100) if total else 0
        SCAN_STATE["message"] = f"{sym} taranıyor"

        try:
            comp = fetch_company(sym + ".IS")
            if not comp:
                continue

            payload = build_payload(comp)
            ds = payload.get("mrq_date", "2000-01-01")

            old_map[sym] = {
                "symbol": sym,
                "status": "success",
                "last_check_time": time.strftime("%Y-%m-%d"),
                "date_str": ds,
                "date_sortable": int(str(ds).replace("-", "")),
                "score": payload.get("score_total_0_100"),
                "target": (payload.get("valuation") or {}).get("target_price"),
                "price": payload.get("price"),
                "band": (payload.get("valuation") or {}).get("confidence_band"),
            }

        except Exception:
            pass

        if (i + 1) % 10 == 0:
            save_json(list(old_map.values())) # MongoDB + Dosya

    # Tarama BİTTİ. Veriyi kaydet.
    final_data = list(old_map.values())
    save_json(final_data)

    # ==============================================================
    # 🌟 GECE OTOMATİK FİYAT GÜNCELLEMESİ 🌟
    # Tarama bittiğinde (03:30 gibi) canlı fiyatları çekip KALICI olarak kaydeder.
    # ==============================================================
    try:
        SCAN_STATE["message"] = "Tarama bitti, canlı fiyatlar güncelleniyor..."
        # Taranan tüm sembolleri al
        all_symbols = list(old_map.keys())
        
        # 1. Hepsini Taze Çek
        full_live_data = fetch_live_prices(all_symbols)
        
        # 2. Veritabanına Yaz (Burada silip yazması güvenlidir çünkü liste tamdır)
        save_live_price_json(full_live_data)
        
    except Exception as e:
        print(f"Oto fiyat güncelleme hatası: {e}")

    SCAN_STATE.update({
        "running": False,
        "finished": True,
        "message": "Tarama tamamlandı",
    })


def start_scan_internal() -> Dict[str, Any]:
    """
    Sadece server içi tetikleme.
    """
    if SCAN_STATE.get("running"):
        return {"status": "success", "message": "Tarama zaten çalışıyor."}

    th = threading.Thread(target=_scan_thread, daemon=True)
    th.start()
    return {
        "status": "success",
        "message": "Otomatik günlük tarama başlatıldı. Sonuçlar tarama bitince güncellenecek.",
    }


# ============================================================
# ENDPOINT WRAPPERS
# ============================================================

def update_database() -> Dict[str, Any]:
    """
    Mobil tetiklemesin diye sadece mesaj.
    """
    return {
        "status": "success",
        "message": "Tarama yalnızca otomatik (server) çalışır. Mobil tetikleyemez.",
    }


def get_scan_status() -> Dict[str, Any]:
    return SCAN_STATE


def get_scan_result() -> Dict[str, Any]:
    return {"status": "success", "data": load_json()}
