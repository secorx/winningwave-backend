# api/premium_ai.py
from __future__ import annotations

import json
import math
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple


# ============================================================
# Premium AI (TEFAS runtime YOK)
# - Input: live_prices.json (03:00 batch), market_cache.json (15dk), funds_master.json (type)
# - Output: predicted_return_pct + confidence_score + direction
# - Market closed => prediction freeze (same day close value remains)
# ============================================================

_LOCK = threading.Lock()

# In-memory freeze cache:
# fund_code -> {"date": "YYYY-MM-DD", "prediction": {...}}
_FREEZE_CACHE: Dict[str, Dict[str, Any]] = {}

# Default BIST session (Istanbul)
_SESSION_OPEN_MIN = 9 * 60 + 40   # 09:40
_SESSION_CLOSE_MIN = 18 * 60 + 10 # 18:10


# ----------------------------
# Utilities
# ----------------------------

def _now() -> datetime:
    return datetime.now()

def _today_str(dt: Optional[datetime] = None) -> str:
    d = dt or _now()
    return d.strftime("%Y-%m-%d")

def _now_str(dt: Optional[datetime] = None) -> str:
    d = dt or _now()
    return d.strftime("%Y-%m-%d %H:%M:%S")

def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).strip()
        if not s:
            return default
        s = s.replace("%", "").replace(",", ".")
        return float(s)
    except Exception:
        return default

def _load_json(path: str, default: Any) -> Any:
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default

