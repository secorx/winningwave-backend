import os
import json
import time
import datetime
import math
import re
import threading
import requests
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Query, HTTPException
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup 

# ============================================================
# GLOBAL CONSTANTS & PATHS
# ============================================================
# Bu dosya api/funds_routes.py olduğu için base dir ayarlaması:
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STATE_DIR = os.path.join(BASE_DIR, "state")
DATA_DIR = os.path.join(BASE_DIR, "data")

os.makedirs(STATE_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

LIVE_PRICES_PATH = os.path.join(DATA_DIR, "live_prices.json")
MARKET_CACHE_PATH = os.path.join(DATA_DIR, "market_data.json")
PORTFOLIO_PATH = os.path.join(DATA_DIR, "portfolio.json")
FUNDS_MASTER_PATH = os.path.join(DATA_DIR, "funds_master.json")
LIVE_LIST_PATH = os.path.join(DATA_DIR, "live_list.json")

router = APIRouter()

# ============================================================
# MEMORY CACHE & LOCKS
# ============================================================
_PRICE_CACHE: Dict[str, Any] = {}
_AI_CACHE: Dict[str, Any] = {}
_AI_DIRECTION_LOCK: Dict[str, Dict] = {}
_PRED_SUMMARY_CACHE: Dict[str, Any] = {}
_PRED_SUMMARY_TS: Dict[str, float] = {}
_PRED_SUMMARY_TTL_SEC = 15.0

_TEFAS_LOCK = threading.Lock()
_AI_LOCK = threading.Lock()
_BG_LOCK = threading.Lock()
_PORTFOLIO_UPDATE_LOCK = threading.Lock()
_LIVE_LIST_UPDATE_LOCK = threading.Lock()
_PRED_SUMMARY_LOCK = threading.Lock()
_BG_STARTED = False

# ============================================================
# YARDIMCI FONKSİYONLAR
# ============================================================
def now_str():
    return datetime.datetime.now(ZoneInfo("Europe/Istanbul")).strftime("%Y-%m-%d %H:%M:%S")

def tefas_effective_date() -> str:
    """TEFAS verisinin geçerli olduğu iş günü"""
    try:
        now = datetime.datetime.now(ZoneInfo("Europe/Istanbul"))
    except:
        now = datetime.datetime.now()
    
    # Hafta sonu kontrolü
    if now.weekday() == 5: # Cumartesi -> Cuma verisi
        offset = 1
    elif now.weekday() == 6: # Pazar -> Cuma verisi
        offset = 2
    else:
        # Hafta içi: 18:30 öncesi ise dünün verisi, sonrası ise bugünün
        if now.hour < 18 or (now.hour == 18 and now.minute < 30):
            offset = 1
            if now.weekday() == 0: offset = 3 # Pazartesi sabahı -> Cuma
        else:
            offset = 0
            
    eff = now - datetime.timedelta(days=offset)
    return eff.strftime("%Y-%m-%d")

def _parse_turkish_float(text: str) -> float:
    try:
        if not text: return 0.0
        clean = text.replace('%', '').replace('.', '').replace(',', '.').strip()
        return float(clean)
    except:
        return 0.0

def save_memory_to_disk():
    """Cache'i diske yazar"""
    try:
        final_data = {"asof": now_str(), "data": _PRICE_CACHE}
        temp = LIVE_PRICES_PATH + ".tmp"
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(final_data, f, ensure_ascii=False, indent=2)
        os.replace(temp, LIVE_PRICES_PATH)
    except Exception as e:
        print(f"❌ Disk Save Error: {e}")

def load_cache_to_memory():
    """Server açılışında diskten RAM'e yükle"""
    global _PRICE_CACHE
    if os.path.exists(LIVE_PRICES_PATH):
        try:
            with open(LIVE_PRICES_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
                _PRICE_CACHE = raw.get("data", {})
        except: pass

def _get_master_map_cached():
    # Basit master okuma
    if os.path.exists(FUNDS_MASTER_PATH):
        try:
            with open(FUNDS_MASTER_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Listeyi map'e çevir {CODE: {name, type}}
                mapping = {}
                for item in data:
                    c = item.get("code")
                    if c: mapping[c] = item
                return mapping
        except: pass
    return {}

def read_market_snapshot(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except: pass
    return {}

def market_change_pct(snap, code):
    for i in snap.get("items", []):
        if i.get("code") == code:
            return i.get("change_pct", 0.0)
    return 0.0

def _get_portfolio_codes() -> List[str]:
    """Portföydeki fon kodlarını döndürür"""
    if os.path.exists(PORTFOLIO_PATH):
        try:
            with open(PORTFOLIO_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return [str(p.get("code") or "").upper().strip() for p in raw.get("positions", []) if p.get("code")]
        except: pass
    return []

def _get_live_list_codes() -> List[str]:
    """Canlı listedeki fon kodlarını döndürür"""
    if os.path.exists(LIVE_LIST_PATH):
        try:
            with open(LIVE_LIST_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return [str(i.get("code") or "").upper().strip() for i in raw.get("items", []) if i.get("code")]
        except: pass
    return []

def _get_newly_added_funds(old_codes: List[str], new_codes: List[str]) -> List[str]:
    """Eski ve yeni listeyi karşılaştırıp yeni eklenenleri döndürür"""
    old_set = set(old_codes)
    new_set = set(new_codes)
    return list(new_set - old_set)

# --- STATE FILES FOR UPDATES ---
PORTFOLIO_UPDATE_STATE_PATH = os.path.join(STATE_DIR, "portfolio_update_state.txt")
LIVE_LIST_UPDATE_STATE_PATH = os.path.join(STATE_DIR, "livelist_update_state.txt")

def _load_portfolio_update_day() -> str:
    if os.path.exists(PORTFOLIO_UPDATE_STATE_PATH):
        with open(PORTFOLIO_UPDATE_STATE_PATH, "r") as f: return f.read().strip()
    return ""

def _save_portfolio_update_day(day_str: str):
    with open(PORTFOLIO_UPDATE_STATE_PATH, "w") as f: f.write(day_str)

def _load_live_list_update_day() -> str:
    if os.path.exists(LIVE_LIST_UPDATE_STATE_PATH):
        with open(LIVE_LIST_UPDATE_STATE_PATH, "r") as f: return f.read().strip()
    return ""

def _save_live_list_update_day(day_str: str):
    with open(LIVE_LIST_UPDATE_STATE_PATH, "w") as f: f.write(day_str)

def _missing_codes_for_day(codes: List[str], effective_day: str) -> List[str]:
    missing = []
    for c in codes:
        cached = _PRICE_CACHE.get(c)
        if not cached:
            missing.append(c)
        else:
            asof = str(cached.get("asof_day") or "").strip()
            # Eger asof bos veya effective_day degilse eksiktir
            if asof != effective_day:
                missing.append(c)
    return missing

# ============================================================
# MARKET DATA FUNCTIONS (MOVED UP FOR VISIBILITY)
# ============================================================

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

def update_market_data():
    """BIST ve USD günceller"""
    try:
        import yfinance as yf
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
            # Atomik yazma
            temp = MARKET_CACHE_PATH + ".tmp"
            with open(temp, "w", encoding="utf-8") as f:
                json.dump({"asof": now_str(), "items": items}, f, ensure_ascii=False, indent=2)
            os.replace(temp, MARKET_CACHE_PATH)
            print(f"🔄 Market Updated: {now_str()}")
        except Exception as e:
            print(f"❌ Market write error: {e}")
        return items
    except ImportError:
        # yfinance yüklü değilse pas geç
        return []

def auto_market_loop():
    while True:
        update_market_data()
        time.sleep(900)

# ============================================================
# SCRAPERS (YENİ VE GÜÇLÜ)
# ============================================================

def _fetch_fintables_full_details(fund_code: str) -> Dict[str, Any]:
    """
    Fintables üzerinden detaylı fon analizi çeker.
    Risk, Kurucu, Pozisyonlar, Karşılaştırmalar.
    """
    # print(f"🕵️ FINTABLES Scrape Başlıyor: {fund_code}")
    url = f"https://fintables.com/fonlar/{fund_code.upper()}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    
    out = {
        "founder": "",
        "risk_value": 0,
        "management_fee": "",
        "stopaj": "",
        "holdings_top": [],  # En büyükler
        "holdings_inc": [],  # Artırılanlar
        "holdings_dec": [],  # Azaltılanlar
        "comparison": {}     # 1000 TL ne oldu
    }

    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return out
        
        soup = BeautifulSoup(r.text, "html.parser")
        
        # 1. SOL PANEL BİLGİLERİ (Kurucu, Risk vs)
        info_cards = soup.find_all("div", class_=re.compile(r"flex.*flex-col")) 
        
        for card in info_cards:
            txt = card.get_text(strip=True)
            if "Kurucu" in txt and not out["founder"]:
                val = txt.replace("Kurucu", "").strip()
                out["founder"] = val
            elif "Risk Değeri" in txt and out["risk_value"] == 0:
                val = re.search(r"(\d)", txt)
                if val: out["risk_value"] = int(val.group(1))
            elif "Yönetim Ücreti" in txt:
                 val = re.search(r"%([\d,]+)", txt)
                 if val: out["management_fee"] = "%" + val.group(1)
            elif "Stopaj" in txt:
                 val = re.search(r"%([\d,]+)", txt)
                 if val: out["stopaj"] = "%" + val.group(1)

        # Alternatif Başlık Tarama
        if not out["founder"]:
            h1 = soup.find("h1")
            if h1:
                parts = h1.get_text().split("-")
                if len(parts) > 1:
                    out["founder"] = parts[1].strip()

        # 2. HİSSE POZİSYONLARI
        tables = soup.find_all("table")
        for tbl in tables:
            headers_txt = tbl.get_text(strip=True).upper()
            rows = []
            tbody = tbl.find("tbody")
            if not tbody: continue
            
            for tr in tbody.find_all("tr"):
                cols = tr.find_all("td")
                if len(cols) >= 2:
                    name = cols[0].get_text(strip=True)
                    ratio_txt = cols[1].get_text(strip=True)
                    ratio = _parse_turkish_float(ratio_txt)
                    rows.append({"code": name, "ratio": ratio})
            
            # Tabloyu tanı
            parent_div = tbl.find_parent("div")
            parent_txt = parent_div.get_text(strip=True).upper() if parent_div else headers_txt

            if "BÜYÜK POZİSYONLAR" in parent_txt or ("ORAN" in headers_txt and len(rows)>0 and not out["holdings_top"]):
                 if not out["holdings_top"]: out["holdings_top"] = rows
            elif "ARTIRILAN" in parent_txt:
                out["holdings_inc"] = rows
            elif "AZALTILAN" in parent_txt:
                out["holdings_dec"] = rows

        # 3. KARŞILAŞTIRMA (1000 TL Ne Oldu)
        comp_items = soup.find_all("div", string=re.compile(r"(BIST 100|Dolar|Altın|Mevduat)"))
        for item in comp_items:
            label = item.get_text(strip=True)
            parent = item.find_parent("div")
            if parent:
                val_txt = parent.get_text(strip=True)
                match = re.search(r"%([\d,]+)", val_txt)
                if match:
                    key = "bist100" if "BIST" in label else ("usd" if "Dolar" in label else ("gold" if "Altın" in label else "deposit"))
                    out["comparison"][key] = _parse_turkish_float(match.group(1))

    except Exception as e:
        print(f"❌ Fintables Scrape Error ({fund_code}): {e}")
    
    return out

def _fetch_tefas_allocation(fund_code: str) -> List[Dict[str, Any]]:
    """TEFAS üzerinden Varlık Dağılımını çeker (Highcharts verisi)."""
    url = f"https://www.tefas.gov.tr/FonAnaliz.aspx?FonKod={fund_code.upper()}"
    headers = {"User-Agent": "Mozilla/5.0"}
    allocation = []
    
    try:
        r = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        
        scripts = soup.find_all("script")
        for script in scripts:
            if script.string and "series:" in script.string and "name: 'Varlık Dağılımı'" in script.string:
                match = re.search(r"data:\s*\[(.*?)\]", script.string, re.DOTALL)
                if match:
                    raw_data = match.group(1)
                    items = re.findall(r"\['(.*?)',\s*([\d\.]+)\]", raw_data)
                    for name, val in items:
                        allocation.append({"name": name, "value": float(val)})
                break
                
    except Exception as e:
        print(f"❌ TEFAS Allocation Error ({fund_code}): {e}")
        
    return allocation

def fetch_fund_live(fund_code: str):
    """Basit TEFAS Fiyat ve Günlük/Yıllık Getiri Çeker"""
    url = f"https://www.tefas.gov.tr/FonAnaliz.aspx?FonKod={fund_code.upper()}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=8)
        soup = BeautifulSoup(r.text, "html.parser")
        
        price = 0.0
        daily = 0.0
        yearly = 0.0
        
        lists = soup.find("ul", class_="top-list")
        if lists:
            items = lists.find_all("li")
            if len(items) >= 1:
                price = _parse_turkish_float(items[0].find("span").get_text())
            if len(items) >= 2:
                daily = _parse_turkish_float(items[1].find("span").get_text())
            if len(items) >= 3:
                yearly = _parse_turkish_float(items[2].find("span").get_text())

        asof = ""
        date_span = soup.find("span", id="MainContent_LabelDate") 
        if date_span:
            asof = date_span.get_text(strip=True)
            try:
                parts = asof.split(".")
                if len(parts) == 3: asof = f"{parts[2]}-{parts[1]}-{parts[0]}"
            except: pass
            
        return {
            "price": price,
            "daily_pct": daily,
            "yearly_pct": yearly,
            "asof_day": asof,
            "source": "HTML"
        }
    except:
        return None

def _fetch_api(fund_code: str):
    # API fallback - Basit implementasyon
    return None 

def premium_build_prediction(fund_code, fund_name, fund_type_from_master, daily_real_pct, bist_change_pct, usd_change_pct, market_asof):
    # Premium AI hesaplama (Basitleştirilmiş)
    
    score = 0.0
    fund_type = fund_type_from_master.upper()
    
    if "HISSE" in fund_type or "HİSSE" in fund_type:
        score = bist_change_pct * 0.8 + daily_real_pct * 0.2
    elif "DOLAR" in fund_type or "YABANCI" in fund_type or "EUROBOND" in fund_type:
        score = usd_change_pct * 0.8 + daily_real_pct * 0.2
    elif "ALTIN" in fund_type or "KIYMETLI" in fund_type:
        # Altın verisi olmadığı için USD'ye endeksliyoruz (yaklaşık) + daily
        score = usd_change_pct * 0.5 + daily_real_pct * 0.5
    else:
        # Mevduat vb.
        score = daily_real_pct

    direction = "NÖTR"
    if score > 0.1: direction = "POZİTİF"
    elif score < -0.1: direction = "NEGATİF"

    confidence = 50 + abs(score * 10)
    if confidence > 95: confidence = 95
    if confidence < 10: confidence = 10

    return {
        "predicted_return_pct": round(score, 2),
        "direction": direction,
        "confidence_score": int(confidence),
        "meta": {"fund_type": fund_type}
    }

# ============================================================
# AI MOTORU
# ============================================================
def calculate_ai_prediction(yearly: float, daily: float, holdings: List[Dict] = []):
    """
    Hisseleri dikkate alarak tahmin üretir.
    Mantık: (Hisse Ağırlığı * BIST Değişimi) + (Nakit * Faiz)
    """
    d_val = daily if daily is not None else 0.0
    
    direction = "NÖTR"
    confidence = 50.0
    estimated_return = 0.0
    
    # 1. Holdings (Hisseler) Analizi
    if holdings:
        # Piyasayı oku (BIST100)
        market_pct = _get_market_change_pct("BIST100")

        total_weight = 0.0
        weighted_sum = 0.0
        
        # Basitleştirilmiş: Tüm hisseler BIST100 gibi hareket ediyor varsayımı (Beta=1)
        # İleride hisse bazlı fiyat çekilerek geliştirilebilir.
        for h in holdings:
            ratio = h.get("ratio", 0.0)
            weighted_sum += (market_pct * ratio)
            total_weight += ratio
            
        if total_weight > 0:
            part1 = weighted_sum / 100.0
            # Kalan kısım (Mevduat/Repo) sabit getiri (~%45 yıllık -> %0.12 günlük)
            part2 = ((100 - total_weight) / 100.0) * 0.125 
            estimated_return = part1 + part2
            
            if estimated_return > 0.2: 
                direction = "POZİTİF"; confidence += 20
            elif estimated_return < -0.2:
                direction = "NEGATİF"; confidence += 20
        else:
            estimated_return = d_val
            if d_val > 0.1: confidence += 10
    else:
        # Hisseleri bilmiyorsak Momentum + Yıllık Performans
        estimated_return = d_val
        if yearly > 50: confidence += 15; direction = "POZİTİF"
        elif yearly < 0: confidence += 10; direction = "NEGATİF"
        
        if d_val > 0.5: direction = "POZİTİF"
        elif d_val < -0.5: direction = "NEGATİF"

    # Sınırlar
    confidence = min(95, max(10, confidence))
    
    return direction, confidence, estimated_return

def get_fund_data_safe(fund_code: str):
    """
    GÜNDE 1 KEZ: TEFAS Fiyat + Fintables Detay + TEFAS Pasta + AI
    """
    fund_code = fund_code.upper()
    effective_day = tefas_effective_date()

    cached = _PRICE_CACHE.get(fund_code)
    
    # Eğer cache boşsa diskten yüklemeyi dene (Servis yeniden başladıysa)
    if not cached and os.path.exists(LIVE_PRICES_PATH):
         try:
             with open(LIVE_PRICES_PATH, "r", encoding="utf-8") as f:
                 full_data = json.load(f).get("data", {})
                 if fund_code in full_data:
                     cached = full_data[fund_code]
                     _PRICE_CACHE[fund_code] = cached
         except: pass

    # Yenileme gerekli mi?
    needs_refresh = True
    if cached:
        cached_asof = cached.get("asof_day", "")
        has_details = "details" in cached and cached["details"].get("risk_value", 0) > 0
        
        # Bugünün verisi varsa ve detaylar tamsa yenileme
        if cached_asof == effective_day and has_details:
            needs_refresh = False

    if not needs_refresh:
        return cached

    with _TEFAS_LOCK:
        # Double check locking
        if fund_code in _PRICE_CACHE:
            c = _PRICE_CACHE[fund_code]
            if c.get("asof_day") == effective_day and "details" in c and c["details"].get("risk_value", 0) > 0:
                return c

        # FETCH ALL DATA
        # 1. Temel Fiyat (TEFAS)
        base_data = fetch_fund_live(fund_code)
        if not base_data:
            return cached if cached else {}
            
        safe_daily = base_data["daily_pct"]

        # 2. Detaylar (Fintables)
        fintables_data = _fetch_fintables_full_details(fund_code)
        
        # 3. Pasta Grafiği (TEFAS JS)
        allocation_data = _fetch_tefas_allocation(fund_code)
        
        # 4. AI Tahmin
        dir_str, conf, est_ret = calculate_ai_prediction(
            base_data["yearly_pct"], 
            safe_daily, 
            fintables_data["holdings_top"]
        )

        new_data = {
            "nav": base_data["price"],
            "daily_return_pct": safe_daily,
            "asof_day": base_data.get("asof_day") or effective_day,
            "last_update": now_str(),
            "source": base_data.get("source", "HTML"),
            
            "details": {
                "founder": fintables_data["founder"],
                "risk_value": fintables_data["risk_value"],
                "management_fee": fintables_data["management_fee"],
                "stopaj": fintables_data["stopaj"],
                "holdings_top": fintables_data["holdings_top"],
                "holdings_inc": fintables_data["holdings_inc"],
                "holdings_dec": fintables_data["holdings_dec"],
                "allocation": allocation_data,
                "comparison": fintables_data["comparison"]
            },
            
            "ai_prediction": {
                "direction": dir_str,
                "confidence": conf,
                "score": round(base_data["yearly_pct"] / 12, 2),
                "predicted_return_pct": round(est_ret, 2)
            },
        }

        _PRICE_CACHE[fund_code] = new_data
        save_memory_to_disk()
        return new_data

# ============================================================
# LIVE AI TAHMİN & SUMMARY
# ============================================================

def get_ai_prediction_live(fund_code: str, daily_real: float) -> Dict[str, Any]:
    try:
        now_tr = datetime.datetime.now(ZoneInfo("Europe/Istanbul"))
    except:
        now_tr = datetime.datetime.now()

    market_open = (
        (now_tr.hour > 9 or (now_tr.hour == 9 and now_tr.minute >= 30)) and
        (now_tr.hour < 18 or (now_tr.hour == 18 and now_tr.minute <= 10))
    )

    fund_code = fund_code.upper()
    now_ts = time.time()

    with _AI_LOCK:
        cached = _AI_CACHE.get(fund_code)
        if not market_open and cached:
            return cached

        ttl = 1 if market_open else 3600
        if cached and (now_ts - cached["_ts"]) < ttl:
            return cached

        # ARTIK TANIMLI OLDUĞU İÇİN HATA VERMEZ
        bist = _get_market_change_pct("BIST100")
        usd = _get_market_change_pct("USDTRY")

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

        jitter = math.sin(now_ts / 60.0) * 0.03
        
        try:
            dt = datetime.datetime.now(ZoneInfo("Europe/Istanbul"))
        except:
            dt = datetime.datetime.now()
        minutes = dt.hour * 60 + dt.minute
        session_pos = max(0.0, min(1.0, (minutes - 570) / (1090 - 570)))
        drift = 0.12 * (1.0 - session_pos)

        predicted = (
            premium_base * 0.70 +
            daily_real * 0.20 +
            drift * 0.07 +
            jitter
        )
        predicted = round(predicted, 2)

        prev = _AI_DIRECTION_LOCK.get(fund_code)
        raw_direction = ("POZİTİF" if predicted > 0 else "NEGATİF" if predicted < 0 else "NÖTR")
        direction = raw_direction

        if prev:
            if raw_direction != prev["direction"]:
                if abs(predicted) < 0.25:
                    direction = prev["direction"]
                else:
                    _AI_DIRECTION_LOCK[fund_code] = {"direction": raw_direction, "ts": now_ts}
            else:
                direction = prev["direction"]
        else:
            _AI_DIRECTION_LOCK[fund_code] = {"direction": raw_direction, "ts": now_ts}

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

        # RAM cache yoksa Disk cache'ten oku (persistence)
        info = _PRICE_CACHE.get(code)
        
        if not info:
            # RAM boşsa disk cache'ten oku
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
    
    # Fallback mekanizması (Liste asla boş dönmesin)
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

# ============================================================
# API ENDPOINTS
# ============================================================

@router.get("/portfolio")
def api_portfolio():
    # 09:30 sonrası otomatik portföy güncelleme
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
        if not code: continue
        qty = float(pos.get("quantity", 0) or 0)

        # Veriyi çek veya cache'den al
        info = get_fund_data_safe(code)
        
        nav = float(info.get("nav", 0.0) or 0.0)
        daily = float(info.get("daily_return_pct", 0.0) or 0.0)
        ai = get_ai_prediction_live(code, daily)
        
        result_list.append({
            "code": code,
            "quantity": qty,
            "nav": nav,
            "value": qty * nav,
            "daily_return_pct": daily,
            "risk_value": info.get("details", {}).get("risk_value", 0),
            "details": info.get("details", {}), 
            "predicted_return_pct": ai.get("predicted_return_pct", daily),
            "confidence_score": ai.get("confidence_score", 50),
            "direction": ai.get("direction", "NÖTR"),
        })

    return {"status": "success", "data": result_list}

@router.get("/detail/{code}")
def api_detail(code: str):
    info = get_fund_data_safe(code)
    if info.get("nav", 0) > 0:
        daily_real = float(info.get("daily_return_pct", 0.0) or 0.0)
        ai = get_ai_prediction_live(code.upper(), daily_real)
        return {
            "status": "success", 
            "data": {
                **info,
                "predicted_return_pct": ai.get("predicted_return_pct", daily_real),
                "confidence_score": ai.get("confidence_score", 50),
                "direction": ai.get("direction", "NÖTR"),
            }
        }
    return {"status": "error", "message": "Veri yok"}

@router.post("/portfolio/set")
def api_pset(payload: Dict[str, Any]):
    try:
        positions = payload.get("positions", [])
        
        previous_codes = _get_portfolio_codes()
        with open(PORTFOLIO_PATH, "w", encoding="utf-8") as f:
            json.dump({"asof": now_str(), "positions": positions}, f, ensure_ascii=False, indent=2)
            
        current_codes = [str(pos.get("code") or "").upper().strip() for pos in positions if pos.get("code")]
        new_funds = _get_newly_added_funds(previous_codes, current_codes)
        if new_funds:
            update_newly_added_funds(new_funds)
    except: pass
    return {"status": "success"}

@router.get("/market")
def api_market():
    data = {"items": []}
    if os.path.exists(MARKET_CACHE_PATH):
        try:
            with open(MARKET_CACHE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except: pass
    return {"status": "success", "data": {"market": data}}

@router.get("/predictions/summary")
def api_predictions_summary(scope: str = "portfolio"):
    global _PRED_SUMMARY_CACHE, _PRED_SUMMARY_TS
    scope = (scope or "portfolio").strip().lower()
    if scope not in ("portfolio", "all"):
        scope = "portfolio"

    with _PRED_SUMMARY_LOCK:
        ts = time.time()
        cached = _PRED_SUMMARY_CACHE.get(scope)
        last_ts = _PRED_SUMMARY_TS.get(scope, 0.0)
        if cached and (ts - last_ts) < _PRED_SUMMARY_TTL_SEC:
            return cached

    # ARTIK TANIMLI OLDUĞU İÇİN HATA VERMEZ
    data = _build_predictions_summary(scope=scope)

    with _PRED_SUMMARY_LOCK:
        _PRED_SUMMARY_CACHE[scope] = data
        _PRED_SUMMARY_TS[scope] = time.time()

    return data

@router.get("/list")
def api_list():
    if os.path.exists(FUNDS_MASTER_PATH):
        try:
            with open(FUNDS_MASTER_PATH, "r", encoding="utf-8") as f:
                master = json.load(f)
        except: master = []
    else: master = []
    return {"status": "success", "data": {"items": master}}

@router.get("/live-list")
def api_live_list():
    maybe_update_live_list_funds()
    if os.path.exists(LIVE_LIST_PATH):
        try:
            with open(LIVE_LIST_PATH, "r", encoding="utf-8") as f:
                raw_list = json.load(f)
        except: raw_list = {"items": []}
    else: raw_list = {"items": []}

    result_list = []
    for item in raw_list.get("items", []):
        code = (item.get("code") or "").upper().strip()
        if not code: continue
        info = get_fund_data_safe(code)
        daily_real = float(info.get("daily_return_pct", 0.0) or 0.0)
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
    try:
        items = payload.get("items", [])
        previous_codes = _get_live_list_codes()
        with open(LIVE_LIST_PATH, "w", encoding="utf-8") as f:
            json.dump({"asof": now_str(), "items": items}, f, ensure_ascii=False, indent=2)
        current_codes = [str(item.get("code") or "").upper().strip() for item in items if item.get("code")]
        new_funds = _get_newly_added_funds(previous_codes, current_codes)
        if new_funds:
            update_newly_added_funds(new_funds)
    except: pass
    return {"status": "success"}

@router.get("/admin/refresh")
def api_refresh():
    m = update_market_data()
    return {"status": "success", "message": "Piyasa Güncellendi.", "market": m}

# ============================================================
# BACKGROUND TASKS
# ============================================================
def update_newly_added_funds(fund_codes: List[str]):
    if not fund_codes: return
    print(f"🚀 Yeni eklenen fonlar güncelleniyor: {', '.join(fund_codes)}")
    for i, code in enumerate(fund_codes, 1):
        try:
            result = get_fund_data_safe(code)
        except Exception as e:
            print(f"💥 {code} güncelleme hatası: {str(e)}")
        time.sleep(0.4)

def maybe_update_portfolio_funds():
    if not _PRICE_CACHE: _save_portfolio_update_day("")
    try: now = datetime.datetime.now(ZoneInfo("Europe/Istanbul"))
    except: now = datetime.datetime.now()
    if now.hour < 9 or (now.hour == 9 and now.minute < 30): return
    today = now.strftime("%Y-%m-%d")
    effective_day = tefas_effective_date()
    run_day = effective_day
    
    with _PORTFOLIO_UPDATE_LOCK:
        if not os.path.exists(PORTFOLIO_PATH):
            _save_portfolio_update_day(run_day)
            return
        try:
            with open(PORTFOLIO_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            codes = [str(p.get("code") or "").upper().strip() for p in raw.get("positions", []) if p.get("code")]
        except: return

        missing = _missing_codes_for_day(codes, effective_day)
        last_day = _load_portfolio_update_day()
        if last_day == run_day and not missing: return
        if not missing:
            _save_portfolio_update_day(run_day)
            return
            
        print(f"🔄 Portföy auto-update: {len(missing)} fon eksik, güncellenecek.")
        for code in missing:
            try: get_fund_data_safe(code)
            except: pass
            time.sleep(0.4)
            
        if not _missing_codes_for_day(codes, effective_day):
            _save_portfolio_update_day(run_day)

def maybe_update_live_list_funds():
    if not _PRICE_CACHE: _save_live_list_update_day("")
    try: now = datetime.datetime.now(ZoneInfo("Europe/Istanbul"))
    except: now = datetime.datetime.now()
    if now.hour < 9 or (now.hour == 9 and now.minute < 30): return
    effective_day = tefas_effective_date()
    run_day = effective_day

    with _LIVE_LIST_UPDATE_LOCK:
        if not os.path.exists(LIVE_LIST_PATH):
            _save_live_list_update_day(run_day)
            return
        try:
            with open(LIVE_LIST_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            codes = [str(i.get("code") or "").upper().strip() for i in raw.get("items", []) if i.get("code")]
        except: return

        missing = _missing_codes_for_day(codes, effective_day)
        last_day = _load_live_list_update_day()
        if last_day == run_day and not missing: return
        if not missing:
            _save_live_list_update_day(run_day)
            return

        print(f"🔄 Canlı liste auto-update: {len(missing)} fon eksik.")
        for code in missing:
            try: get_fund_data_safe(code)
            except: pass
            time.sleep(0.4)

        if not _missing_codes_for_day(codes, effective_day):
            _save_live_list_update_day(run_day)

def _startup_bootstrap_updates():
    time.sleep(2)
    try: maybe_update_portfolio_funds()
    except: pass
    try: maybe_update_live_list_funds()
    except: pass

def _start_background_jobs_once():
    global _BG_STARTED
    with _BG_LOCK:
        if _BG_STARTED: return
        _BG_STARTED = True
        load_cache_to_memory()
        threading.Thread(target=auto_market_loop, daemon=True).start()
        threading.Thread(target=_startup_bootstrap_updates, daemon=True).start()

_start_background_jobs_once()
