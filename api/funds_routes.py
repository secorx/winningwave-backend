# Fon Otomatik Güncelleme Sistemi
# Bu kodu mevcut funds.py dosyanızın yerine koyun - TAM VE EKSİKSİZ VERSİRON

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
from bs4 import BeautifulSoup  # ✅ EKLENDİ: HTML Parsing için

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None
  # ✅ EKLENDİ: Haftasonu ve saat düzeltmesi için

from fastapi import APIRouter

# ✅ EKLENDİ: Premium AI araçları (summary için)
from api.premium_ai import (
    build_premium_prediction as premium_build_prediction,
    load_funds_master_map,
    read_market_snapshot,
    market_change_pct,
)


# ============================================================
# CACHE BASE DIR (LOCAL vs RENDER SAFE)
# ============================================================

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

CACHE_ROOT = os.getenv(
    "CACHE_ROOT",
    BASE_DIR  # local default
)

CACHE_DIR = os.path.join(CACHE_ROOT, "funds_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

# ✅ DATA DIR (HER ZAMAN PROJE İÇİNDE)
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)



# SSL Uyarılarını Kapat
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

router = APIRouter(tags=["funds"])

# ============================================================
# 1. AYARLAR & GLOBAL HAFIZA (OTOMATİK ROOT TESPİTİ)
# ============================================================

def _detect_project_root() -> str:
    """
    funds.py hangi klasörde olursa olsun proje root'unu bulmaya çalışır.
    Öncelik: içinde funds_cache veya data klasörü olan üst dizin.
    """
    here = os.path.abspath(os.path.dirname(__file__))
    candidates = [
        os.path.abspath(os.path.join(here, "..")),        # 1 üst
        os.path.abspath(os.path.join(here, "..", "..")),  # 2 üst
        os.path.abspath(os.path.join(here, "..", "..", "..")),  # 3 üst
    ]
    for c in candidates:
        if os.path.isdir(os.path.join(c, "funds_cache")) or os.path.isdir(os.path.join(c, "data")):
            return c
    # fallback
    return candidates[0]

FUNDS_MASTER_PATH = os.path.join(DATA_DIR, "funds_master.json")
LIVE_PRICES_PATH = os.path.join(CACHE_DIR, "live_prices.json")
# ✅ HİSSE FİYATLARI İÇİN (AI HESAPLAMASINDA KULLANILACAK)
STOCKS_LIVE_PRICES_PATH = os.path.join(DATA_DIR, "live_prices.json") 

PORTFOLIO_PATH = os.path.join(CACHE_DIR, "portfolio.json")
MARKET_CACHE_PATH = os.path.join(CACHE_DIR, "market_cache.json")
PREDICTION_CACHE_PATH = os.path.join(CACHE_DIR, "prediction_cache.json")

# ✅ YENİ: Canlı liste dosyası
LIVE_LIST_PATH = os.path.join(CACHE_DIR, "live_list.json")

# ✅ YENİ: Portföy güncelleme durumu için dosya yolu
PORTFOLIO_UPDATE_STATE_PATH = os.path.join(CACHE_DIR, "portfolio_update_state.json")
# ✅ YENİ: Canlı liste güncelleme durumu için dosya yolu
LIVE_LIST_UPDATE_STATE_PATH = os.path.join(CACHE_DIR, "live_list_update_state.json")

# ✅ YENİ: Fetch Tracking Path (Tekrar çekimi önlemek için - Artık logic içinde kullanılmıyor ama dosya tanımı kalsın)
FETCH_TRACKING_PATH = os.path.join(CACHE_DIR, "fetch_tracking.json")

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
# ✅ PATCH 3.1 & 3.2: Timestamp artık dict (scope bazlı)
_PRED_SUMMARY_TS: Dict[str, float] = {}
_PRED_SUMMARY_LOCK = threading.Lock()
_PRED_SUMMARY_TTL_SEC = 15  # 15 sn cache (UI refresh için yeterli)

# ================================
# 🔒 Background jobs start guard (uvicorn --reload safe)
# ================================
# ✅ PATCH 0.1: Tek seferlik başlatma kilidi
_BG_STARTED = False
_BG_LOCK = threading.Lock()

# ================================
# GÜNLİK PORTFÖY & CANLI LİSTE UPDATE KİLİDİ
# ================================
# Not: Artık global değişken yerine diskten okuyoruz, sadece Lock kaldı.
_PORTFOLIO_UPDATE_LOCK = threading.Lock()
_LIVE_LIST_UPDATE_LOCK = threading.Lock()

# ============================================================
# 2. YARDIMCI FONKSİYONLAR
# ============================================================

# ✅ GÜNCELLENDİ: now_str() Istanbul saatine göre
def now_str() -> str:
    try:
        if ZoneInfo:
            return datetime.now(ZoneInfo("Europe/Istanbul")).strftime("%Y-%m-%d %H:%M:%S")
    except:
        pass
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ✅ GÜNCELLENDİ: today_str() Istanbul saatine göre
def today_str() -> str:
    try:
        if ZoneInfo:
            return datetime.now(ZoneInfo("Europe/Istanbul")).strftime("%Y-%m-%d")
    except:
        pass
    return datetime.now().strftime("%Y-%m-%d")

# ✅ YARDIMCI: Önceki iş gününü bul
def _prev_business_day(d):
    d = d - timedelta(days=1)
    while d.weekday() >= 5:  # 5=Sat, 6=Sun
        d = d - timedelta(days=1)
    return d

# ✅ DÜZELTİLDİ: TEFAS Effective Date (Haftasonu + 09:30 Kuralı)
def tefas_effective_date() -> str:
    try:
        now = datetime.now(ZoneInfo("Europe/Istanbul"))
    except:
        now = datetime.now()

    today = now.date()
    after_0930 = (now.hour > 9) or (now.hour == 9 and now.minute >= 30)

    if today.weekday() >= 5:
        # Hafta sonu: TEFAS hâlâ Perşembe'yi verir (Cuma verisi “yayınlanmış” sayılmaz)
        d = _prev_business_day(_prev_business_day(today))
    else:
        if after_0930:
            # 09:30 sonrası: dünün iş günü
            d = _prev_business_day(today)
        else:
            # 09:30 öncesi: iki önceki iş günü
            d = _prev_business_day(_prev_business_day(today))

    return d.strftime("%Y-%m-%d")

