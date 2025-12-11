# coding: utf-8
# temel_analiz/hesaplayicilar/puan_karti.py

from __future__ import annotations
from typing import Dict, Any, List, Optional, Tuple
from time import sleep

from temel_analiz.veri.modeller import CompanyData
from temel_analiz.veri_saglayicilar.veri_saglayici import fetch_company
from temel_analiz.hesaplayicilar.oranlar import all_metrics
from temel_analiz.hesaplayicilar.degerleme import compute_target_price

def _scale(val: Optional[float], target: float, tolerance: float, is_higher_better: bool = True) -> float:
    if val is None: return 50.0
    diff = (val - target) if is_higher_better else (target - val)
    score = 50.0 + (diff / tolerance) * 50.0
    return max(0.0, min(100.0, score))

def _avg(lst: List[Optional[float]]) -> float:
    valid = [x for x in lst if x is not None]
    return sum(valid) / len(valid) if valid else 50.0

def _get_sector_weights(sector_code: str) -> Dict[str, float]:
    # 1. Finansallar: Karlılık (ROE) Kraldır.
    if sector_code in ["BANK", "INSURANCE", "FINANCE", "FACTORING", "LEASING"]:
        return {"prof": 0.60, "lev": 0.05, "cf": 0.05, "val": 0.30}
    
    # 2. GYO: Borç ve Değerleme önemlidir.
    elif sector_code == "REIT":
        return {"prof": 0.20, "lev": 0.35, "cf": 0.10, "val": 0.35}
    
    # 3. Ağır Sanayi: Nakit Akışı ve Borç Yönetimi.
    elif sector_code in ["AVIATION", "TELECOM", "ENERGY", "STEEL", "MINING", "REFINERY", "LOGISTICS"]:
        return {"prof": 0.30, "lev": 0.25, "cf": 0.25, "val": 0.20}
    
    # 4. Hızlı Tüketim & Teknoloji: Büyüme (Karlılık içinde) ve Ciro.
    elif sector_code in ["RETAIL", "FOOD", "TECH", "DEFENSE", "TEXTILE"]:
        return {"prof": 0.40, "lev": 0.20, "cf": 0.20, "val": 0.20}
    
    # 5. Diğer
    else:
        return {"prof": 0.35, "lev": 0.25, "cf": 0.20, "val": 0.20}

def calculate_score(company: CompanyData, m: Dict[str, Any], target_price: float) -> Tuple[float, Dict[str, float]]:
    sec = company.sector_normalized
    prof = m.get("profitability", {})
    lev = m.get("leverage_liquidity", {})
    cf = m.get("cashflow_quality", {})
    price = company.last_price or 1.0
    
    # --- 1. KARLILIK SKORU ---
    if sec in ["BANK", "INSURANCE", "FINANCE"]:
        s_prof = _avg([_scale(prof.get("roe"), 0.35, 0.15), _scale(prof.get("roa"), 0.03, 0.02)])
    else:
        s_prof = _avg([
            _scale(prof.get("net_margin"), 0.08, 0.08),
            _scale(prof.get("ebitda_margin"), 0.12, 0.10),
            _scale(prof.get("roe"), 0.30, 0.20)
        ])

    # --- 2. BORÇLULUK SKORU ---
    if sec in ["BANK", "INSURANCE", "FINANCE"]:
        s_lev = 85.0
    else:
        # Sektörel Borç Toleransı
        target_nde = 4.5 if sec in ["AVIATION", "TELECOM", "ENERGY", "LOGISTICS", "CONSTRUCTION"] else 2.5
        s_lev = _avg([
            _scale(lev.get("debt_to_equity"), 1.5, 1.0, is_higher_better=False),
            _scale(lev.get("current_ratio"), 1.2, 0.5),
            _scale(lev.get("net_debt_ebitda"), target_nde, 2.5, is_higher_better=False)
        ])

    # --- 3. NAKİT AKIŞI SKORU ---
    if sec in ["BANK", "INSURANCE", "REIT", "FINANCE"]:
        s_cf = 70.0
    else:
        # Teknoloji ve Enerji şirketleri yüksek çarpanla işlem görür (EV/EBITDA)
        target_ev = 12.0 if sec in ["TECH", "DEFENSE", "ENERGY"] else 8.0
        s_cf = _avg([
            _scale(cf.get("fcf_margin"), 0.05, 0.10),
            _scale(cf.get("ev_ebitda"), target_ev, 5.0, is_higher_better=False)
        ])
        
    # --- 4. DEĞERLEME / POTANSİYEL SKORU ---
    # Potansiyel ne kadar yüksekse skor o kadar artar.
    upside = 0
    if target_price and price:
        upside = ((target_price - price) / price) * 100.0
    
    # %50 potansiyel 100 puandır. -%20 potansiyel 30 puandır.
    s_val = 50.0 + (upside * 0.6)
    s_val = max(10.0, min(100.0, s_val))
    
    w = _get_sector_weights(sec)
    total = (s_prof * w["prof"]) + (s_lev * w["lev"]) + (s_cf * w["cf"]) + (s_val * w["val"])
    
    subscores = {"profitability": s_prof, "leverage_liquidity": s_lev, "cashflow_quality": s_cf, "valuation_score": s_val}
    return total, subscores

def build_payload(company: CompanyData) -> Dict[str, Any]:
    if not company or not company.periods: return {}
    m = all_metrics(company)
    target_price, band, method_desc = compute_target_price(company, m)
    
    total_score, subscores = calculate_score(company, m, target_price)
    
    return {
        "symbol": company.symbol,
        "sector": company.sector_normalized,
        "price": company.last_price,
        "mrq_date": company.most_recent_quarter_date(),
        "metrics": m,
        "subscores": subscores,
        "score_total_0_100": total_score,
        "valuation": {
            "target_price": target_price,
            "confidence_band": band,
            "method": method_desc,
            "target_date": "12 Ay"
        }
    }

def analyze_symbols(symbols: List[str], save: bool = False, sleep_sec: float = 0.0) -> Tuple[List[Dict[str, Any]], List[Tuple[str, str]]]:
    payloads = []
    errors = []
    for s in symbols:
        try:
            c = fetch_company(s)
            if not c.periods:
                errors.append((s, "Veri yok."))
                continue
            payloads.append(build_payload(c))
            if sleep_sec > 0: sleep(sleep_sec)
        except Exception as e:
            errors.append((s, str(e)))
    return payloads, errors

def pie_data_from_payload(p: Dict[str, Any]) -> Tuple[List[str], List[float]]:
    sub = p.get("subscores", {})
    return (
        ["Karlılık", "Borçluluk", "Nakit/Kalite", "Potansiyel"], 
        [sub.get("profitability", 0.0), sub.get("leverage_liquidity", 0.0), sub.get("cashflow_quality", 0.0), sub.get("valuation_score", 0.0)]
    )