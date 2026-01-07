# api/funds_routes.py
# FINTABLES KALİTESİNDE - KAP VAKUM MODU - %100 FIX - FULL FILE (TEK SATIR EKSİK YOK)

from __future__ import annotations

import os
import json
import time
import threading
import math
import re
import requests
import urllib3
import io
import zipfile
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus, urljoin
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from bs4 import BeautifulSoup  # HTML Parsing için

# 🔥 KRİTİK IMPORT: YFINANCE EKLENDİ
import yfinance as yf

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

from fastapi import APIRouter

# ✅ EKLENDİ: Premium AI araçları (summary için)
# Eğer bu dosya yoksa hata vermemesi için try-except bloğu eklendi
try:
    from api.premium_ai import (
        build_premium_prediction as premium_build_prediction,
        load_funds_master_map,
        read_market_snapshot,
        market_change_pct,
    )
    PREMIUM_AI_AVAILABLE = True
except ImportError:
    PREMIUM_AI_AVAILABLE = False
    # Dummy fonksiyonlar (Import hatası durumunda kodun çökmemesi için)
    def premium_build_prediction(*args, **kwargs): return {}
    def load_funds_master_map(*args, **kwargs): return {}
    def read_market_snapshot(*args, **kwargs): return {}
    def market_change_pct(*args, **kwargs): return 0.0


# ============================================================
# CACHE BASE DIR (LOCAL vs RENDER SAFE)
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

BASE_DIR = _detect_project_root()

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
# 1. AYARLAR & GLOBAL HAFIZA
# ============================================================

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

# ✅ YENİ: Fetch Tracking Path (Tekrar çekimi önlemek için)
FETCH_TRACKING_PATH = os.path.join(CACHE_DIR, "fetch_tracking.json")

# GLOBAL DEĞİŞKENLER & LOCKLAR
_PRICE_CACHE: Dict[str, Dict] = {}
_TEFAS_LOCK = threading.Lock()
_AI_CACHE: Dict[str, Dict[str, Any]] = {}
_AI_LOCK = threading.Lock()
_AI_DIRECTION_LOCK: Dict[str, Dict[str, Any]] = {}
_MASTER_MAP: Dict[str, Dict[str, Any]] = {}
_MASTER_MAP_TS: float = 0.0
_MASTER_LOCK = threading.Lock()
_MASTER_TTL_SEC = 3600
_PRED_SUMMARY_CACHE: Dict[str, Any] = {}
_PRED_SUMMARY_TS: Dict[str, float] = {}
_PRED_SUMMARY_LOCK = threading.Lock()
_PRED_SUMMARY_TTL_SEC = 15
_PORTFOLIO_UPDATE_LOCK = threading.Lock()
_LIVE_LIST_UPDATE_LOCK = threading.Lock()
_BG_STARTED = False
_BG_LOCK = threading.Lock()

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

# ✅ YENİ: FETCH TRACKING HELPER'LARI
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

# ✅ GÜNCELLENDİ: RAM CACHE İÇİNDE GÜNCEL VERİ KONTROLÜ
def _is_code_fresh(code: str, effective_day: str) -> bool:
    code = code.upper().strip()

    def check_rec(r: Dict) -> bool:
        if not r or r.get("nav", 0) <= 0:
            return False
        rec_asof = str(r.get("asof_day") or "").strip()
        if rec_asof == effective_day:
            return True
        if not rec_asof and str(r.get("last_update", "")).startswith(effective_day):
            return True
        return False

    if check_rec(_PRICE_CACHE.get(code)):
        return True

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
    out = []
    for c in codes:
        c2 = (c or "").upper().strip()
        if c2 and not _is_code_fresh(c2, effective_day):
            out.append(c2)
    return out

# ✅ YENİ: Canlı listeden fon kodlarını oku
def _get_live_list_codes() -> List[str]:
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
    prev_set = set(previous_codes)
    new_codes = [code for code in current_codes if code not in prev_set]
    return new_codes

# 📌 DÜZELTME 1: Unicode eksi işareti ve temizleme mantığı
def _parse_turkish_float(text: str) -> float:
    try:
        s = str(text).strip()
        s = s.replace("−", "-")
        s = s.replace("%", "")
        # 1.234,56 -> 1234.56
        if "," in s and "." in s:
            s = s.replace(".", "").replace(",", ".")
        elif "," in s:
            s = s.replace(",", ".")
        s = re.sub(r"[^0-9.-]", "", s)
        return float(s)
    except:
        return 0.0
    
def _detect_equity_based_from_positions(positions: Optional[List[Dict[str, Any]]]) -> bool:
    """
    Seçenek A mantığı:
    - 'positions' içinde gerçek hisse kodları (ASELS, THYAO gibi) varsa True
    - Sadece kategori/varlık sınıfı (Hisse Senedi, Mevduat, DİBS vb.) varsa False
    """
    if not positions:
        return False

    # TEFAS allocation fallback'ında gelen kategori adları vb.
    non_equity_keywords = [
        "HİSSE SENEDİ", "HISSE SENEDI", "MEVDUAT", "DİBS", "DIBS",
        "TAHVİL", "TAHVIL", "BONO", "EUROBOND", "KATILIM",
        "ALTIN", "GÜMÜŞ", "GUMUS", "DÖVİZ", "DOVIZ",
        "REPO", "FON", "KAMU", "ÖZEL", "OZEL", "KİRA", "KIRA",
        "BORÇLANMA", "BORCLANMA", "VADELİ", "VADELI", "BPP",
        "TERS REPO", "EMTİA", "EMTIA", "PAY", "HİSSE"
    ]

    for it in positions:
        code = str(it.get("code") or "").strip().upper()

        if not code:
            continue

        # Kategori gibi duranlar (TEFAS allocation'dan gelen "Hisse Senedi" vb.)
        if any(k in code for k in non_equity_keywords):
            continue

        # Çok uzun/garip stringler kategori olabilir
        if len(code) > 12:
            continue

        # Gerçek hisse kodu için basit kural: 3-6 harf/rakam, boşluk yok
        if re.fullmatch(r"[A-Z0-9]{3,6}", code):
            return True

    return False


