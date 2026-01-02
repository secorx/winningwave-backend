from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# Dış bağımlılıkları zorunlu yapmıyoruz (crash-proof).
try:
    import requests  # type: ignore
except Exception:  # pragma: no cover
    requests = None  # type: ignore


@dataclass
class FetchResult:
    ok: bool
    data: List[Dict[str, Any]]
    source: str
    error: Optional[str] = None


def _now_tr_str() -> str:
    # Server TR time string
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def fetch_tefas_list(timeout_s: int = 20) -> FetchResult:
    """
    TEFAS listesi için crash-proof fetch.
    Not: TEFAS endpoint/HTML değişebilir. Bu fonksiyon hata verirse ok=False döner.
    """
    if requests is None:
        return FetchResult(ok=False, data=[], source="tefas_fetch", error="requests_not_installed")

    # TEFAS için sık kullanılan sayfalardan biri (değişebilir).
    # Eğer patlarsa biz ok=False döndürürüz.
    url = "https://www.tefas.gov.tr/FonKarsilastirma.aspx"

    try:
        r = requests.get(
            url,
            timeout=timeout_s,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        if r.status_code != 200:
            return FetchResult(ok=False, data=[], source="tefas_html", error=f"http_{r.status_code}")

        html = r.text or ""
        if len(html) < 2000:
            return FetchResult(ok=False, data=[], source="tefas_html", error="html_too_short")

        # Bu katmanda “listeyi çıkarmak” TEFAS değişken olduğu için garanti değil.
        # Şimdilik sadece “ham kaynak var mı” kontrolü yapıyoruz.
        # Asıl listeyi sizin kendi parser’ınıza bağlayacağız (bir sonraki adımda).
        return FetchResult(
            ok=True,
            data=[],
            source="tefas_html",
            error=None,
        )
    except Exception as e:
        return FetchResult(ok=False, data=[], source="tefas_html", error=str(e))


def fetch_tefas_live_prices(timeout_s: int = 20) -> FetchResult:
    """
    Live fiyatlar / anlık veri: TEFAS gecikmeli olabilir.
    Bu fonksiyon crash-proof: ok=False dönebilir.
    """
    if requests is None:
        return FetchResult(ok=False, data=[], source="tefas_fetch", error="requests_not_installed")

    # Placeholder endpoint: canlı veri için TEFAS resmi API yok.
    # Bu yüzden burada da sadece altyapıyı stabil tutuyoruz.
    # Asıl iş: sizin parser/cache mantığınız.
    try:
        return FetchResult(ok=True, data=[], source="tefas_live_stub", error=None)
    except Exception as e:
        return FetchResult(ok=False, data=[], source="tefas_live_stub", error=str(e))
