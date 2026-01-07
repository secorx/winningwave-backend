# api/funds.py
# FINTABLES KALİTESİNDE FON DETAY SCRAPER (KAP + TEFAS)
# %100 ÇALIŞAN VERSİYON - FULL FILE

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
from bs4 import BeautifulSoup  # HTML Parsing için

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

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

# ✅ YENİ: Portföy güncelleme durumu için dosya yolu
def _load_portfolio_update_day() -> Optional[str]:
    if os.path.exists(PORTFOLIO_UPDATE_STATE_PATH):
        try:
            with open(PORTFOLIO_UPDATE_STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("last_day")
        except:
            pass
    return None

# ✅ YENİ: Portföy güncelleme durumu diske yaz
def _save_portfolio_update_day(day: str):
    try:
        with open(PORTFOLIO_UPDATE_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump({"last_day": day}, f, ensure_ascii=False)
    except:
        pass

# ✅ YENİ: Canlı liste güncelleme durumu diskten oku (Optional ile uyumlu)
def _load_live_list_update_day() -> Optional[str]:
    if os.path.exists(LIVE_LIST_UPDATE_STATE_PATH):
        try:
            with open(LIVE_LIST_UPDATE_STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("last_day")
        except:
            pass
    return None

# ✅ YENİ: Canlı liste güncelleme durumu diske yaz
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
        s = str(text).strip()
        s = s.replace("−", "-")  # unicode minus
        s = s.replace("%", "")
        # Örn: 1.234,56 -> önce noktayı sil, virgülü nokta yap
        if "," in s and "." in s:
            s = s.replace(".", "").replace(",", ".")
        elif "," in s:
            s = s.replace(",", ".")
        
        # Sadece sayı, nokta ve eksi kalsın
        s = re.sub(r"[^0-9.-]", "", s)
        return float(s)
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
# 3. VERİ ÇEKME MOTORU (TEFAS & KAP - İŞ YATIRIM)
# ============================================================

def _fetch_html_tefas(fund_code: str):
    """TEFAS Ana Sayfasından Fiyat ve Getiri Verisi"""
    print(f"🌐 TEFAS HTML deniyorum: {fund_code}")
    url = f"https://www.tefas.gov.tr/FonAnaliz.aspx?FonKod={fund_code.upper()}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    
    try:
        r = requests.get(url, headers=headers, timeout=10, verify=False)
        if r.status_code == 200:
            price, daily, yearly = 0.0, 0.0, 0.0
            
            # Fiyat
            m = re.search(r"Son Fiyat.*?<span>([\d,\.]+)</span>", r.text, re.DOTALL)
            if m: price = _parse_turkish_float(m.group(1))
            
            # Günlük
            m = re.search(r"Günlük Getiri.*?<span>(.*?)</span>", r.text, re.DOTALL)
            if m: daily = _parse_turkish_float(m.group(1))
            
            # Yıllık
            m = re.search(r"Son 1 Yıl.*?<span>(.*?)</span>", r.text, re.DOTALL)
            if m: yearly = _parse_turkish_float(m.group(1))
            
            if price > 0:
                return {"price": price, "daily_pct": daily, "yearly_pct": yearly, "source": "HTML"}
    except Exception as e:
        print(f"❌ TEFAS HTML Hata: {e}")
    return None

def _fetch_api_tefas(fund_code: str):
    """TEFAS API Yedek (Fiyat için)"""
    url = "https://www.tefas.gov.tr/api/DB/BindHistoryInfo"
    try:
        end = datetime.now()
        start = end - timedelta(days=7)
        payload = {
            "fontip": "YAT",
            "fonkod": fund_code.upper(),
            "bastarih": start.strftime("%d.%m.%Y"),
            "bittarih": end.strftime("%d.%m.%Y"),
        }
        r = requests.post(url, data=payload, timeout=10, verify=False)
        if r.status_code == 200:
            data = r.json().get("data", [])
            if data:
                # En son tarihi bul
                valid = []
                for i in data:
                    # Tarih parse (Unix ms timestamp geliyor genelde)
                    ts = i.get("TARIH", 0)
                    if ts: valid.append(i)
                
                if valid:
                    # Tarihe göre sırala (en büyük tarih en başa)
                    # "TARIH" alanı genellikle timestamp (long) gelir.
                    valid.sort(key=lambda x: x.get("TARIH", 0), reverse=True)
                    
                    last = valid[0] 
                    price = _parse_turkish_float(last.get("FIYAT", 0))
                    if price > 0:
                        return {"price": price, "daily_pct": None, "yearly_pct": 0.0, "source": "API", "asof_day": datetime.fromtimestamp(last.get("TARIH", 0)/1000).strftime("%Y-%m-%d")}
    except:
        pass
    return None

def fetch_fund_live(fund_code: str):
    html = _fetch_html_tefas(fund_code)
    if html: return html
    api = _fetch_api_tefas(fund_code)
    if api: return api
    return None

# ============================================================
# 🔥 YENİ: KAP (İŞ YATIRIM) & TEFAS (PASTA) SCRAPER
# ============================================================

def _fetch_tefas_allocation(fund_code: str) -> Optional[List[Dict[str, Any]]]:
    """TEFAS'tan Varlık Dağılımını (Pasta Grafik) çeker"""
    print(f"🥧 TEFAS Allocation deniyorum: {fund_code}")
    url = f"https://www.tefas.gov.tr/FonAnaliz.aspx?FonKod={fund_code.upper()}"
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        r = requests.get(url, headers=headers, timeout=10, verify=False)
        if r.status_code == 200:
            # Regex ile Highcharts verisini bul: data: [['Hisse', 20], ['Mevduat', 80]]
            match = re.search(r"data:\s*(\[\[.*?\]\])", r.text)
            if match:
                raw = match.group(1).replace("'", '"')
                try:
                    data = json.loads(raw)
                    # [["Hisse", 20], ["Mevduat", 80]] -> [{"name":"Hisse","value":20}, ...]
                    return [{"name": i[0], "value": float(i[1])} for i in data if len(i) == 2 and float(i[1]) > 0]
                except:
                    pass
    except Exception as e:
        print(f"❌ TEFAS Allocation Hatası: {e}")
    
    return None

def _fetch_kap_portfolio_from_isyatirim(fund_code: str) -> Optional[Dict[str, Any]]:
    """
    İş Yatırım Fon Detay Sayfasından KAP Verilerini Çeker (Resmi Kaynak Scraper)
    """
    print(f"🏛️ İş Yatırım (KAP) Verisi Çekiliyor: {fund_code}")
    
    url = f"https://www.isyatirim.com.tr/tr-tr/analiz/fonlar/Sayfalar/Fon-Detay.aspx?FonKodu={fund_code.upper()}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    }
    
    try:
        r = requests.get(url, headers=headers, timeout=12)
        if r.status_code != 200: return None
        
        soup = BeautifulSoup(r.text, "html.parser")
        details = {
            "positions": [],
            "info": {"risk_value": 4, "founder": ""}, # Default değerler
            "allocation": [] 
        }
        
        # 1. KURUCU BİLGİSİ (H1 Başlığından veya Breadcrumb'dan)
        # Genelde sayfa başlığı: "Fon Detay - AFT - AK PORTFÖY YENİ TEKNOLOJİLER..."
        h1 = soup.find("div", {"class": "page-title"})
        if h1:
            raw_title = h1.get_text(strip=True)
            # Koddan sonrasını al
            if fund_code.upper() in raw_title:
                parts = raw_title.split(fund_code.upper())
                if len(parts) > 1:
                    details["info"]["founder"] = parts[1].strip(" -")
        
        # 2. RİSK DEĞERİ (Tablo veya Metin İçinde Ara)
        # İş Yatırım'da "Risk Değeri" genelde bir label veya td içindedir.
        risk_elem = soup.find(string=re.compile("Risk Değeri"))
        if risk_elem:
            try:
                # Parent container'a bak
                parent = risk_elem.find_parent("tr") or risk_elem.find_parent("div")
                if parent:
                    txt = parent.get_text(strip=True)
                    match = re.search(r"Risk Değeri.*?(\d)", txt)
                    if match:
                        details["info"]["risk_value"] = int(match.group(1))
            except:
                pass

        # 3. EN BÜYÜK POZİSYONLAR (Tablo Analizi)
        # Sayfadaki tüm tabloları gez, başlığında "Menkul" veya "Kod" ve "Oran" geçen tabloyu bul.
        tables = soup.find_all("table")
        for table in tables:
            headers_text = [th.get_text(strip=True).lower() for th in table.find_all("th")]
            
            is_pos_table = (
                (any("kod" in h for h in headers_text) or any("menkul" in h for h in headers_text)) and
                (any("oran" in h for h in headers_text) or any("%" in h for h in headers_text) or any("dağılım" in h for h in headers_text))
            )
            
            if is_pos_table:
                rows = table.find_all("tr")[1:] # Header hariç
                for row in rows:
                    cols = row.find_all("td")
                    if len(cols) >= 2:
                        name_code = cols[0].get_text(strip=True)
                        ratio_str = cols[1].get_text(strip=True)
                        
                        if not ratio_str: continue

                        ratio = _parse_turkish_float(ratio_str)
                        # Temiz kod: "THYAO" veya "AKBNK Hisse" -> "THYAO"
                        clean_code = name_code.split()[0].strip().upper()
                        
                        # Filtreleme: TOPLAM, Mevduat vb. hariç, sadece anlamlı oranlar
                        if "TOPLAM" in clean_code or len(clean_code) < 3: continue
                        if ratio > 0.1: # %0.1 altını alma
                            details["positions"].append({"code": clean_code, "ratio": ratio})
                
                if details["positions"]: break # Tabloyu bulduk, döngüden çık

        print(f"✅ İş Yatırım Data: {len(details['positions'])} pozisyon, Risk: {details['info']['risk_value']}")
        return details

    except Exception as e:
        print(f"❌ İş Yatırım Scraping Error: {e}")
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
                if isinstance(data, list):
                    for item in data:
                        sym = item.get("symbol", "").replace(".IS", "")
                        chg = item.get("chgPct", 0.0)
                        prices[sym] = float(chg)
                elif isinstance(data, dict) and "data" in data:
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
    Fonun içindeki hisselerin anlık (Live) verilerine göre ağırlıklı tahmin üretir.
    """
    # 1. Klasik (Baz) Skor (Geçmiş performans)
    d_val = daily if daily is not None else 0.0
    
    direction = "NÖTR"
    confidence = 50
    
    # Baz Yön Belirleme
    if yearly > 40:
        confidence += 20
        direction = "POZİTİF"
    elif yearly < 0:
        confidence += 10
        direction = "NEGATİF"

    # 2. HİSSE BAZLI CANLI SKOR (Kritik Bölüm)
    stock_impact = 0.0
    
    if holdings:
        live_stocks = _load_live_stocks()
        if live_stocks:
            total_w = 0.0
            weighted_change = 0.0
            
            for h in holdings:
                code = h.get("code", "")
                ratio = h.get("ratio", 0.0)
                
                live_chg = live_stocks.get(code)
                
                if live_chg is not None:
                    weighted_change += (live_chg * ratio)
                    total_w += ratio
            
            # Fonun NAV üzerindeki tahmini anlık etkisi (Hisse bacağı)
            # Oranlar zaten % olarak geliyor (Örn: 5.4 -> %5.4)
            # Etki = (Hisse Değişimi * Hisse Oranı) / 100
            
            if total_weight := total_w: # Walrus operator for neatness, or just use total_w
                 pass
            
            # Basit ağırlıklı ortalama değil, portföye katkı:
            stock_impact = weighted_change / 100.0 
            
            # Yönü canlı veriye göre revize et
            if stock_impact > 0.2:
                direction = "POZİTİF"
                confidence = min(95, confidence + 20)
            elif stock_impact < -0.2:
                direction = "NEGATİF"
                confidence = min(95, confidence + 20)
            elif abs(stock_impact) < 0.1:
                # Yatay seyir
                pass

    # Final Tahmin (Hisse etkisi baskın, günlük veri destekleyici)
    # stock_impact: Tahmini % değişim (Örn: +1.5)
    estimated_return = stock_impact
    
    # Eğer hisse verisi yoksa veya çok azsa, eski usul daily'ye dön
    if not holdings or stock_impact == 0.0:
        estimated_return = d_val
        
    # Yön Text Revize
    if estimated_return > 0.15:
        direction = "POZİTİF"
    elif estimated_return < -0.15:
        direction = "NEGATİF"
    else:
        direction = "NÖTR"

    return direction, confidence, estimated_return

def get_fund_data_safe(fund_code: str):
    """
    GÜNDE 1 KEZ TEFAS + KAP ENTEGRASYONLU VERİ ÇEKER
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

    # Diskten Yükleme
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
    
    # Detay verisi var mı kontrol et
    has_details = False
    if cached and "details" in cached:
        d = cached["details"]
        if d.get("positions") or d.get("info", {}).get("risk_value"):
            has_details = True

    is_new_fund = not cached
    force_fetch = False
    
    if is_new_fund:
        force_fetch = True
    elif not has_details: 
        force_fetch = True 
    elif cached_asof != effective_day:
        force_fetch = True

    # Piyasalar kapalıysa ve eski veri varsa zorlama (Cache'i koru)
    if (not is_weekend) and before_open and not is_new_fund and has_details:
        force_fetch = False

    if not force_fetch and cached:
        return cached

    # Eğer cache yok ve fetch de kapalıysa boş dön
    if not cached and not force_fetch:
        return {"nav": 0.0, "daily_return_pct": 0.0}

    with _TEFAS_LOCK:
        # Double check inside lock
        cached = _PRICE_CACHE.get(fund_code)
        
        # Tekrar kontrol (Race condition önlemi)
        has_details_inner = False
        if cached and "details" in cached:
             if cached["details"].get("positions") or cached["details"].get("info", {}).get("risk_value"):
                 has_details_inner = True

        if cached and cached.get("asof_day") == effective_day and has_details_inner:
            return cached

        print(f"🚀 FORCE FETCH (X-RAY): {fund_code}")

        data = None
        if force_fetch:
            data = fetch_fund_live(fund_code)

        if data and data.get("price", 0) > 0:
            asof_day = (data.get("asof_day") or "").strip()
            if not asof_day:
                api_meta = _fetch_api_tefas(fund_code)
                asof_day = api_meta["asof_day"] if api_meta and "asof_day" in api_meta else effective_day

            safe_daily = data["daily_pct"] if data["daily_pct"] is not None else 0.0

            # 🔥 YENİ: DETAYLARI ÇEK (İŞ YATIRIM / KAP)
            details = _fetch_kap_portfolio_from_isyatirim(fund_code)
            
            # TEFAS'tan Allocation (Pasta Grafik) al
            allocation = _fetch_tefas_allocation(fund_code)
            
            if details:
                if allocation:
                     details["allocation"] = allocation 
            else:
                # Başarısızsa boş obje ama allocation varsa ekle
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
             # Fetch başarısız olduysa eski veriyi koru
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

        # Formül: Premium Base %50 + Holdings %40 + Daily %10
        predicted = (
            premium_base * 0.50 +
            holdings_impact * 0.40 +
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
      - "all": funds_master içindeki tüm fonlar
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

    # compute predictions
    items: List[Dict[str, Any]] = []
    by_type_acc: Dict[str, Dict[str, float]] = {}  # type -> {sum, cnt}

    for code in codes:
        rec = master.get(code, {}) if isinstance(master, dict) else {}
        fund_name = str(rec.get("name") or "")
        fund_type = str(rec.get("type") or "")

        # 📌 RAM cache yoksa Disk cache'ten oku
        info = _PRICE_CACHE.get(code)
        
        if not info:
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
