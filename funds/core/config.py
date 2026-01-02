from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = BASE_DIR / "cache"
DATA_DIR = BASE_DIR / "data"

# Opsiyonel: kullanıcı tanımlı portföy/favori kaynağı
PORTFOLIO_DEF = DATA_DIR / "portfolio_definition.json"
FAVORITES_DEF = DATA_DIR / "favorites_definition.json"
