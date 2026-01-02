# funds/jobs/run_daily_funds.py

import os
import json
import random
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parents[2]
CACHE_DIR = BASE_DIR / "funds" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_LIST = [
    {"code": "AFT", "name": "Ak Portföy Fon Sepeti Fonu", "tefas": True},
    {"code": "TCD", "name": "İş Portföy Değişken Fon", "tefas": True},
]

RANGES = {
    "7D": 7,
    "1M": 30,
    "3M": 90,
    "6M": 180,
    "1Y": 365,
}


def _atomic_write(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def _safe_read(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _nonempty(path: Path) -> bool:
    try:
        return path.exists() and path.stat().st_size > 5
    except Exception:
        return False


def _ensure_base(force: bool) -> None:
    fl = CACHE_DIR / "funds_list.json"
    if force or not _nonempty(fl):
        _atomic_write(fl, DEFAULT_LIST)

    base_payloads = {
        "portfolio_cache.json": {"items": [], "currency": "TRY"},
        "favorites_cache.json": {"codes": []},
    }

    for name, payload in base_payloads.items():
        p = CACHE_DIR / name
        if force or not _nonempty(p):
            _atomic_write(p, payload)

    st = CACHE_DIR / "funds_status.json"
    if force or not _nonempty(st):
        _atomic_write(
            st,
            {
                "last_update": None,
                "source": "tefas_html_parse",
                "error_count": 0,
                "note": "Veriler gecikmeli olabilir",
                "status": "veri_guncelleniyor",
            },
        )


def _normalize_funds(raw: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if isinstance(raw, list):
        for it in raw:
            if isinstance(it, dict):
                code = str(it.get("code", "")).strip().upper()
                if code:
                    out.append(
                        {
                            "code": code,
                            "name": it.get("name", code),
                            "tefas": bool(it.get("tefas", True)),
                        }
                    )
            elif isinstance(it, str):
                code = it.strip().upper()
                if code:
                    out.append({"code": code, "name": code, "tefas": True})
    return out


def _gen_history(days: int, base: float) -> List[Dict[str, Any]]:
    pts: List[Dict[str, Any]] = []
    val = float(base)
    for i in range(days):
        val *= 1 + random.uniform(-0.01, 0.01)
        pts.append(
            {
                "date": (datetime.now() - timedelta(days=days - i)).strftime(
                    "%Y-%m-%d"
                ),
                "value": round(val, 4),
            }
        )
    return pts


def _direction(daily_ret: float) -> str:
    if daily_ret > 0.15:
        return "Pozitif"
    if daily_ret < -0.15:
        return "Negatif"
    return "Nötr"


def run() -> None:
    force = os.getenv("FUNDS_FORCE", "0") == "1"
    mode = os.getenv("FUNDS_MODE", "simulated").lower()  # simulated | placeholders

    _ensure_base(force)

    funds_raw = _safe_read(CACHE_DIR / "funds_list.json", [])
    funds = _normalize_funds(funds_raw)

    now = datetime.now()
    live_items: List[Dict[str, Any]] = []
    pred_items: List[Dict[str, Any]] = []
    error_count = 0

    for f in funds:
        code = f["code"]
        name = f["name"]

        detail_p = CACHE_DIR / f"fund_{code}_detail.json"
        port_p = CACHE_DIR / f"fund_{code}_portfolio.json"
        pred_p = CACHE_DIR / f"fund_{code}_prediction.json"

        if mode == "placeholders":
            if force or not _nonempty(detail_p):
                _atomic_write(
                    detail_p,
                    {
                        "code": code,
                        "name": name,
                        "tefas": True,
                        "category": None,
                        "founder": None,
                        "risk": None,
                        "management_fee_daily": None,
                        "management_fee_yearly": None,
                        "stopaj": None,
                        "strategy": None,
                        "note": "veri_guncelleniyor",
                    },
                )

            for rk in RANGES:
                hp = CACHE_DIR / f"fund_{code}_history_{rk}.json"
                if force or not _nonempty(hp):
                    _atomic_write(
                        hp,
                        {
                            "code": code,
                            "range": rk,
                            "asof": now.strftime("%Y-%m-%d"),
                            "points": [],
                            "note": "veri_guncelleniyor",
                        },
                    )

            if force or not _nonempty(pred_p):
                _atomic_write(
                    pred_p,
                    {
                        "code": code,
                        "date": now.strftime("%Y-%m-%d"),
                        "direction": "Nötr",
                        "predicted_return_pct": None,
                        "confidence_score": 0,
                        "features_used": [],
                        "disclaimer": "Yatırım tavsiyesi değildir",
                        "note": "veri_guncelleniyor",
                    },
                )

            if force or not _nonempty(port_p):
                _atomic_write(
                    port_p,
                    {
                        "code": code,
                        "asof": now.strftime("%Y-%m-%d"),
                        "breakdown": [],
                        "note": "veri_guncelleniyor",
                    },
                )
            continue

        base_nav = round(random.uniform(1.5, 4.5), 4)
        daily_ret = round(random.uniform(-1.2, 1.2), 2)
        conf = random.randint(40, 75)
        dir_val = _direction(daily_ret)

        if force or not _nonempty(detail_p):
            _atomic_write(
                detail_p,
                {
                    "code": code,
                    "name": name,
                    "tefas": True,
                    "category": "Fon Sepeti",
                    "founder": "Portföy Yönetim A.Ş.",
                    "risk": random.randint(2, 6),
                    "management_fee_daily": 0.01,
                    "management_fee_yearly": 2.5,
                    "stopaj": 10,
                    "strategy": "Çeşitlendirilmiş fon sepeti stratejisi",
                    "note": None,
                },
            )

        for rk, d in RANGES.items():
            hp = CACHE_DIR / f"fund_{code}_history_{rk}.json"
            if force or not _nonempty(hp):
                _atomic_write(
                    hp,
                    {
                        "code": code,
                        "range": rk,
                        "asof": now.strftime("%Y-%m-%d"),
                        "points": _gen_history(d, base_nav),
                        "note": None,
                    },
                )

        if force or not _nonempty(port_p):
            _atomic_write(
                port_p,
                {
                    "code": code,
                    "asof": now.strftime("%Y-%m-%d"),
                    "breakdown": [
                        {"name": "Hisse", "weight_pct": 55},
                        {"name": "Tahvil/Bono", "weight_pct": 25},
                        {"name": "Döviz", "weight_pct": 10},
                        {"name": "Altın", "weight_pct": 5},
                        {"name": "Diğer", "weight_pct": 5},
                    ],
                    "note": None,
                },
            )

        if force or not _nonempty(pred_p):
            _atomic_write(
                pred_p,
                {
                    "code": code,
                    "date": now.strftime("%Y-%m-%d"),
                    "direction": dir_val,
                    "predicted_return_pct": round(
                        daily_ret * random.uniform(0.7, 1.2), 2
                    ),
                    "confidence_score": conf,
                    "features_used": ["history", "volatility"],
                    "disclaimer": "Yatırım tavsiyesi değildir",
                    "note": None,
                },
            )

        live_items.append(
            {"code": code, "nav": base_nav, "daily_return_pct": daily_ret}
        )
        pred_items.append(
            {"code": code, "direction": dir_val, "confidence_score": conf}
        )

    live_p = CACHE_DIR / "live_cache.json"
    pred_cache_p = CACHE_DIR / "prediction_cache.json"

    if force or not _nonempty(live_p):
        _atomic_write(
            live_p,
            {
                "asof": now.strftime("%Y-%m-%d %H:%M:%S"),
                "items": live_items,
            },
        )

    if force or not _nonempty(pred_cache_p):
        _atomic_write(
            pred_cache_p,
            {
                "asof": now.strftime("%Y-%m-%d"),
                "items": pred_items,
            },
        )

    _atomic_write(
        CACHE_DIR / "funds_status.json",
        {
            "last_update": now.strftime("%Y-%m-%d %H:%M:%S"),
            "source": "simulated_tefas" if mode == "simulated" else "placeholders",
            "error_count": error_count,
            "note": "Veriler gecikmeli olabilir",
            "status": "partial" if mode == "simulated" else "veri_guncelleniyor",
        },
    )


if __name__ == "__main__":
    run()
