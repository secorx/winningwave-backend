# api/technical_services.py
from __future__ import annotations

import os
import json
import time
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple, Optional
from zoneinfo import ZoneInfo

import yfinance as yf


# ============================================================
# PATHS
# ============================================================
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(BASE_DIR, "data")

SYMBOLS_PATH = os.path.join(DATA_DIR, "technical_symbols.json")

CACHE_DIR = os.path.join(BASE_DIR, "technical_cache")
CANDLES_DIR = os.path.join(CACHE_DIR, "candles")  # 15m cache
DAILY_DIR = os.path.join(CACHE_DIR, "daily")      # 1d cache

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(CANDLES_DIR, exist_ok=True)
os.makedirs(DAILY_DIR, exist_ok=True)

# ============================================================
# CONFIG
# ============================================================
ALLOWED_TF = {"15m", "30m", "1h", "1d"}

TTL_15M_SECONDS = 15 * 60     # 15 dakika
TTL_1D_SECONDS = 6 * 60 * 60  # 6 saat (günlük veri için yeterli stabil)

# yfinance intraday limitleri için güvenli period
YF_15M_PERIOD = "60d"   # 15m için genelde 60gün limit; stabil
YF_1D_PERIOD = "2y"     # günlük için daha geniş

# Grafikte çok ağır olmaması için max mum sayısı (mobilde render rahat)
MAX_CANDLES_RETURN = 600

IST_TZ = ZoneInfo("Europe/Istanbul")

# Sembol RAM cache
_SYMBOLS_LOCK = threading.Lock()
_SYMBOLS_CACHE: Optional[Dict[str, Any]] = None
_SYMBOLS_CACHE_TS = 0.0

# Aynı anda aynı sembole çoklu istek gelince tek fetch olsun
_FETCH_LOCKS: Dict[str, threading.Lock] = {}
_FETCH_LOCKS_GUARD = threading.Lock()


# ============================================================
# HELPERS
# ============================================================
def _now_ts() -> float:
    return time.time()


def _iso_istanbul(dt_obj: datetime) -> str:
    """
    dt_obj tz-aware olmalı.
    Istanbul'a çevirip ISO string döner.
    """
    if dt_obj.tzinfo is None:
        dt_obj = dt_obj.replace(tzinfo=timezone.utc)
    return dt_obj.astimezone(IST_TZ).replace(microsecond=0).isoformat()


def _safe_float(x: Any) -> float:
    try:
        if x is None:
            return 0.0
        # pandas Series gelirse
        if hasattr(x, "iloc"):
            return float(x.iloc[0])
        return float(x)
    except Exception:
        return 0.0


def _safe_int(x: Any) -> int:
    try:
        if x is None:
            return 0
        # pandas Series gelirse
        if hasattr(x, "iloc"):
            return int(x.iloc[0])
        return int(x)
    except Exception:
        return 0



