# coding: utf-8
# temel_analiz/hesaplayicilar/oranlar.py

from __future__ import annotations
from typing import Dict, Any, Optional
from temel_analiz.veri.modeller import CompanyData

def _safe_div(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or b == 0: return None
    return float(a) / float(b)

def _clean_percent(val: Optional[float]) -> Optional[float]:
    """Yahoo bazen 15.4 (yüzde) bazen 0.154 (ondalık) döner. Bunu normalize edelim."""
    if val is None: return None
    # Eğer değer > 5 ise muhtemelen yüzde olarak gelmiştir (ROE > 500% nadirdir ama 5.0 ROE makuldür)
    # Ancak bazı kriz şirketlerinde ROE %1000 olabilir. Burası biraz heuristic.
    # Güvenli yaklaşım: Oranlar genelde 0-1 arasıdır.
    return val

def raw_multiples(company: CompanyData) -> Dict[str, Optional[float]]:
    if not company.metrics_ttm: return {}
    price = company.last_price
    
    # PE
    pe = company.metrics_ttm.get('trailingPE') or _safe_div(price, company.metrics_ttm.get('trailingEps'))
    
    # PB
    pb = company.metrics_ttm.get('priceToBook') or _safe_div(price, company.metrics_ttm.get('bookValue'))
    
    return {"pe": pe, "pb": pb}

def profitability(company: CompanyData) -> Dict[str, Optional[float]]:
    m = company.metrics_ttm
    
    net_margin = m.get('profitMargins') # Genelde 0.15 gibi döner
    roe = m.get('returnOnEquity')       # Genelde 0.25 gibi döner
    roa = m.get('returnOnAssets')
    
    # EBITDA Margin (Manuel hesaplanan periodlardan almak daha güvenli olabilir)
    mrq = company.mrq()
    ebitda_margin = None
    if mrq and mrq.revenue > 0:
        ebitda_margin = mrq.ebitda / mrq.revenue

    return {"net_margin": net_margin, "roe": roe, "roa": roa, "ebitda_margin": ebitda_margin}

def leverage_liquidity(company: CompanyData) -> Dict[str, Optional[float]]:
    m = company.metrics_ttm
    mrq = company.mrq()
    
    debt_to_equity = m.get('debtToEquity')
    # Yahoo % olarak verir (örn 150.4), biz ratio istiyoruz (1.504)
    if debt_to_equity and debt_to_equity > 10: 
        debt_to_equity = debt_to_equity / 100.0
        
    current_ratio = m.get('currentRatio')
    
    # Net Borç / FAVÖK (TTM)
    # TTM FAVÖK hesaplaması: Son 4 çeyreğin toplamı
    ebitda_ttm = 0.0
    if len(company.periods) >= 4:
        ebitda_ttm = sum(p.ebitda for p in company.periods[:4])
    else:
        ebitda_ttm = m.get('ebitda') or 0.0

    net_debt_ebitda = None
    if mrq and ebitda_ttm > 0:
        # Net Borç MRQ'dan alınır
        net_debt = mrq.total_debt - mrq.cash_and_equivalents
        net_debt_ebitda = net_debt / ebitda_ttm

    return {
        "debt_to_equity": debt_to_equity, 
        "net_debt_ebitda": net_debt_ebitda, 
        "current_ratio": current_ratio
    }

def cashflow_quality(company: CompanyData) -> Dict[str, Optional[float]]:
    m = company.metrics_ttm
    mrq = company.mrq()
    
    # FCF (TTM)
    fcf_ttm = m.get('freeCashflow')
    
    revenue = m.get('totalRevenue')
    if not revenue and mrq: revenue = mrq.revenue * 4 # Basit yıllıklandırma
    
    mcap = m.get('marketCap')
    if not mcap and company.last_price and mrq: mcap = company.last_price * mrq.shares_out

    fcf_margin = _safe_div(fcf_ttm, revenue)
    fcf_yield = _safe_div(fcf_ttm, mcap)

    ev_ebitda = m.get('enterpriseToEbitda')
    
    return {"fcf_margin": fcf_margin, "fcf_yield": fcf_yield, "ev_ebitda": ev_ebitda}

def all_metrics(company: CompanyData) -> Dict[str, Any]:
    if not company: return {}
    return {
        "info": {
            "mostRecentQuarter": company.most_recent_quarter_date(),
            "currency": company.currency
        },
        "raw": raw_multiples(company),
        "profitability": profitability(company),
        "growth": {}, # Sadeleştirdik
        "leverage_liquidity": leverage_liquidity(company),
        "cashflow_quality": cashflow_quality(company),
    }