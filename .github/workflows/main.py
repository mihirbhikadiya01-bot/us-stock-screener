"""
============================================================================
 US / Canada Multibagger Screener Bot  —  SINGLE FILE (main.py)
============================================================================
 A SCREENER ONLY. No buy/sell calls, no price targets, no advice. It scans
 US (and optionally Canadian) stocks, scores them on QUALITY + GROWTH +
 TECHNICAL TREND, ranks them, and sends one weekly Telegram report. You make
 every decision.

 SCORING (out of 100):
   Growth ............... 25
   Profitability ........ 20
   Technical / Trend .... 25   <-- CANSLIM + Minervini-style trend rules
   Financial Strength ... 15
   Valuation ............ 15

 The technical layer turns two well-known growth/momentum methodologies into
 numeric rules (no text copied from any book):
   - William O'Neil / CANSLIM: strong relative strength, near new highs,
     earnings leadership.
   - Mark Minervini's "Trend Template": price above rising 50/150/200-day
     moving averages, within range of the 52-week high, well off the low.
 These catch stocks already in confirmed uptrends with leading fundamentals —
 the historical profile of big multibagger winners. (Not a guarantee.)

 DATA: yfinance (free). US tickers plain (AAPL); Canada uses ".TO".
       Relative Strength is ranked across whatever universe you scan.

 WRITES: latest_results.csv, history.csv (for New / Removed tracking).

 ⚠ Screening/education tool only. Data may be incomplete or delayed.
============================================================================
"""

import os
import csv
import math
import time
import datetime as dt
from io import StringIO

import pandas as pd

try:
    import yfinance as yf
except Exception:
    yf = None

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import requests

UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")}

# ===========================================================================
# CONFIG (from environment / GitHub Secrets)
# ===========================================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

TEST_MODE = os.getenv("TEST_MODE", "True").lower() in ("1", "true", "yes")

# UNIVERSE (when TEST_MODE is False):
#   "SP500"   -> S&P 500 (large caps; reliable; ~500 names; ~30-45 min)
#   "NASDAQ"  -> all NASDAQ-listed (~3000; lots of growth small caps; 1.5-3 hrs)
#   "ALL_US"  -> NASDAQ + NYSE + AMEX (~6000; whole US market; covers S&P,
#                Nasdaq-100, Dow, etc. since they are all subsets; 3-5 hrs)
#   "ALL_NA"  -> ALL_US + Canada (whole North-American coverage we can get)
#   "TSX"     -> bundled Canadian large caps (.TO)
#   "BUNDLED" -> the hardcoded list below (fast, no download)
UNIVERSE = os.getenv("UNIVERSE", "SP500").upper()

RUN_SCHEDULER = os.getenv("RUN_SCHEDULER", "True").lower() in ("1", "true", "yes")
REQUEST_DELAY_SECONDS = float(os.getenv("REQUEST_DELAY_SECONDS", "0.7"))
MAX_STOCKS = int(os.getenv("MAX_STOCKS", "0"))   # 0 = no limit

# Weekly run: Sunday 17:00 America/New_York is awkward across DST, so we use a
# simple UTC schedule in the workflow. Here we just track the day for the loop.
TZ = dt.timezone.utc
RUN_WEEKDAY = 6      # Sunday
RUN_HOUR = 22        # 22:00 UTC (~5-6 PM US Eastern depending on DST)
RUN_MINUTE = 0

LATEST_CSV = "latest_results.csv"
HISTORY_CSV = "history.csv"

# ===========================================================================
# UNIVERSE
# ===========================================================================
TEST_STOCKS = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN"]

# Fallback bundled list: a mix of mega caps + known growth names.
BUNDLED = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "TSLA", "AMD", "CRM",
    "ADBE", "NFLX", "COST", "LLY", "UNH", "V", "MA", "HD", "PG", "KO",
    "NOW", "INTU", "PANW", "SNPS", "CDNS", "ANET", "MELI", "SHOP", "CRWD", "DDOG",
    "ZS", "NET", "SNOW", "MDB", "TEAM", "WDAY", "FTNT", "ABNB", "UBER", "AXON",
    "CELH", "ELF", "DECK", "MNST", "ODFL", "FAST", "POOL", "TTD", "DKNG", "PLTR",
    "SMCI", "ARM", "VRT", "CAVA", "DUOL", "TOST", "RBLX", "HIMS", "ONON", "GRAB",
]