# ✅ DÜZELTİLDİ: load_cache_to_memory
def load_cache_to_memory():
    global _PRICE_CACHE
    if not os.path.exists(LIVE_PRICES_PATH):
        _PRICE_CACHE = {}
    else:
        try:
            with open(LIVE_PRICES_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict) and "data" in raw:
                _PRICE_CACHE = raw["data"]
            else:
                _PRICE_CACHE = raw
            print(f"✅ RAM cache yüklendi: {len(_PRICE_CACHE)} fon")
        except Exception as e:
            print(f"❌ Cache yüklenedi: {e}")
            _PRICE_CACHE = {}

# ✅ ADIM 3: KAYIT FORMATI DÜZELTİLDİ
def save_memory_to_disk():
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
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception as e:
        print(f"❌ _atomic_write_json({path}): {e}")

# ✅ EKLENDİ: master map'i cacheli oku
def _get_master_map_cached() -> Dict[str, Dict[str, Any]]:
    global _MASTER_MAP, _MASTER_MAP_TS
    if not PREMIUM_AI_AVAILABLE:
        return {}

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
    print(f"🌐 TEFAS HTML deniyorum: {fund_code}")
    url = f"https://www.tefas.gov.tr/FonAnaliz.aspx?FonKod={fund_code.upper()}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    
    try:
        r = requests.get(url, headers=headers, timeout=10, verify=False)
        r.encoding = 'utf-8' # ENCODING FIX
        if r.status_code == 200:
            price, daily, yearly = 0.0, 0.0, 0.0
            
            m = re.search(r"Son Fiyat.*?<span>([\d,\.]+)</span>", r.text, re.DOTALL)
            if m: price = _parse_turkish_float(m.group(1))
            
            m = re.search(r"Günlük Getiri.*?<span>(.*?)</span>", r.text, re.DOTALL)
            if m: daily = _parse_turkish_float(m.group(1))
            
            m = re.search(r"Son 1 Yıl.*?<span>(.*?)</span>", r.text, re.DOTALL)
            if m: yearly = _parse_turkish_float(m.group(1))
            
            if price > 0:
                return {"price": price, "daily_pct": daily, "yearly_pct": yearly, "source": "HTML"}
    except Exception as e:
        print(f"❌ TEFAS HTML Hata: {e}")
    return None

def _fetch_api_tefas(fund_code: str):
    """TEFAS API Yedek"""
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
                valid = []
                for i in data:
                    ts = i.get("TARIH", 0)
                    if ts: valid.append(i)
                
                if valid:
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
        r.encoding = 'utf-8'
        if r.status_code == 200:
            match = re.search(r"data\s*:\s*(\[\[.*?\]\])", r.text, re.DOTALL)
            if match:
                raw = match.group(1).replace("'", '"')
                try:
                    data = json.loads(raw)
                    return [{"name": i[0], "value": float(i[1])} for i in data if len(i) == 2 and float(i[1]) > 0]
                except:
                    pass
    except Exception as e:
        print(f"❌ TEFAS Allocation Hatası: {e}")
    
    return None


# ============================================================
# 🔥 YENİ (ZORUNLU): FINTABLES FULL DETAILS SCRAPER (HTML + BeautifulSoup)
# ============================================================

