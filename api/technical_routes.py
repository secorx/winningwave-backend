# api/technical_routes.py
from __future__ import annotations

from fastapi import APIRouter, Query

from .technical_services import list_symbols, get_candles, ALLOWED_TF

router = APIRouter()


@router.get("/symbols")
def technical_symbols(q: str = Query(default="", description="Search by code or name")):
    items = list_symbols(q=q)
    return {"status": "success", "count": len(items), "items": items}


@router.get("/candles")
def technical_candles(
    symbol: str = Query(..., description="Whitelist symbol code, e.g. GARAN or XU100"),
    tf: str = Query("15m", description="15m|30m|1h|1d")
):
    res = get_candles(symbol=symbol, tf=tf)
    if res.get("status") != "success":
        return {"status": "error", "error": res.get("error", "unknown_error"), "message": res.get("message", "")}
    return {"status": "success", "data": res["data"], "allowed_tf": sorted(list(ALLOWED_TF))}