# Bundled Canadian large caps (Toronto Stock Exchange, ".TO" for yfinance).
TSX_BUNDLED = [
    "RY.TO", "TD.TO", "BNS.TO", "BMO.TO", "CM.TO", "ENB.TO", "CNQ.TO", "TRP.TO",
    "SU.TO", "CNR.TO", "CP.TO", "BN.TO", "BAM.TO", "MFC.TO", "SLF.TO", "GWO.TO",
    "SHOP.TO", "ATD.TO", "WCN.TO", "CSU.TO", "GIB-A.TO", "DOL.TO", "FNV.TO",
    "AEM.TO", "ABX.TO", "NTR.TO", "TECK-B.TO", "FM.TO", "L.TO", "MRU.TO",
    "QSR.TO", "T.TO", "BCE.TO", "FTS.TO", "EMA.TO", "WSP.TO", "STN.TO", "TRI.TO",
]


def _sp500():
    try:
        r = requests.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                         headers=UA, timeout=30)
        tables = pd.read_html(StringIO(r.text))
        df = tables[0]
        col = "Symbol" if "Symbol" in df.columns else df.columns[0]
        return [str(s).strip().replace(".", "-") for s in df[col].dropna()]
    except Exception as e:
        print(f"  S&P 500 download failed: {e}")
        return None


def _nasdaq_files(include_other=True):
    """Download NASDAQ Trader symbol directory. Returns clean common-stock list."""
    syms = []
    sources = [("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
                "Symbol", "ETF", "Test Issue")]
    if include_other:
        sources.append(("https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
                        "ACT Symbol", "ETF", "Test Issue"))
    for url, sym_col, etf_col, test_col in sources:
        try:
            r = requests.get(url, headers=UA, timeout=30)
            df = pd.read_csv(StringIO(r.text), sep="|")
            # Drop the footer row ("File Creation Time...")
            df = df[~df[sym_col].astype(str).str.contains("File Creation", na=False)]
            if test_col in df.columns:
                df = df[df[test_col] == "N"]
            if etf_col in df.columns:
                df = df[df[etf_col] == "N"]
            for s in df[sym_col].dropna():
                s = str(s).strip()
                # Keep plain common-stock tickers; skip warrants/units/pref ($, ., etc.)
                if s and s.isalpha() and len(s) <= 5:
                    syms.append(s)
        except Exception as e:
            print(f"  {url} failed: {e}")
    return sorted(set(syms)) if syms else None


def get_universe():
    if TEST_MODE:
        return TEST_STOCKS
    if UNIVERSE == "BUNDLED":
        tickers = BUNDLED
    elif UNIVERSE == "TSX":
        tickers = TSX_BUNDLED
    elif UNIVERSE == "SP500":
        tickers = _sp500() or BUNDLED
    elif UNIVERSE == "NASDAQ":
        tickers = _nasdaq_files(include_other=False) or BUNDLED
    elif UNIVERSE == "ALL_US":
        tickers = _nasdaq_files(include_other=True) or BUNDLED
    elif UNIVERSE == "ALL_NA":
        us = _nasdaq_files(include_other=True) or BUNDLED
        tickers = list(dict.fromkeys(us + TSX_BUNDLED))  # US + Canada, de-duped
    else:
        tickers = BUNDLED
    if MAX_STOCKS and MAX_STOCKS > 0:
        tickers = tickers[:MAX_STOCKS]
    return tickers


# ===========================================================================
# HELPERS
# ===========================================================================
def _safe(v):
    try:
        if v is None:
            return None
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _row(df, names):
    if df is None or not hasattr(df, "index") or df.empty:
        return None
    for name in names:
        if name in df.index:
            s = df.loc[name].dropna()
            if not s.empty:
                try:
                    s = s.sort_index(ascending=False)
                except Exception:
                    pass
                return s
    return None


