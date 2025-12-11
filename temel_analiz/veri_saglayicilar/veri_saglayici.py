# coding: utf-8
# temel_analiz/veri_saglayicilar/veri_saglayici.py
from __future__ import annotations
import os
import time
import random
import yfinance as yf
import pandas as pd
from temel_analiz.veri.modeller import CompanyData, FinancialPeriod
from temel_analiz.veri_saglayicilar.yerel_csv import load_company_from_csv

# --- GÜVENLİ IMPORT BLOĞU ---
try:
    from temel_analiz.veri_saglayicilar.sektor_verisi import get_sector_from_map
except ImportError:
    try:
        from sektor_verisi import get_sector_from_map
    except ImportError:
        def get_sector_from_map(symbol: str):
            return None


def _detect_sector_smart(symbol: str, info: dict) -> str:
    """
    Sektörü akıllıca tespit eder:
    - Önce kendi BIST sektor haritandan bakar
    - Sonra Yahoo info.sector / info.industry üzerinden normalize eder
    """
    mapped_sector = get_sector_from_map(symbol)
    if mapped_sector:
        return mapped_sector

    s = str(info.get("sector", "")).lower()
    i = str(info.get("industry", "")).lower()

    if "bank" in s:
        return "BANK"
    if "insurance" in s:
        return "INSURANCE"
    if "real estate" in s or "reit" in s:
        return "REIT"
    if "airlines" in i:
        return "AVIATION"
    if "telecom" in s:
        return "TELECOM"

    return "INDUSTRY"


def fetch_company(symbol: str) -> CompanyData:
    """
    Dışarıdan kullandığın ana fonksiyon.
    - 3 kez deneme yapar.
    - Hız limiti (429) için bekler ve tekrar dener.
    - Delist / veri yok / currentTradingPeriod hatalarını kalıcı 'VERİ YOK' sayar.
    """
    sym = symbol.upper().strip()

    # --- STEALTH MOD: Rastgele Bekleme ---
    # Ban yememek için 0.2 - 0.6 sn arası bekle
    time.sleep(random.uniform(0.2, 0.6))

    # 3 Kez Deneme Hakkı
    for attempt in range(3):
        try:
            return _fetch_internal(sym)
        except Exception as e:
            err_msg = str(e).lower()

            # Hız limiti / 429
            if "too many requests" in err_msg or "429" in err_msg:
                print(f"⚠️  UYARI ({sym}): Hız sınırı! 5 saniye bekleniyor...")
                time.sleep(5)
                continue

            # Kalıcı veri yok: delist / hiç fiyat / currentTradingPeriod bug'i / timestamp keyerror
            elif (
                "delisted" in err_msg
                or "no data" in err_msg
                or "currenttradingperiod" in err_msg
                or err_msg.startswith("timestamp(")
            ):
                if attempt == 0:
                    print(
                        f"❌  VERİ YOK ({sym}): "
                        f"Borsa kotundan çıkmış, fiyat verisi yok veya Yahoo tarafında seans/fiyat bilgisi eksik."
                    )
                return None

            # Diğer hatalar (Bağlantı kopması vs) için tekrar dene
            else:
                if attempt == 2:
                    print(f"❌  HATA ({sym}): {e}")
                    return None

            time.sleep(1)

    return None


def _fetch_internal(sym: str) -> CompanyData:
    """
    Asıl iş yapan fonksiyon:
    - Varsa yerel CSV'den okur.
    - Yoksa Yahoo Finance üzerinden finansal tabloları çeker.
    - CompanyData nesnesi üretir.
    """
    # Önce yerel CSV var mı bak
    csv_path = os.path.join("data", f"{sym}.csv")
    if os.path.exists(csv_path):
        cd = load_company_from_csv(sym, csv_path)
        if cd:
            return cd

    ticker = yf.Ticker(sym)

    # Bağlantıyı tetikle (bazı yfinance bug'larını azaltıyor)
    try:
        ticker.history(period="5d")
    except Exception:
        pass

    fast_info = ticker.fast_info
    last_price = fast_info.last_price if fast_info else None

    info = ticker.info or {}
    if not info and last_price:
        info = {}

    sector_normalized = _detect_sector_smart(sym, info)
    raw_sector = info.get("sector", "Unknown")
    shares_out = info.get("sharesOutstanding") or (fast_info.shares if fast_info else None)

    # Tabloları çek
    fin_q = ticker.quarterly_financials
    bal_q = ticker.quarterly_balance_sheet
    cf_q = ticker.quarterly_cashflow

    # Eğer çeyreklikler boşsa yıllıklara fallback
    if fin_q.empty:
        fin_q = ticker.financials
        bal_q = ticker.balance_sheet
        cf_q = ticker.cashflow
        if fin_q.empty:
            # Hata fırlat ki yukarıda yakalayıp log basalım
            raise ValueError("Finansal tablolar boş.")

    periods: list[FinancialPeriod] = []

    # Son 4–5 kolonu al (en yeni → eski)
    cols = fin_q.columns[:5]

    for date_col in cols:
        def get_val(df: pd.DataFrame, keys):
            for k in keys:
                if k in df.index:
                    val = df.loc[k, date_col]
                    if pd.notna(val):
                        return float(val)
            return 0.0

        rev = get_val(fin_q, ["Total Revenue", "Operating Revenue", "Revenue"])
        net_inc = get_val(fin_q, ["Net Income", "Net Income Common Stockholders"])

        ebitda = get_val(fin_q, ["EBITDA", "Normalized EBITDA"])
        if ebitda == 0.0 and sector_normalized not in ["BANK", "INSURANCE"]:
            op_inc = get_val(fin_q, ["Operating Income", "EBIT"])
            depr = abs(get_val(cf_q, ["Depreciation", "Depreciation And Amortization"]))
            ebitda = op_inc + depr

        equity = get_val(bal_q, ["Stockholders Equity", "Total Equity Gross Minority Interest"])
        assets = get_val(bal_q, ["Total Assets"])
        liab = get_val(
            bal_q,
            ["Total Liabilities Net Minority Interest", "Total Liabilities"],
        )

        total_debt = get_val(bal_q, ["Total Debt", "Total Financial Debt"])
        if total_debt == 0.0 and sector_normalized not in ["BANK", "INSURANCE"]:
            total_debt = get_val(bal_q, ["Long Term Debt"]) + get_val(bal_q, ["Current Debt"])

        cash = get_val(bal_q, ["Cash And Cash Equivalents", "Cash Financial"])
        cfo = get_val(cf_q, ["Operating Cash Flow", "Total Cash From Operating Activities"])
        capex = abs(get_val(cf_q, ["Capital Expenditure", "Capital Expenditures"]))

        periods.append(
            FinancialPeriod(
                period=date_col.strftime("%Y-%m-%d"),
                revenue=rev,
                net_income=net_inc,
                ebitda=ebitda,
                equity=equity,
                assets=assets,
                liabilities=liab,
                cfo=cfo,
                capex=capex,
                shares_out=shares_out if shares_out else 1.0,
                price=None,
                total_debt=total_debt,
                cash_and_equivalents=cash,
            )
        )

    if last_price and periods:
        periods[0].price = last_price

    return CompanyData(
        symbol=sym,
        raw_sector=raw_sector,
        sector_normalized=sector_normalized,
        periods=periods,
        last_price=last_price,
        metrics_ttm=info,
    )
