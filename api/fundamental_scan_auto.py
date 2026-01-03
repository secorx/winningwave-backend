# C:\Users\yelli\Desktop\SENTEZ_AI_TEMEL_ANALIZ_M\api\fundamental_scan_auto.py
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
# PATHS (sadece temel analiz tarayıcı otomasyonu)
# ============================================================

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

SCANNER_STATE_PATH = os.path.join(DATA_DIR, "scanner_state.json")
SCANNER_SNAPSHOT_PATH = os.path.join(DATA_DIR, "scanner_snapshot.json")

# Processler arası kilit (aynı makinede çoklu worker/import durumlarına karşı)
SCANNER_LOCK_PATH = os.path.join(DATA_DIR, "scanner_lock.lock")


# ============================================================
# INTERNAL LOCKS
# ============================================================

_STATE_LOCK = threading.Lock()
_BG_THREAD_LOCK = threading.Lock()
_BG_THREAD_RUNNING = False

# İstersen uzun tarama için güvenli takeover penceresi (dakika)
# Örn: server çöker, state "running" kalırsa 90 dk sonra yeniden başlatmaya izin ver.
STALE_RUNNING_MINUTES = 90


# ============================================================
# TIME HELPERS
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

def _today_str_tr() -> str:
    return _now_tr().strftime("%Y-%m-%d")

def _is_after_0300(dt: datetime) -> bool:
    return (dt.hour > 3) or (dt.hour == 3 and dt.minute >= 0)

def _scan_day_key(dt: datetime) -> str:
    """
    Gün anahtarı:
    - 03:00 öncesi: 'dün' (çünkü bugünün taraması henüz başlamamalı)
    - 03:00 sonrası: 'bugün'
    """
    if _is_after_0300(dt):
        return dt.strftime("%Y-%m-%d")
    return (dt.date() - timedelta(days=1)).strftime("%Y-%m-%d")


# ============================================================
# ATOMIC JSON IO
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
# PROCESS LOCK (atomic create)
# ============================================================

def _try_acquire_process_lock() -> bool:
    """
    SCANNER_LOCK_PATH dosyasını atomik oluşturur.
    Başarırsa kilit alınmıştır. Başaramazsa başka process/thread çalışıyordur.
    """
    try:
        fd = os.open(SCANNER_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(_fmt_dt(_now_tr()))
        return True
    except FileExistsError:
        return False
    except Exception:
        # güvenli tarafta kal
        return False

def _release_process_lock() -> None:
    try:
        if os.path.exists(SCANNER_LOCK_PATH):
            os.remove(SCANNER_LOCK_PATH)
    except Exception:
        pass


# ============================================================
# STATE MACHINE
# ============================================================

def get_scanner_state() -> Dict[str, Any]:
    """
    UI her girişte bunu okuyabilir.
    """
    st = _safe_read_json(SCANNER_STATE_PATH) or {}
    snap = _safe_read_json(SCANNER_SNAPSHOT_PATH) or {}
    return {
        "state": st,
        "snapshot": snap,
        "server_time_tr": _fmt_dt(_now_tr()),
    }

def _is_running_state_stale(state: Dict[str, Any]) -> bool:
    """
    running state çok eskiyse (server crash vb.) takeover izni verir.
    """
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
        "mode": mode,           # auto | admin
        "started_at": started_at,
        "finished_at": finished_at,
        "updated_at": _fmt_dt(_now_tr()),
    }
    _atomic_write_json(SCANNER_STATE_PATH, obj)

def _write_snapshot(day: str, payload: Dict[str, Any]) -> None:
    obj = {
        "day": day,
        "asof": _fmt_dt(_now_tr()),
        "data": payload,
    }
    _atomic_write_json(SCANNER_SNAPSHOT_PATH, obj)


# ============================================================
# PUBLIC API: AUTO TRIGGER
# ============================================================

