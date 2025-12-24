# Düzeltilmiş funds.py kodu
# Bu kodu mevcut funds.py dosyanızın yerine koyun

from __future__ import annotations

import os
import json
import time
import threading
import math
import re
import requests
import urllib3
import yfinance as yf
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

# ✅ EKLENDİ: Premium AI araçları (summary için)
from api.premium_ai import (
    build_premium_prediction as premium_build_prediction,
    load_funds_master_map,
    read_market_snapshot,
    market_change_pct,
)

# SSL Uyarılarını Kapat
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

router = APIRouter(tags=["funds"])

# ============================================================
# 1. AYARLAR & GLOBAL HAFIZA
# ============================================================

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CACHE_DIR = os.path.join(BASE_DIR, "funds_cache")
DATA_DIR = os.path.join(BASE_DIR, "data")
FUNDS_MASTER_PATH = os.path.join(DATA_DIR, "funds_master.json")
LIVE_PRICES_PATH = os.path.join(CACHE_DIR, "live_prices.json")
PORTFOLIO_PATH = os.path.join(CACHE_DIR, "portfolio.json")
MARKET_CACHE_PATH = os.path.join(CACHE_DIR, "market_cache.json")
PREDICTION_CACHE_PATH = os.path.join(CACHE_DIR, "prediction_cache.json")

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

# RAM CACHE (TEFAS için)
_PRICE_CACHE: Dict[str, Dict] = {}
_TEFAS_LOCK = threading.Lock()

# AI TAHMİN CACHE (TEFAS'SIZ, 5 sn)
_AI_CACHE: Dict[str, Dict[str, Any]] = {}
_AI_LOCK = threading.Lock()

# 🔒 Direction Lock Cache
_AI_DIRECTION_LOCK: Dict[str, Dict[str, Any]] = {}

# ✅ EKLENDİ: funds_master map cache (type/name için)
_MASTER_MAP: Dict[str, Dict[str, Any]] = {}
_MASTER_MAP_TS: float = 0.0
_MASTER_LOCK = threading.Lock()
_MASTER_TTL_SEC = 3600  # 1 saat

# ✅ EKLENDİ: Predictions Summary cache (çok hızlı UI için)
_PRED_SUMMARY_CACHE: Dict[str, Any] = {}
_PRED_SUMMARY_TS: float = 0.0
_PRED_SUMMARY_LOCK = threading.Lock()
_PRED_SUMMARY_TTL_SEC = 15  # 15 sn cache (UI refresh için yeterli)

# ============================================================
# 2. YARDIMCI FONKSİYONLAR
# ============================================================

def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")

# ✅ YENİ: TEFAS Effective Date (18:30 öncesi dün, sonrası bugün)
def tefas_effective_date() -> str:
    now = datetime.now()
    if (now.hour > 18) or (now.hour == 18 and now.minute >= 30):
        return now.strftime("%Y-%m-%d")        # bugünün verisi
    else:
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")  # dünkü veri

# 📌 DÜZELTME 1: Unicode eksi işareti ve temizleme mantığı güncellendi
def _parse_turkish_float(text: str) -> float:
    try:
        s = str(text)
        s = s.replace("−", "-")  # 🔴 KRİTİK: unicode minus normalize
        s = re.sub(r"[^0-9,.-]", "", s)
        return float(s.replace(",", "."))
    except:
        return 0.0

def load_cache_to_memory():
    """Server açılınca diskteki veriyi RAM'e yükler"""
    global _PRICE_CACHE
    if os.path.exists(LIVE_PRICES_PATH):
        try:
            with open(LIVE_PRICES_PATH, "r", encoding="utf-8") as f:
                _PRICE_CACHE = json.load(f)
            print(f"✅ {_PRICE_CACHE.__len__()} fon hafızaya yüklendi.")
        except:
            _PRICE_CACHE = {}

