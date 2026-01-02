from __future__ import annotations

from typing import Any, Dict, List
from pathlib import Path

from funds.core.io import read_json


def build_portfolio_cache(def_path: Path) -> Dict[str, Any]:
    """
    portfolio_definition.json yoksa bile crash yok.
    """
    default_def = {"items": []}
    definition = read_json(def_path, default_def)

    items = definition.get("items", [])
    if not isinstance(items, list):
        items = []

    # UI stabil kalsın
    return {
        "status": "success" if items else "veri_guncelleniyor",
        "data": items,
    }