def maybe_start_daily_scan_after_0300(
    scan_runner: Callable[[], Dict[str, Any]],
) -> Dict[str, Any]:
    """
    03:00 sonrası ilk girişte günlük taramayı başlatır.
    - Gün içinde 1 defa (done olana kadar asla ikinci kez başlamaz)
    - running iken başka kullanıcı gelirse yeni tarama başlamaz
    - Tarama bitince snapshot yazar, state=done
    """
    now = _now_tr()
    if not _is_after_0300(now):
        # 03:00 öncesi asla tetikleme
        return get_scanner_state()

    day_key = _scan_day_key(now)

    with _STATE_LOCK:
        state = _safe_read_json(SCANNER_STATE_PATH) or {}

        # Bugün done ise: hiç dokunma
        if state.get("day") == day_key and state.get("status") == "done":
            return get_scanner_state()

        # Bugün running ise: ikinci kez başlatma
        if state.get("day") == day_key and state.get("status") == "running":
            # running stale ise takeover yapabiliriz
            if not _is_running_state_stale(state):
                return get_scanner_state()

        # running ama stale veya gün farklıysa: yeni auto başlatılabilir
        # Process lock almayı dene
        if not _try_acquire_process_lock():
            # kilit alınamadı -> başka yerde tarama başladı
            return get_scanner_state()

        # state'i running yap (en kritik kısım)
        _write_state(
            day=day_key,
            status="running",
            mode="auto",
            started_at=_fmt_dt(_now_tr()),
            finished_at=None,
        )

    # Background thread başlat (request’i bloklamasın)
    _start_scan_background(scan_runner=scan_runner, day_key=day_key, mode="auto")

    return get_scanner_state()


def start_admin_scan(
    scan_runner: Callable[[], Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Admin override.
    - Saat/gün bakmaz.
    - Yine de process lock kullanır: aynı anda 2 admin taraması açılmaz.
    """
    day_key = _scan_day_key(_now_tr())

    with _STATE_LOCK:
        if not _try_acquire_process_lock():
            return get_scanner_state()

        _write_state(
            day=day_key,
            status="running",
            mode="admin",
            started_at=_fmt_dt(_now_tr()),
            finished_at=None,
        )

    _start_scan_background(scan_runner=scan_runner, day_key=day_key, mode="admin")
    return get_scanner_state()


def _start_scan_background(scan_runner: Callable[[], Dict[str, Any]], day_key: str, mode: str) -> None:
    """
    Aynı process içinde de çift thread’i engeller.
    """
    global _BG_THREAD_RUNNING

    with _BG_THREAD_LOCK:
        if _BG_THREAD_RUNNING:
            return
        _BG_THREAD_RUNNING = True

    def _job():
        global _BG_THREAD_RUNNING
        try:
            # Asıl tarama
            result = scan_runner() or {}
            # Snapshot yaz
            _write_snapshot(day_key, result)
            # Done state
            with _STATE_LOCK:
                _write_state(
                    day=day_key,
                    status="done",
                    mode=mode,
                    started_at=str((_safe_read_json(SCANNER_STATE_PATH) or {}).get("started_at") or _fmt_dt(_now_tr())),
                    finished_at=_fmt_dt(_now_tr()),
                )
        except Exception as e:
            # Hata olursa running kalmasın: idle’a çek ki bir sonraki giriş yeniden deneyebilsin
            with _STATE_LOCK:
                _write_state(
                    day=day_key,
                    status="idle",
                    mode=mode,
                    started_at=None,
                    finished_at=None,
                )
            # İstersen burada ayrı bir error log dosyası da yazabiliriz
            try:
                err_path = os.path.join(DATA_DIR, "scanner_last_error.txt")
                with open(err_path, "w", encoding="utf-8") as f:
                    f.write(f"{_fmt_dt(_now_tr())} | {repr(e)}\n")
            except Exception:
                pass
        finally:
            _release_process_lock()
            with _BG_THREAD_LOCK:
                _BG_THREAD_RUNNING = False

    t = threading.Thread(target=_job, daemon=True)
    t.start()
