import json
import os

# 1. Bu dosyanın (scriptin) olduğu yer: .../SENTEZ_AI_TEMEL_ANALIZ_M/api
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

# 2. Bir üst klasöre çık (Proje Ana Dizini): .../SENTEZ_AI_TEMEL_ANALIZ_M
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)

# 3. Data klasörü ana dizindedir
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

DUMP_PATH = os.path.join(DATA_DIR, "tefas_dump.json")
OUTPUT_PATH = os.path.join(DATA_DIR, "funds_master.json")

def convert():
    print("🔄 Dönüştürme Başlıyor...")
    print(f"📂 Okunacak Dosya: {DUMP_PATH}")

    try:
        # 1. Ham veriyi oku
        with open(DUMP_PATH, "r", encoding="utf-8") as f:
            raw_data = json.load(f)

        # 'data' anahtarının içini al, yoksa direkt listeyi al
        items = raw_data.get("data", []) if isinstance(raw_data, dict) else raw_data

        clean_list = []
        
        # 2. Formatı Değiştir (FONKODU -> code)
        for item in items:
            code = item.get("FONKODU")
            name = item.get("FONUNVAN") # veya FONUNADI

            if code and name:
                clean_list.append({
                    "code": code,
                    "name": name
                })

        # 3. Sırala
        clean_list.sort(key=lambda x: x["code"])

        # 4. Temiz dosyayı kaydet
        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump(clean_list, f, ensure_ascii=False, indent=2)

        print(f"✅ BİTTİ! Toplam {len(clean_list)} fon başarıyla dönüştürüldü.")
        print(f"💾 Kaydedilen Yer: {OUTPUT_PATH}")

    except Exception as e:
        print(f"❌ HATA: {e}")

if __name__ == "__main__":
    convert()