def _fetch_fintables_full_details(fund_code: str) -> Optional[Dict[str, Any]]:
    """
    Fintables fon detay sayfasını (HTML) parse ederek aşağıdaki verileri döndürür:

    {
      "positions": [{ "code": "GARAN", "ratio": 12.3 }],
      "increased": [{ "code": "THYAO", "ratio": 3.1, "delta": 0.8 }],
      "decreased": [{ "code": "ASELS", "ratio": 2.2, "delta": -1.1 }],
      "risk_value": 1-7,
      "yearly_management_fee": "%2.90",
      "withholding_tax": "%17.5",
      "founder": "...",
      "comparison_1000tl": [
        { "label": "Fon", "value": 1093, "pct": 9.3 },
        { "label": "BIST100", "value": 1097, "pct": 9.7 }
      ]
    }

    ❗ Hata olursa None döner; sistem asla çökmez.
    """
    try:
        code = (fund_code or "").strip().upper()
        if not code:
            return None

        # Fintables URL'leri (redirect ihtimaline karşı birkaç aday)
        # Not: Fintables bazen rotaları değiştiriyor; bu yüzden çoklu deneme var.
        url_candidates = [
            f"https://fintables.com/fon/{code}",
            f"https://fintables.com/fon/{code.lower()}",
            f"https://fintables.com/tefas/fon/{code}",
            f"https://fintables.com/tefas/{code}",
        ]

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Connection": "keep-alive",
        }

        html = None
        for url in url_candidates:
            try:
                r = requests.get(url, headers=headers, timeout=12)
                if r.status_code == 200 and (r.text or "").strip():
                    html = r.text
                    break
            except Exception:
                continue

        if not html:
            return None

        soup = BeautifulSoup(html, "html.parser")

        out: Dict[str, Any] = {
            "positions": [],
            "increased": [],
            "decreased": [],
            "risk_value": None,
            "yearly_management_fee": None,
            "withholding_tax": None,
            "founder": None,
            "comparison_1000tl": [],
        }

        # ------------------------------------------------------------
        # 1) META: Kurucu / Risk / Ücret / Stopaj
        # ------------------------------------------------------------
        try:
            text_all = soup.get_text(" ", strip=True)
        except Exception:
            text_all = str(html)

        def _extract_pct(label_regex: str) -> Optional[str]:
            try:
                m = re.search(label_regex + r".{0,60}?%?\s*([0-9]{1,2}(?:[.,][0-9]{1,2})?)", text_all, re.IGNORECASE)
                if not m:
                    return None
                v = m.group(1)
                v = v.replace(",", ".")
                # format: "%2.00"
                return f"%{v}"
            except Exception:
                return None

        # Risk Değeri: 1-7
        try:
            m = re.search(r"Risk\s*Değeri.{0,40}?([1-7])", text_all, re.IGNORECASE)
            if m:
                out["risk_value"] = int(m.group(1))
        except Exception:
            pass

        # Yıllık Yönetim Ücreti
        out["yearly_management_fee"] = _extract_pct(r"Yıllık\s*Yönetim\s*Ücreti")

        # Stopaj Oranı
        out["withholding_tax"] = _extract_pct(r"Stopaj\s*(Oranı|Orani)?")

        # Kurucu
        try:
            # "Kurucu  ATLAS PORTFÖY YÖNETİMİ A.Ş." gibi
            m = re.search(r"Kurucu\s+(.{2,80}?)(?=(Yıllık\s*Yönetim\s*Ücreti|Stopaj|Risk\s*Değeri|Fon\s*Kodu|$))", text_all, re.IGNORECASE)
            if m:
                founder = m.group(1).strip(" :|-")
                # çok uzunsa kırp (yanlış capture)
                if 2 <= len(founder) <= 80:
                    out["founder"] = founder
        except Exception:
            pass

        # ------------------------------------------------------------
        # 2) TABLO PARSE: Pozisyonlar / Artırılan / Azaltılan
        # ------------------------------------------------------------
        def _looks_like_stock_code(s: str) -> bool:
            s = (s or "").strip().upper()
            if not s:
                return False
            if len(s) > 12:
                return False
            if any(k in s for k in ["TOPLAM", "DİĞER", "DIGER", "BİLİNMEYEN", "BILINMEYEN"]):
                return False
            return re.fullmatch(r"[A-Z0-9]{3,6}", s) is not None

        def _parse_rows_from_table(table) -> List[Dict[str, Any]]:
            items: List[Dict[str, Any]] = []
            try:
                rows = table.find_all("tr")
                for row in rows:
                    cols = row.find_all(["td", "th"])
                    if len(cols) < 2:
                        continue

                    c0 = cols[0].get_text(" ", strip=True)
                    c1 = cols[1].get_text(" ", strip=True)

                    # code temizle
                    code_txt = (c0 or "").strip().upper()
                    # icon/extra metinleri temizleme (parantez vs)
                    if "(" in code_txt:
                        code_txt = code_txt.split("(")[0].strip()
                    code_txt = re.sub(r"[^A-Z0-9]", "", code_txt)

                    if not _looks_like_stock_code(code_txt):
                        continue

                    # satırdaki tüm sayıları yakala (oran + değişim olabilir)
                    nums = []
                    for col in cols[1:]:
                        t = col.get_text(" ", strip=True)
                        if not t:
                            continue
                        # "%51,25" gibi değerler
                        val = _parse_turkish_float(t)
                        if val != 0.0 or ("0" in str(t)):
                            nums.append(val)

                    if not nums:
                        continue

                    ratio = float(nums[0])
                    delta = float(nums[1]) if len(nums) >= 2 else None

                    item = {"code": code_txt, "ratio": ratio}
                    if delta is not None:
                        item["delta"] = delta
                    items.append(item)
            except Exception:
                return items
            return items

        # Bütün tablolardan adayları topla
        all_items: List[Dict[str, Any]] = []
        try:
            for tbl in soup.find_all("table"):
                all_items.extend(_parse_rows_from_table(tbl))
        except Exception:
            pass

        # Deduplicate (en yüksek ratio'yu tut)
        uniq: Dict[str, Dict[str, Any]] = {}
        for it in all_items:
            c = it.get("code")
            if not c:
                continue
            prev = uniq.get(c)
            if (prev is None) or (float(it.get("ratio", 0.0) or 0.0) > float(prev.get("ratio", 0.0) or 0.0)):
                uniq[c] = it

        positions = list(uniq.values())
        positions.sort(key=lambda x: float(x.get("ratio", 0.0) or 0.0), reverse=True)
        out["positions"] = positions[:20]  # güvenli limit

        # Increased / Decreased: delta varsa işaretine göre ayır
        inc: List[Dict[str, Any]] = []
        dec: List[Dict[str, Any]] = []
        for it in positions:
            d = it.get("delta")
            if d is None:
                continue
            try:
                dval = float(d)
                if dval > 0:
                    inc.append(it)
                elif dval < 0:
                    dec.append(it)
            except Exception:
                continue

        inc.sort(key=lambda x: float(x.get("delta", 0.0) or 0.0), reverse=True)
        dec.sort(key=lambda x: float(x.get("delta", 0.0) or 0.0))

        out["increased"] = inc[:10]
        out["decreased"] = dec[:10]

        # ------------------------------------------------------------
        # 3) 1000 TL Ne Oldu? (best-effort; bulunamazsa boş)
        # ------------------------------------------------------------
        try:
            # Bölüm başlığını bulup yakınındaki metni tarayalım.
            # Fintables bu veriyi bazen grafik datası olarak HTML içine gömüyor.
            idx = (html or "").lower().find("1.000")
            if idx == -1:
                idx = (html or "").lower().find("1000")
            snippet = (html or "")[idx: idx + 6000] if idx != -1 else (html or "")[:6000]

            # Örnek hedef: label + TL + % değerleri
            # Tam garanti değil, ama yakalarsa güzel.
            # label: harf/rakam, value: TL (tam sayı), pct: yüzde
            pat = re.compile(r"([A-Z0-9]{2,10})[^0-9]{0,40}([0-9]{1,3}(?:\.[0-9]{3})*)(?:\s*TL)?[^0-9%-]{0,40}%\s*([0-9]{1,2}(?:[.,][0-9]{1,2})?)", re.IGNORECASE)
            matches = pat.findall(snippet)
            tmp = []
            for (lbl, val_s, pct_s) in matches:
                try:
                    val = int(val_s.replace(".", ""))
                    pct = float(pct_s.replace(",", "."))
                    tmp.append({"label": lbl.upper(), "value": val, "pct": pct})
                except Exception:
                    continue

            # Çok fazla şey yakalanırsa en anlamlı ilk 6'yı al
            if tmp:
                out["comparison_1000tl"] = tmp[:6]
        except Exception:
            pass

        # Hiçbir şey yakalanmadıysa None döndürmek yerine boş içerik dönebiliriz.
        # Ancak KAP/TEFAS merge mantığında "None" daha net; burada minimal kontrol:
        has_any = bool(out["positions"]) or bool(out["risk_value"]) or bool(out["founder"])
        return out if has_any else None

    except Exception as e:
        print(f"❌ Fintables Scraper Error ({fund_code}): {e}")
        return None