def _cagr(s):
    if s is None or len(s) < 2:
        return None
    latest, oldest = _safe(s.iloc[0]), _safe(s.iloc[-1])
    yrs = len(s) - 1
    if not latest or not oldest or oldest <= 0 or latest <= 0:
        return None
    return ((latest / oldest) ** (1.0 / yrs) - 1.0) * 100.0


def _consistency(s):
    if s is None or len(s) < 2:
        return None
    vals = [_safe(v) for v in list(s.values)[::-1]]
    vals = [v for v in vals if v is not None]
    if len(vals) < 2:
        return None
    ups = sum(1 for i in range(1, len(vals)) if vals[i] > vals[i - 1])
    return ups, len(vals) - 1


def _band(value, bands, default=0):
    if value is None:
        return default
    for low, high, pts in bands:
        if (low is None or value >= low) and (high is None or value < high):
            return pts
    return default


# ===========================================================================
# FETCH (fundamentals + price history for technicals)
# ===========================================================================
def fetch_stock(ticker):
    d = {"ticker": ticker, "name": ticker, "ok": False, "missing": []}
    if yf is None:
        d["error"] = "yfinance not installed"
        return d
    try:
        tk = yf.Ticker(ticker)
        try:
            info = tk.get_info()
        except Exception:
            info = getattr(tk, "info", {}) or {}
        if not isinstance(info, dict):
            info = {}

        d["name"] = info.get("shortName") or info.get("longName") or ticker
        d["sector"] = info.get("sector")
        d["currency"] = info.get("currency") or "USD"

        try:
            income = tk.financials
        except Exception:
            income = None
        try:
            balance = tk.balance_sheet
        except Exception:
            balance = None
        try:
            cash = tk.cashflow
        except Exception:
            cash = None

        # ---- Valuation / size ----
        mc = _safe(info.get("marketCap"))
        d["market_cap"] = mc
        d["price"] = _safe(info.get("currentPrice")) or _safe(info.get("regularMarketPrice"))
        d["pe"] = _safe(info.get("trailingPE"))
        d["pb"] = _safe(info.get("priceToBook"))
        d["peg"] = _safe(info.get("trailingPegRatio")) or _safe(info.get("pegRatio"))
        d["ps"] = _safe(info.get("priceToSalesTrailing12Months"))
        d["eps"] = _safe(info.get("trailingEps"))
        d["bvps"] = _safe(info.get("bookValue"))

        # ---- Financial strength ----
        de = None
        td = _row(balance, ["Total Debt"])
        eq = _row(balance, ["Stockholders Equity", "Common Stock Equity"])
        if td is not None and eq is not None:
            a, b = _safe(td.iloc[0]), _safe(eq.iloc[0])
            if a is not None and b and b > 0:
                de = a / b
        if de is None and info.get("debtToEquity") is not None:
            de = _safe(info.get("debtToEquity"))
            de = de / 100.0 if de is not None else None
        d["debt_to_equity"] = de
        d["current_ratio"] = _safe(info.get("currentRatio"))

        ebit = _row(income, ["Operating Income", "EBIT"])
        interest = _row(income, ["Interest Expense"])
        ic = None
        if ebit is not None and interest is not None:
            e, i = _safe(ebit.iloc[0]), _safe(interest.iloc[0])
            if e is not None and i is not None and abs(i) > 0:
                ic = e / abs(i)
        d["interest_coverage"] = ic

        ocf = _row(cash, ["Operating Cash Flow", "Total Cash From Operating Activities"])
        d["operating_cash_flow"] = _safe(ocf.iloc[0]) if ocf is not None \
            else _safe(info.get("operatingCashflow"))
        fcf = _row(cash, ["Free Cash Flow"])
        d["free_cash_flow"] = _safe(fcf.iloc[0]) if fcf is not None \
            else _safe(info.get("freeCashflow"))

        # ---- Profitability ----
        roe = _safe(info.get("returnOnEquity"))
        d["roe"] = roe * 100.0 if roe is not None else None
        npm = _safe(info.get("profitMargins"))
        d["net_margin"] = npm * 100.0 if npm is not None else None
        opm = _safe(info.get("operatingMargins"))
        d["operating_margin"] = opm * 100.0 if opm is not None else None

        roce = None
        ta = _row(balance, ["Total Assets"])
        cl = _row(balance, ["Current Liabilities", "Total Current Liabilities"])
        if ebit is not None and ta is not None and cl is not None:
            e, A, C = _safe(ebit.iloc[0]), _safe(ta.iloc[0]), _safe(cl.iloc[0])
            if e is not None and A is not None and C is not None and (A - C) > 0:
                roce = e / (A - C) * 100.0
        d["roce"] = roce

        # ---- Growth ----
        rev = _row(income, ["Total Revenue"])
        ni = _row(income, ["Net Income", "Net Income Common Stockholders"])
        eps_s = _row(income, ["Diluted EPS", "Basic EPS"])
        rg = _cagr(rev)
        if rg is None and info.get("revenueGrowth") is not None:
            rg = _safe(info.get("revenueGrowth"))
            rg = rg * 100.0 if rg is not None else None
        d["revenue_growth"] = rg
        pg = _cagr(ni)
        if pg is None and info.get("earningsGrowth") is not None:
            pg = _safe(info.get("earningsGrowth"))
            pg = pg * 100.0 if pg is not None else None
        d["profit_growth"] = pg
        d["eps_growth"] = _cagr(eps_s)
        d["revenue_consistency"] = _consistency(rev)
        d["profit_consistency"] = _consistency(ni)
        d["latest_net_income"] = _safe(ni.iloc[0]) if ni is not None else None

        # ---- Price history -> technicals ----
        _attach_technicals(d, tk, info)

        key = ["roe", "net_margin", "operating_margin", "revenue_growth",
               "profit_growth", "pe", "eps", "sma200", "rs_return"]
        found = sum(1 for f in key if d.get(f) is not None)
        d["confidence"] = round(found / len(key), 2)
        d["missing"] = [f for f in key if d.get(f) is None]
        d["ok"] = found > 0
    except Exception as e:
        d["error"] = str(e)
        d["ok"] = False
    return d


