# scripts/tefas_batch_scrape.py
"""
TEFAŞ Fon Verisi Çekme Scripti
TEFAŞ T+1 sistemine uygun şekilde çalışır:
- Bugün sadece dünün NAV değerleri kesinleşmiş olur
- Bugünün NAV değerleri henüz açıklanmamış olabilir
"""
from __future__ import annotations

import os
import sys
import json
import time
import random
import threading
import argparse
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============================================================
# GLOBAL KİLİT – AYNI ANDA SADECE 1 SCRAPE
# ============================================================
_BATCH_LOCK = threading.Lock()
_BATCH_RUNNING = False

# ============================================================
# YOLLAR
# ============================================================
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CACHE_DIR = os.path.join(BASE_DIR, "funds_cache")
DATA_DIR = os.path.join(BASE_DIR, "data")

FUNDS_MASTER_PATH = os.path.join(DATA_DIR, "funds_master.json")
LIVE_PRICES_PATH = os.path.join(CACHE_DIR, "live_prices.json")

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

# ============================================================
# BAN-DOSTU AYARLAR
# ============================================================
BATCH_SIZE = 30
ROW_SLEEP = (1.5, 3.0)
BATCH_SLEEP = (10, 18)

TEFAS_API_URL = "https://www.tefas.gov.tr/api/DB/BindHistoryInfo"
TEFAS_HOME = "https://www.tefas.gov.tr/"

# Daha güvenilir headers
REQ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://www.tefas.gov.tr",
    "Referer": "https://www.tefas.gov.tr/",
    "Connection": "close",
}

# ============================================================
# YARDIMCILAR
# ============================================================
def now_str() -> str:
    """Şu anki zamanı formatla"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def log(msg: str, level: str = "INFO"):
    """Renkli log çıktısı"""
    colors = {
        "INFO": "\033[94m",      # Mavi
        "SUCCESS": "\033[92m",   # Yeşil
        "WARNING": "\033[93m",   # Sarı
        "ERROR": "\033[91m",     # Kırmızı
    }
    reset = "\033[0m"
    timestamp = datetime.now().strftime("%H:%M:%S")
    color = colors.get(level, "")
    print(f"{color}[{timestamp}] [{level}] {msg}{reset}")

def _parse_float(v: Any) -> float:
    """TEFAŞ'tan gelen sayısal değerleri parse et"""
    try:
        s = str(v).strip()
        s = s.replace("−", "-")       # Unicode minus düzelt
        s = s.replace("%", "").strip()
        s = s.replace(",", ".").strip()
        if not s:
            return 0.0
        return float(s)
    except Exception:
        return 0.0

def _parse_tefas_date(v: Any) -> Optional[datetime]:
    """
    TEFAS 'TARIH' alanını parse et:
      - ms timestamp: 1766448000000 (int/float)
      - ms timestamp string: "1766448000000"
      - Türk formatı: "23.12.2025"
    """
    try:
        if v is None:
            return None

        if isinstance(v, (int, float)):
            if float(v) <= 0:
                return None
            return datetime.fromtimestamp(float(v) / 1000.0)

        s = str(v).strip()
        if s.isdigit():
            iv = int(s)
            if iv <= 0:
                return None
            return datetime.fromtimestamp(iv / 1000.0)

        # Türk tarih formatını dene
        return datetime.strptime(s, "%d.%m.%Y")
    except Exception:
        return None

def _get_previous_business_day(dt: datetime = None) -> datetime:
    """
    Bir önceki iş gününü bul
    Türkiye'de hafta sonu: Cumartesi (5), Pazar (6)
    """
    if dt is None:
        dt = datetime.now()
    
    one_day = timedelta(days=1)
    current = dt - one_day
    
    # Hafta sonlarını atla
    while current.weekday() >= 5:
        current -= one_day
    
    return current