def save_memory_to_disk():
    """RAM cache'i diske atomik yaz"""
    try:
        tmp = LIVE_PRICES_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_PRICE_CACHE, f, ensure_ascii=False, indent=2)
        os.replace(tmp, LIVE_PRICES_PATH)
    except:
        pass

# ✅ EKLENDİ: master map'i cacheli oku (type/name)
def _get_master_map_cached() -> Dict[str, Dict[str, Any]]:
    global _MASTER_MAP, _MASTER_MAP_TS
    ts = time.time()
    if _MASTER_MAP and (ts - _MASTER_MAP_TS) < _MASTER_TTL_SEC:
        return _MASTER_MAP

    with _MASTER_LOCK:
        ts = time.time()
        if _MASTER_MAP and (ts - _MASTER_MAP_TS) < _MASTER_TTL_SEC:
            return _MASTER_MAP
        _MASTER_MAP = load_funds_master_map(FUNDS_MASTER_PATH)
        _MASTER_MAP_TS = ts
        return _MASTER_MAP

# ============================================================
# 3. VERİ ÇEKME MOTORU (TEFAS)
# ============================================================

def _fetch_html(fund_code: str):
    url = f"https://www.tefas.gov.tr/FonAnaliz.aspx?FonKod={fund_code.upper()}"
    headers = {"User-Agent": "Mozilla/5.0", "Connection": "close"}
    try:
        r = requests.get(url, headers=headers, timeout=5, verify=False)
        if r.status_code == 200:
            html = r.text
            p_match = re.search(r"Son Fiyat.*?<span>([\d,]+)</span>", html, re.DOTALL)
            d_match = re.search(r"Günlük Getiri.*?<span>(.*?)</span>", html, re.DOTALL)
            y_match = re.search(r"Son 1 Yıl.*?<span>(.*?)</span>", html, re.DOTALL)

            price = _parse_turkish_float(p_match.group(1)) if p_match else 0.0
            daily = _parse_turkish_float(d_match.group(1)) if d_match else 0.0
            yearly = _parse_turkish_float(y_match.group(1)) if y_match else 0.0

            if price > 0:
                return {"price": price, "daily_pct": daily, "yearly_pct": yearly, "source": "HTML"}
    except:
        pass
    return None

def _fetch_api(fund_code: str):
    url = "https://www.tefas.gov.tr/api/DB/BindHistoryInfo"
    headers = {"User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest"}
    try:
        end = datetime.now()
        start = end - timedelta(days=5)
        payload = {
            "fontip": "YAT",
            "fonkod": fund_code.upper(),
            "bastarih": start.strftime("%d.%m.%Y"),
            "bittarih": end.strftime("%d.%m.%Y"),
        }
        r = requests.post(url, data=payload, headers=headers, timeout=5, verify=False)
        data = r.json().get("data", [])
        if data:
            data.sort(key=lambda x: datetime.strptime(x["TARIH"], "%d.%m.%Y"))
            last = data[-1]
            price = _parse_turkish_float(last["FIYAT"])
            if price > 0:
                return {
                    "price": price,
                    "daily_pct": None,   # 🔴 API'den günlük getiri hesaplanmaz
                    "yearly_pct": 0.0,
                    "source": "API"
                }
    except:
        pass
    return None

def fetch_fund_live(fund_code: str):
    html = _fetch_html(fund_code)
    if html:
        return html   # ✅ TEFAS sitesindeki % neyse O

    api = _fetch_api(fund_code)
    if api:
        # daily_pct API'den gelmez → dokunma (ASLA 0.0 yapma)
        return api

    return None