def _attach_technicals(d, tk, info):
    """Compute moving averages, 52-wk position, and 6-month return for RS."""
    try:
        hist = tk.history(period="1y", interval="1d", auto_adjust=True)
    except Exception:
        hist = None
    for k in ("sma50", "sma150", "sma200", "sma200_prev", "high_52w", "low_52w",
              "rs_return", "vol_ratio"):
        d[k] = None
    if hist is None or hist.empty or "Close" not in hist:
        return
    close = hist["Close"].dropna()
    if len(close) < 60:
        return
    price = _safe(close.iloc[-1])
    d["price"] = d.get("price") or price

    def sma(n, shift=0):
        if len(close) >= n + shift:
            return _safe(close.iloc[-(n + shift):len(close) - shift if shift else None].mean()
                         if shift else close.iloc[-n:].mean())
        return None

    d["sma50"] = _safe(close.tail(50).mean()) if len(close) >= 50 else None
    d["sma150"] = _safe(close.tail(150).mean()) if len(close) >= 150 else None
    d["sma200"] = _safe(close.tail(200).mean()) if len(close) >= 200 else None
    # 200-DMA about one month (21 trading days) ago, to test the slope
    if len(close) >= 221:
        d["sma200_prev"] = _safe(close.iloc[-221:-21].mean())
    d["high_52w"] = _safe(close.max())
    d["low_52w"] = _safe(close.min())
    # 6-month (~126 trading days) return -> used for Relative Strength ranking
    if len(close) >= 127 and price:
        past = _safe(close.iloc[-127])
        if past and past > 0:
            d["rs_return"] = (price / past - 1.0) * 100.0
    # Volume confirmation: recent 10-day vs 50-day average
    if "Volume" in hist:
        vol = hist["Volume"].dropna()
        if len(vol) >= 50:
            v10, v50 = _safe(vol.tail(10).mean()), _safe(vol.tail(50).mean())
            if v10 and v50 and v50 > 0:
                d["vol_ratio"] = v10 / v50


