"""ML-1 anomaly detection — data quality and node health flags.

v1 rules (rules + robust statistics, no model):
1. Per-symbol return spike: MAD z-score vs the symbol's own history.
2. Per-symbol volume spike: MAD z-score vs the symbol's own history.
3. Partial / zero-volume bars.
4. Per-feed row-count drop vs trailing median.
5. Per-feed freshness drop vs expected cadence.

Output: rows for `app.data_anomalies` table (kind, subject, ts, score, details).

Usage:
    from ml1_anomalies import return_spike, volume_spike, zero_volume_bars
    anomalies = return_spike(prices_df, symbol_col="symbol", ts_col="ts", ret_col="ret", mad_window=252)
"""
from __future__ import annotations

import pandas as pd


def _mad_zscore(series: pd.Series) -> pd.Series:
    """Robust z-score using median absolute deviation."""
    median = series.median()
    mad = (series - median).abs().median()
    if mad == 0 or pd.isna(mad):
        return pd.Series(0.0, index=series.index)
    return 0.6745 * (series - median) / mad


def return_spike(
    prices: pd.DataFrame,
    symbol_col: str = "symbol",
    ts_col: str = "ts",
    ret_col: str = "ret",
    mad_window: int = 252,
    z_threshold: float = 5.0,
) -> pd.DataFrame:
    """Flag per-symbol daily returns that are outliers vs the symbol's own history.

    Uses a rolling MAD z-score over `mad_window` days. Returns anomalies
    with kind="return_spike", subject=symbol, score=z-score.
    """
    df = prices.sort_values([symbol_col, ts_col]).copy()
    df["z"] = df.groupby(symbol_col)[ret_col].transform(
        lambda s: _mad_zscore(s.rolling(mad_window, min_periods=max(10, mad_window // 10)))
    )
    mask = df["z"].abs() > z_threshold
    if not mask.any():
        return df.iloc[0:0].copy()
    out = df[mask].copy()
    out = out.rename(columns={"z": "score"})
    out["kind"] = "return_spike"
    out["subject"] = out[symbol_col]
    out["details"] = out.apply(
        lambda r: {"return": r[ret_col], "z_score": r["score"], "window": mad_window}, axis=1
    )
    return out[["kind", "subject", ts_col, "score", "details"]].rename(columns={ts_col: "ts"})


def volume_spike(
    prices: pd.DataFrame,
    symbol_col: str = "symbol",
    ts_col: str = "ts",
    vol_col: str = "volume",
    mad_window: int = 252,
    z_threshold: float = 5.0,
) -> pd.DataFrame:
    """Flag per-symbol daily volume that is an outlier vs the symbol's own history.

    Returns anomalies with kind="volume_spike", subject=symbol, score=z-score.
    """
    df = prices.sort_values([symbol_col, ts_col]).copy()
    df["z"] = df.groupby(symbol_col)[vol_col].transform(
        lambda s: _mad_zscore(s.rolling(mad_window, min_periods=max(10, mad_window // 10)))
    )
    mask = df["z"].abs() > z_threshold
    if not mask.any():
        return df.iloc[0:0].copy()
    out = df[mask].copy()
    out = out.rename(columns={"z": "score"})
    out["kind"] = "volume_spike"
    out["subject"] = out[symbol_col]
    out["details"] = out.apply(
        lambda r: {"volume": r[vol_col], "z_score": r["score"], "window": mad_window}, axis=1
    )
    return out[["kind", "subject", ts_col, "score", "details"]].rename(columns={ts_col: "ts"})


def zero_volume_bars(
    prices: pd.DataFrame,
    symbol_col: str = "symbol",
    ts_col: str = "ts",
    vol_col: str = "volume",
) -> pd.DataFrame:
    """Flag bars with zero or missing volume (partial bars, scraper breakage).

    Returns anomalies with kind="zero_volume", subject=symbol, score=1.0.
    """
    df = prices.copy()
    mask = df[vol_col].isna() | (df[vol_col] <= 0)
    if not mask.any():
        return df.iloc[0:0].copy()
    out = df[mask].copy()
    out["kind"] = "zero_volume"
    out["subject"] = out[symbol_col]
    out["score"] = 1.0
    out["details"] = out.apply(
        lambda r: {"volume": r[vol_col]}, axis=1
    )
    return out[["kind", "subject", ts_col, "score", "details"]].rename(columns={ts_col: "ts"})


def feed_row_count_drop(
    counts: pd.DataFrame,
    feed_col: str = "feed",
    ts_col: str = "ts",
    count_col: str = "row_count",
    lookback: int = 5,
    drop_pct: float = 0.5,
) -> pd.DataFrame:
    """Flag per-feed row-count drops vs trailing median.

    For each feed, compares today's row count to the median of the last
    `lookback` days. Flags drops > `drop_pct` (e.g. 50% fewer rows than usual).

    Returns anomalies with kind="feed_row_drop", subject=feed.
    """
    df = counts.sort_values([feed_col, ts_col]).copy()
    df["median_count"] = (
        df.groupby(feed_col)[count_col]
        .transform(lambda s: s.rolling(lookback, min_periods=2).median())
    )
    mask = df[count_col] < df["median_count"] * (1 - drop_pct)
    if not mask.any():
        return df.iloc[0:0].copy()
    out = df[mask].copy()
    out["kind"] = "feed_row_drop"
    out["subject"] = out[feed_col]
    out["score"] = (df.loc[mask, "median_count"] - df.loc[mask, count_col]) / df.loc[mask, "median_count"]
    out["details"] = out.apply(
        lambda r: {
            "count": r[count_col],
            "median_count": r["median_count"],
            "drop_pct": drop_pct,
        },
        axis=1,
    )
    return out[["kind", "subject", ts_col, "score", "details"]].rename(columns={ts_col: "ts"})


def feed_freshness_drop(
    freshness: pd.DataFrame,
    feed_col: str = "feed",
    ts_col: str = "ts",
    fresh_col: str = "fresh",
    cadence_hours: int = 24,
) -> pd.DataFrame:
    """Flag per-feed freshness drops: fresh=False or stale beyond cadence.

    Returns anomalies with kind="feed_stale", subject=feed, score=1.0.
    """
    df = freshness.copy()
    mask = ~df[fresh_col].astype(bool)
    if not mask.any():
        return df.iloc[0:0].copy()
    out = df[mask].copy()
    out["kind"] = "feed_stale"
    out["subject"] = out[feed_col]
    out["score"] = 1.0
    out["details"] = out.apply(
        lambda r: {"fresh": r[fresh_col], "cadence_hours": cadence_hours}, axis=1
    )
    return out[["kind", "subject", ts_col, "score", "details"]].rename(columns={ts_col: "ts"})


def detect_all(
    prices: pd.DataFrame | None = None,
    counts: pd.DataFrame | None = None,
    freshness: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Run all v1 anomaly rules and concatenate results."""
    parts = []
    if prices is not None:
        parts.append(return_spike(prices))
        parts.append(volume_spike(prices))
        parts.append(zero_volume_bars(prices))
    if counts is not None:
        parts.append(feed_row_count_drop(counts))
    if freshness is not None:
        parts.append(feed_freshness_drop(freshness))
    parts = [p for p in parts if len(p) > 0]
    if not parts:
        return pd.DataFrame(columns=["kind", "subject", "ts", "score", "details"])
    return pd.concat(parts, ignore_index=True)


if __name__ == "__main__":
    # Smoke test
    import numpy as np

    np.random.seed(42)
    n = 100
    prices = pd.DataFrame(
        {
            "symbol": ["AAA"] * n,
            "ts": pd.date_range("2026-01-01", periods=n, freq="D"),
            "ret": np.random.normal(0, 0.02, n),
            "volume": np.random.randint(100_000, 1_000_000, n),
        }
    )
    # Inject anomalies
    prices.loc[10, "ret"] = 0.5  # return spike
    prices.loc[50, "volume"] = 0  # zero volume bar
    leaks = return_spike(prices)
    zvol = zero_volume_bars(prices)
    print(f"Return spikes: {len(leaks)}, Zero-volume bars: {len(zvol)}")
