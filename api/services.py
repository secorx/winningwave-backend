# api/services.py

import os
import json
import threading
import time
import random
from typing import Dict, Any, List, Optional, Tuple

import requests
import yfinance as yf

from temel_analiz.hesaplayicilar.puan_karti import analyze_symbols, build_payload
from temel_analiz.veri_saglayicilar.veri_saglayici import fetch_company
from temel_analiz.veri_saglayicilar.yerel_csv import load_all_symbols


# ============================================================
# JSON YOLLARI
# ============================================================

def _find_piyasa_json() -> str:
    """
    PC versiyonundaki piyasa_verisi.json ile %100 aynı dosyayı
    bulmaya çalışır.

    DİKKAT:
    Mobil projede asıl dosyamız:
      SENTEZ_AI_TEMEL_ANALIZ_M/data/piyasa_verisi.json

    Bu yüzden önce data/ altını, sonra kökü deniyoruz.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(here, ".."))

    candidates = [
        # ÖNCE data/ klasörü (PC ile birebir aynı yer)
        os.path.join(project_root, "data", "piyasa_verisi.json"),
        os.path.join(here, "data", "piyasa_verisi.json"),

        # Sonra kök (eğer sadece oraya koyduysan)
        os.path.join(project_root, "piyasa_verisi.json"),
        os.path.join(here, "piyasa_verisi.json"),
    ]

    for p in candidates:
        if os.path.exists(p):
            return os.path.abspath(p)

    # Hiçbiri yoksa data/ altında oluştur
    default_path = os.path.join(project_root, "data", "piyasa_verisi.json")
    os.makedirs(os.path.dirname(default_path), exist_ok=True)
    return os.path.abspath(default_path)


DATA_PATH = _find_piyasa_json()
DATA_DIR = os.path.dirname(DATA_PATH)

LIVE_PRICE_PATH = os.path.join(DATA_DIR, "live_prices.json")
RADAR_CACHE_PATH = os.path.join(DATA_DIR, "radar_cache.json")


# ============================================================
# ATOMİK JSON YAZ
# ============================================================

def _atomic_write_json(path: str, obj: Any) -> None:
    """
    Dosya yazımı sırasında yarım/bozuk JSON oluşmasını engeller.
    (tmp yaz -> replace)
    """
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        # Disk hatası vs. olursa API çökmesin
        pass


def _safe_read_json(path: str, default: Any) -> Any:
    try:
        if not os.path.exists(path):
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


# ============================================================
# JSON OKU / YAZ
# ============================================================

def load_json() -> List[Dict[str, Any]]:
    data = _safe_read_json(DATA_PATH, [])
    return data if isinstance(data, list) else []


def save_json(data: List[Dict[str, Any]]) -> None:
    _atomic_write_json(DATA_PATH, data)


# ============================================================
# CANLI FİYAT CACHE
# ============================================================

def save_live_price_json(data: List[Dict[str, Any]]) -> None:
    _atomic_write_json(LIVE_PRICE_PATH, data)


def load_live_price_json() -> List[Dict[str, Any]]:
    data = _safe_read_json(LIVE_PRICE_PATH, [])
    return data if isinstance(data, list) else []


# ============================================================
# HEDEF FİYAT RADAR CACHE  (PC’de yok, mobil için ek)
# ============================================================

def save_radar_cache(data: List[Dict[str, Any]]) -> None:
    _atomic_write_json(RADAR_CACHE_PATH, data)


def load_radar_cache() -> List[Dict[str, Any]]:
    data = _safe_read_json(RADAR_CACHE_PATH, [])
    return data if isinstance(data, list) else []


# ============================================================
# YAHOO FİYAT HELPER (Hisse + Endeks için ortak)
# ============================================================

def _yahoo_price(
    yahoo_symbol: str,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    PC tarafındaki Hedef Fiyat Radar widget'ında kullanılan mantığa
    uygun şekilde Yahoo'dan fiyat çeker.

    Dönüş: (last_price, prev_close, daily_pct)
    """
    try:
        ticker = yf.Ticker(yahoo_symbol)

        fi = getattr(ticker, "fast_info", None)
        last = getattr(fi, "last_price", None) if fi else None
        if last is None:
            last = getattr(fi, "last_close", None) if fi else None

        prev_close = getattr(fi, "previous_close", None) if fi else None

        # fast_info çalışmazsa / eksikse → history fallback
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
# TEKLİ ANALİZ
# ============================================================

def analyze_single(symbol: str) -> Dict[str, Any]:
    symbol = symbol.upper()
    if not symbol.endswith(".IS"):
        symbol += ".IS"

    # PC tarafında da analyze_symbols bu şekilde kullanılıyor
    result, errors = analyze_symbols([symbol], save=False, sleep_sec=0.05)
    if errors and not result:
        return {"status": "error", "error": errors[0][1]}

    return {"status": "success", "data": result[0]}


# ============================================================
# SCANNER (SADECE OKUMA)
# ============================================================

def get_scanner() -> Dict[str, Any]:
    data = load_json()
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
# CANLI FİYAT (BIST HİSSE)
# ============================================================

