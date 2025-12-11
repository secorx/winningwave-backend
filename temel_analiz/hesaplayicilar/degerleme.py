# coding: utf-8
# temel_analiz/hesaplayicilar/degerleme.py

from __future__ import annotations
from typing import Dict, Any, Optional, Tuple
import math
from temel_analiz.veri.modeller import CompanyData

# --- KURUMSAL DEĞERLEME KÜTÜPHANESİ ---
SECTOR_PARAMS = {
    "BANK":      {"method": "PB", "multiplier": 1.60, "desc": "PD/DD"},
    "INSURANCE": {"method": "PB", "multiplier": 3.00, "desc": "PD/DD"},
    "FINANCE":   {"method": "PB", "multiplier": 2.50, "desc": "PD/DD"},
    "REIT":      {"method": "PB", "multiplier": 1.30, "desc": "PD/DD (NAV)"},
    "HOLDING":   {"method": "PB_PE_MIX", "multiplier": 1.20, "desc": "Net Aktif Değer"},
    "DEFENSE":   {"method": "EV_EBITDA", "multiplier": 20.0, "desc": "FD/FAVÖK"},
    "TECH":      {"method": "EV_EBITDA", "multiplier": 22.0, "desc": "FD/FAVÖK"},
    "AVIATION":  {"method": "EV_EBITDA", "multiplier": 8.00, "desc": "FD/FAVÖK"},
    "ENERGY":    {"method": "EV_EBITDA", "multiplier": 12.0, "desc": "FD/FAVÖK"},
    "TELECOM":   {"method": "EV_EBITDA", "multiplier": 7.50, "desc": "FD/FAVÖK"},
    "RETAIL":    {"method": "EV_EBITDA", "multiplier": 13.0, "desc": "FD/FAVÖK"},
    "FOOD":      {"method": "EV_EBITDA", "multiplier": 14.0, "desc": "FD/FAVÖK"},
    "INDUSTRY":  {"method": "EV_EBITDA", "multiplier": 9.00, "desc": "FD/FAVÖK"}
}

def calculate_graham_value(eps: float, bvps: float) -> float:
    """Benjamin Graham İçsel Değer Formülü: Sqrt(22.5 * EPS * BVPS)"""
    if eps <= 0 or bvps <= 0: return 0.0
    try:
        return math.sqrt(22.5 * eps * bvps)
    except: return 0.0

def calculate_lynch_value(eps: float, growth_rate: float) -> float:
    """Peter Lynch Adil Değer Formülü: PEG Ratio = 1"""
    if eps <= 0: return 0.0
    # Büyüme oranını %5 ile %35 arasında sınırla (Aşırı uçuk değerleri engelle)
    safe_growth = min(max(growth_rate, 0.05), 0.35)
    fair_pe = safe_growth * 100 # Örn: %20 büyüme = 20 F/K
    return eps * fair_pe

