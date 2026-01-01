import json
import os

# Yollar
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR) 
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

YAT_PATH = os.path.join(DATA_DIR, "tefas_dump.json")      # Yatırım Fonları (969 Adet)
EME_PATH = os.path.join(DATA_DIR, "tefas_dump_EME.json")  # Emeklilik Fonları (300 Adet)
OUTPUT_PATH = os.path.join(DATA_DIR, "funds_master.json") # Çıktı

def merge():
    print("🔄 Birleştirme Başlıyor...")
    
    all_funds = []
    seen_codes = set()

    # 1. Yatırım Fonlarını Oku
    if os.path.exists(YAT_PATH):
        with open(YAT_PATH, "r", encoding="utf-8") as f:
            yat_data = json.load(f)
            items = yat_data.get("data", []) if isinstance(yat_data, dict) else yat_data
            
            for item in items:
                # Hem eski (data/data) hem yeni (direkt liste) formatını destekle
                code = item.get("FONKODU") or item.get("code")
                name = item.get("FONUNVAN") or item.get("name")
                
                if code and code not in seen_codes:
                    seen_codes.add(code)
                    all_funds.append({"code": code, "name": name})
            
            print(f"✅ Yatırım Fonları Eklendi: {len(items)} adet")
    else:
        print("⚠️ Yatırım fonu dosyası bulunamadı!")

    # 2. Emeklilik Fonlarını Oku
    if os.path.exists(EME_PATH):
        with open(EME_PATH, "r", encoding="utf-8") as f:
            eme_data = json.load(f)
            items = eme_data.get("data", []) if isinstance(eme_data, dict) else eme_data
            
            count = 0
            for item in items:
                code = item.get("FONKODU")
                name = item.get("FONUNVAN")
                
                if code and code not in seen_codes:
                    seen_codes.add(code)
                    all_funds.append({"code": code, "name": name})
                    count += 1
            
            print(f"✅ Emeklilik Fonları Eklendi: {count} adet")
    else:
        print("⚠️ Emeklilik fonu dosyası bulunamadı!")

    # 3. Sırala ve Kaydet
    all_funds.sort(key=lambda x: x["code"])
    
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(all_funds, f, ensure_ascii=False, indent=2)

    print("-" * 50)
    print(f"🎉 TOPLAM FON SAYISI: {len(all_funds)}")
    print(f"💾 Kaydedildi: {OUTPUT_PATH}")

if __name__ == "__main__":
    merge()