def calculate_ai_prediction(yearly: float, daily: float):
    # Eğer daily None gelirse (API fallback ve cache yoksa) hata almamak için 0.0 kabul et
    d_val = daily if daily is not None else 0.0
    
    direction = "NÖTR"
    confidence = 50
    if yearly > 40:
        confidence += 20
        direction = "POZİTİF"
    elif yearly < 0:
        confidence += 10
        direction = "NEGATİF"
    if d_val > 0.1:
        if direction == "POZİTİF":
            confidence += 10
        elif direction == "NÖTR":
            direction = "POZİTİF"
    elif d_val < -0.1:
        if direction == "NEGATİF":
            confidence += 10
        elif direction == "POZİTİF":
            confidence -= 15
    return direction, min(95, max(10, confidence))

def get_fund_data_safe(fund_code: str):
    """
    GÜNDE 1 KEZ TEFAS:
    - Aynı gün içinde aynı fonu tekrar çekmez.
    - RAM cache + disk persist.
    """
    fund_code = fund_code.upper()
    
    # ✅ 2️⃣ DEĞİŞİKLİK: Effective Date kullan
    effective_day = tefas_effective_date()

    cached = _PRICE_CACHE.get(fund_code)

    # 🔴 FALLBACK: Batch scrape ile gelen ama RAM'e girmemiş fonlar
    if not cached:
        # live_prices.json'dan oku
        if os.path.exists(LIVE_PRICES_PATH):
            try:
                with open(LIVE_PRICES_PATH, "r", encoding="utf-8") as f:
                    disk_cache = json.load(f)
                disk_rec = disk_cache.get(fund_code)
                if disk_rec and disk_rec.get("nav", 0) > 0:
                    # 👇 DÜZELTME 1: last_update yoksa effective_day baz al
                    if "last_update" not in disk_rec:
                        disk_rec["last_update"] = effective_day + " 18:30:00"
                    _PRICE_CACHE[fund_code] = disk_rec
                    return disk_rec
            except:
                pass

    # ✅ 3️⃣ DEĞİŞİKLİK: Cache kontrolünü effective_day'e bağla
    if cached and cached.get("last_update", "").split(" ")[0] == effective_day:
        return cached

    with _TEFAS_LOCK:
        cached = _PRICE_CACHE.get(fund_code)
        # Double check lock içinde
        if cached and cached.get("last_update", "").split(" ")[0] == effective_day:
            return cached

        # 👇 DÜZELTME 2 (GÜÇLENDİRME): Gün içinde veri çekmeyi engelle
        if datetime.now().strftime("%Y-%m-%d") != effective_day:
            # Eğer effective_day bugün değilse (yani 18:30 öncesindeyiz)
            # ve elimizde cached yoksa veya cached eski günse
            # burada TEFAS'a gitmek riskli (yanlış veri gelebilir).
            # Yine de hiç veri yoksa mecbur gideceğiz, ama cached varsa dönelim.
            if cached:
                return cached
            # Cache yoksa mecburen fetch_fund_live çağrılacak

        data = fetch_fund_live(fund_code)

        if data:
            # 🔒 GÜNLÜK GETİRİ MUTLAK KİLİT
            if cached:
                cached_day = cached.get("last_update", "").split(" ")[0]
                if cached_day == effective_day:
                    # ❗ Günlük getiri KİLİTLİ → HTML ne getirirse getirsin kullanma
                    safe_daily = cached.get("daily_return_pct", 0.0)
                else:
                    safe_daily = data["daily_pct"] if data["daily_pct"] is not None else 0.0
            else:
                safe_daily = data["daily_pct"] if data["daily_pct"] is not None else 0.0

            # ✅ FIX 2: AI prediction'a safe_daily gönder
            dir_str, conf = calculate_ai_prediction(data["yearly_pct"], safe_daily)

            # ✅ 4️⃣ & 5️⃣ DEĞİŞİKLİK: daily_return_pct ASLA None OLMAZ ve last_update sabittir
            new_data = {
                "nav": data["price"],
                "daily_return_pct": safe_daily,  # FIXED: safe_daily kullan
                "last_update": effective_day + " 18:30:00",
                "ai_prediction": {
                    "direction": dir_str,
                    "confidence": conf,
                    "score": round(data["yearly_pct"] / 12, 2),
                },
            }
            _PRICE_CACHE[fund_code] = new_data
            save_memory_to_disk()
            time.sleep(0.1)
            return new_data

    return cached if cached else {"nav": 0.0, "daily_return_pct": 0.0}

