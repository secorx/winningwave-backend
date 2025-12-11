# temel_analiz/veri_saglayicilar/yerel_csv.py

import os
import json
from typing import List, Optional
from temel_analiz.veri.modeller import CompanyData, FinancialPeriod
import csv

CSV_HINT = """
Beklenen CSV başlıkları:
period,revenue,net_income,ebitda,equity,assets,liabilities,cfo,capex,shares_out,price
"""

def load_company_from_csv(symbol: str, path: str) -> Optional[CompanyData]:
    if not os.path.exists(path):
        return None

    periods = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                periods.append(FinancialPeriod(
                    period=row["period"],
                    revenue=float(row["revenue"]),
                    net_income=float(row["net_income"]),
                    ebitda=float(row["ebitda"]),
                    equity=float(row["equity"]),
                    assets=float(row["assets"]),
                    liabilities=float(row["liabilities"]),
                    cfo=float(row["cfo"]),
                    capex=float(row["capex"]),
                    shares_out=float(row["shares_out"]),
                    price=float(row["price"]) if row.get("price") else None,
                ))
            except Exception as e:
                raise ValueError(f"CSV okuma hatası: {e}\n{CSV_HINT}") from e

    if not periods:
        return None

    last_price = periods[-1].price
    return CompanyData(symbol=symbol, sector="N/A", periods=periods, last_price=last_price)


def load_all_symbols() -> List[str]:
    """
    Tarama motoru için sembolleri okur.
    Mobil projenin gerçek yoluna göre %100 doğru çalışır:
    SENTEZ_AI_TEMEL_ANALIZ_M/data/piyasa_verisi.json
    """

    # Bu dosya tam olarak burada olmalı:
    # SENTEZ_AI_TEMEL_ANALIZ_M/data/piyasa_verisi.json

    base = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(base, "..", ".."))
    json_path = os.path.join(project_root, "data", "piyasa_verisi.json")

    if not os.path.exists(json_path):
        return []

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []

    symbols = [row.get("symbol") for row in data if row.get("symbol")]
    return sorted(set(symbols))
