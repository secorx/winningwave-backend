from __future__ import annotations

import datetime
from typing import Any, Dict

from funds.core.config import CACHE_DIR, DATA_DIR, PORTFOLIO_DEF, FAVORITES_DEF
from funds.core.io import atomic_write_json, ensure_dir
from funds.fetchers.tefas_fetch import fetch_tefas_list, fetch_tefas_live_prices
from funds.processors.list_builder import build_funds_list
from funds.processors.portfolio import build_portfolio_cache
from funds.processors.favorites import build_favorites_cache
from funds.processors.live import build_live_cache
from funds.processors.prediction import build_prediction_cache


def _now_str() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run_funds_update(force: bool = False) -> Dict[str, Any]:
    """
    Crash-proof orchestrator:
    - Cache klasörleri yoksa oluşturur
    - Her adım try/except yerine, fonksiyonlar zaten güvenli döner
    - funds_status.json her zaman yazılır
    """
    ensure_dir(CACHE_DIR)
    ensure_dir(DATA_DIR)

    error_count = 0
    source = "tefas_html_parse"

    # 1) TEFAS list fetch
    fr_list = fetch_tefas_list()
    if not fr_list.ok:
        error_count += 1

    funds_list_obj = build_funds_list(fr_list.ok, fr_list.data)

    # 2) Portfolio/Favorites (local definition)
    portfolio_obj = build_portfolio_cache(PORTFOLIO_DEF)
    favorites_obj = build_favorites_cache(FAVORITES_DEF)

    # 3) Live (stub fetch) - sonraki adımda gerçek parse bağlanacak
    fr_live = fetch_tefas_live_prices()
    if not fr_live.ok:
        error_count += 1
    live_obj = build_live_cache(fr_live.ok, fr_live.data)

    # 4) Prediction (placeholder)
    prediction_obj = build_prediction_cache([])

    # 5) Status (zorunlu alanlar)
    status_obj = {
        "last_update": _now_str(),
        "source": source,
        "error_count": int(error_count),
        "note": "Veriler gecikmeli olabilir",
        "status": "success" if error_count == 0 else "partial",
    }

    # 6) Write caches (atomic)
    atomic_write_json(CACHE_DIR / "funds_list.json", funds_list_obj)
    atomic_write_json(CACHE_DIR / "portfolio_cache.json", portfolio_obj)
    atomic_write_json(CACHE_DIR / "favorites_cache.json", favorites_obj)
    atomic_write_json(CACHE_DIR / "live_cache.json", live_obj)
    atomic_write_json(CACHE_DIR / "prediction_cache.json", prediction_obj)
    atomic_write_json(CACHE_DIR / "funds_status.json", status_obj)

    return {"status": "success", "written": True, "error_count": error_count}