# ============================================================
# 4. MARKET DATA (BIST / USD) – 15 DK
# ============================================================

def update_market_data():
    """BIST ve USD günceller"""
    items = []
    tickers = {"USDTRY": "USDTRY=X", "BIST100": "XU100.IS", "BIST30": "XU030.IS"}
    for c, s in tickers.items():
        try:
            t = yf.Ticker(s)
            info = t.fast_info
            p = info.last_price
            prev = info.previous_close
            pct = ((p - prev) / prev) * 100 if prev else 0.0
            items.append({"code": c, "value": round(p, 4), "change_pct": round(pct, 2)})
        except:
            items.append({"code": c, "value": 0.0, "change_pct": 0.0})

    try:
        with open(MARKET_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump({"asof": now_str(), "items": items}, f, ensure_ascii=False, indent=2)
        print(f"🔄 Market Updated: {now_str()}")
    except:
        pass
    return items

def _get_market_change_pct(code: str) -> float:
    """AI tahmin için market yüzdesini okur (TEFAS değil)"""
    try:
        if os.path.exists(MARKET_CACHE_PATH):
            with open(MARKET_CACHE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            for it in data.get("items", []):
                if it.get("code") == code:
                    return float(it.get("change_pct", 0.0) or 0.0)
    except:
        pass
    return 0.0

# ============================================================
# 5. AI TAHMİN (TEFAS YOK) – 5 SN
# ============================================================

def get_ai_prediction_live(fund_code: str, daily_real: float) -> Dict[str, Any]:
    """
    🔒 Direction kilidi
    🌊 Yumuşak jitter
    🧠 Premium AI anchor
    TEFAS'a DOKUNMAZ
    """
    fund_code = fund_code.upper()
    now_ts = time.time()

    with _AI_LOCK:
        cached = _AI_CACHE.get(fund_code)
        if cached and (now_ts - cached["_ts"]) < 5:
            return cached

        # ===============================
        # MARKET VERİLERİ
        # ===============================
        bist = _get_market_change_pct("BIST100")
        usd = _get_market_change_pct("USDTRY")

        # ===============================
        # 🧠 PREMIUM AI ANCHOR (TEK SATIR MANTIĞI)
        # ===============================
        master = _get_master_map_cached()
        rec = master.get(fund_code, {})
        fund_name = rec.get("name", "")
        fund_type = rec.get("type", "")

        premium = premium_build_prediction(
            fund_code=fund_code,
            fund_name=fund_name,
            fund_type_from_master=fund_type,
            daily_real_pct=daily_real,
            bist_change_pct=bist,
            usd_change_pct=usd,
            market_asof=now_str(),
        )
        premium_base = float(premium.get("predicted_return_pct", 0.0))

        # ===============================
        # 🌊 SOFT JITTER (ÇOK KÜÇÜK)
        # ===============================
        # deterministik (random yok)
        jitter = math.sin(now_ts / 60.0) * 0.03  # max ±0.03

        # ===============================
        # GÜN İÇİ DRIFT (KAPANIŞA SIFIRLANIR)
        # ===============================
        dt = datetime.now()
        minutes = dt.hour * 60 + dt.minute
        session_pos = max(0.0, min(1.0, (minutes - 570) / (1090 - 570)))
        drift = 0.12 * (1.0 - session_pos)

        # ===============================
        # 🎯 FİNAL TAHMİN (AĞIRLIKLI)
        # ===============================
        predicted = (
            premium_base * 0.70 +
            daily_real * 0.20 +
            drift * 0.07 +
            jitter
        )
        predicted = round(predicted, 2)

        # ===============================
        # 🔒 DIRECTION LOCK
        # ===============================
        prev = _AI_DIRECTION_LOCK.get(fund_code)

        raw_direction = (
            "POZİTİF" if predicted > 0
            else "NEGATİF" if predicted < 0
            else "NÖTR"
        )

        direction = raw_direction

        if prev:
            # yön değişimi için eşik
            if raw_direction != prev["direction"]:
                # küçük değişimde yönü KORU
                if abs(predicted) < 0.25:
                    direction = prev["direction"]
                else:
                    # yön değişti ama TS güncelle
                    _AI_DIRECTION_LOCK[fund_code] = {
                        "direction": raw_direction,
                        "ts": now_ts,
                    }
            else:
                direction = prev["direction"]
        else:
            _AI_DIRECTION_LOCK[fund_code] = {
                "direction": raw_direction,
                "ts": now_ts,
            }

        confidence = int(min(95, max(10, 55 + abs(predicted) * 10)))

        out = {
            "predicted_return_pct": predicted,
            "direction": direction,
            "confidence_score": confidence,
            "asof": now_str(),
            "_ts": now_ts,
        }

        _AI_CACHE[fund_code] = out
        return out

# ============================================================
# 6. OTOMATİK ZAMANLAYICI (MARKET DATA İÇİN)
# ============================================================

def auto_market_loop():
    """Server açık olduğu sürece her 15 dakikada bir çalışır"""
    while True:
        update_market_data()
        time.sleep(900)  # 15 dakika bekle

# Server başladığında hafızayı yükle ve döngüyü başlat
load_cache_to_memory()

# Döngüyü arka planda başlat
_market_thread = threading.Thread(target=auto_market_loop, daemon=True)
_market_thread.start()

# ============================================================
# 6.5 ✅ PREMIUM AI SUMMARY (TIP ÖZET + TOP FONLAR)
# ============================================================

def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).strip().replace(",", ".").replace("%", "")
        return float(s) if s else default
    except:
        return default

def _build_predictions_summary(scope: str = "portfolio") -> Dict[str, Any]:
    """
    scope:
      - "portfolio": sadece portföydeki fonlar
      - "all": funds_master içindeki tüm fonlar (1269 fon olabilir)
    """
    # market snapshot (premium_ai yardımcıları ile)
    snap = read_market_snapshot(MARKET_CACHE_PATH)
    bist = market_change_pct(snap, "BIST100")
    usd = market_change_pct(snap, "USDTRY")
    market_asof = str(snap.get("asof") or "")

    master = _get_master_map_cached()

    # universe seçimi
    codes: List[str] = []

    if scope == "all":
        codes = list(master.keys())
    else:
        # portfolio
        if os.path.exists(PORTFOLIO_PATH):
            try:
                with open(PORTFOLIO_PATH, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                for pos in raw.get("positions", []):
                    c = str(pos.get("code") or "").upper().strip()
                    if c:
                        codes.append(c)
            except:
                codes = []
        # fallback: boşsa, yine de birkaç örnek döndürme yerine boş dönecek

    # compute predictions
    items: List[Dict[str, Any]] = []
    by_type_acc: Dict[str, Dict[str, float]] = {}  # type -> {sum, cnt}

    for code in codes:
        rec = master.get(code, {}) if isinstance(master, dict) else {}
        fund_name = str(rec.get("name") or "")
        fund_type = str(rec.get("type") or "")

        # 📌 DÜZELTME 2: RAM cache yoksa Disk cache'ten oku (persistence)
        info = _PRICE_CACHE.get(code)
        
        if not info:
            # 🔴 RAM boşsa disk cache'ten oku
            if os.path.exists(LIVE_PRICES_PATH):
                try:
                    with open(LIVE_PRICES_PATH, "r", encoding="utf-8") as f:
                        disk = json.load(f)
                    info = disk.get(code, {})
                except:
                    info = {}

        daily_real = _safe_float(info.get("daily_return_pct") if info else 0.0, 0.0)

        out = premium_build_prediction(
            fund_code=code,
            fund_name=fund_name,
            fund_type_from_master=fund_type,
            daily_real_pct=daily_real,
            bist_change_pct=float(bist or 0.0),
            usd_change_pct=float(usd or 0.0),
            market_asof=market_asof,
        )

        pred = _safe_float(out.get("predicted_return_pct"), 0.0)
        conf = int(_safe_float(out.get("confidence_score"), 50))
        direction = str(out.get("direction") or "NOTR")
        typ = str(out.get("meta", {}).get("fund_type") or fund_type or "DIGER")

        items.append({
            "code": code,
            "name": fund_name,
            "type": typ,
            "predicted_return_pct": round(pred, 2),
            "confidence_score": conf,
            "direction": direction,
        })

        acc = by_type_acc.get(typ)
        if not acc:
            by_type_acc[typ] = {"sum": pred, "cnt": 1.0}
        else:
            acc["sum"] += pred
            acc["cnt"] += 1.0

    # by_type averages
    by_type = []
    for t, acc in by_type_acc.items():
        cnt = int(acc["cnt"])
        avg = (acc["sum"] / acc["cnt"]) if acc["cnt"] else 0.0
        by_type.append({
            "type": t,
            "avg_pct": round(avg, 2),
            "count": cnt,
        })

    # sort by avg desc (kurumsal görünüm)
    by_type.sort(key=lambda x: x.get("avg_pct", 0.0), reverse=True)

    # top funds: pred desc, conf >= 65
    top_funds = [x for x in items if int(x.get("confidence_score", 0)) >= 65]
    top_funds.sort(key=lambda x: (x.get("predicted_return_pct", 0.0), x.get("confidence_score", 0)), reverse=True)
    
    # ✅ FIX 3: Fallback mekanizması (Liste asla boş dönmesin)
    if not top_funds:
        items.sort(key=lambda x: (x.get("predicted_return_pct", 0.0), x.get("confidence_score", 0)), reverse=True)
        top_funds = items[:8]
    else:
        top_funds = top_funds[:8]

    return {
        "status": "success",
        "asof": now_str(),
        "scope": scope,
        "market": {
            "asof": market_asof,
            "bist_change_pct": round(float(bist or 0.0), 2),
            "usd_change_pct": round(float(usd or 0.0), 2),
        },
        "by_type": by_type,
        "top_funds": top_funds,
        "count": len(items),
    }

# ============================================================
# 7. API ENDPOINTS
# ============================================================

@router.get("/admin/refresh")
def api_refresh():
    m = update_market_data()
    return {"status": "success", "message": "Piyasa Güncellendi.", "market": m}

@router.get("/market")
def api_market():
    data = {"items": []}
    if os.path.exists(MARKET_CACHE_PATH):
        try:
            with open(MARKET_CACHE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except:
            pass
    return {"status": "success", "data": {"market": data}}

@router.get("/predictions/summary")
def api_predictions_summary(scope: str = "portfolio"):
    """
    ✅ Yeni endpoint:
      GET /funds/predictions/summary?scope=portfolio
      GET /funds/predictions/summary?scope=all

    Döner:
      by_type: tip bazlı ortalamalar
      top_funds: güçlü fonlar listesi
    """
    global _PRED_SUMMARY_CACHE, _PRED_SUMMARY_TS
    scope = (scope or "portfolio").strip().lower()
    if scope not in ("portfolio", "all"):
        scope = "portfolio"

    # 15 sn cache
    with _PRED_SUMMARY_LOCK:
        ts = time.time()
        cached = _PRED_SUMMARY_CACHE.get(scope)
        if cached and (ts - _PRED_SUMMARY_TS) < _PRED_SUMMARY_TTL_SEC:
            return cached

    data = _build_predictions_summary(scope=scope)

    with _PRED_SUMMARY_LOCK:
        _PRED_SUMMARY_CACHE[scope] = data
        _PRED_SUMMARY_TS = time.time()

    return data

@router.get("/portfolio")
def api_portfolio():
    if os.path.exists(PORTFOLIO_PATH):
        try:
            with open(PORTFOLIO_PATH, "r", encoding="utf-8") as f:
                raw_portfolio = json.load(f)
        except:
            raw_portfolio = {"positions": []}
    else:
        raw_portfolio = {"positions": []}

    result_list = []
    for pos in raw_portfolio.get("positions", []):
        code = (pos.get("code") or "").upper().strip()
        if not code:
            continue
        qty = float(pos.get("quantity", 0) or 0)

        # TEFAŞ cacheli gerçek veri (günde 1 kere)
        info = get_fund_data_safe(code)
        daily_real = float(info.get("daily_return_pct", 0.0) or 0.0)

        # AI tahmin (sadece yön için)
        ai = get_ai_prediction_live(code, daily_real)

        # 🎯 ÇÖZÜM: Mobil app'in beklediği alanları gerçek TEFAŞ verilerine bağla
        result_list.append({
            "code": code,
            "quantity": qty,
            "nav": info.get("nav", 0.0),
            "daily_return_pct": daily_real,                    # ✅ TEFAŞ gerçek %
            
            # 🎯 ÇÖZÜM: Mobil'in predicted_return_pct alanına TEFAŞ gerçek % koy
            "predicted_return_pct": daily_real,               # ✅ GERÇEK % (TEFAŞ)
            "confidence_score": ai.get("confidence_score", 50),
            "direction": ai.get("direction", "NÖTR"),
            
            "value": qty * float(info.get("nav", 0.0) or 0.0),

            # ESKİ alanı koru (mevcut sistemle uyumlu)
            "prediction": info.get("ai_prediction", {}),
        })

    return {"status": "success", "data": result_list}

@router.post("/portfolio/set")
def api_pset(payload: Dict[str, Any]):
    """
    payload: {"positions":[{"code":"AFT","quantity":10}, ...]}
    """
    try:
        positions = payload.get("positions", [])
        with open(PORTFOLIO_PATH, "w", encoding="utf-8") as f:
            json.dump({"asof": now_str(), "positions": positions}, f, ensure_ascii=False, indent=2)
    except:
        pass
    return {"status": "success"}

@router.get("/list")
def api_list():
    if os.path.exists(FUNDS_MASTER_PATH):
        try:
            with open(FUNDS_MASTER_PATH, "r", encoding="utf-8") as f:
                master = json.load(f)
        except:
            master = []
    else:
        master = []
    return {"status": "success", "data": {"items": master}}

@router.get("/detail/{code}")
def api_detail(code: str):
    # Detayda cacheli hızlı dön (günde 1 TEFAS)
    info = get_fund_data_safe(code)
    if info.get("nav", 0) > 0:
        daily_real = float(info.get("daily_return_pct", 0.0) or 0.0)
        ai = get_ai_prediction_live(code.upper(), daily_real)
        return {
            "status": "success",
            "data": {
                **info,
                # 🎯 ÇÖZÜM: Mobil kolay kullansın diye düz alanlar
                "predicted_return_pct": daily_real,           # ✅ GERÇEK % (TEFAŞ)
                "confidence_score": ai.get("confidence_score", 50),
                "direction": ai.get("direction", "NÖTR"),
            }
        }
    return {"status": "error", "message": "Veri yok"}

from scripts.tefas_batch_scrape import run_batch_scrape

@router.get("/admin/refresh-tefas")
def admin_refresh_tefas():
    """
    TEFAS toplu batch scrape.
    Runtime API'yi etkilemez.
    """
    result = run_batch_scrape()
    return {
        "status": "success",
        "message": "TEFAS batch scrape tamamlandı",
        "result": result
    }