def _load_fund_codes() -> List[str]:
    """Fon kodlarını funds_master.json'dan yükle"""
    if not os.path.exists(FUNDS_MASTER_PATH):
        log(f"Fon master dosyası bulunamadı: {FUNDS_MASTER_PATH}", "WARNING")
        return []
    
    try:
        with open(FUNDS_MASTER_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        codes = []
        for it in data:
            c = str(it.get("code") or "").strip().upper()
            if c:
                codes.append(c)
        result = sorted(set(codes))
        log(f"Toplam {len(result)} fon kodu yüklendi", "INFO")
        return result
    except Exception as e:
        log(f"Fon kodları yüklenirken hata: {e}", "ERROR")
        return []

def _safe_json_from_response(r: requests.Response) -> Optional[dict]:
    """
    TEFAS bazen content-type yanlış döndürür.
    Bu yüzden birden fazla yöntem deneriz.
    """
    # Yöntem 1: Standart json()
    try:
        return r.json()
    except Exception:
        pass

    # Yöntem 2: Text içinden JSON bul
    try:
        txt = (r.text or "").strip()
        if not txt:
            return None

        # BOM ve boşlukları temizle
        idx = txt.find("{")
        if idx >= 0:
            txt = txt[idx:]
        return json.loads(txt)
    except Exception:
        pass
    
    return None

def _get_prices_from_rows(rows: List[dict]) -> Optional[Tuple[str, float, str, float]]:
    """
    rows içinden en güncel 2 günü bul
    
    TEFAŞ T+1 sisteminde:
    - Bugün açıklanan fiyat aslında dünkü işlemin fiyatıdır
    - Bugünün fiyatı henüz belli değil
    
    Bu fonksiyon:
    - En güncel geçerli fiyatı (T) bulur
    - Bir önceki geçerli fiyatı (T-1) bulur
    """
    by_date: Dict[str, float] = {}
    for row in rows:
        dt = _parse_tefas_date(row.get("TARIH"))
        if not dt:
            continue
        day = dt.strftime("%Y-%m-%d")
        nav = _parse_float(row.get("FIYAT"))
        if nav > 0:
            by_date[day] = nav

    if len(by_date) < 2:
        log(f"Yetersiz veri: {len(by_date)} gün bulundu", "WARNING")
        return None

    # Tarihleri sırala (en eski -> en yeni)
    days = sorted(by_date.keys())
    
    # Son iki günü al
    day_t = days[-1]      # En güncel
    day_t1 = days[-2]     # Bir önceki
    
    nav_t = by_date[day_t]
    nav_t1 = by_date[day_t1]
    
    if nav_t1 <= 0:
        log("Geçersiz NAV değeri tespit edildi", "WARNING")
        return None
    
    log(f"Veri bulundu: {day_t1} -> {day_t}", "SUCCESS")
    return (day_t, nav_t, day_t1, nav_t1)

# ============================================================
# TEK FON – FON GEÇMİŞİ ÇEK
# ============================================================
def fetch_fund_history(session: requests.Session, fund_code: str, 
                       max_retries: int = 3) -> Optional[Dict[str, Any]]:
    """
    Tek bir fonun geçmiş verilerini çek
    
    T+1 sisteminde çalışma mantığı:
    1. Bugün dünkü fiyatlar açıklanır
    2. Biz bugün dünün fiyatını çekiyoruz
    3. Günlük getiriyi hesaplarken iki gün önceki fiyatı da alırız
    """
    fund_code = fund_code.strip().upper()
    
    # T+1 sistemi: Bugün sadece dünün verisi kesinleşmiş
    # Bu yüzden end_date olarak dünü kullanmalıyız
    end = _get_previous_business_day()
    start = end - timedelta(days=35)  # Biraz daha geniş aralık
    
    payload = {
        "fontip": "YAT",
        "fonkod": fund_code,
        "bastarih": start.strftime("%d.%m.%Y"),
        "bittarih": end.strftime("%d.%m.%Y"),
    }
    
    log(f"İstek gönderiliyor: {fund_code} ({start.strftime('%d.%m.%Y')} - {end.strftime('%d.%m.%Y')})", "INFO")
    
    for attempt in range(1, max_retries + 1):
        try:
            r = session.post(
                TEFAS_API_URL, 
                data=payload, 
                headers=REQ_HEADERS, 
                timeout=30, 
                verify=False
            )
            
            if r.status_code != 200:
                log(f"HTTP {r.status_code}, tekrar denenecek...", "WARNING")
                time.sleep(1 * attempt)
                continue
            
            js = _safe_json_from_response(r)
            if not js:
                log("Boş yanıt, tekrar denenecek...", "WARNING")
                time.sleep(1 * attempt)
                continue
            
            rows = js.get("data") or []
            if not rows:
                log("Veri yok, tekrar denenecek...", "WARNING")
                time.sleep(1 * attempt)
                continue
            
            # En güncel 2 günü bul
            result = _get_prices_from_rows(rows)
            if not result:
                log("Yetersiz gün sayısı (min 2 gün gerekli)", "WARNING")
                return None
            
            day_t, nav_t, day_t1, nav_t1 = result
            
            if nav_t1 <= 0:
                log("Geçersiz önceki gün NAV", "WARNING")
                return None
            
            # Günlük getiriyi hesapla
            daily = ((nav_t - nav_t1) / nav_t1) * 100.0
            
            return {
                "nav": round(nav_t, 6),
                "daily_return_pct": round(daily, 4),
                "last_update": f"{day_t} 18:30:00",
                "daily_calc": "API_LAST2DAYS",
                "ai_prediction": {
                    "direction": "NÖTR",
                    "confidence": 50,
                    "score": 0.0,
                },
                "_debug": {
                    "current_date": day_t,
                    "previous_date": day_t1,
                    "current_nav": nav_t,
                    "previous_nav": nav_t1,
                }
            }
            
        except requests.exceptions.Timeout:
            log(f"Zaman aşımı (deneme {attempt}/{max_retries})", "WARNING")
            time.sleep(2 * attempt)
            
        except requests.exceptions.RequestException as e:
            log(f"Ağ hatası: {e}", "ERROR")
            time.sleep(2 * attempt)
            
        except Exception as e:
            log(f"Beklenmeyen hata: {e}", "ERROR")
            time.sleep(2 * attempt)
            break  # Bilinmeyen hata, tekrar deneme
    
    log(f"{fund_code} için veri alınamadı", "ERROR")
    return None

# ============================================================
# ANA BATCH FONKSİYONU
# ============================================================
def run_batch_scrape(codes: Optional[List[str]] = None, 
                     verbose: bool = False) -> Dict[str, Any]:
    """
    Belirtilen fon kodlarının verilerini çek
    
    Args:
        codes: Çekilecek fon kodları (None = tüm kodlar)
        verbose: Detaylı çıktı
    
    Returns:
        İşlem sonuçları
    """
    global _BATCH_RUNNING
    
    with _BATCH_LOCK:
        if _BATCH_RUNNING:
            log("Batch zaten çalışıyor!", "WARNING")
            return {"status": "running"}
        _BATCH_RUNNING = True
    
    start_time = time.time()
    start_timestamp = now_str()
    
    try:
        # Fon kodlarını yükle
        if codes is None or len(codes) == 0:
            all_codes = _load_fund_codes()
            if not all_codes:
                log("Fon kodu bulunamadı! --codes parametresi kullanın.", "ERROR")
                return {"status": "error", "message": "No fund codes found"}
        else:
            all_codes = [c.strip().upper() for c in codes if c.strip()]
        
        total = len(all_codes)
        processed = 0
        success_count = 0
        failed_codes = []
        results: Dict[str, Any] = {}
        
        log(f"Başlangıç: {total} fon çekilecek", "INFO")
        log(f"Tarih aralığı: Son iş günü baz alınacak (T+1 sistemi)", "INFO")
        
        # Session oluştur ve cookies al
        session = requests.Session()
        try:
            log("TEFAŞ cookies alınıyor...", "INFO")
            resp = session.get(
                TEFAS_HOME, 
                headers={"User-Agent": REQ_HEADERS["User-Agent"]}, 
                timeout=20, 
                verify=False
            )
            if resp.status_code == 200:
                log("Cookie başarıyla alındı", "SUCCESS")
            else:
                log(f"Cookie alma uyarı: HTTP {resp.status_code}", "WARNING")
        except Exception as e:
            log(f"Cookie alma hatası: {e}", "WARNING")
        
        # Batch işleme
        for i in range(0, total, BATCH_SIZE):
            batch = all_codes[i:i + BATCH_SIZE]
            batch_num = i // BATCH_SIZE + 1
            total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
            
            log(f"\n--- Batch {batch_num}/{total_batches} ({len(batch)} fon) ---", "INFO")
            
            for code in batch:
                processed += 1
                data = None
                try:
                    data = fetch_fund_history(session, code)
                    if data:
                        results[code] = data
                        success_count += 1
                        status = "✓"
                    else:
                        failed_codes.append(code)
                        status = "✗"
                except Exception as e:
                    failed_codes.append(code)
                    status = f"✗({e})"
                
                # İlerleme çıktısı
                pct = processed / total * 100.0
                elapsed = time.time() - start_time
                avg_per_fund = elapsed / processed if processed > 0 else 0
                eta_seconds = avg_per_fund * (total - processed)
                eta_str = f"{int(eta_seconds // 60)}dk {int(eta_seconds % 60)}sn"
                
                log(f"[{processed:3d}/{total}] %{pct:5.1f} | {code} {status} | Kalan: ~{eta_str}", 
                    "SUCCESS" if data else "WARNING")
                
                # Rate limiting
                time.sleep(random.uniform(*ROW_SLEEP))
            
            # Batch arası bekleme (son batch değilse)
            if i + BATCH_SIZE < total:
                wait_time = random.uniform(*BATCH_SLEEP)
                log(f"Batch arası bekleme: {wait_time:.1f}s", "INFO")
                time.sleep(wait_time)
        
        # Sonucu kaydet
        output_data = {
            "timestamp": start_timestamp,
            "count": success_count,
            "total": total,
            "failed": failed_codes,
            "data": results
        }
        
        # Atomik yazım
        tmp_path = LIVE_PRICES_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, LIVE_PRICES_PATH)
        
        elapsed_total = time.time() - start_time
        log(f"\n{'='*50}", "INFO")
        log(f"İŞLEM TAMAMLANDI", "SUCCESS")
        log(f"Başarılı: {success_count}/{total}", "SUCCESS" if success_count == total else "WARNING")
        if failed_codes:
            log(f"Başarısız: {', '.join(failed_codes[:5])}{'...' if len(failed_codes) > 5 else ''}", "WARNING")
        log(f"Toplam süre: {elapsed_total:.1f}s", "INFO")
        log(f"Çıktı: {LIVE_PRICES_PATH}", "INFO")
        log(f"{'='*50}\n", "INFO")
        
        return {
            "status": "completed",
            "count": success_count,
            "total": total,
            "failed": failed_codes,
            "elapsed_seconds": round(elapsed_total, 2)
        }
        
    except KeyboardInterrupt:
        log("Kullanıcı tarafından durduruldu", "WARNING")
        return {"status": "interrupted"}
        
    except Exception as e:
        log(f"Kritik hata: {e}", "ERROR")
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}
        
    finally:
        _BATCH_RUNNING = False