# ============================================================
# 🔥 YENİ: KAP (RESMİ) FON BİLDİRİMLERİNDEN PORTFÖY RAPORU (EXCEL) OKUMA
# ============================================================

def _kap_find_fund_notifications_url(fund_code: str) -> Optional[str]:
    """KAP üzerinde ilgili fonun /tr/fon-bildirimleri/... sayfasını bulur.

    Strateji:
      1) KAP arama sayfasında fon kodu ile arama yap
      2) Sonuçlardan ilk /tr/fon-bildirimleri/ linkini yakala

    Bulamazsa None döner.
    """
    try:
        code = (fund_code or "").strip().upper()
        if not code:
            return None
        search_url = f"https://www.kap.org.tr/tr/arama?q={quote_plus(code)}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Connection": "keep-alive",
        }
        r = requests.get(search_url, headers=headers, timeout=12)
        r.encoding = "utf-8"
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text or "", "html.parser")
        for a in soup.find_all("a", href=True):
            href = a.get("href") or ""
            if "/tr/fon-bildirimleri/" in href:
                if f"/{code.lower()}-" in href.lower() or href.lower().endswith(f"/{code.lower()}") or f" {code} " in (a.get_text(" ", strip=True) + " "):
                    return urljoin("https://www.kap.org.tr", href)
        for a in soup.find_all("a", href=True):
            href = a.get("href") or ""
            if "/tr/fon-bildirimleri/" in href:
                return urljoin("https://www.kap.org.tr", href)
    except:
        return None
    return None



def _kap_list_portfolio_disclosures(notif_url: str, fund_code: str, max_items: int = 30) -> List[str]:
    """Fon bildirimleri sayfasından 'Portföy Dağılım Raporu' bildirim linklerini listeler.

    ✅ Kritiklik:
      - KAP'ta bazı aylarda ilgili rapor olmayabilir. Biz "en güncel bulunan" raporu seçmek isteriz.
      - Bu yüzden tarih yakalayıp (varsa) en yeni -> eski sıralarız.
      - Eğer tarih yakalanamazsa sayfadaki doğal sıra korunur.
    """
    out: List[str] = []
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Connection": "keep-alive",
        }
        r = requests.get(notif_url, headers=headers, timeout=12)
        r.encoding = "utf-8"
        if r.status_code != 200:
            return out
        soup = BeautifulSoup(r.text or "", "html.parser")

        # Row bazlı tarama (tarih + link + başlık)
        items: List[Dict[str, Any]] = []

        def _parse_date_from_text(t: str) -> Optional[datetime]:
            t = (t or "").strip()
            # dd.mm.yyyy
            mm = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", t)
            if mm:
                try:
                    return datetime(int(mm.group(3)), int(mm.group(2)), int(mm.group(1)))
                except:
                    return None
            # yyyy-mm-dd
            mm = re.search(r"(\d{4})-(\d{2})-(\d{2})", t)
            if mm:
                try:
                    return datetime(int(mm.group(1)), int(mm.group(2)), int(mm.group(3)))
                except:
                    return None
            return None

        for row in soup.find_all("tr"):
            row_txt = (row.get_text(" ", strip=True) or "").lower()
            if ("portföy" in row_txt and "dağılım" in row_txt) or ("portfoy" in row_txt and "dagilim" in row_txt):
                a = row.find("a", href=True)
                if not a:
                    continue
                href = a.get("href") or ""
                if "/tr/Bildirim/" not in href and "/tr/bildirim/" not in href:
                    continue
                full = urljoin("https://www.kap.org.tr", href)

                # Tarih yakala (satır metninden)
                dt = _parse_date_from_text(row.get_text(" ", strip=True))

                items.append({"url": full, "dt": dt})

        # Eğer tablolu yapı yakalanamadıysa, link metinlerinden ara (fallback)
        if not items:
            for a in soup.find_all("a", href=True):
                href = a.get("href") or ""
                txt2 = (a.get_text(" ", strip=True) or "").lower()
                if ("portföy" in txt2 and "dağılım" in txt2) or ("portfoy" in txt2 and "dagilim" in txt2):
                    if "/tr/Bildirim/" in href or "/tr/bildirim/" in href:
                        full = urljoin("https://www.kap.org.tr", href)
                        items.append({"url": full, "dt": None})

        # Sırala: tarih varsa yeni -> eski
        if any(it.get("dt") for it in items):
            items.sort(key=lambda x: (x.get("dt") is not None, x.get("dt") or datetime(1970, 1, 1)), reverse=True)

        seen = set()
        for it in items:
            u = it.get("url")
            if not u or u in seen:
                continue
            seen.add(u)
            out.append(u)
            if len(out) >= max_items:
                break

    except:
        return out
    return out