def fetch_many(tickers):
    results = []
    n = len(tickers)
    for i, t in enumerate(tickers, 1):
        if i % 25 == 0 or i == 1:
            print(f"  [{i}/{n}] ...")
        results.append(fetch_stock(t))
        if i < n:
            time.sleep(REQUEST_DELAY_SECONDS)
    return results


# ===========================================================================
# RELATIVE STRENGTH RATING (rank 6-month returns across the scanned universe)
# ===========================================================================
def compute_rs_ratings(results):
    valid = [(d, d["rs_return"]) for d in results if d.get("rs_return") is not None]
    valid.sort(key=lambda x: x[1])
    n = len(valid)
    for rank, (d, _) in enumerate(valid, 1):
        d["rs_rating"] = max(1, round(rank / n * 99)) if n else None
    for d in results:
        d.setdefault("rs_rating", None)


# ===========================================================================
# TECHNICAL TREND TEMPLATE (Minervini-style, expressed as our own rules)
# ===========================================================================
def trend_template_count(d):
    """Return (#criteria met out of 7, list_of_pass_bools)."""
    price = d.get("price")
    s50, s150, s200 = d.get("sma50"), d.get("sma150"), d.get("sma200")
    s200p = d.get("sma200_prev")
    hi, lo = d.get("high_52w"), d.get("low_52w")
    checks = []
    checks.append(price is not None and s150 is not None and s200 is not None
                  and price > s150 and price > s200)            # 1
    checks.append(s150 is not None and s200 is not None and s150 > s200)  # 2
    checks.append(s200 is not None and s200p is not None and s200 > s200p)  # 3 rising
    checks.append(s50 is not None and s150 is not None and s200 is not None
                  and s50 > s150 > s200)                        # 4
    checks.append(price is not None and s50 is not None and price > s50)   # 5
    checks.append(price is not None and lo is not None and lo > 0
                  and price >= 1.30 * lo)                       # 6 >=30% above low
    checks.append(price is not None and hi is not None and hi > 0
                  and price >= 0.75 * hi)                       # 7 within 25% of high
    return sum(1 for c in checks if c), checks