def _parse_asof_ts(asof: str) -> Optional[datetime]:
    # expected: "YYYY-MM-DD HH:MM:SS"
    try:
        return datetime.strptime(asof, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


# ----------------------------
# Fund type model
# ----------------------------

@dataclass(frozen=True)
class FundModelSpec:
    w_bist: float
    w_usd: float
    w_drift: float
    vol_cap: float        # max abs predicted return %
    base_error: float     # used in confidence shaping

# Core mapping for Premium model
_FUND_SPECS: Dict[str, FundModelSpec] = {
    # Most equity-like
    "HISSE":     FundModelSpec(w_bist=0.65, w_usd=0.10, w_drift=0.15, vol_cap=1.20, base_error=0.90),
    "KARMA":     FundModelSpec(w_bist=0.45, w_usd=0.20, w_drift=0.10, vol_cap=0.80, base_error=0.80),
    "BORCLANMA": FundModelSpec(w_bist=0.15, w_usd=0.05, w_drift=0.03, vol_cap=0.30, base_error=0.55),
    # FX / commodity linked
    "ALTIN":     FundModelSpec(w_bist=0.05, w_usd=0.70, w_drift=0.05, vol_cap=1.00, base_error=0.85),
    "DOVIZ":     FundModelSpec(w_bist=0.00, w_usd=0.85, w_drift=0.02, vol_cap=1.00, base_error=0.85),
    # Safe fallback
    "DIGER":     FundModelSpec(w_bist=0.30, w_usd=0.10, w_drift=0.05, vol_cap=0.50, base_error=0.75),
}

# Heuristic keyword inference (for when funds_master lacks "type")
# IMPORTANT: You should still populate funds_master.json with type for best accuracy.
_TYPE_RULES: Tuple[Tuple[str, str], ...] = (
    (r"\bALTIN\b|ALTIN FON|GOLD|KIYMETLİ|KIYMETLI", "ALTIN"),
    (r"\bDÖVİZ\b|\bDOVIZ\b|EURO|USD|DOLAR|YABANCI PARA", "DOVIZ"),
    (r"EUROBOND|DIŞ BORÇ|DIS BORC|YURTDIŞI|YURTDISI", "DOVIZ"),
    (r"PARA PİYASASI|PARA PIYASASI|KISA VADELİ|KISA VADELI|LİKİT|LIKIT", "BORCLANMA"),
    (r"BORÇLANMA|BORCLANMA|TAHVİL|TAHVIL|BONO|KİRA SERTİFİKASI|KIRA SERTIFIKASI", "BORCLANMA"),
    (r"HİSSE|HISSE|BIST|BİST|ENDEKS", "HISSE"),
    (r"KARMA|DEĞİŞKEN|DEGISKEN|FON SEPETI|FON SEPETİ|KATILIM", "KARMA"),
)

def infer_fund_type(code: str, name: str) -> str:
    hay = f"{code} {name}".upper()
    hay = hay.replace("İ", "I").replace("Ö", "O").replace("Ü", "U").replace("Ğ", "G").replace("Ş", "S").replace("Ç", "C")
    for pat, typ in _TYPE_RULES:
        if re.search(pat, hay, flags=re.IGNORECASE):
            return typ
    return "DIGER"

def normalize_fund_type(ft: Any) -> str:
    t = (str(ft or "").strip().upper() or "DIGER")
    t = t.replace("İ", "I")
    return t if t in _FUND_SPECS else "DIGER"


# ----------------------------
# Market session logic
# ----------------------------

def is_market_open(dt: Optional[datetime] = None) -> bool:
    d = dt or _now()
    if d.weekday() >= 5:  # 5=Saturday, 6=Sunday
        return False
    minutes = d.hour * 60 + d.minute
    return (_SESSION_OPEN_MIN <= minutes <= _SESSION_CLOSE_MIN)

def session_ratio(dt: Optional[datetime] = None) -> float:
    """
    0.0 at open, 1.0 at close. Outside session clamps to 0/1.
    """
    d = dt or _now()
    minutes = d.hour * 60 + d.minute
    denom = float(_SESSION_CLOSE_MIN - _SESSION_OPEN_MIN) if (_SESSION_CLOSE_MIN > _SESSION_OPEN_MIN) else 1.0
    r = (minutes - _SESSION_OPEN_MIN) / denom
    return _clamp(r, 0.0, 1.0)


# ----------------------------
# Core Premium prediction
# ----------------------------

def _direction_from_return(pct: float) -> str:
    if pct > 0:
        return "POZITIF"
    if pct < 0:
        return "NEGATIF"
    return "NOTR"

def _calc_drift(spec: FundModelSpec, sess_r: float) -> float:
    # drift fades to ~0 near close
    return spec.w_drift * (1.0 - sess_r)

def premium_predict_return_pct(
    daily_real_pct: float,
    bist_change_pct: float,
    usd_change_pct: float,
    fund_type: str,
    sess_r: float,
) -> Tuple[float, Dict[str, float]]:
    """
    Return: (predicted_return_pct, components)
    """
    ft = normalize_fund_type(fund_type)
    spec = _FUND_SPECS.get(ft, _FUND_SPECS["DIGER"])

    drift = _calc_drift(spec, sess_r)

    # Premium blend:
    # - daily_real: anchor (yesterday/last known real daily move)
    # - market factors: explainable driver
    # - drift: intraday expectation, fades to 0 into close
    raw = (daily_real_pct * 0.55) + (bist_change_pct * spec.w_bist) + (usd_change_pct * spec.w_usd) + drift

    # Volatility cap by fund type (prevents "uçan AI")
    capped = _clamp(raw, -spec.vol_cap, spec.vol_cap)

    comps = {
        "anchor_daily_real": daily_real_pct * 0.55,
        "bist_component": bist_change_pct * spec.w_bist,
        "usd_component": usd_change_pct * spec.w_usd,
        "drift_component": drift,
        "raw": raw,
        "capped": capped,
    }
    return round(capped, 2), comps


# ----------------------------
# Smarter confidence score (Premium)
# ----------------------------

def premium_confidence_score(
    *,
    predicted_pct: float,
    daily_real_pct: float,
    bist_change_pct: float,
    usd_change_pct: float,
    fund_type: str,
    sess_r: float,
    market_is_open: bool,
    market_age_sec: Optional[float],
    has_explicit_type: bool,
) -> int:
    """
    Confidence philosophy:
    - Better near close (sess_r -> 1) because drift -> 0 and fair-value stabilizes.
    - Penalize stale market data.
    - Penalize sign conflicts: predicted vs drivers vs anchor.
    - Penalize missing type (heuristic inference).
    - Type-specific base error guides ceiling/floor.
    """
    ft = normalize_fund_type(fund_type)
    spec = _FUND_SPECS.get(ft, _FUND_SPECS["DIGER"])

    # Base
    score = 55

    # Market open vs closed: if market closed, we want stable/frozen but not "overconfident"
    if not market_is_open:
        score -= 6

    # Near close -> higher confidence
    # 0.0..1.0 => +0..+14
    score += int(round(14.0 * sess_r))

    # Data freshness penalty
    if market_age_sec is None:
        score -= 10
    else:
        # <= 10 min ok, then gradually penalize
        if market_age_sec <= 600:
            score += 3
        elif market_age_sec <= 1800:
            score -= 2
        else:
            # every extra 30 min -> -4, capped
            extra = max(0.0, (market_age_sec - 1800) / 1800.0)
            score -= int(min(18, round(extra * 4)))

    # Type known vs inferred
    if not has_explicit_type:
        score -= 6  # heuristic is decent but not perfect

    # Sign consistency checks (lightweight but effective)
    # Compare signs of predicted with main driver direction
    def sgn(x: float) -> int:
        return 1 if x > 0 else (-1 if x < 0 else 0)

    sp = sgn(predicted_pct)
    sd = sgn(daily_real_pct)
    sb = sgn(bist_change_pct)
    su = sgn(usd_change_pct)

    # For ALTIN/DOVIZ, USD is a major driver; for HISSE/KARMA, BIST is a major driver.
    if ft in ("ALTIN", "DOVIZ"):
        # if predicted contradicts USD sign strongly, penalize
        if su != 0 and sp != 0 and sp != su:
            score -= 8
    else:
        if sb != 0 and sp != 0 and sp != sb:
            score -= 6

    # If predicted contradicts anchor daily_real strongly, small penalty
    if sd != 0 and sp != 0 and sp != sd:
        score -= 4

    # Magnitude realism: if prediction is near cap, reduce confidence slightly
    cap = spec.vol_cap
    if cap > 0 and abs(predicted_pct) >= 0.9 * cap:
        score -= 4

    # Type-based tightening (lower-error types can have slightly higher ceiling)
    # Use base_error to shape a soft ceiling
    # base_error ~ 0.55..0.90 => ceiling ~ 88..82 (lower base_error -> higher ceiling)
    ceiling = int(round(92 - (spec.base_error * 10)))
    ceiling = _clamp(ceiling, 78, 92)

    # Floor
    floor = 10

    return int(_clamp(score, floor, ceiling))


# ----------------------------
# Public API (integration point)
# ----------------------------

def build_premium_prediction(
    *,
    fund_code: str,
    fund_name: str,
    fund_type_from_master: Optional[str],
    daily_real_pct: float,
    bist_change_pct: float,
    usd_change_pct: float,
    market_asof: Optional[str],
    now_dt: Optional[datetime] = None,
    freeze_when_closed: bool = True,
) -> Dict[str, Any]:
    """
    Returns:
      {
        "predicted_return_pct": float,
        "direction": str,
        "confidence_score": int,
        "asof": str,
        "model": "premium_v1",
        "frozen": bool,
        "components": {...},
        "meta": {...}
      }
    """
    dt = now_dt or _now()
    today = _today_str(dt)

    # Fund type: master > heuristic
    has_explicit_type = bool(fund_type_from_master and str(fund_type_from_master).strip())
    ft = normalize_fund_type(fund_type_from_master) if has_explicit_type else infer_fund_type(fund_code, fund_name)

    m_open = is_market_open(dt)
    sess_r = session_ratio(dt)

    # market freshness
    market_age_sec: Optional[float] = None
    if market_asof:
        ts = _parse_asof_ts(market_asof)
        if ts:
            market_age_sec = (dt - ts).total_seconds()

    # Freeze logic:
    # - if closed: return stored frozen prediction for today if exists
    # - else: compute and update freeze cache continuously (last computed becomes "close value" effectively)
    frozen = False
    key = fund_code.upper().strip()

    with _LOCK:
        if freeze_when_closed and not m_open:
            cached = _FREEZE_CACHE.get(key)
            if cached and cached.get("date") == today and isinstance(cached.get("prediction"), dict):
                out = dict(cached["prediction"])
                out["frozen"] = True
                return out
            # If no cache, compute with sess_r=1.0 (close-like, drift ~0)
            sess_r = 1.0
            frozen = True

    predicted, comps = premium_predict_return_pct(
        daily_real_pct=daily_real_pct,
        bist_change_pct=bist_change_pct,
        usd_change_pct=usd_change_pct,
        fund_type=ft,
        sess_r=sess_r,
    )

    conf = premium_confidence_score(
        predicted_pct=predicted,
        daily_real_pct=daily_real_pct,
        bist_change_pct=bist_change_pct,
        usd_change_pct=usd_change_pct,
        fund_type=ft,
        sess_r=sess_r,
        market_is_open=m_open,
        market_age_sec=market_age_sec,
        has_explicit_type=has_explicit_type,
    )

    out = {
        "predicted_return_pct": predicted,
        "direction": _direction_from_return(predicted),
        "confidence_score": conf,
        "asof": _now_str(dt),
        "model": "premium_v1",
        "frozen": frozen,
        "components": comps,
        "meta": {
            "fund_type": ft,
            "market_open": m_open,
            "session_ratio": round(sess_r, 4),
            "market_asof": market_asof or "",
            "market_age_sec": None if market_age_sec is None else int(market_age_sec),
        },
    }

    # Update freeze cache when market is open, or when we computed a close-like value
    with _LOCK:
        _FREEZE_CACHE[key] = {"date": today, "prediction": out}

    return out


# ----------------------------
# Optional helpers (loading master & market cache)
# ----------------------------

def load_funds_master_map(funds_master_path: str) -> Dict[str, Dict[str, Any]]:
    """
    returns: code -> {"name":..., "type":...}
    """
    raw = _load_json(funds_master_path, default=[])
    out: Dict[str, Dict[str, Any]] = {}
    if isinstance(raw, list):
        for it in raw:
            if not isinstance(it, dict):
                continue
            code = str(it.get("code") or "").strip().upper()
            if not code:
                continue
            out[code] = {
                "name": str(it.get("name") or "").strip(),
                "type": str(it.get("type") or "").strip(),
            }
    return out

def read_market_snapshot(market_cache_path: str) -> Dict[str, Any]:
    """
    Expected market_cache.json:
      {"asof": "...", "items": [{"code":"BIST100","change_pct":..}, ...]}
    """
    data = _load_json(market_cache_path, default={})
    if not isinstance(data, dict):
        return {"asof": "", "items": []}
    if "items" not in data:
        return {"asof": data.get("asof", ""), "items": []}
    return {"asof": data.get("asof", ""), "items": data.get("items", [])}

def market_change_pct(market_snapshot: Dict[str, Any], code: str) -> float:
    items = market_snapshot.get("items") or []
    for it in items:
        if isinstance(it, dict) and str(it.get("code") or "") == code:
            return _safe_float(it.get("change_pct"), 0.0)
    return 0.0