def fetch_live_price_single(symbol: str) -> Optional[Dict[str, Any]]:
    """
    BIST hissesi için canlı fiyat.

    1) borsa.doviz.com (PC mantığına yakın, gerçek zamanlı)
    2) Yahoo Finance (X.IS)
    3) Yerel scanner datası (piyasa_verisi.json)

    Dönüşte:
      - price her zaman float
      - prev her zaman float
      - chgPct her zaman float
    Böylece Flutter tarafında toStringAsFixed() NULL üzerinde patlamaz.
    """
    sym = symbol.upper()
    short = sym.replace(".IS", "")
    yahoo_symbol = short + ".IS"

    # 1) Borsa.com gerçek zamanlı fiyat
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

    # 2) Yahoo fallback
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

    # 3) Yerel data fallback (scanner JSON)
    try:
        all_data = load_json()
        for x in all_data:
            if x.get("symbol", "").upper() == short:
                price_f = float(x.get("price") or 0)
                if price_f <= 0:
                    break
                prev_f = price_f
                return {
                    "symbol": short,
                    "price": round(price_f, 2),
                    "prev": round(prev_f, 2),
                    "chgPct": 0.0,
                }
    except Exception:
        pass

    return None


def fetch_live_prices(symbols: List[str]) -> List[Dict[str, Any]]:
    """
    PC'deki radar mantığına benzer şekilde:
    - Sembolleri tek tek, sıralı şekilde çeker
    - Aralarda küçük sleep ile hız limiti dostu çalışır
    """
    out: List[Dict[str, Any]] = []

    uniq_syms = sorted(set(symbols))

    for s in uniq_syms:
        d = fetch_live_price_single(s)
        if d:
            out.append(d)

        time.sleep(random.uniform(0.08, 0.16))

    out.sort(key=lambda x: x["symbol"])
    return out


def get_live_prices(symbols: List[str]) -> Dict[str, Any]:
    """
    /live_prices endpoint'i → MarketPricesPage burayı kullanıyor.
    """
    data = fetch_live_prices(symbols)
    save_live_price_json(data)
    return {"status": "success", "data": data}


def get_saved_live_prices() -> Dict[str, Any]:
    """
    Daha önce kaydedilmiş canlı fiyat listesini döner.
    """
    return {"status": "success", "data": load_live_price_json()}


# ============================================================
# HEDEF FİYAT RADARI (PC tarzı + cache)
# ============================================================

RADAR_STATE: Dict[str, Any] = {
    "refresh_running": False,
    "last_refresh_ts": 0.0,
}


def _build_radar_from_local_only() -> List[Dict[str, Any]]:
    data = load_json()
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
            "symbol": x["symbol"],
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
        data = load_json()
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

            symbol = x.get("symbol", "")
            if not symbol:
                continue

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
        save_radar_cache(radar)
        RADAR_STATE["last_refresh_ts"] = time.time()

    finally:
        RADAR_STATE["refresh_running"] = False


def get_radar() -> Dict[str, Any]:
    cached = load_radar_cache()
    if cached:
        data = cached
    else:
        data = _build_radar_from_local_only()
        save_radar_cache(data)

    if not RADAR_STATE.get("refresh_running", False):
        th = threading.Thread(target=_radar_refresh_thread)
        th.daemon = True
        th.start()

    return {"status": "success", "data": data}


# ============================================================
# ENDEKS VERİLERİ (XU100 / XU030)
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
# TARAMA MOTORU
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

    old = load_json()
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
            save_json(list(old_map.values()))

    save_json(list(old_map.values()))

    SCAN_STATE.update({
        "running": False,
        "finished": True,
        "message": "Tarama tamamlandı",
    })


# ============================================================
# TARAMA BAŞLATMA (SADECE SERVER İÇİ)
# ============================================================

def start_scan_internal() -> Dict[str, Any]:
    """
    ⚠️ SADECE SERVER İÇİN:
    - main.py içindeki 03:00 scheduler bunu çağıracak.
    - Mobil / HTTP üzerinden tarama başlatma YOK.
    """
    if SCAN_STATE.get("running"):
        return {
            "status": "success",
            "message": "Tarama zaten çalışıyor.",
        }

    th = threading.Thread(target=_scan_thread)
    th.daemon = True
    th.start()

    return {
        "status": "success",
        "message": "Otomatik günlük tarama başlatıldı. Sonuçlar tarama bitince güncellenecek.",
    }


# ============================================================
# ENDPOINT SARAN FONKSİYONLAR
# ============================================================

def update_database() -> Dict[str, Any]:
    """
    ❌ MOBİL / HTTP TARAFI TARAMAYI ASLA BAŞLATMAZ.

    Bu endpoint sadece bilgi mesajı döner.
    Gerçek tarama sadece start_scan_internal() ile (server scheduler 03:00) başlar.
    """
    return {
        "status": "success",
        "message": "Tarama yalnızca her gece 03:00'te otomatik başlar. Mobil tetikleyemez.",
    }


def get_scan_status() -> Dict[str, Any]:
    return SCAN_STATE


def get_scan_result() -> Dict[str, Any]:
    return {"status": "success", "data": load_json()}