# ===========================================================================
# SCORING
# ===========================================================================
def score_stock(d):
    flags = []

    # GROWTH (25)
    gr = 0
    gr += _band(d.get("revenue_growth"), [(25, None, 6), (15, 25, 5), (8, 15, 3), (None, 8, 0)])
    gr += _band(d.get("profit_growth"), [(25, None, 6), (15, 25, 5), (8, 15, 3), (None, 8, 0)])
    gr += _band(d.get("eps_growth"), [(25, None, 5), (15, 25, 4), (8, 15, 2), (None, 8, 0)])
    rc = d.get("revenue_consistency")
    if rc and rc[1] > 0 and rc[0] / rc[1] >= 0.75:
        gr += 4
    pc = d.get("profit_consistency")
    if pc and pc[1] > 0 and pc[0] / pc[1] >= 0.75:
        gr += 4

    # PROFITABILITY (20)
    prof = 0
    prof += _band(d.get("roe"), [(20, None, 6), (15, 20, 5), (10, 15, 3), (None, 10, 0)])
    prof += _band(d.get("roce"), [(20, None, 6), (15, 20, 5), (10, 15, 3), (None, 10, 0)])
    prof += _band(d.get("net_margin"), [(15, None, 4), (10, 15, 3), (5, 10, 1), (None, 5, 0)])
    prof += _band(d.get("operating_margin"), [(20, None, 4), (15, 20, 3), (10, 15, 1), (None, 10, 0)])

    # TECHNICAL / TREND (25) = trend template (15) + relative strength (10)
    tcount, _ = trend_template_count(d)
    d["trend_pass"] = tcount
    tech = round(tcount / 7 * 15)
    rs = d.get("rs_rating")
    tech += _band(rs, [(90, None, 10), (80, 90, 8), (70, 80, 6), (50, 70, 3), (None, 50, 0)])
    d["trend_template"] = (tcount == 7 and (rs or 0) >= 70)

    # FINANCIAL STRENGTH (15)
    fs = 0
    de = d.get("debt_to_equity")
    fs += _band(de, [(None, 0.5, 6), (0.5, 1.0, 4), (1.0, 2.0, 2), (2.0, None, 0)])
    if de is not None and de > 3:
        flags.append("HIGH DEBT")
    fs += _band(d.get("current_ratio"), [(2, None, 3), (1.5, 2, 2), (1, 1.5, 1), (None, 1, 0)])
    fs += _band(d.get("interest_coverage"), [(8, None, 3), (4, 8, 2), (2, 4, 1), (None, 2, 0)])
    if (d.get("free_cash_flow") or 0) > 0:
        fs += 3

    # VALUATION (15)  PE(5)+PEG(6)+PB/PS(4)
    val = 0
    pe = d.get("pe")
    val += _band(pe, [(None, 20, 5), (20, 35, 4), (35, 60, 2), (60, None, 0)])
    val += _band(d.get("peg"), [(None, 1, 6), (1, 1.5, 5), (1.5, 2.5, 3), (2.5, None, 0)])
    pb = d.get("pb")
    if pb is not None:
        val += _band(pb, [(None, 3, 4), (3, 8, 2), (8, 15, 1), (15, None, 0)])
    elif d.get("ps") is not None:
        val += _band(d.get("ps"), [(None, 3, 4), (3, 8, 2), (8, 15, 1), (15, None, 0)])

    total = gr + prof + tech + fs + val
    d.update({"growth_pts": gr, "prof": prof, "tech": tech, "fs": fs, "val": val,
              "score": round(total), "flags": flags})
    d["strengths"] = _strengths(d)
    d["concerns"] = _concerns(d, flags)
    return d


def _strengths(d):
    out = []
    if (d.get("revenue_growth") or 0) >= 15:
        out.append("Strong revenue growth")
    if (d.get("eps_growth") or 0) >= 15:
        out.append("Strong EPS growth")
    if (d.get("roe") or 0) >= 15:
        out.append("High ROE")
    if d.get("trend_template"):
        out.append("Confirmed uptrend")
    if (d.get("rs_rating") or 0) >= 85:
        out.append(f"Top relative strength ({d['rs_rating']})")
    if (d.get("free_cash_flow") or 0) > 0:
        out.append("Positive FCF")
    return out[:4] or ["Meets basic bar"]


def _concerns(d, flags):
    out = []
    if "HIGH DEBT" in flags:
        out.append("High debt")
    if d.get("price") and d.get("sma200") and d["price"] < d["sma200"]:
        out.append("Below 200-day avg (downtrend)")
    if (d.get("rs_rating") or 100) < 50:
        out.append("Weak relative strength")
    if (d.get("pe") or 0) > 60:
        out.append("Very rich valuation")
    if (d.get("latest_net_income") or 0) < 0:
        out.append("Not yet profitable")
    if d.get("confidence", 1) < 0.6:
        out.append("Incomplete data")
    return out[:4] or ["Normal market risk"]


# ===========================================================================
# CATEGORIES
# ===========================================================================
def is_multibagger(d):
    mc = d.get("market_cap")
    if mc is None or not (3e8 <= mc <= 1.5e10):   # ~$300M to $15B
        return False
    if (d.get("pe") or 0) > 120:
        return False
    return (
        (d.get("revenue_growth") or 0) > 15 and
        ((d.get("eps_growth") or 0) > 15 or (d.get("profit_growth") or 0) > 15) and
        (d.get("rs_rating") or 0) >= 80 and
        (d.get("trend_pass") or 0) >= 6 and
        d.get("score", 0) > 70
    )