def _kap_download_first_excel_attachment(disclosure_url: str) -> Optional[bytes]:
    """Bir bildirim sayfasından ilk Excel ekini indirir (xlsx/xls)."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Connection": "keep-alive",
        }
        r = requests.get(disclosure_url, headers=headers, timeout=12)
        r.encoding = "utf-8"
        if r.status_code != 200:
            return None

        soup = BeautifulSoup(r.text or "", "html.parser")

        candidates: List[str] = []
        for a in soup.find_all("a", href=True):
            href = a.get("href") or ""
            text = (a.get_text(" ", strip=True) or "").lower()
            if "/api/file/download/" in href:
                full = urljoin("https://www.kap.org.tr", href)
                if ("excel" in text) or ("xlsx" in href.lower()) or ("xls" in href.lower()):
                    candidates.append(full)

        if not candidates:
            for a in soup.find_all("a", href=True):
                href = a.get("href") or ""
                if "/api/file/download/" in href:
                    candidates.append(urljoin("https://www.kap.org.tr", href))

        if not candidates:
            return None

        dl = candidates[0]
        rr = requests.get(dl, headers=headers, timeout=20)
        if rr.status_code != 200:
            return None
        if not rr.content or len(rr.content) < 2000:
            return None
        return rr.content

    except:
        return None


def _xlsx_table_rows_from_bytes(xlsx_bytes: bytes) -> List[List[str]]:
    """XLSX dosyasını (bytes) minimum bağımlılıkla satır listesine çevirir."""
    rows_out: List[List[str]] = []

    try:
        import openpyxl  # type: ignore
        from io import BytesIO
        wb = openpyxl.load_workbook(BytesIO(xlsx_bytes), data_only=True, read_only=True)
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                if row is None:
                    continue
                rows_out.append(["" if v is None else str(v).strip() for v in row])
            if rows_out:
                break
        if rows_out:
            return rows_out
    except:
        pass

    try:
        zf = zipfile.ZipFile(io.BytesIO(xlsx_bytes))
        shared_strings: List[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            ss_xml = zf.read("xl/sharedStrings.xml")
            root = ET.fromstring(ss_xml)
            ns = ""
            if root.tag.startswith("{"):
                ns = root.tag.split("}")[0] + "}"
            for si in root.findall(f"{ns}si"):
                texts = []
                for t in si.findall(f".//{ns}t"):
                    if t.text:
                        texts.append(t.text)
                shared_strings.append("".join(texts).strip())

        sheet_name = None
        for name in zf.namelist():
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"):
                sheet_name = name
                break
        if not sheet_name:
            return rows_out

        sh_xml = zf.read(sheet_name)
        root = ET.fromstring(sh_xml)
        ns = ""
        if root.tag.startswith("{"):
            ns = root.tag.split("}")[0] + "}"

        for row in root.findall(f".//{ns}row"):
            row_vals: List[str] = []
            for c in row.findall(f"{ns}c"):
                t = c.get("t")
                v = c.find(f"{ns}v")
                val = ""
                if v is not None and v.text is not None:
                    if t == "s":
                        try:
                            idx = int(v.text)
                            val = shared_strings[idx] if 0 <= idx < len(shared_strings) else ""
                        except:
                            val = ""
                    else:
                        val = v.text
                row_vals.append(str(val).strip())
            if any(x for x in row_vals):
                rows_out.append(row_vals)
    except:
        return rows_out

    return rows_out


def _parse_kap_portfolio_positions_from_xlsx(xlsx_bytes: bytes) -> List[Dict[str, Any]]:
    """KAP Portföy Dağılım Raporu excelinden hisse kodu ve portföy oranı yakalar."""
    rows = _xlsx_table_rows_from_bytes(xlsx_bytes)
    if not rows:
        return []

    header_idx = -1
    code_col = None
    ratio_col = None

    for i in range(min(60, len(rows))):
        r = rows[i]
        joined = " | ".join([str(x).lower() for x in r if x is not None])
        if any(k in joined for k in ["portföy", "portfoy"]) and any(k in joined for k in ["oran", "%"]):
            for j, cell in enumerate(r):
                c = (cell or "").lower()
                if code_col is None and any(k in c for k in ["kod", "hisse", "borsa", "menkul", "sembol", "symbol"]):
                    code_col = j
                if ratio_col is None and any(k in c for k in ["oran", "%", "pay"]):
                    ratio_col = j
            header_idx = i
            break

    if header_idx == -1:
        for i in range(min(60, len(rows))):
            r = rows[i]
            joined = " ".join([str(x).lower() for x in r])
            if ("kod" in joined or "hisse" in joined) and ("oran" in joined or "%" in joined):
                header_idx = i
                code_col = 0
                ratio_col = max(0, len(r) - 1)
                break

    if header_idx == -1:
        return []

    if code_col is None:
        code_col = 0
    if ratio_col is None:
        ratio_col = max(0, len(rows[header_idx]) - 1)

    out: List[Dict[str, Any]] = []
    blank_run = 0

    for r in rows[header_idx + 1:]:
        if not any((x or "").strip() for x in r):
            blank_run += 1
            if blank_run >= 10:
                break
            continue
        blank_run = 0

        raw_code = ""
        if code_col < len(r):
            raw_code = (r[code_col] or "").strip()

        if not raw_code:
            for cell in r:
                s = (cell or "").strip().upper()
                mm = re.search(r"\b[A-Z0-9]{3,6}\b", s)
                if mm:
                    raw_code = mm.group(0)
                    break

        raw_ratio = ""
        if ratio_col < len(r):
            raw_ratio = (r[ratio_col] or "").strip()

        if not raw_ratio:
            for cell in reversed(r):
                s = (cell or "").strip()
                if any(ch.isdigit() for ch in s) and ("%" in s or "," in s or "." in s):
                    raw_ratio = s
                    break

        if not raw_code or not raw_ratio:
            continue

        code = raw_code.upper().replace(".IS", "").replace(".E", "").strip()
        if len(code) > 10:
            continue

        ratio = _parse_turkish_float(raw_ratio)
        if ratio <= 0:
            continue

        out.append({"code": code, "ratio": float(ratio)})

    merged: Dict[str, float] = {}
    for it in out:
        c = it.get("code")
        r = float(it.get("ratio", 0.0) or 0.0)
        if not c:
            continue
        merged[c] = merged.get(c, 0.0) + r

    final = [{"code": k, "ratio": v} for k, v in merged.items()]
    final.sort(key=lambda x: x.get("ratio", 0.0), reverse=True)
    return final


def _compute_increased_decreased(curr: List[Dict[str, Any]], prev: List[Dict[str, Any]], top_n: int = 10):
    """İki portföy arasında artan/azalan pozisyonları çıkarır."""
    try:
        curr_map = {str(x.get("code") or "").upper(): float(x.get("ratio", 0.0) or 0.0) for x in (curr or [])}
        prev_map = {str(x.get("code") or "").upper(): float(x.get("ratio", 0.0) or 0.0) for x in (prev or [])}

        inc = []
        dec = []
        all_codes = set(curr_map.keys()) | set(prev_map.keys())
        for c in all_codes:
            if not c:
                continue
            d = curr_map.get(c, 0.0) - prev_map.get(c, 0.0)
            if abs(d) < 1e-9:
                continue
            item = {"code": c, "ratio": curr_map.get(c, 0.0), "delta": d}
            if d > 0:
                inc.append(item)
            else:
                dec.append(item)

        inc.sort(key=lambda x: x.get("delta", 0.0), reverse=True)
        dec.sort(key=lambda x: x.get("delta", 0.0))

        return inc[:top_n], dec[:top_n]
    except:
        return [], []


def _fetch_kap_portfolio_from_kap(fund_code: str) -> Optional[Dict[str, Any]]:
    """KAP resmi fon bildirimlerinden 'Portföy Dağılım Raporu' eklerini indirip okur."""
    print(f"🏛️ KAP (Fon Bildirimleri) Verisi Çekiliyor: {fund_code}")
    try:
        notif_url = _kap_find_fund_notifications_url(fund_code)
        if not notif_url:
            return None

        disclosures = _kap_list_portfolio_disclosures(notif_url, fund_code, max_items=40)
        if not disclosures:
            return None

        excels: List[bytes] = []
        for durl in disclosures[:12]:
            b = _kap_download_first_excel_attachment(durl)
            if b:
                excels.append(b)
            if len(excels) >= 2:
                break

        if not excels:
            return None

        current_positions = _parse_kap_portfolio_positions_from_xlsx(excels[0])
        prev_positions = _parse_kap_portfolio_positions_from_xlsx(excels[1]) if len(excels) > 1 else []

        if not current_positions:
            return None

        increased, decreased = _compute_increased_decreased(current_positions, prev_positions, top_n=10)

        details = {
            "positions": current_positions,
            "increased": increased,
            "decreased": decreased,
            "info": {"risk_value": 4, "founder": ""},
            "allocation": [],
        }
        print(f"✅ KAP Portfolio: {fund_code} positions={len(current_positions)} inc={len(increased)} dec={len(decreased)}")
        return details

    except Exception as e:
        print(f"❌ KAP Portfolio Error: {e}")
        return None

def _fetch_kap_portfolio_from_isyatirim(fund_code: str) -> Optional[Dict[str, Any]]:
    """
    İş Yatırım Fon Detay Sayfasından KAP Verilerini Çeker (Resmi Kaynak Scraper)
    CERRAH MODU: AFT gibi fon sepetleri veya karmaşık tablolar için iyileştirildi.
    """
    print(f"🏛️ İş Yatırım (KAP) Verisi Çekiliyor: {fund_code}")

    # ✅ ÖNCELİK 1: KAP resmi fon bildirimleri (Portföy Dağılım Raporu - Excel)
    try:
        kap_details = _fetch_kap_portfolio_from_kap(fund_code)
        if kap_details and kap_details.get("positions"):
            return kap_details
    except Exception as e:
        print(f"❌ KAP (fon-bildirimleri) Hata: {e}")

    
    url = f"https://www.isyatirim.com.tr/tr-tr/analiz/fonlar/Sayfalar/Fon-Detay.aspx?FonKodu={fund_code.upper()}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    }
    
    try:
        r = requests.get(url, headers=headers, timeout=12)
        r.encoding = 'utf-8' # Encoding fix
        if r.status_code != 200: return None
        
        soup = BeautifulSoup(r.text, "html.parser")
        details = {
            "positions": [],
            "increased": [], # Flutter null check hatası vermesin diye boş liste
            "decreased": [], # Flutter null check hatası vermesin diye boş liste
            "info": {"risk_value": 4, "founder": ""},
            "allocation": [] 
        }
        
        # 1. KURUCU BİLGİSİ
        h1 = soup.find("div", {"class": "page-title"})
        if h1:
            raw_title = h1.get_text(strip=True)
            if fund_code.upper() in raw_title:
                parts = raw_title.split(fund_code.upper())
                if len(parts) > 1:
                    details["info"]["founder"] = parts[1].strip(" -")
        
        # 2. RİSK DEĞERİ
        risk_elem = soup.find(string=re.compile("Risk Değeri"))
        if risk_elem:
            try:
                parent = risk_elem.find_parent("tr") or risk_elem.find_parent("div")
                if parent:
                    txt = parent.get_text(strip=True)
                    match = re.search(r"Risk Değeri.*?(\d)", txt)
                    if match:
                        details["info"]["risk_value"] = int(match.group(1))
            except:
                pass

        # 3. EN BÜYÜK POZİSYONLAR (Gelişmiş Tablo Bulma - VAKUM MODU)
        tables = soup.find_all("table")
        candidates = [] # Olası tablolar

        for table in tables:
            rows = table.find_all("tr")
            if len(rows) < 2: continue # Başlık + en az 1 veri olmalı
            
            temp_list = []
            
            for row in rows[1:]:
                cols = row.find_all("td")
                if len(cols) >= 2:
                    name_code = cols[0].get_text(strip=True)
                    ratio_str = cols[1].get_text(strip=True)
                    
                    if not ratio_str: continue

                    try:
                        ratio = _parse_turkish_float(ratio_str)
                        clean_code = name_code.strip().upper()
                        
                        if len(clean_code) > 2 and "TOPLAM" not in clean_code:
                            if "(" in clean_code:
                                clean_code = clean_code.split("(")[0].strip()
                            
                            if ratio > 0.01: # %0.01 üstü
                                temp_list.append({"code": clean_code, "ratio": ratio})
                    except:
                        continue
            
            if len(temp_list) > 0:
                candidates.append(temp_list)

        # En iyi adayı seç
        if candidates:
            candidates.sort(key=len, reverse=True)
            details["positions"] = candidates[0]
            details["positions"].sort(key=lambda x: x["ratio"], reverse=True)

        print(f"✅ İş Yatırım Data: {len(details['positions'])} pozisyon, Risk: {details['info']['risk_value']}")
        return details

    except Exception as e:
        print(f"❌ İş Yatırım Scraping Error: {e}")
        return None

# ============================================================
# 🔥 YENİ: HİSSE BAZLI AI SKORLAMA (LIVE STOCK DATA ILE)
# ============================================================
def _load_live_stocks() -> Dict[str, float]:
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
    ✅ GÜNCEL (İstenen Mantık):
      (Bilinen hisseler * canlı borsa) + (Bilinmeyen kısım * endeks)

    - holdings: [{"code":"GARAN","ratio":12.3}, ...]  -> ratio portföy ağırlığı (%)
    - canlı hisse verisi: api/data/live_prices.json (services.py'nin ürettiği)
    - endeks: market_cache.json içindeki BIST100 değişimi (yoksa fallback)
    """
    # 1) Baz skor / varsayılanlar
    d_val = daily if daily is not None else 0.0

    direction = "NÖTR"
    confidence = 50

    try:
        if yearly > 40:
            confidence += 20
        elif yearly < 0:
            confidence += 10
    except Exception:
        pass

    # 2) Bilinen hisse katkısı (canlı borsa)
    known_return = 0.0              # % cinsinden toplam katkı (ör: 0.18)
    known_ratio_sum = 0.0           # % (ör: 65.4)
    matched_cnt = 0

    live_stocks = _load_live_stocks() if holdings else {}

    if holdings and live_stocks:
        for h in holdings:
            try:
                code = (h.get("code") or "").strip().upper()
                ratio = float(h.get("ratio", 0.0) or 0.0)

                if ratio <= 0:
                    continue

                clean_code = code.replace(".E", "").replace(".IS", "").strip()

                # canlı fiyat JSON'unda genelde GARAN / THYAO gibi gelir
                live_chg = live_stocks.get(clean_code)
                if live_chg is None:
                    live_chg = live_stocks.get(clean_code + ".IS")

                if live_chg is None:
                    continue

                live_chg = float(live_chg)

                # katkı: ratio% * live_chg% / 100  -> sonuç yüzde puan (0.18 gibi)
                known_return += (ratio * live_chg) / 100.0
                known_ratio_sum += ratio
                matched_cnt += 1
            except Exception:
                continue

    # 3) Bilinmeyen kısım (endeks)
    # BIST100 yoksa BIST30 -> yoksa 0
    index_pct = 0.0
    try:
        index_pct = float(_get_market_change_pct("BIST100") or 0.0)
        if index_pct == 0.0:
            index_pct = float(_get_market_change_pct("BIST30") or 0.0)
    except Exception:
        index_pct = 0.0

    unknown_ratio = max(0.0, 100.0 - known_ratio_sum)
    unknown_return = (unknown_ratio * index_pct) / 100.0

    estimated_return = known_return + unknown_return

    # 4) Fallback (endeks yoksa veya hiç eşleşme yoksa)
    # - Endeks verisi 0 gelirse, en azından TEFAS günlük getiriyi referans al (mevcut akışı bozma)
    if (index_pct == 0.0) and (estimated_return == 0.0):
        estimated_return = d_val

    # 5) Direction / Confidence
    # (eşikler UI'daki "Tahmini Etki" mantığıyla uyumlu; 0.10 = %0.10)
    if estimated_return > 0.10:
        direction = "POZİTİF"
    elif estimated_return < -0.10:
        direction = "NEGATİF"
    else:
        direction = "NÖTR"

    # Eşleşen hisse sayısı + bilinen ağırlık arttıkça güveni yükselt
    try:
        if matched_cnt >= 3 and known_ratio_sum >= 25:
            confidence = min(95, confidence + 15)
        if matched_cnt >= 6 and known_ratio_sum >= 50:
            confidence = min(95, confidence + 15)
        if abs(estimated_return) >= 0.50:
            confidence = min(95, confidence + 10)
    except Exception:
        pass

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

    if (not is_weekend) and before_open and not is_new_fund and has_details:
        force_fetch = False

    if not force_fetch and cached:
        return cached

    if not cached and not force_fetch:
        return {"nav": 0.0, "daily_return_pct": 0.0}

    with _TEFAS_LOCK:
        cached = _PRICE_CACHE.get(fund_code)
        
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
                details = {
                    "positions": [],
                    "increased": [],
                    "decreased": [],
                    "info": {},
                    "allocation": allocation if allocation else []
                }

            
            # 🔥 YENİ: FINTABLES FULL DETAILS BACKUP (KAP/İşYatırım boşsa)
            # Amaç: KAP raporu gecikirse bile kullanıcı ekranda veri görsün.
            fintables = None
            try:
                need_ft = False
                if not details:
                    need_ft = True
                else:
                    if not details.get("positions"):
                        need_ft = True
                    info_obj = details.get("info", {}) if isinstance(details.get("info"), dict) else {}
                    if (not info_obj.get("risk_value")) and (not info_obj.get("founder")):
                        need_ft = True

                if need_ft:
                    print(f"🌐 Fintables Full Details deniyorum: {fund_code}")
                    fintables = _fetch_fintables_full_details(fund_code)
            except Exception as e:
                print(f"❌ Fintables deneme hatası ({fund_code}): {e}")
                fintables = None

            if fintables:
                # LISTLER (KAP varsa KAP'ı bozma; sadece eksikleri doldur)
                if not details.get("positions") and fintables.get("positions"):
                    details["positions"] = fintables.get("positions", [])
                if not details.get("increased") and fintables.get("increased"):
                    details["increased"] = fintables.get("increased", [])
                if not details.get("decreased") and fintables.get("decreased"):
                    details["decreased"] = fintables.get("decreased", [])

                # INFO alanı (Flutter meta chip'leri buradan okuyor)
                if "info" not in details or not isinstance(details.get("info"), dict):
                    details["info"] = {}
                info_obj = details["info"]

                if not info_obj.get("risk_value") and fintables.get("risk_value"):
                    info_obj["risk_value"] = fintables.get("risk_value")
                if not info_obj.get("yearly_management_fee") and fintables.get("yearly_management_fee"):
                    info_obj["yearly_management_fee"] = fintables.get("yearly_management_fee")
                if not info_obj.get("withholding_tax") and fintables.get("withholding_tax"):
                    info_obj["withholding_tax"] = fintables.get("withholding_tax")
                if not info_obj.get("founder") and fintables.get("founder"):
                    info_obj["founder"] = fintables.get("founder")

                # 1000 TL Ne Oldu? (varsa)
                if fintables.get("comparison_1000tl"):
                    details["comparison_1000tl"] = fintables.get("comparison_1000tl", [])

