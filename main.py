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
import json
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

# ---- LONG-TERM WATCHLIST: 3 cap-band categories, tightly capped counts ----
LT_LARGE_N   = int(os.getenv("LT_LARGE_N", "5"))     # Cat A  Large Cap
LT_MIDLG_N   = int(os.getenv("LT_MIDLG_N", "5"))     # Cat B  Mid Cap
LT_SMALL_N   = int(os.getenv("LT_SMALL_N", "10"))    # Cat C  Small/Mid
LT_LARGE_MIN = float(os.getenv("LT_LARGE_MIN", "50e9"))
LT_LARGE_MAX = float(os.getenv("LT_LARGE_MAX", "500e9"))
LT_MIDLG_MIN = float(os.getenv("LT_MIDLG_MIN", "21e9"))
LT_MIDLG_MAX = float(os.getenv("LT_MIDLG_MAX", "49e9"))
LT_SMALL_MIN = float(os.getenv("LT_SMALL_MIN", "1e9"))
LT_SMALL_MAX = float(os.getenv("LT_SMALL_MAX", "20e9"))
LT_MIN_SCORE = float(os.getenv("LT_MIN_SCORE", "62"))  # weak names never forced in
LT_US_ONLY   = os.getenv("LT_US_ONLY", "True").lower() in ("1", "true", "yes")

# ---- OPTIONS WATCHLIST ----
OPT_ENABLE     = os.getenv("OPT_ENABLE", "True").lower() in ("1", "true", "yes")
OPT_OUT        = int(os.getenv("OPT_OUT", "10"))         # final option ideas (max)
OPT_SHORTLIST  = int(os.getenv("OPT_SHORTLIST", "30"))   # pull option chains for top N
OPT_DTE_MIN    = int(os.getenv("OPT_DTE_MIN", "45"))
OPT_DTE_MAX    = int(os.getenv("OPT_DTE_MAX", "90"))
OPT_MIN_OI     = int(os.getenv("OPT_MIN_OI", "100"))     # contract liquidity floor
OPT_MIN_VOL    = int(os.getenv("OPT_MIN_VOL", "10"))
OPT_MIN_PRICE  = float(os.getenv("OPT_MIN_PRICE", "5"))  # underlying min price
OPT_MIN_AVGVOL = float(os.getenv("OPT_MIN_AVGVOL", "300000"))  # underlying avg volume
OPT_IV_HV_MAX  = float(os.getenv("OPT_IV_HV_MAX", "1.6"))      # skip very rich IV
OPT_MIN_SIDE   = int(os.getenv("OPT_MIN_SIDE", "3"))   # min ideas per side (if available)
ETF_MIN_IDEAS  = int(os.getenv("ETF_MIN_IDEAS", "3"))  # daily ETF ideas minimum
ETF_CORE       = [s.strip().upper() for s in
                  os.getenv("ETF_CORE", "SPY,QQQ,IWM").split(",") if s.strip()]

# ---- LEAPS: long-dated options on top fundamental names ----
LEAPS_OUT        = int(os.getenv("LEAPS_OUT", "3"))
LEAPS_DTE_MIN    = int(os.getenv("LEAPS_DTE_MIN", "180"))    # >= ~6 months
LEAPS_DTE_TARGET = int(os.getenv("LEAPS_DTE_TARGET", "540")) # prefer ~18 months

# ---- RUN MODE, DATA SOURCE, LIVE TRIGGERS ----
# RUN_MODE: "longterm" (Saturday full scan) | "options" (10am/3pm) | "live" (intraday)
RUN_MODE       = os.getenv("RUN_MODE", "longterm").lower()
TRADIER_TOKEN  = os.getenv("TRADIER_TOKEN", "")               # free Tradier API token
TRADIER_BASE   = os.getenv("TRADIER_BASE", "https://api.tradier.com/v1")

WATCHLIST_JSON   = "watchlist.json"     # built weekly; monitored intraday
IV_HISTORY_CSV   = "iv_history.csv"     # banked each run -> real IV Rank over time
ALERT_STATE_JSON = "alert_state.json"   # de-dupes intraday alerts (once per day)

WATCHLIST_MAX       = int(os.getenv("WATCHLIST_MAX", "45"))
LIVE_NEAR_PCT       = float(os.getenv("LIVE_NEAR_PCT", "1.5"))   # % from level = "approaching"
LIVE_ALERT_APPROACH = os.getenv("LIVE_ALERT_APPROACH", "True").lower() in ("1", "true", "yes")
LIVE_PULLBACK_PCT   = float(os.getenv("LIVE_PULLBACK_PCT", "1.5"))  # near SMA-4/8 = entry zone

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

# Liquid, optionable ETFs — included in the technical/options pipeline only
# (no fundamentals, so they are excluded from the long-term company watchlist)
ETFS = [
    "SPY", "QQQ", "IWM", "DIA", "SMH", "SOXX", "XLK", "XLF", "XLE", "XLV",
    "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XBI", "IBB", "ARKK", "KRE",
    "XOP", "OIH", "GDX", "GDXJ", "GLD", "SLV", "USO", "UNG", "TLT", "HYG",
    "EEM", "FXI", "EWZ", "EWJ", "INDA", "KWEB", "ITB", "XHB", "JETS", "XRT",
]
ETF_SET = {e.upper() for e in ETFS}
ETF_INCLUDE = os.getenv("ETF_INCLUDE", "True").lower() in ("1", "true", "yes")


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
    if ETF_INCLUDE and not TEST_MODE:
        tickers = list(dict.fromkeys(tickers + ETFS))
    if MAX_STOCKS and MAX_STOCKS > 0:
        tickers = tickers[:MAX_STOCKS]
    return tickers


def _nasdaq100():
    try:
        r = requests.get("https://en.wikipedia.org/wiki/Nasdaq-100",
                         headers=UA, timeout=30)
        for df in pd.read_html(StringIO(r.text)):
            cols = [str(c) for c in df.columns]
            for cand in ("Ticker", "Symbol"):
                if cand in cols:
                    syms = [str(s).strip().replace(".", "-") for s in df[cand].dropna()]
                    syms = [s for s in syms if s and len(s) <= 6]
                    if len(syms) >= 80:
                        return syms
    except Exception as e:
        print(f"  Nasdaq-100 download failed: {e}")
    return None


def get_intraday_universe():
    """S&P 500 + NASDAQ-100 + saved watchlist tickers (for 9:45/3:00 runs)."""
    if TEST_MODE:
        return TEST_STOCKS
    sp = _sp500() or []
    nd = _nasdaq100() or []
    et = ETFS if ETF_INCLUDE else []
    wl = [w.get("ticker") for w in load_watchlist()]
    tickers = list(dict.fromkeys(sp + nd + et + [t for t in wl if t])) or BUNDLED
    if MAX_STOCKS and MAX_STOCKS > 0:
        tickers = tickers[:MAX_STOCKS]
    return tickers


# ---- Watchlist + alert-state persistence (committed back to the repo) ----
def load_watchlist():
    try:
        with open(WATCHLIST_JSON, encoding="utf-8") as f:
            return json.load(f).get("items", [])
    except Exception:
        return []


def save_watchlist(items):
    try:
        with open(WATCHLIST_JSON, "w", encoding="utf-8") as f:
            json.dump({"updated": dt.datetime.now(TZ).isoformat(),
                       "items": items[:WATCHLIST_MAX]}, f, indent=1)
        print(f"  Watchlist saved: {len(items[:WATCHLIST_MAX])} names")
    except Exception as e:
        print(f"  Watchlist save failed: {e}")


