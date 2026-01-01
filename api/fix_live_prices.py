import json

path = "funds_cache/live_prices.json"

with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)

for k, v in data.items():
    v["last_update"] = "2025-12-22 18:30:00"

with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print("✅ last_update toplu düzeltildi")