# === SEÇENEK A (FINTABLES LOGIC): HİSSE BAZLI MI? ===

            # 1️⃣ Önce KAP / positions'tan bak
            is_equity = _detect_equity_based_from_positions(details.get("positions"))

            # 2️⃣ Eğer positions boşsa → MASTER MAP'ten fon türüne bak
            if not is_equity:
                master = _get_master_map_cached()
                rec = master.get(fund_code, {}) if isinstance(master, dict) else {}
                fund_type = str(rec.get("type") or "").lower()

                # Hisse senedi fonuysa ZORLA TRUE
                if "hisse" in fund_type:
                    is_equity = True


            # 2.5️⃣ Eğer master map net değilse ama TEFAS allocation içinde "Hisse/Pay" varsa equity kabul et
            # (YANLIŞ POZİTİF "Bu fon hisse bazlı değildir" yazısını engeller)
            if not is_equity:
                try:
                    alloc_list = details.get("allocation") or []
                    for a in alloc_list:
                        nm = str(a.get("name") or "").lower()
                        val = float(a.get("value") or 0.0)
                        if val > 0 and ("hisse" in nm or "pay" in nm):
                            is_equity = True
                            break
                except:
                    pass

            details["is_equity_based"] = is_equity

            # 3️⃣ NOTE MANTIĞI
            if is_equity:
                if not details.get("positions"):
                    details["note"] = (
                        "Bu ay için KAP portföy raporu yayınlanmamıştır. "
                        "Son mevcut veri gösterilir."
                    )
                else:
                    details["note"] = None
            else:
                details["note"] = (
                    "Bu fon hisse bazlı değildir. "
                    "Hisse pozisyonları gösterilmez."
                )

                

            
            # 🔥 YENİ: Eğer bu ay KAP raporu yoksa ama server cache'te önceki pozisyon varsa,
            # onu göster (Flutter cache'e ek olarak server-side güvence).
            try:
                if is_equity and not details.get("positions"):
                    prev_det = (cached or {}).get("details", {}) if cached else {}
                    if isinstance(prev_det, dict) and prev_det.get("positions"):
                        details["positions"] = prev_det.get("positions", [])
                        if not details.get("increased"):
                            details["increased"] = prev_det.get("increased", [])
                        if not details.get("decreased"):
                            details["decreased"] = prev_det.get("decreased", [])
                        # note zaten "KAP raporu yok" şeklinde set edilmiş olabilir; koruyoruz.
            except:
                pass