def is_quality(d):
    mc = d.get("market_cap")
    if mc is None or mc <= 1.5e10:
        return False
    de = d.get("debt_to_equity")
    return (
        (d.get("roe") or 0) > 15 and
        (d.get("roce") or 0) > 12 and
        (de is None or de < 1.5) and
        (d.get("latest_net_income") or 0) > 0 and
        (d.get("price") and d.get("sma200") and d["price"] > d["sma200"]) and
        d.get("score", 0) > 70
    )


def is_momentum(d):
    return (
        (d.get("rs_rating") or 0) >= 90 and
        d.get("trend_template") and
        (d.get("latest_net_income") or 0) > 0
    )


def is_avoid(d):
    if d.get("score", 0) < 55:
        return True
    return ("HIGH DEBT" in d.get("flags", []) and
            d.get("price") and d.get("sma200") and d["price"] < d["sma200"])


# ===========================================================================
# TRACKING
# ===========================================================================
def read_previous():
    if not os.path.exists(LATEST_CSV):
        return set()
    try:
        return set(pd.read_csv(LATEST_CSV)["Ticker"].astype(str).str.upper())
    except Exception:
        return set()


def write_tracking(rows):
    cols = ["Date", "Ticker", "Category", "Score", "MarketCap", "RS", "Strengths", "Concerns"]
    pd.DataFrame(rows, columns=cols).to_csv(LATEST_CSV, index=False)
    write_header = not os.path.exists(HISTORY_CSV)
    with open(HISTORY_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(cols)
        for r in rows:
            w.writerow([r[c] for c in cols])


# ===========================================================================
# TELEGRAM
# ===========================================================================
def _mc(mc):
    if not mc:
        return "N/A"
    if mc >= 1e9:
        return f"${mc/1e9:.1f}B"
    return f"${mc/1e6:.0f}M"


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _g(v):   # growth-style number with sign
    return f"{v:+.0f}%" if v is not None else "–"


def _p(v):   # plain percent
    return f"{v:.0f}%" if v is not None else "–"


def _entry(i, d):
    up = d.get("price") and d.get("sma200") and d["price"] > d["sma200"]
    trend = "📈 uptrend" if up else "📉 below 200d"
    stats = (f"{_mc(d.get('market_cap'))} · RS {d.get('rs_rating', '–')} · "
             f"Rev {_g(d.get('revenue_growth'))} · ROE {_p(d.get('roe'))} · {trend}")
    name = _esc(d["name"][:22])
    head = f"<b>{i}. {_esc(d['ticker'])}</b> · {name} — <b>{d['score']}</b>/100"
    lines = [head, f"   {stats}"]
    concerns = [c for c in d.get("concerns", []) if c != "Normal market risk"]
    if concerns:
        lines.append(f"   ⚠️ {_esc(concerns[0])}")
    return "\n".join(lines)


def _section(title, items, empty="—"):
    out = [f"\n<b>{title}</b>"]
    if items:
        out += [_entry(i, d) for i, d in enumerate(items, 1)]
    else:
        out.append(f"   {empty}")
    return out


def build_message(multi, quality, momo, new_e, removed, notes):
    today = dt.datetime.now(TZ).strftime("%d %b %Y")
    L = ["🏆 <b>Weekly US / Canada Screener</b>", f"🗓 {today} (UTC)"]
    L += _section("🚀 Multibagger Candidates", multi[:5],
                  "None cleared the rules this week.")
    L += _section("💎 Quality Compounders", quality[:5],
                  "None cleared the rules this week.")
    L += _section("⚡ Momentum Leaders (RS 90+)", momo[:5],
                  "None cleared the rules this week.")
    L.append("\n<b>🆕 New this week</b>")
    L.append("   " + (", ".join(new_e[:6]) if new_e else "None"))
    L.append("<b>❌ Dropped off</b>")
    L.append("   " + (", ".join(removed[:6]) if removed else "None"))
    if notes:
        L.append(f"\n⚠️ <i>{len(notes)} stocks had incomplete data (skipped or low confidence).</i>")
    L.append("\n———\n<i>Screening tool only — not investment advice. "
             "Data may be delayed/incomplete. Do your own research.</i>")
    return "\n".join(L)


def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("\n⚠ TELEGRAM secrets not set — printing instead:\n")
        print(text)
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    chunk, chunks = "", []
    for line in text.split("\n"):
        if len(chunk) + len(line) + 1 > 4000:
            chunks.append(chunk)
            chunk = ""
        chunk += line + "\n"
    if chunk.strip():
        chunks.append(chunk)
    ok = True
    for c in chunks:
        try:
            r = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": c,
                                         "parse_mode": "HTML",
                                         "disable_web_page_preview": True}, timeout=30)
            if r.status_code != 200:
                ok = False
                print(f"Telegram error {r.status_code}: {r.text}")
        except Exception as e:
            ok = False
            print(f"Telegram send failed: {e}")
    if ok:
        print("✅ Telegram message sent.")
    return ok


