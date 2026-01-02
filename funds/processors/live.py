from __future__ import annotations

from typing import Any, Dict, List


def build_live_cache(fetch_ok: bool, raw: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not fetch_ok:
        return {"status": "veri_guncelleniyor", "data": []}

    return {"status": "success", "data": raw or []}