def load_alert_state():
    try:
        with open(ALERT_STATE_JSON, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_alert_state(state):
    try:
        # keep only today's keys so the file never grows
        today = dt.date.today().isoformat()
        state = {k: v for k, v in state.items() if v == today}
        with open(ALERT_STATE_JSON, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception:
        pass


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


def _rsi(close, n=14):
    delta = close.diff()
    up = delta.clip(lower=0).rolling(n).mean()
    down = (-delta.clip(upper=0)).rolling(n).mean()
    rs = up / down.replace(0, float("nan"))
    out = 100 - (100 / (1 + rs))
    return _safe(out.iloc[-1])


def _macd_hist(close, fast=12, slow=26, sig=9):
    ema_f = close.ewm(span=fast, adjust=False).mean()
    ema_s = close.ewm(span=slow, adjust=False).mean()
    macd = ema_f - ema_s
    signal = macd.ewm(span=sig, adjust=False).mean()
    return _safe((macd - signal).iloc[-1]), _safe(macd.iloc[-1])


def _atr(hist, n=14):
    h, l, c = hist["High"], hist["Low"], hist["Close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return _safe(tr.rolling(n).mean().iloc[-1])


def _hv(close, n=252):
    rets = (close / close.shift(1)).apply(lambda x: math.log(x) if x and x > 0 else float("nan"))
    rets = rets.dropna().tail(n)
    if len(rets) < 20:
        return None
    return _safe(rets.std() * math.sqrt(252) * 100.0)


def _attach_technicals(d, tk, info):
    """Full indicator suite: SMAs 4/8/50/100/150/200, RSI, MACD, Bollinger, ATR,
    HV, relative volume, 52-wk position, RS return, breakout/pattern heuristics."""
    keys = ("sma4", "sma8", "sma50", "sma100", "sma150", "sma200", "sma200_prev",
            "high_52w", "low_52w", "rs_return", "vol_ratio", "avg_vol", "rsi",
            "macd_hist", "macd", "atr", "hv", "boll_pctb", "boll_up", "boll_lo",
            "dist_sma4", "recent_high_20", "recent_low_20", "recent_high_55",
            "trend_stack", "run_30", "setup", "near_52w_high", "breakout_20")
    for k in keys:
        d[k] = None
    try:
        hist = tk.history(period="1y", interval="1d", auto_adjust=True)
    except Exception:
        hist = None
    if hist is None or hist.empty or "Close" not in hist:
        return
    close = hist["Close"].dropna()
    if len(close) < 60:
        return
    price = _safe(close.iloc[-1])
    d["price"] = d.get("price") or price

    d["sma4"]  = _safe(close.tail(4).mean())
    d["sma8"]  = _safe(close.tail(8).mean())
    d["sma50"] = _safe(close.tail(50).mean()) if len(close) >= 50 else None
    d["sma100"] = _safe(close.tail(100).mean()) if len(close) >= 100 else None
    d["sma150"] = _safe(close.tail(150).mean()) if len(close) >= 150 else None
    d["sma200"] = _safe(close.tail(200).mean()) if len(close) >= 200 else None
    if len(close) >= 221:
        d["sma200_prev"] = _safe(close.iloc[-221:-21].mean())
    d["high_52w"] = _safe(close.max())
    d["low_52w"] = _safe(close.min())
    if len(close) >= 127 and price:
        past = _safe(close.iloc[-127])
        if past and past > 0:
            d["rs_return"] = (price / past - 1.0) * 100.0

    d["rsi"] = _rsi(close)
    d["macd_hist"], d["macd"] = _macd_hist(close)
    d["hv"] = _hv(close)
    try:
        d["atr"] = _atr(hist)
    except Exception:
        d["atr"] = None

    # Bollinger (20, 2)
    if len(close) >= 20:
        mid = close.tail(20).mean()
        sd = close.tail(20).std(ddof=0)
        if sd and sd > 0:
            up, lo = mid + 2 * sd, mid - 2 * sd
            d["boll_up"], d["boll_lo"] = _safe(up), _safe(lo)
            d["boll_pctb"] = _safe((price - lo) / (up - lo))

    # Red Line: distance from SMA-4
    if d["sma4"]:
        d["dist_sma4"] = (price / d["sma4"] - 1.0) * 100.0

    # Recent ranges (prior, excluding today) for breakout detection
    if len(close) >= 21:
        d["recent_high_20"] = _safe(close.iloc[-21:-1].max())
        d["recent_low_20"] = _safe(close.iloc[-21:-1].min())
    if len(close) >= 56:
        d["recent_high_55"] = _safe(close.iloc[-56:-1].max())
    if len(close) >= 31 and price:
        p30 = _safe(close.iloc[-31])
        if p30 and p30 > 0:
            d["run_30"] = (price / p30 - 1.0) * 100.0

    # Trend hierarchy: how much of SMA4>8>50>100>200 holds (0..4)
    chain = [d.get("sma4"), d.get("sma8"), d.get("sma50"), d.get("sma100"), d.get("sma200")]
    if all(v is not None for v in chain):
        stack = sum(1 for i in range(4) if chain[i] > chain[i + 1])
        d["trend_stack"] = stack  # 4 = perfect bullish, 0 = perfect bearish

    if d.get("high_52w"):
        d["near_52w_high"] = price >= 0.97 * d["high_52w"]
    if d.get("recent_high_20"):
        d["breakout_20"] = price > d["recent_high_20"]

    # Volume
    if "Volume" in hist:
        vol = hist["Volume"].dropna()
        if len(vol) >= 50:
            v10, v50 = _safe(vol.tail(10).mean()), _safe(vol.tail(50).mean())
            d["avg_vol"] = v50
            if v10 and v50 and v50 > 0:
                d["vol_ratio"] = v10 / v50

    d["setup"] = _detect_setup(d, close)


def _detect_setup(d, close):
    """Heuristic setup label (NOT precise pattern geometry)."""
    price = d.get("price")
    atr, hi, rh20 = d.get("atr"), d.get("high_52w"), d.get("recent_high_20")
    stack = d.get("trend_stack") or 0
    run = d.get("run_30") or 0
    if price is None:
        return None
    # Volatility contraction (VCP-ish): recent ATR shrinking vs a month ago, near highs
    vcp = False
    try:
        if atr and len(close) >= 60:
            h = close.tail(60); l = close.tail(60)
            rng_now = close.tail(10).max() - close.tail(10).min()
            rng_prev = close.iloc[-30:-10].max() - close.iloc[-30:-10].min()
            if rng_prev and rng_now and rng_now < 0.6 * rng_prev and d.get("near_52w_high"):
                vcp = True
    except Exception:
        pass
    if d.get("breakout_20") and (d.get("vol_ratio") or 0) >= 1.3 and stack >= 3:
        return "Base/range breakout (vol confirmed)"
    if d.get("near_52w_high") and stack >= 3:
        return "52-week-high breakout"
    if vcp:
        return "Volatility contraction (tight base)"
    if run >= 15 and stack >= 3 and (d.get("dist_sma4") or 99) < 8:
        return "Bull flag (pullback in uptrend)"
    if stack <= 1 and run <= -15:
        return "Bear flag / downtrend"
    if stack >= 3:
        return "Uptrend continuation"
    if stack <= 1:
        return "Downtrend"
    return "Neutral / base building"


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


def send_telegram(text, label="report"):
    """Plain-text send (no parse_mode -> no silent HTML-parse rejections).
    Auto-splits on line boundaries well under Telegram's 4096-char limit."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"\n⚠ TELEGRAM secrets not set — printing {label} instead:\n")
        print(text)
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    chunk, chunks = "", []
    for line in text.split("\n"):
        if len(line) > 3800:           # ultra-long single line: hard-wrap
            line = line[:3797] + "..."
        if len(chunk) + len(line) + 1 > 3800:
            chunks.append(chunk)
            chunk = ""
        chunk += line + "\n"
    if chunk.strip():
        chunks.append(chunk)
    ok = True
    for idx, c in enumerate(chunks, 1):
        try:
            r = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": c,
                                         "disable_web_page_preview": True}, timeout=30)
            if r.status_code != 200:
                ok = False
                print(f"Telegram error {r.status_code} on {label} part {idx}: {r.text}")
            time.sleep(0.5)
        except Exception as e:
            ok = False
            print(f"Telegram send failed ({label} part {idx}): {e}")
    if ok:
        print(f"✅ Telegram {label} sent ({len(chunks)} message(s)).")
    return ok


# ===========================================================================
# LONG-TERM WATCHLIST  (tightened to cap-band categories with rich fields)
# ===========================================================================
def _px(x):
    return f"${x:,.2f}" if x is not None else "–"


def _lvl(x):
    return f"${x:,.0f}" if x is not None else "–"


def lt_fields(d, category, rank):
    """Attach institutional-style fields. Targets/levels are MECHANICAL
    rule-based reference points, not predictions."""
    price = d.get("price") or 0
    # Sub-scores rescaled to /100 from the existing model's point buckets
    d["sc_growth"] = round((d.get("growth_pts", 0) / 25) * 100)
    d["sc_quality"] = round(((d.get("prof", 0) + d.get("fs", 0)) / 35) * 100)
    d["sc_value"] = round((d.get("val", 0) / 15) * 100)
    d["sc_tech"] = round((d.get("tech", 0) / 25) * 100)
    d["conviction"] = d.get("score", 0)

    # Buy zone: pullback-to-support idea (50-DMA up to current in an uptrend)
    s50, s200 = d.get("sma50"), d.get("sma200")
    if s50 and price > s50:
        d["buy_zone"] = (s50, price)
    else:
        d["buy_zone"] = (price * 0.97, price * 1.01)
    # Invalidation: long-term thesis weakens below the 200-DMA (or 150)
    d["invalidation"] = s200 or d.get("sma150") or (price * 0.85 if price else None)

    # Mechanical long-term target range from blended growth, compounded
    yrs = {"A": 4.0, "B": 3.5, "C": 3.0}.get(category, 3.5)
    g = [v for v in (d.get("revenue_growth"), d.get("eps_growth"),
                     d.get("profit_growth")) if v is not None]
    gest = sum(g) / len(g) if g else 8.0
    gest = max(4.0, min(gest, 25.0))
    if price:
        d["target_low"] = price * (1 + (gest * 0.7) / 100) ** yrs
        d["target_high"] = price * (1 + (gest * 1.1) / 100) ** yrs
    d["hold_period"] = {"A": "3–5 years", "B": "2–4 years", "C": "2–4 years"}.get(category)

    # Why ranked
    subs = {"Growth": d["sc_growth"], "Quality": d["sc_quality"],
            "Valuation": d["sc_value"], "Technical": d["sc_tech"]}
    top = max(subs, key=subs.get)
    d["why"] = f"#{rank} by conviction in this tier; strongest on {top}."
    return d


def select_long_term(scored):
    def eligible(d):
        if d.get("is_etf"):
            return False        # ETFs: technical/options pipeline only
        if d.get("score", 0) < LT_MIN_SCORE:
            return False
        if LT_US_ONLY and str(d["ticker"]).upper().endswith(".TO"):
            return False
        if (d.get("confidence", 0) or 0) < 0.5:
            return False
        # healthy structure: above 150-DMA or strong trend template
        if not ((d.get("price") and d.get("sma150") and d["price"] > d["sma150"])
                or (d.get("trend_pass") or 0) >= 5):
            return False
        return True

    def in_band(d, lo, hi):
        mc = d.get("market_cap")
        return mc is not None and lo <= mc <= hi

    elig = [d for d in scored if eligible(d)]
    A = sorted([d for d in elig if in_band(d, LT_LARGE_MIN, LT_LARGE_MAX)],
               key=lambda x: x["score"], reverse=True)[:LT_LARGE_N]
    B = sorted([d for d in elig if in_band(d, LT_MIDLG_MIN, LT_MIDLG_MAX)],
               key=lambda x: x["score"], reverse=True)[:LT_MIDLG_N]
    C = sorted([d for d in elig if in_band(d, LT_SMALL_MIN, LT_SMALL_MAX)],
               key=lambda x: x["score"], reverse=True)[:LT_SMALL_N]
    for cat, lst in (("A", A), ("B", B), ("C", C)):
        for i, d in enumerate(lst, 1):
            lt_fields(d, cat, i)
    return A, B, C


# ===========================================================================
# KGS FRAMEWORK + OPTIONS SETUP SCORING
# ===========================================================================
def kgs_score(d, force_direction=None):
    """Return (kgs 0-100, direction). Encodes the KGS rules: trend hierarchy,
    Red Line (distance from SMA-4), Bollinger as dynamic limits, volume context.
    force_direction lets core ETFs be analyzed even when signals are mixed."""
    stack = d.get("trend_stack")
    mh = d.get("macd_hist")
    price, s50 = d.get("price"), d.get("sma50")
    if stack is None or price is None or s50 is None:
        return 0, "neutral"
    if stack >= 3 and (mh or 0) >= 0 and price > s50:
        direction = "bullish"
    elif stack <= 1 and (mh or 0) <= 0 and price < s50:
        direction = "bearish"
    else:
        direction = "neutral"
    if direction == "neutral":
        if force_direction in ("bullish", "bearish"):
            direction = force_direction
        else:
            return 0, "neutral"

    bull = direction == "bullish"
    # Trend hierarchy (0..30)
    pts = (stack if bull else (4 - stack)) / 4 * 30
    # Red Line: distance from SMA-4 (0..15)
    dist = d.get("dist_sma4")
    if dist is not None:
        a = dist if bull else -dist     # alignment with direction
        if -2 <= a <= 6:
            pts += 15                    # near SMA-4, healthy
        elif 6 < a <= 12:
            pts += 8
        elif a < -2:
            pts += 11                    # pulled back toward/under SMA-4
        else:
            pts += 3                     # extended -> chase risk
    # Bollinger as dynamic limits (0..15)
    b = d.get("boll_pctb")
    if b is not None:
        bb = b if bull else (1 - b)
        if 0.2 <= bb <= 0.8:
            pts += 15
        elif bb < 0.2:
            pts += 12                    # near accumulation band
        elif 0.8 < bb <= 1.0:
            pts += 8
        else:
            pts += 3                     # beyond band -> exhaustion caution
    # Volume context (0..10)
    vr = d.get("vol_ratio") or 0
    pts += 10 if vr >= 1.5 else 6 if vr >= 1.2 else 3
    # RSI momentum (0..10)
    rsi = d.get("rsi")
    if rsi is not None:
        rr = rsi if bull else (100 - rsi)
        pts += 10 if 50 <= rr <= 70 else 6 if 70 < rr <= 80 else 4 if 40 <= rr < 50 else 2
    # Relative strength tilt (0..20) for bullish only
    if bull:
        pts += (d.get("rs_rating") or 0) / 99 * 20
    else:
        pts += 10
    return round(min(pts, 100)), direction


def options_setup_score(d, force_direction=None):
    kgs, direction = kgs_score(d, force_direction)
    if direction == "neutral":
        return 0, "neutral", None
    bonus = 0
    setup = d.get("setup") or ""
    if "breakout" in setup.lower():
        bonus += 8
    if "contraction" in setup.lower() or "flag" in setup.lower():
        bonus += 5
    return min(kgs + bonus, 100), direction, setup


def trust_tier(conviction):
    if conviction >= 80:
        return "⭐⭐⭐ HIGH"
    if conviction >= 65:
        return "⭐⭐ MEDIUM"
    return "⭐ SPECULATIVE"


def strategy_matches(d, direction, kgs, uoa=None):
    """Which named strategies does this setup match? Powers the trust line."""
    m = [f"KGS {kgs}/100"]
    setup = (d.get("setup") or "").lower()
    if direction == "bullish" and (d.get("trend_pass") or 0) >= 6:
        m.append("Trend Template")
    if direction == "bullish" and (d.get("rs_rating") or 0) >= 80 and d.get("near_52w_high"):
        m.append("CANSLIM momentum")
    if "breakout" in setup:
        m.append("Breakout")
    if "contraction" in setup:
        m.append("VCP base")
    if "flag" in setup:
        m.append("Flag")
    dist = d.get("dist_sma4")
    if dist is not None and abs(dist) <= 2.5:
        m.append("Red-Line pullback entry")
    b = d.get("boll_pctb")
    if b is not None and direction == "bullish" and b <= 0.2:
        m.append("Lower-band accumulation")
    if b is not None and direction == "bearish" and b >= 0.8:
        m.append("Upper-band exhaustion")
    if uoa is not None and uoa >= 0.8:
        m.append("Unusual options flow")
    if (d.get("trend_stack") or 0) == (4 if direction == "bullish" else 0):
        m.append("Perfect SMA stack")
    return m


def _nearest_strike(df, target):
    try:
        if df is None or len(df) == 0:
            return None
        i = (df["strike"] - target).abs().values.argmin()
        r = df.iloc[int(i)]
        return {"strike": _safe(r["strike"]), "last": _safe(r.get("lastPrice")),
                "bid": _safe(r.get("bid")), "ask": _safe(r.get("ask")),
                "iv": _safe(r.get("impliedVolatility")),
                "oi": _safe(r.get("openInterest")), "vol": _safe(r.get("volume"))}
    except Exception:
        return None


def _pick_expiry(exps, today, dmin=None, dmax=None, target=60):
    dmin = OPT_DTE_MIN if dmin is None else dmin
    dmax = OPT_DTE_MAX if dmax is None else dmax
    best, best_dte = None, None
    for e in exps or []:
        try:
            ed = dt.datetime.strptime(str(e)[:10], "%Y-%m-%d").date()
        except Exception:
            continue
        dte = (ed - today).days
        if dmin <= dte <= dmax:
            if best is None or abs(dte - target) < abs(best_dte - target):
                best, best_dte = str(e)[:10], dte
    return best, best_dte


def _tradier_get(path, params):
    if not TRADIER_TOKEN:
        return None
    try:
        r = requests.get(f"{TRADIER_BASE}/{path}", params=params,
                         headers={"Authorization": f"Bearer {TRADIER_TOKEN}",
                                  "Accept": "application/json"}, timeout=20)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def _tradier_expirations(symbol):
    j = _tradier_get("markets/options/expirations",
                     {"symbol": symbol, "includeAllRoots": "true"})
    try:
        ds = j["expirations"]["date"]
        return ds if isinstance(ds, list) else [ds]
    except Exception:
        return []


def _tradier_chain_df(symbol, expiration):
    j = _tradier_get("markets/options/chains",
                     {"symbol": symbol, "expiration": expiration, "greeks": "true"})
    try:
        opts = j["options"]["option"]
        if isinstance(opts, dict):
            opts = [opts]
        rows = []
        for o in opts:
            g = o.get("greeks") or {}
            rows.append({"strike": o.get("strike"), "lastPrice": o.get("last"),
                         "bid": o.get("bid"), "ask": o.get("ask"),
                         "impliedVolatility": g.get("mid_iv") or g.get("smv_vol"),
                         "openInterest": o.get("open_interest"),
                         "volume": o.get("volume"),
                         "option_type": o.get("option_type")})
        return pd.DataFrame(rows)
    except Exception:
        return None


def get_chain(symbol, direction, dmin=None, dmax=None, target=60):
    """Return (expiration, dte, df) for the chosen expiry window (defaults 45-90 DTE).
    Tradier first (real greeks/IV); yfinance fallback. df columns are normalized:
    strike, lastPrice, bid, ask, impliedVolatility, openInterest, volume."""
    today = dt.date.today()
    want = "call" if direction == "bullish" else "put"
    if TRADIER_TOKEN:
        best, bd = _pick_expiry(_tradier_expirations(symbol), today, dmin, dmax, target)
        if best:
            df = _tradier_chain_df(symbol, best)
            if df is not None and len(df):
                side = df[df["option_type"] == want] if "option_type" in df else df
                if len(side):
                    return best, bd, side.reset_index(drop=True)
    if yf is None:
        return None, None, None
    try:
        tk = yf.Ticker(symbol)
        best, bd = _pick_expiry(list(getattr(tk, "options", []) or []), today, dmin, dmax, target)
        if not best:
            return None, None, None
        ch = tk.option_chain(best)
        df = ch.calls if direction == "bullish" else ch.puts
        return best, bd, df
    except Exception:
        return None, None, None


# ---- IV banking -> real IV Rank / Percentile builds over time ----
def bank_iv(ticker, iv):
    if iv is None:
        return
    try:
        new = not os.path.exists(IV_HISTORY_CSV)
        with open(IV_HISTORY_CSV, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["Date", "Ticker", "IV"])
            w.writerow([dt.date.today().isoformat(), ticker, round(iv, 2)])
    except Exception:
        pass


def compute_iv_rank(ticker, current_iv):
    """Returns (iv_rank, iv_percentile, n_samples). Needs banked history to be useful."""
    if current_iv is None or not os.path.exists(IV_HISTORY_CSV):
        return None, None, 0
    try:
        df = pd.read_csv(IV_HISTORY_CSV)
        s = df[df["Ticker"] == ticker]["IV"].dropna().astype(float).tail(252)
        n = len(s)
        if n < 12:
            return None, None, n
        lo, hi = s.min(), s.max()
        rank = (current_iv - lo) / (hi - lo) * 100 if hi > lo else 50.0
        pct = (s <= current_iv).mean() * 100
        return round(max(0, min(rank, 100))), round(pct), n
    except Exception:
        return None, None, 0



def analyze_option(d, force_direction=None):
    """Pull the option chain for one shortlisted name and build a mechanical
    swing-trade idea (45-90 DTE, 3 strikes, levels, IV/HV, liquidity)."""
    if yf is None and not TRADIER_TOKEN:
        return None
    score, direction, setup = options_setup_score(d, force_direction)
    if direction == "neutral":
        return None
    price, atr = d.get("price"), d.get("atr")
    if not price or not atr or atr <= 0:
        return None
    try:
        best, best_dte, df = get_chain(d["ticker"], direction)
    except Exception:
        return None
    if best is None or df is None or len(df) == 0:
        return None

    # Order-flow proxy: unusual options activity = today's chain volume vs OI
    uoa = None
    try:
        tot_v = float(pd.to_numeric(df.get("volume"), errors="coerce").fillna(0).sum())
        tot_oi = float(pd.to_numeric(df.get("openInterest"), errors="coerce").fillna(0).sum())
        if tot_oi > 0:
            uoa = tot_v / tot_oi
    except Exception:
        pass

    bull = direction == "bullish"
    if bull:
        safe = _nearest_strike(df, price * 0.95)
        bal = _nearest_strike(df, price)
        aggr = _nearest_strike(df, price * 1.08)
    else:
        safe = _nearest_strike(df, price * 1.05)
        bal = _nearest_strike(df, price)
        aggr = _nearest_strike(df, price * 0.92)
    if not bal:
        return None

    # IV (from ATM contract) vs historical volatility proxy
    iv = (bal["iv"] * 100) if bal.get("iv") else None
    hv = d.get("hv")
    iv_hv = (iv / hv) if (iv and hv and hv > 0) else None
    if iv_hv is not None and iv_hv > OPT_IV_HV_MAX:
        return None   # options too rich; spec prefers low IV
    iv_rank, iv_pct, iv_n = compute_iv_rank(d["ticker"], iv)
    bank_iv(d["ticker"], iv)

    # Liquidity
    oi = bal.get("oi") or 0
    ovol = bal.get("vol") or 0
    liquid = (oi >= OPT_MIN_OI) or (ovol >= OPT_MIN_VOL)

    # Mechanical underlying levels
    if bull:
        breakout = d.get("recent_high_20") or price
        entry_lo, entry_hi = price * 0.99, max(price, breakout) * 1.005
        stop = max(d.get("recent_low_20") or 0, price - 2.5 * atr)
        t1, t2, stretch = price + 2 * atr, price + 4 * atr, price + 6 * atr
        rr = (t1 - price) / (price - stop) if price > stop else None
    else:
        breakout = d.get("recent_low_20") or price
        entry_lo, entry_hi = min(price, breakout) * 0.995, price * 1.01
        stop = price + 2.5 * atr
        t1, t2, stretch = price - 2 * atr, price - 4 * atr, price - 6 * atr
        rr = (price - t1) / (stop - price) if stop > price else None

    # Catalyst: next earnings before expiry? (best-effort via yfinance)
    earnings_before = None
    try:
        cal = yf.Ticker(d["ticker"]).calendar if yf is not None else None
        ed = None
        if isinstance(cal, dict):
            v = cal.get("Earnings Date")
            if isinstance(v, (list, tuple)) and v:
                ed = v[0]
            else:
                ed = v
        elif hasattr(cal, "loc") and "Earnings Date" in getattr(cal, "index", []):
            ed = cal.loc["Earnings Date"][0]
        if ed is not None:
            ed = pd.to_datetime(ed).date()
            exp_d = dt.datetime.strptime(best, "%Y-%m-%d").date()
            earnings_before = ed <= exp_d
            d["next_earnings"] = ed.isoformat()
    except Exception:
        pass

    # Conviction blend (+ small order-flow tilt)
    liq_sc = 100 if liquid else 40
    iv_sc = 100 if (iv_hv is not None and iv_hv <= 0.9) else 70 if (iv_hv is not None and iv_hv <= 1.2) else 45
    rr_sc = min((rr or 0) / 3 * 100, 100)
    conviction = round(0.6 * score + 0.18 * liq_sc + 0.12 * iv_sc + 0.10 * rr_sc)
    if uoa is not None and uoa >= 0.8:
        conviction += 4          # unusually active chain today
    conviction = max(0, min(conviction, 100))
    matches = strategy_matches(d, direction, score, uoa)

    # Estimated P/L scenarios (balanced strike, value AT EXPIRY = intrinsic).
    # Mechanical "what if price reaches T1/T2 by expiry" math — NOT a prediction.
    est = None
    try:
        def _prem(s):
            if not s:
                return None
            b, a = s.get("bid"), s.get("ask")
            if b and a and a >= b > 0:
                return (b + a) / 2
            return s.get("last")
        for s in (safe, bal, aggr):
            if s is not None:
                s["prem"] = _prem(s)
        prem = bal.get("prem")
        K = bal.get("strike")
        if prem and prem > 0 and K:
            cost = prem * 100.0
            def _pl(target):
                intr = max(target - K, 0.0) if bull else max(K - target, 0.0)
                pl = intr * 100.0 - cost
                return pl, (pl / cost * 100.0)
            t1_pl, t1_pct = _pl(t1)
            t2_pl, t2_pct = _pl(t2)
            est = {"cost": cost, "t1_pl": t1_pl, "t1_pct": t1_pct,
                   "t2_pl": t2_pl, "t2_pct": t2_pct}
    except Exception:
        est = None

    return {
        "ticker": d["ticker"], "name": d["name"], "direction": direction,
        "is_etf": bool(d.get("is_etf")),
        "trust": trust_tier(conviction), "matches": matches, "est": est,
        "setup": setup, "price": price, "entry": (entry_lo, entry_hi),
        "breakout": breakout, "stop": stop, "t1": t1, "t2": t2, "stretch": stretch,
        "exp": best, "dte": best_dte, "safe": safe, "bal": bal, "aggr": aggr,
        "iv": iv, "hv": hv, "iv_hv": iv_hv, "iv_rank": iv_rank, "iv_pct": iv_pct,
        "iv_n": iv_n, "oi": oi, "ovol": ovol, "uoa": uoa,
        "liquid": liquid, "rr": rr, "conviction": conviction,
        "earnings_before": earnings_before,
        "trend_stack": d.get("trend_stack"), "rsi": d.get("rsi"),
        "macd_hist": d.get("macd_hist"), "rs": d.get("rs_rating"),
        "vol_ratio": d.get("vol_ratio"), "kgs": score,
    }


def build_options_watchlist(scored):
    """Returns (stock_ideas, n_shortlisted, shortlist_ds, etf_ideas).
    Guarantees: both directions represented when available (OPT_MIN_SIDE each),
    and >= ETF_MIN_IDEAS ETF ideas daily (SPY/QQQ/IWM always analyzed)."""
    if not OPT_ENABLE:
        return [], 0, [], []
    stock_cands, etf_cands = [], []
    by_ticker = {d["ticker"].upper(): d for d in scored}
    for d in scored:
        if str(d["ticker"]).upper().endswith(".TO"):
            continue   # US-listed options only
        if (d.get("price") or 0) < OPT_MIN_PRICE:
            continue
        if (d.get("avg_vol") or 0) < OPT_MIN_AVGVOL:
            continue
        sc, direction, _ = options_setup_score(d)
        if direction == "neutral" or sc <= 0:
            if d.get("is_etf") and str(d["ticker"]).upper() in ETF_CORE:
                etf_cands.append((1, d))     # core ETFs analyzed regardless
            continue
        (etf_cands if d.get("is_etf") else stock_cands).append((sc, d))

    # Shortlist stocks with BOTH sides represented
    bull = [(s, d) for s, d in stock_cands if kgs_score(d)[1] == "bullish"]
    bear = [(s, d) for s, d in stock_cands if kgs_score(d)[1] == "bearish"]
    bull.sort(key=lambda x: x[0], reverse=True)
    bear.sort(key=lambda x: x[0], reverse=True)
    n_bear = max(min(len(bear), OPT_SHORTLIST // 3), 0)
    n_bull = OPT_SHORTLIST - n_bear
    shortlist = [d for _, d in bull[:n_bull]] + [d for _, d in bear[:n_bear]]

    # ETF shortlist: core first (forced if neutral), then best others
    etf_cands.sort(key=lambda x: x[0], reverse=True)
    core_ds = [by_ticker[t] for t in ETF_CORE if t in by_ticker]
    other_etfs = [d for _, d in etf_cands if d["ticker"].upper() not in ETF_CORE]
    etf_shortlist = core_ds + other_etfs[:max(ETF_MIN_IDEAS * 2, 6)]

    print(f"  Options shortlist: {len(shortlist)} stocks "
          f"({len(bull[:n_bull])} bull / {len(bear[:n_bear])} bear) "
          f"+ {len(etf_shortlist)} ETFs")

    def _analyze_list(ds, force_core=False):
        out = []
        for i, d in enumerate(ds, 1):
            force = None
            if force_core and str(d["ticker"]).upper() in ETF_CORE:
                stack = d.get("trend_stack")
                if stack is not None and kgs_score(d)[1] == "neutral":
                    force = "bullish" if stack >= 2 else "bearish"
            print(f"    [opt {i}/{len(ds)}] {d['ticker']} ...")
            try:
                idea = analyze_option(d, force_direction=force)
            except Exception as e:
                idea = None
                print(f"      {d['ticker']} option analysis failed: {e}")
            if idea:
                out.append(idea)
            time.sleep(REQUEST_DELAY_SECONDS)
        return out

    stock_ideas = _analyze_list(shortlist)
    etf_ideas = _analyze_list(etf_shortlist, force_core=True)

    # Final stock selection: PURE MERIT — best conviction regardless of side.
    # (Both sides still get analyzed, so the call/put mix itself signals market tilt.)
    stock_ideas.sort(key=lambda x: x["conviction"], reverse=True)
    final = stock_ideas[:OPT_OUT]

    # ETFs: core first, then best others, min ETF_MIN_IDEAS (data permitting)
    etf_ideas.sort(key=lambda x: (0 if x["ticker"].upper() in ETF_CORE else 1,
                                  -x["conviction"]))
    etf_final = etf_ideas[:max(ETF_MIN_IDEAS, min(len(etf_ideas), ETF_MIN_IDEAS + 2))]

    return final[:OPT_OUT], len(shortlist) + len(etf_shortlist), shortlist, etf_final


# ===========================================================================
# LEAPS — long-dated options (>=6 months) on top fundamental names
# ===========================================================================
def analyze_leaps(d):
    """Bullish long-dated idea on a high-conviction fundamental name.
    Targets come from the LONG-TERM model (growth-based range), stop from the
    invalidation level (200-DMA). Mechanical reference math, not advice."""
    price = d.get("price")
    if not price:
        return None
    try:
        best, dte, df = get_chain(d["ticker"], "bullish",
                                  dmin=LEAPS_DTE_MIN, dmax=1100,
                                  target=LEAPS_DTE_TARGET)
    except Exception:
        return None
    if best is None or df is None or len(df) == 0:
        return None
    safe = _nearest_strike(df, price * 0.90)   # ITM = stock-replacement, lower risk
    bal = _nearest_strike(df, price * 1.00)    # ATM
    aggr = _nearest_strike(df, price * 1.15)   # OTM = multibagger profile
    if not bal:
        return None
    iv = (bal["iv"] * 100) if bal.get("iv") else None
    hv = d.get("hv")
    iv_hv = (iv / hv) if (iv and hv and hv > 0) else None
    iv_rank, iv_pct, iv_n = compute_iv_rank(d["ticker"], iv)
    oi = bal.get("oi") or 0
    ovol = bal.get("vol") or 0
    liquid = (oi >= OPT_MIN_OI) or (ovol >= OPT_MIN_VOL)

    reversal = (d.get("trend_stack") or 0) <= 1
    bz = d.get("buy_zone") or (price * 0.97, price * 1.01)
    if reversal and d.get("boll_lo"):
        bz = (d["boll_lo"], price * 1.01)      # accumulation zone near lower band
    stop = d.get("invalidation")
    if stop is None or stop >= price:           # downtrend: 200-DMA is above price
        atr = d.get("atr")
        lo20 = d.get("recent_low_20")
        stop = max(lo20 * 0.97 if lo20 else 0,
                   price - 2.5 * atr if atr else price * 0.85) or price * 0.85
    # Targets scaled to the OPTION'S OWN lifetime (growth compounded over dte)
    yrs = max((dte or 365) / 365.0, 0.5)
    g = [v for v in (d.get("revenue_growth"), d.get("eps_growth"),
                     d.get("profit_growth")) if v is not None]
    gest = max(4.0, min((sum(g) / len(g)) if g else 8.0, 25.0))
    t1 = price * (1 + (gest * 0.7) / 100) ** yrs
    t2 = price * (1 + (gest * 1.1) / 100) ** yrs
    stretch = t2 * 1.15 if t2 else None
    rr = ((t1 - price) / (price - stop)) if (stop and price > stop) else None

    kgs, _dir = kgs_score(d, force_direction="bullish")
    conviction = round(0.55 * d.get("score", 0) + 0.45 * kgs)
    if not liquid:
        conviction = max(0, conviction - 10)
    conviction = max(0, min(conviction, 100))
    matches = ["Fundamental leader"] + strategy_matches(d, "bullish", kgs)
    if reversal:
        matches.insert(1, "KGS capitulation reversal (lower band + volume)")
    if (d.get("rs_rating") or 0) >= 80 and "CANSLIM momentum" not in matches:
        matches.append("RS leader")

    est = None
    try:
        def _prem(sd):
            if not sd:
                return None
            b, a = sd.get("bid"), sd.get("ask")
            if b and a and a >= b > 0:
                return (b + a) / 2
            return sd.get("last")
        for sd in (safe, bal, aggr):
            if sd is not None:
                sd["prem"] = _prem(sd)
        prem, K = bal.get("prem"), bal.get("strike")
        if prem and prem > 0 and K:
            cost = prem * 100.0
            def _pl(tgt):
                pl = max(tgt - K, 0.0) * 100.0 - cost
                return pl, pl / cost * 100.0
            a1, p1 = _pl(t1); a2, p2 = _pl(t2)
            est = {"cost": cost, "t1_pl": a1, "t1_pct": p1, "t2_pl": a2, "t2_pct": p2}
    except Exception:
        pass

    return {"ticker": d["ticker"], "name": d["name"], "direction": "bullish",
            "is_etf": False, "trust": trust_tier(conviction), "matches": matches,
            "setup": ("LEAPS — capitulation reversal play" if reversal else
                      f"LEAPS — long-term compounding ({d.get('hold_period') or '1-2yr+'})"),
            "price": price, "entry": bz, "breakout": None, "stop": stop,
            "t1": t1, "t2": t2, "stretch": stretch, "exp": best, "dte": dte,
            "safe": safe, "bal": bal, "aggr": aggr, "iv": iv, "hv": hv,
            "iv_hv": iv_hv, "iv_rank": iv_rank, "iv_pct": iv_pct, "iv_n": iv_n,
            "oi": oi, "ovol": ovol, "uoa": None, "liquid": liquid, "rr": rr,
            "conviction": conviction, "est": est, "earnings_before": None,
            "trend_stack": d.get("trend_stack"), "rsi": d.get("rsi"),
            "macd_hist": d.get("macd_hist"), "rs": d.get("rs_rating"),
            "vol_ratio": d.get("vol_ratio"), "kgs": kgs}


def build_leaps(scored):
    """Top LEAPS ideas from the long-term fundamental selection."""
    if LEAPS_OUT <= 0:
        return []
    A, B, C = select_long_term(scored)
    def blend(x):
        return 0.55 * x.get("score", 0) + 0.45 * kgs_score(x, "bullish")[0]
    # Technical gate: healthy trend OR a KGS capitulation-reversal signature
    # (volume spike at/below the lower Bollinger band while oversold).
    def _reversal_ok(x):
        return ((x.get("boll_pctb") is not None and x["boll_pctb"] <= 0.15)
                and (x.get("vol_ratio") or 0) >= 1.5
                and (x.get("rsi") is None or x["rsi"] <= 40))
    pool = [x for x in (A + B + C)
            if (x.get("trend_stack") or 0) >= 2 or _reversal_ok(x)]
    cands = sorted(pool, key=blend, reverse=True)
    out = []
    for d in cands[:max(LEAPS_OUT * 3, 8)]:
        print(f"    [leaps] {d['ticker']} ...")
        try:
            idea = analyze_leaps(d)
        except Exception as e:
            idea = None
            print(f"      {d['ticker']} LEAPS failed: {e}")
        if idea:
            out.append(idea)
        if len(out) >= LEAPS_OUT:
            break
        time.sleep(REQUEST_DELAY_SECONDS)
    return out



DIV = "━" * 20


def _lt_block(d):
    bz = d.get("buy_zone") or (None, None)
    L = [f"#{d['_rank']}  {d['ticker']} · {d['name'][:26]}",
         f"   {_mc(d.get('market_cap'))} · {_px(d.get('price'))} · Conviction {d['conviction']}/100",
         f"   G {d['sc_growth']} · Q {d['sc_quality']} · V {d['sc_value']} · T {d['sc_tech']}",
         f"   ✅ {('; '.join(d.get('strengths', [])))[:90]}",
         f"   ⚠ {('; '.join(d.get('concerns', [])))[:90]}",
         f"   🎯 Buy zone {_px(bz[0])}–{_px(bz[1])} · ❌ Invalid below {_px(d.get('invalidation'))}",
         f"   📈 Target ~{_px(d.get('target_low'))}–{_px(d.get('target_high'))} (mechanical) · ⏳ {d.get('hold_period')}",
         f"   ↳ {d.get('why')}"]
    return "\n".join(L)


def build_longterm_report(A, B, C, new_e, removed, date_str):
    for lst in (A, B, C):
        for i, d in enumerate(lst, 1):
            d["_rank"] = i
    L = [DIV, "📈  US LONG-TERM WATCHLIST", f"🗓 {date_str}", DIV,
         f"\n🏆 CATEGORY A — Large Cap  (${int(LT_LARGE_MIN/1e9)}B–${int(LT_LARGE_MAX/1e9)}B)"]
    L += [_lt_block(d) for d in A] or ["   — none cleared the bar —"]
    L += [f"\n🏆 CATEGORY B — Mid Cap  (${int(LT_MIDLG_MIN/1e9)}B–${int(LT_MIDLG_MAX/1e9)}B)"]
    L += [_lt_block(d) for d in B] or ["   — none cleared the bar —"]
    L += [f"\n🏆 CATEGORY C — Small/Mid  (${int(LT_SMALL_MIN/1e9)}B–${int(LT_SMALL_MAX/1e9)}B)"]
    L += [_lt_block(d) for d in C] or ["   — none cleared the bar —"]
    total = len(A) + len(B) + len(C)
    L += [f"\n{DIV}", f"📊 {total} long-term names (cap 20)",
          f"🆕 New: {', '.join(new_e[:8]) if new_e else 'None'}",
          f"❌ Dropped: {', '.join(removed[:8]) if removed else 'None'}"]
    return "\n".join(L)


def _money(x):
    if x is None:
        return "–"
    sign = "+" if x >= 0 else "−"
    return f"{sign}${abs(x):,.0f}"


def _opt_block(o):
    arrow = "📈 CALL" if o["direction"] == "bullish" else "📉 PUT"
    e = o.get("entry") or (None, None)

    def stk(s):
        if not s:
            return "–"
        p = s.get("prem")
        return f"{s['strike']:.0f}" + (f" (${p:.2f})" if p else "")
    ivtag = ""
    if o.get("iv_hv") is not None:
        tag = "cheap" if o["iv_hv"] <= 0.9 else "fair" if o["iv_hv"] <= 1.2 else "rich"
        ivtag = f" ({o['iv_hv']:.1f}x HV {tag})"
    ivr = ""
    if o.get("iv_rank") is not None:
        ivr = f" · IVR {o['iv_rank']}"
    elif o.get("iv_n"):
        ivr = f" · IVR soon({o['iv_n']})"

    L = ["┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄",
         f"#{o['_rank']} {arrow}  {o['ticker']} — {o['name'][:22]}",
         f"{o.get('trust', '–')}  ·  {o['conviction']}/100",
         f"🧩 {' · '.join(o.get('matches', [])[:4]) or '–'}",
         f"📐 {o.get('setup')}",
         "",
         f"💲 Now {_px(o['price'])}",
         f"🎯 Entry {_px(e[0])}–{_px(e[1])}  ·  Trigger {_px(o.get('breakout'))}",
         f"🛑 Stop {_px(o.get('stop'))}",
         f"🏁 T1 {_px(o.get('t1'))} → T2 {_px(o.get('t2'))} → 🚀 {_px(o.get('stretch'))}"
         + (f"  (R/R {o['rr']:.1f})" if o.get("rr") else ""),
         "",
         f"📅 {o['exp']}  ({o['dte']} DTE)",
         f"🎟 Safe {stk(o['safe'])} · Bal {stk(o['bal'])} · Aggr {stk(o['aggr'])}"]
    est = o.get("est")
    if est:
        L.append(f"💵 Bal contract ≈ ${est['cost']:,.0f}:")
        L.append(f"   at T1 {_money(est['t1_pl'])} ({est['t1_pct']:+.0f}%) · "
                 f"at T2 {_money(est['t2_pl'])} ({est['t2_pct']:+.0f}%)")
        L.append(f"   worst case −${est['cost']:,.0f} (−100%)")
    iv_line = (f"📊 IV {o['iv']:.0f}%{ivtag}{ivr} · OI {int(o['oi']):,} · Vol {int(o['ovol']):,}"
               if o.get("iv") else
               f"📊 IV –{ivr} · OI {int(o['oi']):,} · Vol {int(o['ovol']):,}")
    L.append(iv_line)
    if o.get("uoa") is not None and o["uoa"] >= 0.8:
        L.append(f"🔥 Unusual flow ({o['uoa']:.1f}x OI today)")
    if not o.get("liquid"):
        L.append("⚠ Thin liquidity — check bid/ask first")
    if o.get("earnings_before"):
        L.append("⚠ Earnings before expiry — gap/IV risk")
    return "\n".join(L)


def build_options_report(opts, date_str, etf_ideas=None, leaps=None):
    for i, o in enumerate(opts, 1):
        o["_rank"] = i
    L = [DIV, "🎯  OPTIONS WATCHLIST (45–90 DTE)", f"🗓 {date_str}", DIV]
    calls = [o for o in opts if o["direction"] == "bullish"]
    puts = [o for o in opts if o["direction"] == "bearish"]
    L.append(f"\n⚡ {len(opts)} stock ideas — {len(calls)} call-side, {len(puts)} put-side (cap {OPT_OUT})")
    if opts:
        L += [_opt_block(o) for o in opts]
    else:
        L.append("   — no setups cleared the options filters this week —")
    if etf_ideas:
        for i, o in enumerate(etf_ideas, 1):
            o["_rank"] = i
        L += [f"\n{DIV}", "📊  ETF IDEAS (SPY · QQQ · IWM + movers)", DIV]
        L += [_opt_block(o) for o in etf_ideas]
    if leaps:
        for i, o in enumerate(leaps, 1):
            o["_rank"] = i
        L += [f"\n{DIV}", "🏦  LEAPS — LONG-TERM OPTIONS (6mo–2yr+)", DIV,
              "Top fundamental names · targets from the long-term model"]
        L += [_opt_block(o) for o in leaps]
    return "\n".join(L)


def build_catalyst_report(opts, scanned, filtered, rejected, shortlisted):
    L = [DIV, "📅  CATALYSTS & RUN LOG", DIV]
    earn = [o for o in opts if o.get("earnings_before")]
    if earn:
        L.append("\n⚠ Option ideas with earnings before expiry:")
        for o in earn:
            L.append(f"   {o['ticker']} — earnings ~{o.get('next_earnings', 'TBD')} (exp {o['exp']})")
    else:
        L.append("\nNo earnings flagged inside the option windows.")
    L += [f"\n📊 Run log:",
          f"   Scanned: {scanned}",
          f"   Passed data check: {filtered}",
          f"   Rejected/no-data: {rejected}",
          f"   Options shortlisted: {shortlisted}",
          f"   Final option ideas: {len(opts)}"]
    L += ["\n" + DIV, "⚠ RISK DISCLAIMER", DIV,
          "Screening & education tool only — NOT investment advice and NOT a "
          "recommendation to buy or sell. All scores, buy zones, targets, stops, "
          "and strikes are MECHANICAL outputs of fixed rules, not predictions. "
          "Options can lose 100% of their value quickly. IV-vs-HV is a proxy "
          "(true IV Rank needs paid data). Pattern labels are heuristics. Data "
          "via free feeds may be delayed or wrong. Verify everything and do your "
          "own research before risking money."]
    return "\n".join(L)



# ===========================================================================
# RUN
# ===========================================================================
def _wl_item(d, idea=None):
    """Build a persisted watchlist entry with trigger levels."""
    price, atr = d.get("price"), d.get("atr")
    sc, direction, setup = options_setup_score(d)
    if idea:
        e = idea.get("entry") or (None, None)
        return {"ticker": d["ticker"], "name": d.get("name", ""), "direction": idea["direction"],
                "setup": idea.get("setup"), "breakout": idea.get("breakout"),
                "entry_lo": e[0], "entry_hi": e[1], "stop": idea.get("stop"),
                "t1": idea.get("t1"), "t2": idea.get("t2"),
                "conviction": idea.get("conviction"), "ref_price": price}
    if direction == "neutral" or not price or not atr:
        return None
    bull = direction == "bullish"
    breakout = d.get("recent_high_20") if bull else d.get("recent_low_20")
    stop = (max(d.get("recent_low_20") or 0, price - 2.5 * atr) if bull
            else price + 2.5 * atr)
    t1 = price + 2 * atr if bull else price - 2 * atr
    return {"ticker": d["ticker"], "name": d.get("name", ""), "direction": direction,
            "setup": setup, "breakout": breakout,
            "entry_lo": price * 0.99 if bull else None,
            "entry_hi": price * 1.01 if bull else None,
            "stop": stop, "t1": t1, "t2": None, "conviction": sc, "ref_price": price}


def build_and_save_watchlist(scored, opts, shortlist):
    items, seen = [], set()
    for o in opts:
        dsrc = next((x for x in scored if x["ticker"] == o["ticker"]), {})
        it = _wl_item(dsrc or {"ticker": o["ticker"], "name": o["name"]}, idea=o)
        if it:
            items.append(it); seen.add(o["ticker"])
    for d in shortlist:
        if d["ticker"] in seen:
            continue
        it = _wl_item(d)
        if it:
            items.append(it); seen.add(d["ticker"])
        if len(items) >= WATCHLIST_MAX:
            break
    save_watchlist(items)
    return items


def scan_and_score(universe):
    scanned = len(universe)
    print(f"Scanning {scanned} stocks...\n")
    raw = fetch_many(universe)
    compute_rs_ratings(raw)
    scored, rejected = [], 0
    for d in raw:
        if not d.get("ok"):
            rejected += 1
            continue
        d["is_etf"] = str(d.get("ticker", "")).upper() in ETF_SET
        score_stock(d)
        scored.append(d)
    return scored, scanned, len(scored), rejected


# ---------------------------------------------------------------------------
# MODE 1 — Saturday deep scan: long-term report + options + builds watchlist
# ---------------------------------------------------------------------------
def run_longterm():
    now = dt.datetime.now(TZ)
    date_str = now.strftime("%d %b %Y")
    print("=" * 60)
    print("US Screener v2 [LONGTERM] —", now.strftime("%Y-%m-%d %H:%M UTC"))
    print("Mode:", "TEST (5 stocks)" if TEST_MODE else f"UNIVERSE={UNIVERSE}")
    print("=" * 60)

    scored, scanned, filtered, rejected = scan_and_score(get_universe())

    A, B, C = select_long_term(scored)
    current = {d["ticker"].upper() for d in (A + B + C)}
    prev = read_previous()
    new_e = sorted(current - prev)
    removed = sorted(prev - current)

    opts, shortlisted, shortlist, etf_ideas = build_options_watchlist(scored)
    leaps = build_leaps(scored)
    build_and_save_watchlist(scored, opts + etf_ideas, shortlist)

    dstr = now.strftime("%Y-%m-%d")
    rows = []
    for cat, lst in (("A-Large", A), ("B-Mid", B), ("C-Small", C)):
        for d in lst:
            rows.append({"Date": dstr, "Ticker": d["ticker"], "Category": cat,
                         "Score": d["score"], "MarketCap": int(d.get("market_cap") or 0),
                         "RS": d.get("rs_rating"),
                         "Strengths": "; ".join(d.get("strengths", [])),
                         "Concerns": "; ".join(d.get("concerns", []))})
    write_tracking(rows)

    print("\n--- Run log ---")
    print(f"  scanned={scanned}  passed={filtered}  rejected={rejected}")
    print(f"  long-term: A={len(A)} B={len(B)} C={len(C)}  options={len(opts)}/{shortlisted}")
    for d in (A + B + C):
        print(f"  {d['ticker']:8} {d['score']:3}/100  RS {str(d.get('rs_rating')):>3}  {_mc(d.get('market_cap'))}")
    for o in opts:
        print(f"  {o['ticker']:8} {o['direction']:7} conv {o['conviction']:3}  exp {o['exp']}  {o.get('setup')}")

    rpt_lt = build_longterm_report(A, B, C, new_e, removed, date_str)
    rpt_opt = build_options_report(opts, date_str, etf_ideas, leaps)
    rpt_cat = build_catalyst_report(opts, scanned, filtered, rejected, shortlisted)
    print("\n" + rpt_lt + "\n\n" + rpt_opt + "\n\n" + rpt_cat + "\n")
    send_telegram(rpt_lt, "long-term report")
    send_telegram(rpt_opt, "options report")
    send_telegram(rpt_cat, "catalyst report")
    print("Done.")


# ---------------------------------------------------------------------------
# MODE 2 — Market days 9:45 / 3:00 ET: S&P500 + NDX100 + watchlist -> options
# ---------------------------------------------------------------------------
def run_options():
    now = dt.datetime.now(TZ)
    date_str = now.strftime("%d %b %Y %H:%M UTC")
    print("=" * 60)
    print("US Screener v2 [OPTIONS] —", date_str)
    print("=" * 60)

    scored, scanned, filtered, rejected = scan_and_score(get_intraday_universe())
    opts, shortlisted, shortlist, etf_ideas = build_options_watchlist(scored)
    leaps = build_leaps(scored)
    build_and_save_watchlist(scored, opts + etf_ideas, shortlist)   # refresh trigger levels

    print(f"\n  scanned={scanned} passed={filtered} rejected={rejected} "
          f"options={len(opts)}/{shortlisted}")

    rpt_opt = build_options_report(opts, date_str, etf_ideas, leaps)
    rpt_cat = build_catalyst_report(opts, scanned, filtered, rejected, shortlisted)
    print("\n" + rpt_opt + "\n\n" + rpt_cat + "\n")
    send_telegram(rpt_opt, "options report")
    send_telegram(rpt_cat, "catalyst report")
    print("Done.")


# ---------------------------------------------------------------------------
# MODE 3 — Hourly live check: watchlist only, alert ONLY on fresh triggers
# ---------------------------------------------------------------------------
def _live_check_one(w):
    """Returns list of (key, alert_line) for fresh trigger events on one name."""
    if yf is None:
        return []
    t = w.get("ticker")
    try:
        hist = yf.Ticker(t).history(period="5d", interval="1d", auto_adjust=True)
        close = hist["Close"].dropna()
        if len(close) < 2:
            return []
        price, prev = _safe(close.iloc[-1]), _safe(close.iloc[-2])
    except Exception:
        return []
    if not price or not prev:
        return []
    bull = w.get("direction") == "bullish"
    bo, stop = w.get("breakout"), w.get("stop")
    elo, ehi, t1 = w.get("entry_lo"), w.get("entry_hi"), w.get("t1")
    out = []
    nm = f"{t} ({w.get('setup', '')})".strip()
    if bo:
        crossed = (prev <= bo < price) if bull else (prev >= bo > price)
        if crossed:
            out.append((f"{t}:breakout",
                        f"🚀 {nm}\n   {'Broke ABOVE' if bull else 'Broke BELOW'} {_px(bo)} → now {_px(price)}\n"
                        f"   Plan: stop {_px(stop)} · T1 {_px(t1)}"))
        elif LIVE_ALERT_APPROACH and price and abs(price - bo) / bo * 100 <= LIVE_NEAR_PCT:
            side_ok = price < bo if bull else price > bo
            if side_ok:
                out.append((f"{t}:approach",
                            f"👀 {nm}\n   Within {LIVE_NEAR_PCT}% of trigger {_px(bo)} (now {_px(price)})"))
    if bull and elo and ehi and elo <= price <= ehi and (prev < elo or prev > ehi):
        out.append((f"{t}:entryzone",
                    f"🎯 {nm}\n   Pulled into entry zone {_px(elo)}–{_px(ehi)} (now {_px(price)})"))
    if stop:
        hit = (prev > stop >= price) if bull else (prev < stop <= price)
        if hit:
            out.append((f"{t}:stop",
                        f"🛑 {nm}\n   STOP level {_px(stop)} hit (now {_px(price)}) — risk plan triggered"))
    if t1:
        reached = (prev < t1 <= price) if bull else (prev > t1 >= price)
        if reached:
            out.append((f"{t}:t1",
                        f"💰 {nm}\n   Target 1 {_px(t1)} reached (now {_px(price)}) — consider trimming/trailing"))
    return out


def run_live():
    now = dt.datetime.now(TZ)
    print(f"[LIVE] {now:%Y-%m-%d %H:%M UTC} — watchlist trigger check")
    wl = load_watchlist()
    if not wl:
        print("  No watchlist yet (runs after the first Saturday/options scan).")
        return
    state = load_alert_state()
    today = dt.date.today().isoformat()
    alerts = []
    for w in wl:
        for key, line in _live_check_one(w):
            if state.get(key) == today:
                continue            # already alerted today -> stay silent
            state[key] = today
            alerts.append(line)
        time.sleep(0.3)
    save_alert_state(state)
    if alerts:
        msg = "\n".join([DIV, "⚡ LIVE TRIGGER ALERTS", f"🗓 {now:%d %b %Y %H:%M} UTC", DIV, ""]
                        + alerts
                        + ["", "Reference levels, not advice. Verify live quotes."])
        print(msg)
        send_telegram(msg, "live alerts")
    else:
        print(f"  {len(wl)} names checked — no fresh triggers. (Silent, no message.)")


def run_scheduler():
    print(f"Scheduler armed: Sunday {RUN_HOUR:02d}:{RUN_MINUTE:02d} UTC.")
    last = None
    while True:
        now = dt.datetime.now(TZ)
        if (now.weekday() == RUN_WEEKDAY and now.hour == RUN_HOUR
                and now.minute == RUN_MINUTE and last != now.date()):
            run_longterm()
            last = now.date()
        time.sleep(20)


if __name__ == "__main__":
    if RUN_MODE == "options":
        run_options()
    elif RUN_MODE == "live":
        run_live()
    else:
        run_longterm()
    if RUN_SCHEDULER:
        run_scheduler()