# ===========================================================================
# RUN
# ===========================================================================
def run_once():
    now = dt.datetime.now(TZ)
    print("=" * 60)
    print("US/Canada Screener —", now.strftime("%Y-%m-%d %H:%M UTC"))
    print("Mode:", "TEST (5 stocks)" if TEST_MODE else f"UNIVERSE={UNIVERSE}")
    print("=" * 60)

    universe = get_universe()
    print(f"Scanning {len(universe)} stocks...\n")
    raw = fetch_many(universe)

    compute_rs_ratings(raw)   # needs all returns first

    scored, notes = [], []
    for d in raw:
        if not d.get("ok"):
            notes.append(f"{d['ticker']}: no data")
            continue
        score_stock(d)
        scored.append(d)
        if d.get("confidence", 1) < 0.6:
            notes.append(f"{d['ticker']}: low confidence ({', '.join(d['missing'][:3])})")

    multi = sorted([d for d in scored if is_multibagger(d)], key=lambda x: x["score"], reverse=True)
    quality = sorted([d for d in scored if is_quality(d)], key=lambda x: x["score"], reverse=True)
    momo = sorted([d for d in scored if is_momentum(d)],
                  key=lambda x: (x.get("rs_rating") or 0), reverse=True)

    current = {d["ticker"].upper() for d in (multi + quality)}
    prev = read_previous()
    new_e = sorted(current - prev)
    removed = sorted(prev - current)

    date_str = now.strftime("%Y-%m-%d")
    rows = []
    for cat, lst in (("Multibagger", multi), ("Quality", quality), ("Momentum", momo)):
        for d in lst:
            rows.append({"Date": date_str, "Ticker": d["ticker"], "Category": cat,
                         "Score": d["score"], "MarketCap": int(d.get("market_cap") or 0),
                         "RS": d.get("rs_rating"),
                         "Strengths": "; ".join(d["strengths"]),
                         "Concerns": "; ".join(d["concerns"])})
    write_tracking(rows)

    print("\n--- Top scorers ---")
    for d in sorted(scored, key=lambda x: x["score"], reverse=True)[:20]:
        print(f"  {d['ticker']:6} {d['score']:3}/100  RS {str(d.get('rs_rating')):>3}  {_mc(d.get('market_cap'))}")

    msg = build_message(multi, quality, momo, new_e, removed, notes)
    print("\n" + msg + "\n")
    send_telegram(msg)
    print("Done.")


def run_scheduler():
    print(f"Scheduler armed: Sunday {RUN_HOUR:02d}:{RUN_MINUTE:02d} UTC.")
    last = None
    while True:
        now = dt.datetime.now(TZ)
        if (now.weekday() == RUN_WEEKDAY and now.hour == RUN_HOUR
                and now.minute == RUN_MINUTE and last != now.date()):
            run_once()
            last = now.date()
        time.sleep(20)


if __name__ == "__main__":
    run_once()
    if RUN_SCHEDULER:
        run_scheduler()
