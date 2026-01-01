from fastapi import APIRouter
from datetime import datetime
import requests

router = APIRouter()

# ===============================
# PORTFÖY (SABİT – GERÇEK LOT)
# ===============================
PORTFOLIO = [
    {"code": "DFI", "lots": 87933},
    {"code": "AFT", "lots": 1200},
]

# ===============================
# GERÇEK TEFAS ÇEKİCİ
# ===============================
def get_tefas_price(fund_code: str):
    """
    GERÇEK TEFAS – yavaş ama gerçek
    """
    url = "https://www.tefas.gov.tr/api/DB/BindPortfolioAllocation"
    params = {
        "fontip": "YAT",
        "fonkod": fund_code
    }

    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()

    price = float(data["data"][0]["FIYAT"])
    change = float(data["data"][0]["GUNLUK_GETIRI"])

    return price, change


# ===============================
# TEK GERÇEK SNAPSHOT ENDPOINT
# ===============================
@router.get("/portfolio_snapshot")
def portfolio_snapshot():
    funds = []
    total_value = 0.0

    for item in PORTFOLIO:
        price, change = get_tefas_price(item["code"])
        value = price * item["lots"]
        total_value += value

        funds.append({
            "code": item["code"],
            "price": round(price, 4),
            "lots": item["lots"],
            "value": round(value, 2),
            "change": round(change, 4)
        })

    for f in funds:
        f["weight"] = round((f["value"] / total_value) * 100, 2)

    return {
        "portfolio": {
            "total_value": round(total_value, 2),
            "funds": funds
        },
        "indices": {
            "BIST100": {"value": 0.0, "change": 0.0},
            "BIST30": {"value": 0.0, "change": 0.0}
        },
        "timestamp": datetime.now().isoformat()
    }