def compute_target_price(company: CompanyData, metrics: Dict[str, Any]) -> Tuple[Optional[float], Optional[Tuple[float, float]], str]:
    if not company or not company.periods or not company.last_price:
        return (None, None, "Veri Yetersiz")

    mrq = company.mrq()
    sector_code = company.sector_normalized
    price = company.last_price
    
    # Sektör Parametreleri (Varsayılan: INDUSTRY)
    params = SECTOR_PARAMS.get(sector_code, SECTOR_PARAMS["INDUSTRY"])
    base_mult = params["multiplier"]
    
    # --- TEMEL VERİLERİ HAZIRLA (TTM) ---
    periods = company.periods[:4]
    ttm_ebitda = sum((p.ebitda or 0) for p in periods)
    ttm_net_income = sum((p.net_income or 0) for p in periods)
    ttm_revenue = sum((p.revenue or 0) for p in periods)
    
    equity = mrq.equity if mrq.equity else 0
    shares = mrq.shares_out if mrq.shares_out else 1
    net_debt = (mrq.total_debt or 0) - (mrq.cash_and_equivalents or 0)
    
    # Hisse Başına Veriler
    eps = ttm_net_income / shares
    bvps = equity / shares
    revenue_ps = ttm_revenue / shares

    # Büyüme Oranı (Growth) Hesapla
    growth_rate = 0.10 # Varsayılan %10 (Enflasyonist ortam)
    if len(company.periods) >= 5:
        try:
            past_rev = sum((p.revenue or 0) for p in company.periods[4:8])
            if past_rev > 0:
                growth_rate = (ttm_revenue - past_rev) / past_rev
        except: pass

    # ==========================================
    # 1. YÖNTEM: PİYASA ÇARPANLARI (Market Multiples)
    # ==========================================
    market_price = 0.0
    
    # Banka/Finans ise PD/DD kullan
    if params["method"] == "PB" or (ttm_net_income < 0 and ttm_ebitda < 0):
        market_price = bvps * base_mult
    # Sanayi ise FD/FAVÖK kullan
    elif params["method"] == "EV_EBITDA":
        if ttm_ebitda > 0:
            target_ev = ttm_ebitda * base_mult
            # Havacılık için borç düzeltmesi
            debt_impact = net_debt * 0.5 if sector_code == "AVIATION" else net_debt
            market_price = (target_ev - debt_impact) / shares
        elif ttm_net_income > 0:
            market_price = eps * 12.0 # F/K Yedek
        elif ttm_revenue > 0:
            market_price = revenue_ps * 1.5 # Ciro Yedek

    # ==========================================
    # 2. YÖNTEM: BENJAMIN GRAHAM (İçsel Değer)
    # ==========================================
    graham_price = calculate_graham_value(eps, bvps)

    # ==========================================
    # 3. YÖNTEM: PETER LYNCH (Büyüme Değeri)
    # ==========================================
    lynch_price = calculate_lynch_value(eps, growth_rate)

    # ==========================================
    # HİBRİT HESAPLAMA (Ağırlıklı Ortalama)
    # ==========================================
    final_price = 0.0
    methods_used = []
    
    # Ağırlıklar: Piyasa %50, Graham %25, Lynch %25
    # Eğer biri hesaplanamazsa ağırlığı diğerine kaydır
    
    if market_price > 0:
        weight = 0.50
        # Eğer Graham/Lynch yoksa Piyasa %100 olur
        if graham_price == 0 and lynch_price == 0: weight = 1.0
        elif graham_price == 0 or lynch_price == 0: weight = 0.75
        
        final_price += market_price * weight
        methods_used.append("Piyasa Çarpanları")

    if graham_price > 0:
        weight = 0.25 if market_price > 0 else 0.50
        if lynch_price == 0 and market_price > 0: weight = 0.50
        final_price += graham_price * weight
        methods_used.append("Graham (İçsel)")

    if lynch_price > 0:
        weight = 0.25 if market_price > 0 else 0.50
        if graham_price == 0 and market_price > 0: weight = 0.50
        final_price += lynch_price * weight
        methods_used.append("Lynch (Büyüme)")

    # --- SON KONTROLLER ---
    if final_price <= 0:
        # Hiçbir model çalışmadıysa (Ağır zarar eden şirket)
        # En son çare: Defter Değeri veya Ciro
        if bvps > 0: 
            final_price = bvps
            methods_used = ["Defter Değeri (Zarar Nedeniyle)"]
        else:
            return (None, None, "Hesaplanamadı")

    # Yapay Zeka Filtresi (Uçuk fiyatları törpüle)
    if final_price > price * 4.0: final_price = price * 2.5
    if final_price < price * 0.4: final_price = price * 0.6

    band = (final_price * 0.85, final_price * 1.15)
    method_desc = "Karma Model: " + " + ".join(methods_used)

    return (final_price, band, method_desc)