from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


def _find_project_root() -> Path:
    """
    Bu dosyanın bulunduğu yerden yukarı doğru çıkıp 'funds_cache' klasörünü arar.
    Bulursa onun bir üstünü proje kökü kabul eder.
    """
    p = Path(__file__).resolve()
    for parent in [p.parent, *p.parents]:
        if (parent / "funds_cache").exists():
            return parent
    # fallback (en üst)
    return p.parents[len(p.parents) - 1]


BASE_DIR = _find_project_root()
FUNDS_CACHE_DIR = BASE_DIR / "funds_cache"
HISTORY_DIR = FUNDS_CACHE_DIR / "history"
FAVORITES_PATH = FUNDS_CACHE_DIR / "favorites.json"


def _load_history_items(code: str) -> List[Dict[str, Any]]:
    """
    funds_cache/history/{CODE}.json okur ve 'items' listesini döner.
    Beklenen format:
      {"asof": "...", "items": [{"date":"YYYY-MM-DD","price": 10.12}, ...]}
    """
    code = (code or "").upper().strip()
    if not code:
        return []

    path = HISTORY_DIR / f"{code}.json"
    if not path.exists():
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        items = raw.get("items", [])
        return items if isinstance(items, list) else []
    except Exception:
        return []


def _load_favorite_codes() -> List[str]:
    if not FAVORITES_PATH.exists():
        return []
    try:
        with open(FAVORITES_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        codes = raw.get("codes", [])
        if not isinstance(codes, list):
            return []
        return [str(c).upper().strip() for c in codes if str(c).strip()]
    except Exception:
        return []


def build_prediction_cache(input_data: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    """
    Prediction üretir.
    - input_data None ise favorites.json'dan codes okur
    - input_data verilirse [{code:..}, ...] içinden code toplar
    """
    if input_data is None:
        codes = _load_favorite_codes()
    else:
        codes = [str(x.get("code", "")).upper().strip() for x in input_data if isinstance(x, dict)]
        codes = [c for c in codes if c]

    # Favorites boşsa da default fallback istemiyorsan boş döndürür.
    if not codes:
        return {"status": "veri_guncelleniyor", "data": []}

    results: List[Dict[str, Any]] = []

    for code in codes:
        items = _load_history_items(code)
        if len(items) < 2:
            continue

        # fiyatları güvenli çek
        try:
            first = float(items[0]["price"])
            last = float(items[-1]["price"])
        except Exception:
            continue

        if first == 0:
            continue

        change_pct = round(((last - first) / first) * 100.0, 2)

        # son 5 gün momentum (varsa)
        mom5 = None
        if len(items) >= 6:
            try:
                p5 = float(items[-6]["price"])
                mom5 = round(((last - p5) / p5) * 100.0, 2) if p5 else None
            except Exception:
                mom5 = None

        results.append(
            {
                "code": code,
                "last_price": round(last, 6),
                "change_pct": change_pct,
                "direction": "up" if change_pct > 0 else ("down" if change_pct < 0 else "flat"),
                "days": len(items),
                "mom5": mom5,
            }
        )

    if not results:
        return {"status": "veri_guncelleniyor", "data": []}

    return {"status": "success", "data": results}
