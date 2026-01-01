import requests
import json
import os
import time

# Dosya yolları
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Bir üst klasördeki data klasörünü bulalım (api klasörünün dışına çık)
PROJECT_ROOT = os.path.dirname(BASE_DIR) 
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
MASTER_PATH = os.path.join(DATA_DIR, "funds_master.json")

os.makedirs(DATA_DIR, exist_ok=True)

def fetch_and_save_all_funds():
    print("🌍 TEFAS Tüm Fon Listesi İndiriliyor (Güvenli Mod)...")
    
    # Session başlat (Cookie'leri tutar)
    session = requests.Session()
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": "https://www.tefas.gov.tr/FonKarsilastirma.aspx",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://www.tefas.gov.tr"
    }
    
    # 1. ADIM: Ana sayfaya git ve Cookie al (Handshake)
    try:
        print("🤝 TEFAS ile bağlantı kuruluyor...")
        session.get("https://www.tefas.gov.tr/FonKarsilastirma.aspx", headers=headers, timeout=10)
    except Exception as e:
        print(f"⚠️ Handshake hatası (önemsiz olabilir): {e}")

    # 2. ADIM: Listeyi çek
    url = "https://www.tefas.gov.tr/api/DB/BindComparisonFund"
    
    payload = {
        "calismatipi": "1",
        "fontip": "YAT",
        "sfontur": "",
        "kurucukod": "",
        "fongrup": "",
        "bastarih": "01.01.2024",
        "bittarih": "01.01.2024",
        "fonturkod": "",
        "fonunvantip": "",
        "strperiod": "1,1,1,1,1,1,1",
        "islemdurum": ""
    }
    
    try:
        print("📥 Veri indiriliyor...")
        response = session.post(url, data=payload, headers=headers, timeout=30)
        
        if response.status_code == 200:
            # JSON formatında olup olmadığını kontrol et
            try:
                data = response.json().get("data", [])
            except json.JSONDecodeError:
                print("❌ HATA: TEFAS JSON döndürmedi. Muhtemelen IP engellendi.")
                print(f"Sunucu Yanıtı: {response.text[:100]}...")
                return

            print(f"✅ TEFAS'tan {len(data)} adet veri çekildi.")
            
            clean_list = []
            seen_codes = set()
            
            for item in data:
                code = item.get("FONKODU")
                name = item.get("FONUNADI")
                
                if code and code not in seen_codes:
                    seen_codes.add(code)
                    clean_list.append({
                        "code": code,
                        "name": name
                    })
            
            # Alfabetik Sırala
            clean_list.sort(key=lambda x: x["code"])
            
            # Kaydet
            with open(MASTER_PATH, "w", encoding="utf-8") as f:
                json.dump(clean_list, f, ensure_ascii=False, indent=2)
                
            print(f"🎉 İŞLEM TAMAM! Toplam {len(clean_list)} fon '{MASTER_PATH}' dosyasına kaydedildi.")
            
        else:
            print(f"❌ HTTP Hata: {response.status_code}")
            
    except Exception as e:
        print(f"❌ Kritik Hata: {e}")

if __name__ == "__main__":
    fetch_and_save_all_funds()