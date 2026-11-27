"""Daily technical-indicator compute (pure pandas, no TA-Lib needed).

For each ticker in index_prices with >= 50 daily bars, computes over closes:
RSI(14, Wilder), MACD line/signal/histogram (12/26/9), SMA20, SMA50,
Bollinger(20, 2) upper/middle/lower. Writes one row per (ticker, ts, name)
into technical_indicators (ts = last bar), idempotent DELETE+INSERT.

Tickers with < 50 bars are skipped (normal before the 1y backfill lands).
Exit 1 only if zero tickers were computed (signals breakage, not thin data).

Env: PGHOST, PGUSER, PGPASSWORD, PGDATABASE. Runs 00:30 UTC, after prices land.
"""

import os
import sys

import pandas as pd
import psycopg

MIN_BARS = 50


def rsi_wilder(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))


def compute(df: pd.DataFrame) -> dict:
    """Return {indicator_name: value} for the last bar. NaNs dropped by caller."""
    close = df["close"]
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    return {
        "RSI14": rsi_wilder(close).iloc[-1],
        "MACD": macd.iloc[-1],
        "MACD_SIGNAL": signal.iloc[-1],
        "MACD_HIST": (macd - signal).iloc[-1],
        "SMA20": sma20.iloc[-1],
        "SMA50": close.rolling(50).mean().iloc[-1],
        "BB_UPPER": (sma20 + 2 * std20).iloc[-1],
        "BB_MIDDLE": sma20.iloc[-1],
        "BB_LOWER": (sma20 - 2 * std20).iloc[-1],
    }


def main() -> int:
    conn = psycopg.connect(
        host=os.environ.get("PGHOST", "timescaledb.market-data.svc.cluster.local"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ["PGPASSWORD"],
        dbname=os.environ.get("PGDATABASE", "postgres"),
    )
    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT ticker FROM index_prices")
            tickers = [row[0] for row in cur.fetchall()]
    print(f"{len(tickers)} tickers in index_prices", flush=True)
    computed = 0
    skipped = 0
    conn2 = psycopg.connect(
        host=os.environ.get("PGHOST", "timescaledb.market-data.svc.cluster.local"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ["PGPASSWORD"],
        dbname=os.environ.get("PGDATABASE", "postgres"),
    )
    with conn2:
        with conn2.cursor() as cur:
            for ticker in tickers:
                cur.execute(
                    "SELECT ts, close FROM index_prices "
                    "WHERE ticker = %s AND close IS NOT NULL ORDER BY ts",
                    (ticker,),
                )
                bars = cur.fetchall()
                if len(bars) < MIN_BARS:
                    skipped += 1
                    continue
                df = pd.DataFrame(bars, columns=["ts", "close"])
                ts = df["ts"].iloc[-1]
                try:
                    indicators = compute(df)
                except Exception as exc:  # noqa: BLE001 - per-ticker failure tolerated
                    print(f"WARN {ticker} compute failed: {exc}", flush=True)
                    skipped += 1
                    continue
                for name, value in indicators.items():
                    if pd.isna(value):
                        continue
                    cur.execute(
                        "DELETE FROM technical_indicators "
                        "WHERE ticker = %s AND ts = %s AND indicator_name = %s",
                        (ticker, ts, name),
                    )
                    cur.execute(
                        "INSERT INTO technical_indicators "
                        "(ticker, ts, indicator_name, value) "
                        "VALUES (%s, %s, %s, %s)",
                        (ticker, ts, name, float(value)),
                    )
                computed += 1
    conn.close()
    conn2.close()
    print(f"computed indicators for {computed} tickers, skipped {skipped}",
          flush=True)
    if computed == 0:
        print("ERROR: zero tickers computed", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    from mdjob import run_main   # one job_runs row per execution; never fails the job
    sys.exit(run_main("ta-compute", main))