def _read_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_json_atomic(path: str, obj: Dict[str, Any]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _get_lock(key: str) -> threading.Lock:
    with _FETCH_LOCKS_GUARD:
        if key not in _FETCH_LOCKS:
            _FETCH_LOCKS[key] = threading.Lock()
        return _FETCH_LOCKS[key]


def load_symbols() -> Dict[str, Any]:
    """
    technical_symbols.json dosyasını RAM'e alır.
    """
    global _SYMBOLS_CACHE, _SYMBOLS_CACHE_TS
    with _SYMBOLS_LOCK:
        # 60 saniyede bir dosya değişikliği varsayımıyla refresh (geliştirme kolaylığı)
        if _SYMBOLS_CACHE is not None and (_now_ts() - _SYMBOLS_CACHE_TS) < 60:
            return _SYMBOLS_CACHE

        data = _read_json(SYMBOLS_PATH)
        if not data:
            # dosya yoksa veya bozuksa boş whitelist
            data = {"xu100": None, "stocks": []}

        # normalize
        xu = data.get("xu100")
        stocks = data.get("stocks") or []
        if xu and "code" in xu:
            xu["code"] = str(xu["code"]).strip().upper()

        cleaned_stocks = []
        for s in stocks:
            try:
                code = str(s.get("code", "")).strip().upper()
                yf_code = str(s.get("yf", "")).strip()
                name = str(s.get("name", "")).strip()
                stype = str(s.get("type", "stock")).strip()
                if code and yf_code:
                    cleaned_stocks.append({"code": code, "yf": yf_code, "name": name, "type": stype})
            except Exception:
                continue

        data = {"xu100": xu, "stocks": cleaned_stocks}
        _SYMBOLS_CACHE = data
        _SYMBOLS_CACHE_TS = _now_ts()
        return data


def resolve_symbol(symbol: str) -> Optional[Dict[str, str]]:
    """
    Mobilin gönderdiği symbol (GARAN, XU100) -> yf ticker.
    Whitelist dışıysa None döner.
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return None

    data = load_symbols()
    xu = data.get("xu100")
    if xu and xu.get("code") == sym:
        return {"code": xu["code"], "yf": xu["yf"], "name": xu.get("name", ""), "type": xu.get("type", "index")}

    for s in data.get("stocks", []):
        if s.get("code") == sym:
            return {"code": s["code"], "yf": s["yf"], "name": s.get("name", ""), "type": s.get("type", "stock")}

    return None


def list_symbols(q: str = "") -> List[Dict[str, str]]:
    """
    /technical/symbols için: arama destekli liste.
    """
    query = (q or "").strip().lower()
    data = load_symbols()

    out: List[Dict[str, str]] = []
    xu = data.get("xu100")
    if xu:
        out.append({"code": xu["code"], "name": xu.get("name", ""), "type": xu.get("type", "index")})

    for s in data.get("stocks", []):
        out.append({"code": s["code"], "name": s.get("name", ""), "type": s.get("type", "stock")})

    if not query:
        return out

    filtered = []
    for item in out:
        if query in item["code"].lower() or query in (item.get("name", "").lower()):
            filtered.append(item)
    return filtered


def _cache_path_15m(symbol_code: str) -> str:
    return os.path.join(CANDLES_DIR, f"{symbol_code}_15m.json")


def _cache_path_1d(symbol_code: str) -> str:
    return os.path.join(DAILY_DIR, f"{symbol_code}_1d.json")


def _is_cache_fresh(cache_obj: Dict[str, Any], ttl_seconds: int) -> bool:
    try:
        ts = float(cache_obj.get("_cached_at", 0))
        return (_now_ts() - ts) < ttl_seconds
    except Exception:
        return False


def _df_to_candles(df) -> List[Dict[str, Any]]:
    """
    yfinance dataframe -> candle list
    """
    candles: List[Dict[str, Any]] = []
    if df is None or len(df) == 0:
        return candles

    # Index datetime olabilir
    # yfinance bazen tz-aware bazen naive döner
    for idx, row in df.iterrows():
        try:
            dt_obj = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
            if isinstance(dt_obj, datetime):
                if dt_obj.tzinfo is None:
                    # Yahoo çoğu zaman UTC varsayımı ile gelir
                    dt_obj = dt_obj.replace(tzinfo=timezone.utc)
            else:
                continue

            o = _safe_float(row.get("Open"))
            h = _safe_float(row.get("High"))
            l = _safe_float(row.get("Low"))
            c = _safe_float(row.get("Close"))
            v = _safe_int(row.get("Volume"))

            # boş/NaN filtre
            if o == 0.0 and h == 0.0 and l == 0.0 and c == 0.0:
                continue

            candles.append({
                "t": _iso_istanbul(dt_obj),
                "o": o,
                "h": h,
                "l": l,
                "c": c,
                "v": v
            })
        except Exception:
            continue

    # sırala + limit
    candles.sort(key=lambda x: x["t"])
    if len(candles) > MAX_CANDLES_RETURN:
        candles = candles[-MAX_CANDLES_RETURN:]
    return candles


def _fetch_yfinance_15m(yf_symbol: str):
    # auto_adjust=False: OHLC daha stabil; prepost=False: standart
    return yf.download(
        tickers=yf_symbol,
        period=YF_15M_PERIOD,
        interval="15m",
        auto_adjust=False,
        prepost=False,
        progress=False,
        threads=False,
    )


def _fetch_yfinance_1d(yf_symbol: str):
    return yf.download(
        tickers=yf_symbol,
        period=YF_1D_PERIOD,
        interval="1d",
        auto_adjust=False,
        prepost=False,
        progress=False,
        threads=False,
    )


def get_candles(symbol: str, tf: str) -> Dict[str, Any]:
    """
    Dış dünya için tek giriş:
    - 15m: yfinance + disk cache
    - 30m/1h: 15m'den aggregation
    - 1d: yfinance + disk cache (fallback: 15m daily agg)
    """
    tf_norm = (tf or "").strip().lower()
    if tf_norm == "1d":
        tf_norm = "1d"
    if tf_norm not in ALLOWED_TF:
        return {"status": "error", "error": "unsupported_timeframe"}

    sym = resolve_symbol(symbol)
    if not sym:
        return {"status": "error", "error": "unknown_symbol"}

    code = sym["code"]
    yf_code = sym["yf"]

    if tf_norm == "15m":
        return {"status": "success", "data": _get_15m_cached(code, yf_code)}

    if tf_norm in ("30m", "1h"):
        base = _get_15m_cached(code, yf_code)
        if base.get("status") == "error":
            return {"status": "error", "error": base.get("error", "fetch_failed")}
        candles = base["candles"]
        mult = 2 if tf_norm == "30m" else 4
        agg = _aggregate(candles, tf_norm, mult)
        payload = _wrap_payload(code, tf_norm, agg, source="derived", delayed=True, last_update=base.get("last_update"))
        return {"status": "success", "data": payload}

    # 1d
    daily = _get_1d_cached(code, yf_code)
    if daily.get("status") != "error":
        payload = _wrap_payload(code, "1d", daily["candles"], source="yfinance", delayed=True, last_update=daily.get("last_update"))
        return {"status": "success", "data": payload}

    # fallback: 15m'den daily üret
    base = _get_15m_cached(code, yf_code)
    if base.get("status") == "error":
        return {"status": "error", "error": "fetch_failed"}
    candles = base["candles"]
    agg = _aggregate_daily(candles)
    payload = _wrap_payload(code, "1d", agg, source="derived", delayed=True, last_update=base.get("last_update"))
    return {"status": "success", "data": payload}


def _wrap_payload(symbol_code: str, tf: str, candles: List[Dict[str, Any]], source: str, delayed: bool, last_update: Optional[str]) -> Dict[str, Any]:
    return {
        "symbol": symbol_code,
        "timeframe": tf,
        "delayed": bool(delayed),
        "source": source,
        "last_update": last_update or datetime.now(IST_TZ).replace(microsecond=0).isoformat(),
        "candles": candles,
    }


def _get_15m_cached(symbol_code: str, yf_symbol: str) -> Dict[str, Any]:
    """
    15m cache oku. Taze değilse yfinance çekip cache yaz.
    Aynı sembole aynı anda istek gelirse lock ile tek fetch.
    """
    path = _cache_path_15m(symbol_code)

    lock = _get_lock(f"{symbol_code}_15m")
    with lock:
        cached = _read_json(path)
        if cached and _is_cache_fresh(cached, TTL_15M_SECONDS):
            return cached

        # yfinance fetch
        try:
            df = _fetch_yfinance_15m(yf_symbol)
            candles = _df_to_candles(df)

            payload = _wrap_payload(symbol_code, "15m", candles, source="yfinance", delayed=True, last_update=datetime.now(IST_TZ).replace(microsecond=0).isoformat())
            payload["_cached_at"] = _now_ts()

            _write_json_atomic(path, payload)
            return payload
        except Exception as e:
            # cache varsa eskiyi döndür (hata anında bile servis kesilmesin)
            if cached:
                cached["_stale"] = True
                return cached
            return {"status": "error", "error": "yfinance_fetch_failed", "message": str(e)}


def _get_1d_cached(symbol_code: str, yf_symbol: str) -> Dict[str, Any]:
    path = _cache_path_1d(symbol_code)

    lock = _get_lock(f"{symbol_code}_1d")
    with lock:
        cached = _read_json(path)
        if cached and _is_cache_fresh(cached, TTL_1D_SECONDS):
            return cached

        try:
            df = _fetch_yfinance_1d(yf_symbol)
            candles = _df_to_candles(df)
            payload = _wrap_payload(symbol_code, "1d", candles, source="yfinance", delayed=True, last_update=datetime.now(IST_TZ).replace(microsecond=0).isoformat())
            payload["_cached_at"] = _now_ts()
            _write_json_atomic(path, payload)
            return payload
        except Exception as e:
            if cached:
                cached["_stale"] = True
                return cached
            return {"status": "error", "error": "yfinance_fetch_failed", "message": str(e)}


def _parse_iso(t: str) -> Optional[datetime]:
    try:
        # isoformat içinde timezone offset var (+03:00)
        return datetime.fromisoformat(t)
    except Exception:
        return None


def _aggregate(candles: List[Dict[str, Any]], tf: str, mult: int) -> List[Dict[str, Any]]:
    """
    15m candles -> 30m (mult=2) veya 1h (mult=4).
    Sürekli pair/group yapar; zaman hizası sorunlarını tolere eder.
    """
    if not candles:
        return []

    # parse + sort
    parsed: List[Tuple[datetime, Dict[str, Any]]] = []
    for c in candles:
        dt_obj = _parse_iso(c.get("t", ""))
        if not dt_obj:
            continue
        parsed.append((dt_obj, c))
    parsed.sort(key=lambda x: x[0])

    out: List[Dict[str, Any]] = []

    # Grup anahtarı: tf'ye göre floor
    def key_for(dt_obj: datetime) -> datetime:
        if tf == "30m":
            m = (dt_obj.minute // 30) * 30
            return dt_obj.replace(minute=m, second=0, microsecond=0)
        # 1h
        return dt_obj.replace(minute=0, second=0, microsecond=0)

    current_key: Optional[datetime] = None
    bucket: List[Dict[str, Any]] = []

    for dt_obj, c in parsed:
        k = key_for(dt_obj)
        if current_key is None:
            current_key = k
            bucket = [c]
            continue

        if k != current_key:
            out.append(_bucket_to_candle(current_key, bucket))
            current_key = k
            bucket = [c]
        else:
            bucket.append(c)

    if current_key is not None and bucket:
        out.append(_bucket_to_candle(current_key, bucket))

    # limit
    out.sort(key=lambda x: x["t"])
    if len(out) > MAX_CANDLES_RETURN:
        out = out[-MAX_CANDLES_RETURN:]
    return out


def _aggregate_daily(candles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    15m -> 1d fallback aggregation (gün kırılımı Istanbul TZ’ye göre).
    """
    if not candles:
        return []

    parsed: List[Tuple[datetime, Dict[str, Any]]] = []
    for c in candles:
        dt_obj = _parse_iso(c.get("t", ""))
        if not dt_obj:
            continue
        parsed.append((dt_obj, c))
    parsed.sort(key=lambda x: x[0])

    out: List[Dict[str, Any]] = []

    def day_key(dt_obj: datetime) -> datetime:
        # Istanbul'a göre gün başlangıcı
        local = dt_obj.astimezone(IST_TZ)
        return local.replace(hour=0, minute=0, second=0, microsecond=0)

    current_key: Optional[datetime] = None
    bucket: List[Dict[str, Any]] = []

    for dt_obj, c in parsed:
        k = day_key(dt_obj)
        if current_key is None:
            current_key = k
            bucket = [c]
            continue

        if k != current_key:
            out.append(_bucket_to_candle(current_key, bucket))
            current_key = k
            bucket = [c]
        else:
            bucket.append(c)

    if current_key is not None and bucket:
        out.append(_bucket_to_candle(current_key, bucket))

    out.sort(key=lambda x: x["t"])
    if len(out) > MAX_CANDLES_RETURN:
        out = out[-MAX_CANDLES_RETURN:]
    return out


def _bucket_to_candle(bucket_key: datetime, bucket: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    OHLC merge standardı:
    O=first, H=max, L=min, C=last, V=sum
    """
    if not bucket:
        return {"t": _iso_istanbul(bucket_key), "o": 0.0, "h": 0.0, "l": 0.0, "c": 0.0, "v": 0}

    # bucket sırası zaten zaman sırası olmalı; garanti için t ile sort
    bucket_sorted = sorted(bucket, key=lambda x: x.get("t", ""))
    o = _safe_float(bucket_sorted[0].get("o"))
    c = _safe_float(bucket_sorted[-1].get("c"))

    highs = [_safe_float(x.get("h")) for x in bucket_sorted]
    lows = [_safe_float(x.get("l")) for x in bucket_sorted]
    vols = [_safe_int(x.get("v")) for x in bucket_sorted]

    h = max(highs) if highs else 0.0
    l = min(lows) if lows else 0.0
    v = sum(vols) if vols else 0

    return {
        "t": _iso_istanbul(bucket_key),
        "o": o,
        "h": h,
        "l": l,
        "c": c,
        "v": v
    }