# ============================================================
# MANUEL ÇALIŞTIRMA
# ============================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="TEFAŞ fon verilerini çek",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Örnekler:
  python scripts/tefas_batch_scrape.py                    # Tüm fonları çek
  python scripts/tefas_batch_scrape.py --codes DFI,AFT    # Sadece DFI ve AFT
  python scripts/tefas_batch_scrape.py -c "DFI, AFT, YKB" # Fon kodları (boşluklu)
        """
    )
    parser.add_argument(
        "--codes", "-c",
        help="Virgüllü fon kodları: DFI,AFT,YKB",
        default=""
    )
    parser.add_argument(
        "--verbose", "-v",
        help="Detaylı çıktı",
        action="store_true"
    )
    
    args = parser.parse_args()
    
    if args.codes.strip():
        # Virgül ve/veya boşluk ile ayrılmış kodları parse et
        import re
        # Hem virgül hem boşluk ile ayır
        codes = re.split(r'[,;\s]+', args.codes.strip())
        codes = [c.strip().upper() for c in codes if c.strip()]
        log(f"Belirtilen fonlar: {', '.join(codes)}", "INFO")
        result = run_batch_scrape(codes=codes, verbose=args.verbose)
    else:
        log("Fon kodu belirtilmedi, funds_master.json'dan okunacak", "INFO")
        result = run_batch_scrape(verbose=args.verbose)
    
    # Çıkış kodu
    if result.get("status") == "completed" and result.get("count", 0) > 0:
        sys.exit(0)
    elif result.get("status") == "completed":
        sys.exit(1)  # Tamamlandı ama veri yok
    else:
        sys.exit(2)  # Hata