# coding: utf-8
# temel_analiz/veri/modeller.py

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

@dataclass
class FinancialPeriod:
    period: str
    revenue: float
    net_income: float
    ebitda: float
    equity: float
    assets: float
    liabilities: float
    cfo: float
    capex: float
    shares_out: float
    price: Optional[float] = None
    
    # Ekstra alanlar
    total_debt: float = 0.0
    cash_and_equivalents: float = 0.0

@dataclass
class CompanyData:
    symbol: str
    raw_sector: str        # Yahoo'dan gelen ham sektör ismi
    sector_normalized: str # Bizim sistemin tanıdığı kategori (BANK, INDUSTRY, REIT, AVIATION vs.)
    currency: str = "TRY"
    periods: List[FinancialPeriod] = field(default_factory=list)
    last_price: Optional[float] = None
    metrics_ttm: Dict[str, Any] = field(default_factory=dict)

    def mrq(self) -> Optional[FinancialPeriod]:
        return self.periods[0] if self.periods else None

    def most_recent_quarter_date(self) -> str:
        p = self.mrq()
        return p.period if p else "N/A"