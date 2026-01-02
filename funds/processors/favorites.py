from __future__ import annotations

from typing import Any, Dict, List
from pathlib import Path

from funds.core.io import read_json


def build_favorites_cache(def_path: Path) -> Dict[str, Any]:
    default_def = {"codes": []}
    definition = read_json(def_path, default_def)

    codes = definition.get("codes", [])
    if not isinstance(codes, list):
        codes = []

    # UI stabil kalsın
    return {
        "status": "success" if codes else "veri_guncelleniyor",
        "data": [{"code": str(x).upper()} for x in codes],
    }