# ✅ YENİ: Portföy güncelleme durumunu diskten oku (Optional ile uyumlu)
def _load_portfolio_update_day() -> Optional[str]:
    if os.path.exists(PORTFOLIO_UPDATE_STATE_PATH):
        try:
            with open(PORTFOLIO_UPDATE_STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("last_day")
        except:
            pass
    return None

# ✅ YENİ: Portföy güncelleme durumunu diske yaz
def _save_portfolio_update_day(day: str):
    try:
        with open(PORTFOLIO_UPDATE_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump({"last_day": day}, f, ensure_ascii=False)
    except:
        pass

# ✅ YENİ: Canlı liste güncelleme durumunu diskten oku (Optional ile uyumlu)
def _load_live_list_update_day() -> Optional[str]:
    if os.path.exists(LIVE_LIST_UPDATE_STATE_PATH):
        try:
            with open(LIVE_LIST_UPDATE_STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("last_day")
        except:
            pass
    return None

# ✅ YENİ: Canlı liste güncelleme durumunu diske yaz
def _save_live_list_update_day(day: str):
    try:
        with open(LIVE_LIST_UPDATE_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump({"last_day": day}, f, ensure_ascii=False)
    except:
        pass

# ✅ YENİ: FETCH TRACKING HELPER'LARI (Artık aktif kullanılmıyor ama dosya tanımı kalsın)
def _load_fetch_tracking() -> Dict[str, str]:
    if os.path.exists(FETCH_TRACKING_PATH):
        try:
            with open(FETCH_TRACKING_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {}

def _save_fetch_tracking(data: Dict[str, str]):
    try:
        with open(FETCH_TRACKING_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except:
        pass

# ✅ GÜNCELLENDİ: RAM CACHE İÇİNDE GÜNCEL VERİ KONTROLÜ (asof_day bazlı)
def _is_code_fresh(code: str, effective_day: str) -> bool:
    """
    Bir fon kodu effective_day için güncel mi?
    - asof_day kontrol edilir.
    - RAM cache'e bakar, yoksa disk cache'ten bakar.
    """
    code = code.upper().strip()

    def check_rec(r: Dict) -> bool:
        if not r or r.get("nav", 0) <= 0:
            return False
        # ✅ Öncelik asof_day
        rec_asof = str(r.get("asof_day") or "").strip()
        if rec_asof == effective_day:
            return True
        # asof_day yoksa (eski veri) ama last_update tutuyorsa (legacy)
        if not rec_asof and str(r.get("last_update", "")).startswith(effective_day):
            return True
        return False

    # 1) RAM check
    if check_rec(_PRICE_CACHE.get(code)):
        return True

    # 2) Disk check
    if os.path.exists(LIVE_PRICES_PATH):
        try:
            with open(LIVE_PRICES_PATH, "r", encoding="utf-8") as f:
                disk_raw = json.load(f)
            disk_data = disk_raw.get("data", {}) if isinstance(disk_raw, dict) else {}
            if check_rec(disk_data.get(code)):
                return True
        except:
            pass

    return False

def _missing_codes_for_day(codes: List[str], effective_day: str) -> List[str]:
    """codes içinden effective_day için güncel olmayanları döndürür."""
    out = []
    for c in codes:
        c2 = (c or "").upper().strip()
        if c2 and not _is_code_fresh(c2, effective_day):
            out.append(c2)
    return out

# ✅ YENİ: Canlı listeden fon kodlarını oku
def _get_live_list_codes() -> List[str]:
    """Canlı listedeki fon kodlarını döndür"""
    codes = []
    if os.path.exists(LIVE_LIST_PATH):
        try:
            with open(LIVE_LIST_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            for item in data.get("items", []):
                code = str(item.get("code") or "").upper().strip()
                if code:
                    codes.append(code)
        except:
            pass
    return codes

# ✅ YENİ: Portföyden fon kodlarını oku
def _get_portfolio_codes() -> List[str]:
    """Portföydeki fon kodlarını döndür"""
    codes = []
    if os.path.exists(PORTFOLIO_PATH):
        try:
            with open(PORTFOLIO_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            for pos in data.get("positions", []):
                code = str(pos.get("code") or "").upper().strip()
                if code:
                    codes.append(code)
        except:
            pass
    return codes

# ✅ YENİ: İlk defa eklenen fonları tespit et
def _get_newly_added_funds(previous_codes: List[str], current_codes: List[str]) -> List[str]:
    """Yeni eklenen fon kodlarını döndür"""
    prev_set = set(previous_codes)
    new_codes = [code for code in current_codes if code not in prev_set]
    return new_codes

# 📌 DÜZELTME 1: Unicode eksi işareti ve temizleme mantığı güncellendi
def _parse_turkish_float(text: str) -> float:
    try:
        s = str(text)
        s = s.replace("−", "-")  # 🔴 KRİTİK: unicode minus normalize
        s = re.sub(r"[^0-9,.-]", "", s)
        return float(s.replace(",", "."))
    except:
        return 0.0

# ✅ DÜZELTİLDİ: 1️⃣ load_cache_to_memory()
def load_cache_to_memory():
    """Server açılınca diskteki veriyi RAM'e yükler"""
    global _PRICE_CACHE
    
    if not os.path.exists(LIVE_PRICES_PATH):
        _PRICE_CACHE = {}
    else:
        try:
            with open(LIVE_PRICES_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)

            # ✅ KRİTİK: batch output içinden SADECE data'yı al
            if isinstance(raw, dict) and "data" in raw:
                _PRICE_CACHE = raw["data"]
            else:
                _PRICE_CACHE = raw

            print(f"✅ RAM cache yüklendi: {len(_PRICE_CACHE)} fon")

        except Exception as e:
            print(f"❌ Cache yüklenedi: {e}")
            _PRICE_CACHE = {}

    # ✅ DEBUG PRINTS (İSTENİLEN)
    print(f"🧭 BASE_DIR={BASE_DIR}")
    print(f"🧭 PORTFOLIO_PATH={PORTFOLIO_PATH} exists={os.path.exists(PORTFOLIO_PATH)}")
    print(f"🧭 LIVE_LIST_PATH={LIVE_LIST_PATH} exists={os.path.exists(LIVE_LIST_PATH)}")
    print(f"🧭 LIVE_PRICES_PATH={LIVE_PRICES_PATH} exists={os.path.exists(LIVE_PRICES_PATH)}")

# ✅ ADIM 3: KAYIT FORMATI DÜZELTİLDİ (Batch scraper uyumlu)
def save_memory_to_disk():
    """RAM cache'i diske atomik yaz"""
    try:
        tmp = LIVE_PRICES_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                {"data": _PRICE_CACHE, "asof": now_str()},
                f,
                ensure_ascii=False,
                indent=2
            )
        os.replace(tmp, LIVE_PRICES_PATH)
    except Exception as e:
        print(f"❌ save_memory_to_disk: {e}")

# ✅ PATCH 1.1: Atomik JSON yazma helper'ı
def _atomic_write_json(path: str, obj: Any):
    """JSON'u atomik yaz (yarım dosya / bozuk JSON riskini azaltır)."""
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception as e:
        print(f"❌ _atomic_write_json({path}): {e}")

# ✅ EKLENDİ: master map'i cacheli oku (type/name için)
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
    print(f"🌐 TEFAS HTML deniyorum: {fund_code}")
    url = f"https://www.tefas.gov.tr/FonAnaliz.aspx?FonKod={fund_code.upper()}"
    
    # 🔧 ACİL ÇÖZÜM: Daha güçlü headers ve timeout
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none"
    }
    
    try:
        # 🔧 ACİL ÇÖZÜM: Timeout'u 15 saniyeye çıkar
        session = requests.Session()
        r = session.get(url, headers=headers, timeout=15, verify=False)
        print(f"📊 TEFAS HTML Response: {r.status_code} | Content-Length: {len(r.text)}")
        
        if r.status_code == 200 and len(r.text) > 1000:  # Minimum içerik kontrolü
            html = r.text
            
            # 🔧 ACİL ÇÖZÜM: Daha esnek regex pattern'leri
            # Fiyat için birden fazla pattern dene
            price_patterns = [
                r"Son Fiyat.*?<span>([\d,\.]+)</span>",
                r"NAV.*?<span>([\d,\.]+)</span>", 
                r"Fiyat.*?<span>([\d,\.]+)</span>",
                r"<span.*?class.*?fiyat.*?>([\d,\.]+)</span>",
                r"(\d+,\d{4})"  # Genel sayı formatı
            ]
            
            price = 0.0
            for pattern in price_patterns:
                match = re.search(pattern, html, re.DOTALL)
                if match:
                    price = _parse_turkish_float(match.group(1))
                    if price > 0:
                        print(f"✅ Fiyat bulundu ({pattern}): {price}")
                        break
            
            # Günlük getiri için birden fazla pattern
            daily_patterns = [
                r"Günlük Getiri.*?<span>(.*?)</span>",
                r"Günlük.*?<span>(.*?)</span>",
                r"Daily.*?<span>(.*?)</span>",
                r"<span.*?günlük.*?>(.*?)</span>",
            ]
            
            daily = 0.0
            for pattern in daily_patterns:
                match = re.search(pattern, html, re.DOTALL)
                if match:
                    daily = _parse_turkish_float(match.group(1))
                    if daily != 0.0:
                        print(f"✅ Günlük getiri bulundu ({pattern}): {daily}%")
                        break
            
            # Yıllık getiri için pattern
            yearly = 0.0
            yearly_match = re.search(r"Son 1 Yıl.*?<span>(.*?)</span>", html, re.DOTALL)
            if yearly_match:
                yearly = _parse_turkish_float(yearly_match.group(1))
            
            if price > 0:
                print(f"🎯 TEFAS HTML BAŞARILI: {fund_code} - Fiyat: {price}, Günlük: {daily}%, Yıllık: {yearly}%")
                return {"price": price, "daily_pct": daily, "yearly_pct": yearly, "source": "HTML"}
            else:
                print(f"❌ TEFAS HTML FİYAT BULUNAMADI: {fund_code}")
                # HTML içeriğini debug için kaydet
                debug_path = f"debug_{fund_code}.html"
                with open(debug_path, "w", encoding="utf-8") as f:
                    f.write(html)
                print(f"💾 HTML içeriği kaydedildi: {debug_path}")
                
        else:
            print(f"❌ TEFAS HTML HTTP HATA: {fund_code} - Status: {r.status_code}, Length: {len(r.text)}")
            
    except requests.exceptions.Timeout:
        print(f"⏰ TEFAS HTML TIMEOUT: {fund_code} - 15 saniye aşıldı")
    except requests.exceptions.ConnectionError:
        print(f"🔌 TEFAS HTML BAĞLANTI HATASI: {fund_code} - İnternet bağlantısı kontrol edilmeli")
    except Exception as e:
        print(f"❌ TEFAS HTML GENEL HATA: {fund_code} - {str(e)}")
    
    return None

# ✅ EKLENDİ: TEFAS tarih parse yardımcısı
def _parse_tefas_date(s: str) -> Optional[datetime]:
    s = (s or "").strip()
    if not s:
        return None

    # sık gelen formatlar
    fmts = (
        "%d.%m.%Y",
        "%d/%m/%Y",
        "%Y-%m-%d",
        "%d.%m.%Y %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    )

    for fmt in fmts:
        try:
            return datetime.strptime(s, fmt)
        except:
            pass

    # bazen "25.12.2025 00:00:00.000" gibi geliyor -> noktadan sonrası kırp
    try:
        s2 = s.split(".000")[0]
        return datetime.strptime(s2, "%d.%m.%Y %H:%M:%S")
    except:
        return None

def _fetch_api(fund_code: str):
    print(f"🌐 TEFAS API deniyorum: {fund_code}")
    url = "https://www.tefas.gov.tr/api/DB/BindHistoryInfo"
    
    # 🔧 ACİL ÇÖZÜM: Daha güçlü headers
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://www.tefas.gov.tr",
        "Referer": "https://www.tefas.gov.tr/",
        "Connection": "keep-alive"
    }
    
    try:
        # ✅ GÜNCELLENDİ: `end` tarihi İstanbul saatine göre
        try:
            end = datetime.now(ZoneInfo("Europe/Istanbul"))
        except:
            end = datetime.now()
        start = end - timedelta(days=7)  # 5 gün yerine 7 gün yap
        
        payload = {
            "fontip": "YAT",
            "fonkod": fund_code.upper(),
            "bastarih": start.strftime("%d.%m.%Y"),
            "bittarih": end.strftime("%d.%m.%Y"),
        }
        
        print(f"📡 TEFAS API Request: {fund_code} - {start.strftime('%d.%m.%Y')} to {end.strftime('%d.%m.%Y')}")
        
        # 🔧 ACİL ÇÖZÜM: Timeout'u 15 saniyeye çıkar
        r = requests.post(url, data=payload, headers=headers, timeout=15, verify=False)
        print(f"📊 TEFAS API Response: {r.status_code} | Content-Length: {len(r.text)}")
        
        if r.status_code == 200:
            try:
                response_data = r.json()
                data = response_data.get("data", [])
                print(f"📈 TEFAS API Data Count: {len(data) if data else 0} records")
                
                if data and len(data) > 0:
                    # En güncel veriyi bul
                    valid_data = []
                    for item in data:
                        # Key isimleri TEFAS tarafında bazen değişebiliyor
                        dt = _parse_tefas_date(
                            item.get("TARIH") or item.get("Tarih") or item.get("tarih") or ""
                        )
                        if dt:
                            valid_data.append((dt, item))
                    
                    if valid_data:
                        valid_data.sort(key=lambda x: x[0], reverse=True)  # En yeni tarih en başta
                        last_date, last_item = valid_data[0]
                        # Güvenli fiyat parse
                        price = _parse_turkish_float(last_item.get("FIYAT") or last_item.get("Fiyat") or last_item.get("fiyat") or 0)
                        
                        print(f"💰 TEFAS API Son Tarih: {last_date.strftime('%d.%m.%Y')} - Fiyat: {price}")
                        
                        if price > 0:
                            print(f"🎯 TEFAS API BAŞARILI: {fund_code} - Fiyat: {price}")
                            return {
                                "price": price,
                                "daily_pct": None,   # 🔴 API'den günlük getiri hesaplanmaz
                                "yearly_pct": 0.0,
                                "source": "API",
                                "asof_day": last_date.strftime("%Y-%m-%d"),  # ✅ KRİTİK: API'den gelen gerçek tarih
                            }
                        else:
                            print(f"❌ TEFAS API GEÇERSİZ FİYAT: {fund_code} - {price}")
                    else:
                        print(f"❌ TEFAS API GEÇERLI TARİH BULUNAMADI: {fund_code}")
                else:
                    print(f"❌ TEFAS API VERI YOK: {fund_code} - Boş response")
                    
            except ValueError as e:
                print(f"❌ TEFAS API JSON HATA: {fund_code} - {str(e)}")
                print(f"Raw Response: {r.text[:200]}...")
        else:
            print(f"❌ TEFAS API HTTP HATA: {fund_code} - Status: {r.status_code}")
            
    except requests.exceptions.Timeout:
        print(f"⏰ TEFAS API TIMEOUT: {fund_code} - 15 saniye aşıldı")
    except requests.exceptions.ConnectionError:
        print(f"🔌 TEFAS API BAĞLANTI HATASI: {fund_code} - İnternet bağlantısı kontrol edilmeli")
    except Exception as e:
        print(f"❌ TEFAS API GENEL HATA: {fund_code} - {str(e)}")
    
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

# ============================================================
# 🔥 YENİ: FINTABLES & TEFAS DETAY SCRAPER (X-RAY)
# ============================================================

def _fetch_tefas_allocation(fund_code: str) -> Optional[List[Dict[str, Any]]]:
    """TEFAS'tan Varlık Dağılımını (Pasta Grafik) çeker"""
    print(f"🥧 TEFAS Allocation deniyorum: {fund_code}")
    url = f"https://www.tefas.gov.tr/FonAnaliz.aspx?FonKod={fund_code.upper()}"
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        r = requests.get(url, headers=headers, timeout=10, verify=False)
        if r.status_code == 200:
            html = r.text
            # Highcharts data'sını regex ile yakala
            # series: [{ name: 'Varlık Dağılımı', data: [["Hisse Senedi",43.58],...] }]
            
            pattern = r"series:\s*\[\{\s*name:\s*'Varlık Dağılımı',\s*data:\s*(\[\[.*?\]\])"
            match = re.search(pattern, html, re.DOTALL)
            
            if match:
                json_str = match.group(1).replace("'", '"')
                try:
                    # Basit bir JS array -> Python list dönüşümü
                    # data: [["Hisse", 40], ["Mevduat", 60]]
                    raw_data = json.loads(json_str)
                    allocation = []
                    for item in raw_data:
                        if len(item) == 2:
                            allocation.append({"name": item[0], "value": float(item[1])})
                    return allocation
                except:
                    pass
    except Exception as e:
        print(f"❌ TEFAS Allocation Hatası: {e}")
    
    return None

def _fetch_fintables_full_details(fund_code: str) -> Optional[Dict[str, Any]]:
    """
    Fintables Scraper - GÜÇLENDİRİLMİŞ VERSİYON
    """
    print(f"💎 Fintables Detay Çekiliyor: {fund_code}")
    url = f"https://fintables.com/fonlar/{fund_code.upper()}"
    
    # 🛡️ Anti-Bot Headers (Gerçek Tarayıcı Gibi Davran)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.google.com/",
        "Upgrade-Insecure-Requests": "1"
    }

    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            print(f"❌ Fintables HTTP {r.status_code}")
            return None

        soup = BeautifulSoup(r.text, "html.parser")
        
        details = {
            "positions": [],
            "increased": [],
            "decreased": [],
            "info": {"founder": "", "risk_value": 0, "mgmt_fee": "", "stopaj": ""},
            "performance_chart": []
        }

        # 1. POZİSYONLAR TABLOSUNU BUL (Daha zeki yöntem)
        all_tables = soup.find_all("table")
        
        for table in all_tables:
            txt = table.get_text().lower()
            rows = table.find_all("tr")
            if len(rows) < 2: continue

            # Bu tablonun ne tablosu olduğunu başlığından veya üstündeki divden anlamaya çalış
            parent_txt = table.parent.parent.get_text().lower() if table.parent and table.parent.parent else ""
            
            parsed_rows = []
            for row in rows[1:]: # Başlığı atla
                cols = row.find_all("td")
                if len(cols) >= 2:
                    # İlk kolon hisse kodu, ikinci kolon oran (genelde)
                    code_cand = cols[0].get_text(strip=True).split(" ")[0] # "THYAO (Türk Hava..)" -> "THYAO"
                    ratio_cand = cols[1].get_text(strip=True)
                    
                    # Sayısal kontrol
                    try:
                        ratio_val = _parse_turkish_float(ratio_cand)
                        if len(code_cand) >= 3 and ratio_val > 0:
                            parsed_rows.append({"code": code_cand, "ratio": ratio_val})
                    except:
                        pass
            
            if not parsed_rows: continue

            if "artırılan" in parent_txt or "artırılan" in txt:
                details["increased"] = parsed_rows
            elif "azaltılan" in parent_txt or "azaltılan" in txt:
                details["decreased"] = parsed_rows
            elif "büyük pozisyonlar" in parent_txt or "büyük pozisyonlar" in txt:
                details["positions"] = parsed_rows
            else:
                # Hiçbir başlık uymuyorsa ama veri varsa ve ana liste boşsa, bunu ana liste yap
                if not details["positions"]:
                    details["positions"] = parsed_rows

        # 2. KÜNYE BİLGİLERİ (Risk, Kurucu vb.)
        full_text = soup.get_text(" ", strip=True)
        
        # Risk Değeri (Regex ile avla: "Risk Değeri 7")
        risk_match = re.search(r"Risk Değeri\s*[:]?\s*(\d)", full_text, re.IGNORECASE)
        if risk_match:
            details["info"]["risk_value"] = int(risk_match.group(1))
        
        # Kurucu
        founder_match = re.search(r"Kurucu\s+(.*?)(?=\s+Yıllık|$)", full_text, re.IGNORECASE)
        if founder_match:
            details["info"]["founder"] = founder_match.group(1).strip()

        print(f"✅ Fintables Data: {len(details['positions'])} pozisyon, Risk: {details['info'].get('risk_value')}")
        return details

    except Exception as e:
        print(f"❌ Fintables Error: {e}")
        return None

# ============================================================
# 🔥 YENİ: HİSSE BAZLI AI SKORLAMA (LIVE STOCK DATA ILE)
# ============================================================
def _load_live_stocks() -> Dict[str, float]:
    """Services.py tarafından üretilen hisse fiyatlarını okur"""
    prices = {}
    if os.path.exists(STOCKS_LIVE_PRICES_PATH):
        try:
            with open(STOCKS_LIVE_PRICES_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                # data formatı genelde [{"symbol": "THYAO", "chgPct": 2.5}, ...] şeklindedir
                if isinstance(data, list):
                    for item in data:
                        sym = item.get("symbol", "").replace(".IS", "")
                        chg = item.get("chgPct", 0.0)
                        prices[sym] = float(chg)
                elif isinstance(data, dict) and "data" in data: # Wrapper varsa
                     for item in data["data"]:
                        sym = item.get("symbol", "").replace(".IS", "")
                        chg = item.get("chgPct", 0.0)
                        prices[sym] = float(chg)
        except:
            pass
    return prices

def calculate_ai_prediction(yearly: float, daily: float, holdings: List[Dict[str, Any]] = None):
    """
    YENİ NESİL AI TAHMİNİ:
    Eğer 'holdings' (Fintables'tan gelen hisse listesi) varsa,
    bu hisselerin CANLI piyasa değişimlerine göre fona puan verir.
    """
    # 1. Klasik (Baz) Skor
    d_val = daily if daily is not None else 0.0
    
    direction = "NÖTR"
    confidence = 50
    
    # Baz puanlama (Geçmiş performans)
    if yearly > 40:
        confidence += 20
        direction = "POZİTİF"
    elif yearly < 0:
        confidence += 10
        direction = "NEGATİF"

    # Günlük hareket (TEFAS verisi - Dünkü kapanış)
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

    # 2. HİSSE BAZLI CANLI SKOR (Eğer veri varsa)
    stock_impact = 0.0
    
    if holdings:
        live_stocks = _load_live_stocks()
        if live_stocks:
            total_w = 0
            weighted_change = 0
            
            for h in holdings:
                code = h.get("code", "")
                ratio = h.get("ratio", 0.0)
                
                # Hissenin canlı değişimini bul
                live_chg = live_stocks.get(code)
                
                if live_chg is not None:
                    weighted_change += (live_chg * ratio)
                    total_w += ratio
            
            # Fonun içindeki hisselerin ortalama değişimi
            if total_w > 0:
                avg_stock_change = weighted_change / total_w
                stock_impact = avg_stock_change
                
                # Skoru güncelle
                if avg_stock_change > 0.5: # Hisseler bugün coşmuş
                    direction = "POZİTİF"
                    confidence = min(95, confidence + 15)
                elif avg_stock_change < -0.5: # Hisseler bugün çakılmış
                    direction = "NEGATİF"
                    confidence = min(95, confidence + 15)
    
    # Tahmin edilen getiri (Basit model)
    # (Hisse etkisi * 0.7) + (TEFAS dünkü getiri * 0.3)
    estimated_return = (stock_impact * 0.7) + (d_val * 0.3)
    
    # Yönü estimated_return belirlesin
    if estimated_return > 0.1:
        direction = "POZİTİF"
    elif estimated_return < -0.1:
        direction = "NEGATİF"

    return direction, confidence, estimated_return


def get_fund_data_safe(fund_code: str):
    """
    GÜNDE 1 KEZ TEFAS + FINTABLES ENTEGRASYONLU
    """
    fund_code = fund_code.upper()
    effective_day = tefas_effective_date()

    try:
        now = datetime.now(ZoneInfo("Europe/Istanbul"))
    except:
        now = datetime.now()
    before_open = now.hour < 9 or (now.hour == 9 and now.minute < 30)
    is_weekend = now.weekday() >= 5

    cached = _PRICE_CACHE.get(fund_code)

    if not cached:
        if os.path.exists(LIVE_PRICES_PATH):
            try:
                with open(LIVE_PRICES_PATH, "r", encoding="utf-8") as f:
                    disk_raw = json.load(f)
                disk_data = disk_raw.get("data", {}) if isinstance(disk_raw, dict) else {}
                if disk_data.get(fund_code):
                    cached = disk_data[fund_code]
                    _PRICE_CACHE[fund_code] = cached
            except:
                pass

    cached_asof = (cached.get("asof_day") or "").strip() if cached else ""
    
    # Detay verisi var mı kontrol et (Yeni eklenen özellik)
    has_details = cached and "details" in cached and cached["details"].get("positions")

    is_new_fund = not cached
    force_fetch = False
    
    if is_new_fund:
        force_fetch = True
    elif not has_details: 
        # Veri var ama detay yoksa, detay çekmek için zorla (günde 1 kere)
        force_fetch = True 
    elif cached_asof != effective_day:
        force_fetch = True

    if (not is_weekend) and before_open and not is_new_fund and has_details:
        force_fetch = False

    if not force_fetch and cached:
        return cached

    if not cached and not force_fetch:
        return {"nav": 0.0, "daily_return_pct": 0.0}

    with _TEFAS_LOCK:
        cached = _PRICE_CACHE.get(fund_code)
        if cached and cached.get("asof_day") == effective_day and "details" in cached and cached["details"].get("positions"):
            return cached

        print(f"🚀 FORCE FETCH (X-RAY): {fund_code}")

        data = None
        if force_fetch:
            data = fetch_fund_live(fund_code)

        if data and data.get("price", 0) > 0:
            asof_day = (data.get("asof_day") or "").strip()
            if not asof_day:
                api_meta = _fetch_api(fund_code)
                asof_day = api_meta["asof_day"] if api_meta else effective_day

            safe_daily = data["daily_pct"] if data["daily_pct"] is not None else 0.0

            # 🔥 YENİ: DETAYLARI ÇEK
            # 1. Fintables'tan detayları (Pozisyonlar, Risk vb.) al
            details = _fetch_fintables_full_details(fund_code)
            
            # 2. TEFAS'tan Allocation (Pasta Grafik) al (Yedek veya tamamlayıcı)
            allocation = _fetch_tefas_allocation(fund_code)
            
            if details:
                if allocation:
                     details["allocation"] = allocation # TEFAS verisi daha temiz oluyor genelde
            else:
                # Fintables başarısızsa boş obje oluştur, en azından allocation ekle
                details = {
                    "positions": [],
                    "info": {},
                    "allocation": allocation if allocation else []
                }

            # 🔥 YENİ: AI Hesapla (Pozisyon verisiyle)
            holdings = details.get("positions", [])
            dir_str, conf, est_ret = calculate_ai_prediction(data["yearly_pct"], safe_daily, holdings)

            new_data = {
                "nav": data["price"],
                "daily_return_pct": safe_daily,
                "asof_day": asof_day,
                "last_update": asof_day + " 18:30:00",
                "source": data.get("source", "HTML"),
                "details": details, # ✅ ZENGİN VERİ EKLENDİ
                "ai_prediction": {
                    "direction": dir_str,
                    "confidence": conf,
                    "score": round(data["yearly_pct"] / 12, 2),
                    "estimated_return": round(est_ret, 2) # ✅ YENİ
                },
            }

            _PRICE_CACHE[fund_code] = new_data
            save_memory_to_disk()
            return new_data
        
        elif force_fetch and cached:
             # TEFAS ana veri başarısız ama cache var -> Detayları güncellemeye çalış
             # (Opsiyonel: Sadece detay eksikse buraya düşebilir)
             pass

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

    # ✅ PATCH 2: Atomik yazma
    try:
        _atomic_write_json(MARKET_CACHE_PATH, {"asof": now_str(), "items": items})
        print(f"🔄 Market Updated: {now_str()}")
    except Exception as e:
        print(f"❌ Market write error: {e}")
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

    # ===============================
    # ⏰ PİYASA AÇIK / KAPALI KONTROLÜ
    # ===============================
    try:
        now_tr = datetime.now(ZoneInfo("Europe/Istanbul"))
    except:
        now_tr = datetime.now()

    # BIST: 09:30 – 18:10 arası açık kabul edelim
    market_open = (
        (now_tr.hour > 9 or (now_tr.hour == 9 and now_tr.minute >= 30)) and
        (now_tr.hour < 18 or (now_tr.hour == 18 and now_tr.minute <= 10))
    )

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
        
        # Eğer cached veri varsa ve "predicted_return_pct" yoksa (eski cache), yenile
        if cached and "predicted_return_pct" not in cached:
             cached = None

        # ⛔ PİYASA KAPALIYSA → CANLI AI KİLİTLENİR
        if not market_open and cached:
            return cached

        # Market açıksa cache'i kısalt
        try:
            now_tr = datetime.now(ZoneInfo("Europe/Istanbul"))
        except:
            now_tr = datetime.now()
        ttl = 1 if market_open else 3600  # Kapalıyken 1 saat kilit


        if cached and (now_ts - cached["_ts"]) < ttl:
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
            bist_change_pct=float(bist or 0.0),
            usd_change_pct=float(usd or 0.0),
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
        # ✅ GÜNCELLENDİ: dt İstanbul saatine göre
        try:
            dt = datetime.now(ZoneInfo("Europe/Istanbul"))
        except:
            dt = datetime.now()
        minutes = dt.hour * 60 + dt.minute
        session_pos = max(0.0, min(1.0, (minutes - 570) / (1090 - 570)))
        drift = 0.12 * (1.0 - session_pos)

        # ===============================
        # 🎯 FİNAL TAHMİN (AĞIRLIKLI)
        # ===============================
        # Eğer cached veride hisse bazlı tahmin varsa (estimated_return), onu da kat
        fund_data = _PRICE_CACHE.get(fund_code, {})
        holdings_impact = 0.0
        if "ai_prediction" in fund_data:
             holdings_impact = fund_data["ai_prediction"].get("estimated_return", 0.0)

        # Formül: Premium Base %60 + Holdings %30 + Daily %10
        predicted = (
            premium_base * 0.60 +
            holdings_impact * 0.30 +
            daily_real * 0.10 +
            drift * 0.05 +
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

        # 📌 DÜZELME 2: RAM cache yoksa Disk cache'ten oku (persistence)
        info = _PRICE_CACHE.get(code)
        
        if not info:
            # 🔴 RAM boşsa disk cache'ten oku
            if os.path.exists(LIVE_PRICES_PATH):
                try:
                    with open(LIVE_PRICES_PATH, "r", encoding="utf-8") as f:
                        disk_raw = json.load(f)
                    disk_data = disk_raw.get("data", {})
                    info = disk_data.get(code, {})
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
# 7. YENİ: OTOMATİK GÜNCELLEME SİSTEMİ
# ============================================================

def update_newly_added_funds(fund_codes: List[str]):
    """
    Yeni eklenen fonları hemen günceller
    """
    if not fund_codes:
        return
        
    print(f"🚀 Yeni eklenen fonlar güncelleniyor: {', '.join(fund_codes)}")
    
    for i, code in enumerate(fund_codes, 1):
        print(f"📈 [{i}/{len(fund_codes)}] Güncelleniyor: {code}")
        try:
            result = get_fund_data_safe(code)
            if result and result.get("nav", 0) > 0:
                print(f"✅ {code} başarıyla güncellendi - Fiyat: {result['nav']:.4f}")
            else:
                print(f"❌ {code} güncellenemedi - Veri alınamadı")
        except Exception as e:
            print(f"💥 {code} güncelleme hatası: {str(e)}")
        
        time.sleep(0.4)  # Ban koruması
    
    print(f"🎯 Tüm yeni fonlar işlendi: {len(fund_codes)} adet")

# ✅ GÜNCELLENDİ: "Any" yerine tüm portföyün güncel olup olmadığını kontrol eder ve timezone düzeltmesi
def maybe_update_portfolio_funds():
    """
    09:30 sonrası portföy fonlarını GÜNDE 1 KEZ (effective_day bazlı) tamamlar.
    """
    # Eğer server restart olmuşsa (RAM cache boşsa) günlük kilidi resetle
    if not _PRICE_CACHE:
        _save_portfolio_update_day("")

    try:
        now = datetime.now(ZoneInfo("Europe/Istanbul"))
    except:
        now = datetime.now()
    if now.hour < 9 or (now.hour == 9 and now.minute < 30):
        return

    today = now.strftime("%Y-%m-%d")
    effective_day = tefas_effective_date()
    run_day = effective_day  # ✅ State anahtarı bu olmalı

    with _PORTFOLIO_UPDATE_LOCK:
        # Portföy yoksa state yazıp çık
        if not os.path.exists(PORTFOLIO_PATH):
            _save_portfolio_update_day(run_day)
            return

        # Portföy kodlarını oku
        try:
            with open(PORTFOLIO_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            codes = [
                (p.get("code") or "").upper().strip()
                for p in raw.get("positions", [])
                if p.get("code")
            ]
        except Exception as e:
            print(f"❌ Portföy okuma hata: {e}")
            return

        # ✅ DEBUG PRINT (İSTENİLEN)
        print(f"🧪 Portfolio codes={len(codes)} | state_day={_load_portfolio_update_day()} | today={today} | effective_day={effective_day}")

        # Eksikleri bul (RAM + disk üzerinden)
        missing = _missing_codes_for_day(codes, effective_day)
        last_day = _load_portfolio_update_day()

        # ✅ SADECE: run_day state yazılmış VE portföyde eksik yoksa erken çık
        if last_day == run_day and not missing:
            return

        # Eksik yoksa state'i düzelt ve çık
        if not missing:
            _save_portfolio_update_day(run_day)
            return

        print(f"🔄 Portföy auto-update: {len(missing)}/{len(codes)} fon eksik, güncellenecek. effective_day={effective_day}")

        # Sadece eksikleri güncelle
        for code in missing:
            try:
                get_fund_data_safe(code)
            except Exception as e:
                print(f"❌ Portföy update hata ({code}): {e}")
            time.sleep(0.4)  # 🔒 BAN KORUMASI

        # Gün bitti (portföy tamamlandı mı kontrol et) → state yaz
        missing2 = _missing_codes_for_day(codes, effective_day)
        if not missing2:
            _save_portfolio_update_day(run_day)
            print(f"✅ Portföy fonları tamamlandı ({run_day})")
        else:
            print(f"⚠️ Portföy fonları kısmi kaldı: {len(missing2)} fon hâlâ eksik; sonraki istekte tekrar denenecek.")

# ✅ GÜNCELLENDİ: "Any" yerine tüm canlı listenin güncel olup olmadığını kontrol eder ve timezone düzeltmesi
def maybe_update_live_list_funds():
    """
    09:30 sonrası canlı listedeki fonları GÜNDE 1 KEZ (effective_day bazlı) tamamlar.
    """
    # Eğer server restart olmuşsa (RAM cache boşsa) günlük kilidi resetle
    if not _PRICE_CACHE:
        _save_live_list_update_day("")

    try:
        now = datetime.now(ZoneInfo("Europe/Istanbul"))
    except:
        now = datetime.now()
    if now.hour < 9 or (now.hour == 9 and now.minute < 30):
        return

    today = now.strftime("%Y-%m-%d")
    effective_day = tefas_effective_date()
    run_day = effective_day  # ✅ State anahtarı bu olmalı

    with _LIVE_LIST_UPDATE_LOCK:
        # Liste yoksa state yazıp çık
        if not os.path.exists(LIVE_LIST_PATH):
            _save_live_list_update_day(run_day)
            return

        # Liste kodlarını oku
        try:
            with open(LIVE_LIST_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            codes = [
                (item.get("code") or "").upper().strip()
                for item in raw.get("items", [])
                if item.get("code")
            ]
        except Exception as e:
            print(f"❌ Canlı liste okuma hata: {e}")
            return

        # Eksikleri bul
        missing = _missing_codes_for_day(codes, effective_day)
        last_day = _load_live_list_update_day()

        # ✅ SADECE: run_day state yazılmış VE listede eksik yoksa erken çık
        if last_day == run_day and not missing:
            return

        # Eksik yoksa state'i düzelt ve çık
        if not missing:
            _save_live_list_update_day(run_day)
            return

        print(f"🔄 Canlı liste auto-update: {len(missing)}/{len(codes)} fon eksik, güncellenecek. effective_day={effective_day}")

        # Sadece eksikleri güncelle
        for code in missing:
            try:
                get_fund_data_safe(code)
            except Exception as e:
                print(f"❌ Canlı liste update hata ({code}): {e}")
            time.sleep(0.4)  # Ban koruması

        # Gün bitti mi kontrol et → state yaz
        missing2 = _missing_codes_for_day(codes, effective_day)
        if not missing2:
            _save_live_list_update_day(run_day)
            print(f"✅ Canlı liste fonları tamamlandı ({run_day})")
        else:
            print(f"⚠️ Canlı liste fonları kısmi kaldı: {len(missing2)} fon hâlâ eksik; sonraki istekte tekrar denenecek.")

# ============================================================
# 8. API ENDPOINTS
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

    # ✅ PATCH 3.4: 15 sn cache (scope bazlı)
    with _PRED_SUMMARY_LOCK:
        ts = time.time()
        cached = _PRED_SUMMARY_CACHE.get(scope)
        last_ts = _PRED_SUMMARY_TS.get(scope, 0.0)
        if cached and (ts - last_ts) < _PRED_SUMMARY_TTL_SEC:
            return cached

    data = _build_predictions_summary(scope=scope)

    # ✅ PATCH 3.6: TS scope bazlı update
    with _PRED_SUMMARY_LOCK:
        _PRED_SUMMARY_CACHE[scope] = data
        _PRED_SUMMARY_TS[scope] = time.time()

    return data

@router.get("/portfolio")
def api_portfolio():
    # 🔥 09:30 sonrası otomatik portföy güncelleme
    maybe_update_portfolio_funds()

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
            
            # 🎯 ÇÖZÜM: Mobil'in predicted_return_pct alanına AI TAHMİNİ koy (Fix 2)
            "predicted_return_pct": ai.get("predicted_return_pct", daily_real), 
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
    
    YENİ: Fon eklendiğinde otomatik güncelleme
    """
    try:
        positions = payload.get("positions", [])
        
        # ✅ YENİ: Önceki fon kodlarını oku
        previous_codes = _get_portfolio_codes()
        
        # Portföyü kaydet
        with open(PORTFOLIO_PATH, "w", encoding="utf-8") as f:
            json.dump({"asof": now_str(), "positions": positions}, f, ensure_ascii=False, indent=2)
        
        # ✅ YENİ: Yeni eklenen fonları tespit et ve güncelle
        current_codes = [str(pos.get("code") or "").upper().strip() for pos in positions if pos.get("code")]
        new_funds = _get_newly_added_funds(previous_codes, current_codes)
        
        if new_funds:
            print(f"🆕 Yeni fonlar tespit edildi: {', '.join(new_funds)}")
            update_newly_added_funds(new_funds)
        
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

@router.get("/live-list")
def api_live_list():
    """
    ✅ YENİ: Canlı liste endpoint'i
    09:30 sonrası otomatik güncelleme yapar
    """
    # 09:30 sonrası otomatik canlı liste güncelleme
    maybe_update_live_list_funds()
    
    if os.path.exists(LIVE_LIST_PATH):
        try:
            with open(LIVE_LIST_PATH, "r", encoding="utf-8") as f:
                raw_list = json.load(f)
        except:
            raw_list = {"items": []}
    else:
        raw_list = {"items": []}

    result_list = []
    for item in raw_list.get("items", []):
        code = (item.get("code") or "").upper().strip()
        if not code:
            continue

        # TEFAŞ cacheli gerçek veri (günde 1 kere)
        info = get_fund_data_safe(code)
        daily_real = float(info.get("daily_return_pct", 0.0) or 0.0)

        # AI tahmin
        ai = get_ai_prediction_live(code, daily_real)

        result_list.append({
            "code": code,
            "name": item.get("name", ""),
            "nav": info.get("nav", 0.0),
            "daily_return_pct": daily_real,
            "predicted_return_pct": ai.get("predicted_return_pct", daily_real),
            "confidence_score": ai.get("confidence_score", 50),
            "direction": ai.get("direction", "NÖTR"),
            "type": item.get("type", ""),
        })

    return {"status": "success", "data": result_list}

@router.post("/live-list/set")
def api_live_list_set(payload: Dict[str, Any]):
    """
    payload: {"items":[{"code":"AFT","name":"..."}, ...]}
    
    YENİ: Canlı listeye fon eklendiğinde otomatik güncelleme
    """
    try:
        items = payload.get("items", [])
        
        # ✅ YENİ: Önceki fon kodlarını oku
        previous_codes = _get_live_list_codes()
        
        # Canlı listeyi kaydet
        with open(LIVE_LIST_PATH, "w", encoding="utf-8") as f:
            json.dump({"asof": now_str(), "items": items}, f, ensure_ascii=False, indent=2)
        
        # ✅ YENİ: Yeni eklenen fonları tespit et ve güncelle
        current_codes = [str(item.get("code") or "").upper().strip() for item in items if item.get("code")]
        new_funds = _get_newly_added_funds(previous_codes, current_codes)
        
        if new_funds:
            print(f"🆕 Canlı listeye yeni fonlar eklendi: {', '.join(new_funds)}")
            update_newly_added_funds(new_funds)
        
    except:
        pass
    return {"status": "success"}

@router.get("/detail/{code}")
def api_detail(code: str):
    # Detayda cacheli hızlı dön (günde 1 TEFAS)
    info = get_fund_data_safe(code)
    if info.get("nav", 0) > 0:
        daily_real = float(info.get("daily_return_pct", 0.0) or 0.0)
        ai = get_ai_prediction_live(code.upper(), daily_real)
        
        # Eğer Fintables'tan gelen detaylı AI skoru varsa (hisse bazlı), onu da ekle
        predicted_return = ai.get("predicted_return_pct", daily_real)
        if "ai_prediction" in info and "estimated_return" in info["ai_prediction"]:
             # Cache'teki hisse bazlı skoru kullanabiliriz, ama live market data daha taze
             # O yüzden get_ai_prediction_live fonksiyonu zaten bunu birleştiriyor.
             pass

        return {
            "status": "success",
            "data": {
                **info,
                # 🎯 ÇÖZÜM: Mobil kolay kullansın diye düz alanlar (Fix 2)
                "predicted_return_pct": predicted_return,
                "confidence_score": ai.get("confidence_score", 50),
                "direction": ai.get("direction", "NÖTR"),
            }
        }
    return {"status": "error", "message": "Veri yok"}

# @router.get("/admin/refresh-tefas")
# def admin_refresh_tefas():
#     """
#     TEFAS toplu batch scrape.
#     Runtime API'yi etkilemez.
#     """
#     result = run_batch_scrape()
#     return {
#         "status": "success",
#         "message": "TEFAS batch scrape tamamlandı",
#         "result": result
#     }

# ✅ EKLENDİ: Server açılışında bootstrap güncellemesi
def _startup_bootstrap_updates():
    # Uvicorn import sırasında hemen saldırmasın, biraz bekle
    time.sleep(2)

    # Server 09:30 sonrası açıldıysa anında dene; değilse endpoint zaten tetikler.
    try:
        maybe_update_portfolio_funds()
    except Exception as e:
        print(f"❌ Startup portfolio bootstrap hata: {e}")

    try:
        maybe_update_live_list_funds()
    except Exception as e:
        print(f"❌ Startup live-list bootstrap hata: {e}")

# ✅ PATCH 4.2: Threadleri tek sefer başlat (reload-safe)
def _start_background_jobs_once():
    """Uvicorn reload / çoklu import durumunda thread'leri tek sefer başlat."""
    global _BG_STARTED
    with _BG_LOCK:
        if _BG_STARTED:
            return
        _BG_STARTED = True

        # 1) Cache'i RAM'e yükle
        load_cache_to_memory()

        # 2) Market loop thread
        t_market = threading.Thread(target=auto_market_loop, daemon=True)
        t_market.start()

        # 3) Startup bootstrap thread
        t_boot = threading.Thread(target=_startup_bootstrap_updates, daemon=True)
        t_boot.start()

# ✅ Import olur olmaz çalıştır (ama tek sefer)
_start_background_jobs_once()