# 4. TEFAS BACKUP (Eğer İş Yatırım boş döndüyse ve TEFAS allocation varsa)
            # DFI gibi fonlarda KAP verisi olmayabilir, TEFAS'taki genel dağılımı kullan.
            if not details["positions"] and details.get("allocation"):
                for item in details["allocation"]:
                    details["positions"].append({
                        "code": item["name"],  # Örn: "Hisse Senedi", "Mevduat"
                        "ratio": item["value"]
                    })
                details["positions"].sort(key=lambda x: x["ratio"], reverse=True)

            

            # 🔥 YENİ: AI Hesapla (Pozisyon verisiyle)
            holdings = details.get("positions", []) if bool(details.get("is_equity_based")) else []
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

    try:
        _atomic_write_json(MARKET_CACHE_PATH, {"asof": now_str(), "items": items})
        print(f"🔄 Market Updated: {now_str()}")
    except Exception as e:
        print(f"❌ Market write error: {e}")
    return items

def _get_market_change_pct(code: str) -> float:
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
        jitter = math.sin(now_ts / 60.0) * 0.03

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

            # ✅ UI için: hisse bazlı mı? (sekme göster/gizle)
            "is_equity_based": bool((info.get("details", {}) or {}).get("is_equity_based", False)),
            "note": (info.get("details", {}) or {}).get("note"),
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
            # ✅ UI için
            "is_equity_based": bool((info.get("details", {}) or {}).get("is_equity_based", False)),
            "note": (info.get("details", {}) or {}).get("note"),
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
                # ✅ UI için
                "is_equity_based": bool((info.get("details", {}) or {}).get("is_equity_based", False)),
                "note": (info.get("details", {}) or {}).get("note"),
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
