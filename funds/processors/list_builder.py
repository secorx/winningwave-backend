from __future__ import annotations

from typing import Any, Dict, List
import datetime


def build_funds_list(fetch_ok: bool, raw: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Mobil crash-proof format:
    - Her zaman dict döner
    - status: success | veri_guncelleniyor
    - data: list
    """
    if not fetch_ok:
        return {"status": "veri_guncelleniyor", "data": []}

    # Şimdilik “liste yok” ise bile success dönelim (UI boş liste gösterebilir)
    # Sonraki adımda gerçek parse eklenecek.
    return {
        "status": "success",
        "data": raw or [],
        "meta": {
            "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    }
