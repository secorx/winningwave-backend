# C:\Users\yelli\Desktop\SENTEZ_AI_TEMEL_ANALIZ_M\api\live_prices_auto.py
from __future__ import annotations

import os
import json
import time
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Callable

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None


# ============================================================
# PATHS (sadece temel analiz canlı fiyat otomasyonu)
# ============================================================

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(BASE_DIR, "data")  # services.py ile aynı klasör
os.makedirs(DATA_DIR, exist_ok=True)

LIVE_STATE_PATH = os.path.join(DATA_DIR, "live_prices_state.json")
LIVE_SNAPSHOT_PATH = os.path.join(DATA_DIR, "live_prices_snapshot.json")
LIVE_LOCK_PATH = os.path.join(DATA_DIR, "live_prices_lock.lock")

_STATE_LOCK = threading.Lock()
_BG_THREAD_LOCK = threading.Lock()
_BG_THREAD_RUNNING = False

# 4-5 dk sürer ama worst-case crash olursa 30 dk sonra takeover izni
STALE_RUNNING_MINUTES = 30


# ============================================================
# TIME HELPERS (TR)
# ============================================================

def _now_tr() -> datetime:
    if ZoneInfo:
        try:
            return datetime.now(ZoneInfo("Europe/Istanbul"))
        except Exception:
            pass
    return datetime.now()

def _fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def _is_after_0330(dt: datetime) -> bool:
    return (dt.hour > 3) or (dt.hour == 3 and dt.minute >= 30)

def _day_key_0330(dt: datetime) -> str:
    """
    Gün anahtarı:
      - 03:30 sonrası: bugün
      - 03:30 öncesi: dün (bugünün canlı fiyat güncellemesi henüz başlamasın)
    """
    if _is_after_0330(dt):
        return dt.strftime("%Y-%m-%d")
    return (dt.date() - timedelta(days=1)).strftime("%Y-%m-%d")


# ============================================================
# ATOMIC JSON
# ============================================================

def _atomic_write_json(path: str, obj: Any) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def _safe_read_json(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ============================================================
# PROCESS LOCK (multi-worker safety)
# ============================================================

def _try_acquire_lock() -> bool:
    try:
        fd = os.open(LIVE_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(_fmt_dt(_now_tr()))
        return True
    except FileExistsError:
        return False
    except Exception:
        return False

def _release_lock() -> None:
    try:
        if os.path.exists(LIVE_LOCK_PATH):
            os.remove(LIVE_LOCK_PATH)
    except Exception:
        pass


# ============================================================
# STATE + SNAPSHOT
# ============================================================

def get_live_prices_state() -> Dict[str, Any]:
    st = _safe_read_json(LIVE_STATE_PATH) or {}
    snap = _safe_read_json(LIVE_SNAPSHOT_PATH) or {}
    return {
        "state": st,
        "snapshot": snap,
        "server_time_tr": _fmt_dt(_now_tr()),
    }

def _is_running_stale(state: Dict[str, Any]) -> bool:
    if (state or {}).get("status") != "running":
        return False
    started_at = str(state.get("started_at") or "").strip()
    if not started_at:
        return True
    try:
        dt = datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return True
    age_sec = (datetime.now() - dt).total_seconds()
    return age_sec > (STALE_RUNNING_MINUTES * 60)

def _write_state(day: str, status: str, mode: str, started_at: Optional[str], finished_at: Optional[str]) -> None:
    obj = {
        "day": day,
        "status": status,       # idle | running | done
        "mode": mode,           # auto | manual
        "started_at": started_at,
        "finished_at": finished_at,
        "updated_at": _fmt_dt(_now_tr()),
    }
    _atomic_write_json(LIVE_STATE_PATH, obj)

def _write_snapshot(day: str, payload: Dict[str, Any]) -> None:
    obj = {
        "day": day,
        "asof": _fmt_dt(_now_tr()),
        "data": payload,
    }
    _atomic_write_json(LIVE_SNAPSHOT_PATH, obj)


# ============================================================
# PUBLIC: AUTO TRIGGER
# ============================================================

def maybe_start_daily_live_prices_after_0330(
    runner: Callable[[], Dict[str, Any]],
    mode: str = "auto",
) -> Dict[str, Any]:
    """
    03:30 sonrası ilk tetiklemede canlı fiyat refresh başlatır.
    - günde 1 defa (done olana kadar ikinci kez başlamaz)
    - running iken başka kullanıcı gelirse yeni job başlamaz
    - biterse snapshot yazar, state=done
    """
    now = _now_tr()
    if not _is_after_0330(now):
        return get_live_prices_state()

    day_key = _day_key_0330(now)

    with _STATE_LOCK:
        state = _safe_read_json(LIVE_STATE_PATH) or {}

        # bugün done -> hiç dokunma
        if state.get("day") == day_key and state.get("status") == "done":
            return get_live_prices_state()

        # bugün running -> ikinci kez başlatma (stale değilse)
        if state.get("day") == day_key and state.get("status") == "running":
            if not _is_running_stale(state):
                return get_live_prices_state()

        # lock al
        if not _try_acquire_lock():
            return get_live_prices_state()

        _write_state(
            day=day_key,
            status="running",
            mode=mode,
            started_at=_fmt_dt(_now_tr()),
            finished_at=None,
        )

    _start_bg(runner=runner, day_key=day_key, mode=mode)
    return get_live_prices_state()


def _start_bg(runner: Callable[[], Dict[str, Any]], day_key: str, mode: str) -> None:
    global _BG_THREAD_RUNNING

    with _BG_THREAD_LOCK:
        if _BG_THREAD_RUNNING:
            return
        _BG_THREAD_RUNNING = True

    def _job():
        global _BG_THREAD_RUNNING
        try:
            result = runner() or {}
            _write_snapshot(day_key, result)
            with _STATE_LOCK:
                st = _safe_read_json(LIVE_STATE_PATH) or {}
                started_at = str(st.get("started_at") or _fmt_dt(_now_tr()))
                _write_state(
                    day=day_key,
                    status="done",
                    mode=mode,
                    started_at=started_at,
                    finished_at=_fmt_dt(_now_tr()),
                )
        except Exception as e:
            # hata olursa idle'a çek ki sonraki girişte tekrar deneyebilsin
            with _STATE_LOCK:
                _write_state(
                    day=day_key,
                    status="idle",
                    mode=mode,
                    started_at=None,
                    finished_at=None,
                )
            try:
                err_path = os.path.join(DATA_DIR, "live_prices_last_error.txt")
                with open(err_path, "w", encoding="utf-8") as f:
                    f.write(f"{_fmt_dt(_now_tr())} | {repr(e)}\n")
            except Exception:
                pass
        finally:
            _release_lock()
            with _BG_THREAD_LOCK:
                _BG_THREAD_RUNNING = False

    t = threading.Thread(target=_job, daemon=True)
    t.start()
