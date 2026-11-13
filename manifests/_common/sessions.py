"""Exchange trading sessions — is a daily bar final yet?

Why (found 2026-11-12): CronJob schedules run in the control plane's local time
(America/Los_Angeles; manual §3 "NODE-LOCAL" / find F20), so the price writers
fire while some markets are still trading:
  * stock-prices at 23:30 PT = 13:00 IST  -> mid-session NSE bars
  * yfinance-fetcher at 22:30 PT = 06:30 UTC -> mid-session HK/Shanghai/
    Singapore/Seoul/Nifty-microcap bars
The writers kept only the newest bar, so those intraday snapshots were stored as
closes and never corrected (^HSI / 000001.SS / ^STI off by up to 0.7 %).
Writers now drop any bar whose session has not closed (+ BUFFER for Yahoo to
settle the final print) and rewrite their whole fetch window, so the next run
stores the final bar.

Pure + stdlib only (zoneinfo); copied into each build context like mdjob.py.
"""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

BUFFER = timedelta(minutes=30)

# exchange -> (IANA tz, local close). Futures/DXY: CME settlement day ends 17:00 ET.
EXCHANGES = {
    "NSE": ("Asia/Kolkata", time(15, 30)),
    "US": ("America/New_York", time(16, 15)),          # 16:15 covers ^VIX
    "CME": ("America/New_York", time(17, 0)),
    "TSX": ("America/Toronto", time(16, 0)),
    "B3": ("America/Sao_Paulo", time(18, 0)),
    "BMV": ("America/Mexico_City", time(15, 0)),
    "LSE": ("Europe/London", time(16, 35)),
    "XETRA": ("Europe/Berlin", time(17, 35)),
    "EURONEXT_PA": ("Europe/Paris", time(17, 35)),
    "EURONEXT_AMS": ("Europe/Amsterdam", time(17, 35)),
    "STOXX": ("Europe/Berlin", time(17, 35)),
    "BME": ("Europe/Madrid", time(17, 35)),
    "MIL": ("Europe/Rome", time(17, 35)),
    "SIX": ("Europe/Zurich", time(17, 30)),
    "JPX": ("Asia/Tokyo", time(15, 30)),
    "HKEX": ("Asia/Hong_Kong", time(16, 10)),
    "SSE": ("Asia/Shanghai", time(15, 0)),
    "KRX": ("Asia/Seoul", time(15, 30)),
    "TWSE": ("Asia/Taipei", time(13, 30)),
    "ASX": ("Australia/Sydney", time(16, 10)),
    "SGX": ("Asia/Singapore", time(17, 10)),
}

TICKER_EXCHANGE = {
    "^GSPC": "US", "^IXIC": "US", "^DJI": "US", "^RUT": "US", "^VIX": "US",
    "^RUI": "US", "^SP400": "US", "^SP600": "US",
    "^FTSE": "LSE", "^GDAXI": "XETRA", "^FCHI": "EURONEXT_PA", "^STOXX50E": "STOXX",
    "^IBEX": "BME", "FTSEMIB.MI": "MIL", "^SSMI": "SIX", "^AEX": "EURONEXT_AMS",
    "^N225": "JPX", "^HSI": "HKEX", "000001.SS": "SSE", "399001.SZ": "SSE",
    "^KS11": "KRX", "^TWII": "TWSE", "^AXJO": "ASX", "^STI": "SGX",
    "^GSPTSE": "TSX", "^BVSP": "B3", "^MXX": "BMV",
    "DX-Y.NYB": "CME", "GC=F": "CME", "SI=F": "CME", "CL=F": "CME",
    "^NSEI": "NSE", "^NSEBANK": "NSE", "^CNXIT": "NSE", "^CNXPHARMA": "NSE",
    "^BSESN": "NSE", "^INDIAVIX": "NSE", "^CRSLDX": "NSE", "^CNXAUTO": "NSE",
    "^CNXFMCG": "NSE",
}
SUFFIX_EXCHANGE = {".NS": "NSE", ".BO": "NSE", ".SS": "SSE", ".SZ": "SSE", ".MI": "MIL"}


def exchange_of(ticker):
    """Exchange key for a ticker. Plain equity symbols (no suffix, no ^/=) are US
    listings in our universes. Unknown index/future tickers -> None (caller keeps
    the bar: better a partial bar than silently losing a series)."""
    if ticker in TICKER_EXCHANGE:
        return TICKER_EXCHANGE[ticker]
    for suffix, ex in SUFFIX_EXCHANGE.items():
        if ticker.endswith(suffix):
            return ex
    if ticker.startswith("^") or "=" in ticker or "." in ticker:
        return None
    return "US"


def session_open(ticker, bar_date, now=None):
    """True if the bar dated `bar_date` (exchange-local trade date) is not final yet:
    it is today's or a future session there and the close (+BUFFER) hasn't passed."""
    ex = exchange_of(ticker)
    if ex is None:
        return False
    tz, close = EXCHANGES[ex]
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(ZoneInfo(tz))
    if isinstance(bar_date, datetime):
        bar_date = bar_date.date()
    if bar_date > local.date():
        return True
    if bar_date < local.date():
        return False
    return local < datetime.combine(local.date(), close, ZoneInfo(tz)) + BUFFER


def closed_bars(ticker, bars, now=None):
    """Filter [(ts, ...), ...] to bars whose session has closed (ts = trade date)."""
    return [b for b in bars if not session_open(ticker, b[0], now)]

