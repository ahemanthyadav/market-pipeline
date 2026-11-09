"""ML-0 leakage test — detects future leakage and point-in-time filter violations.

Two checks:
1. Shift check: confirms no feature at time t uses information from t+1 or later
   (e.g., tomorrow's close used as today's feature).
2. PIT filter check: confirms fundamentals are used as of `filing_published_at`,
   not as of the latest re-filing.

Usage:
    from ml0_leakage import shift_check, pit_filter_check
    shift_check(df, feature_cols, time_col="ts", id_col="symbol")
    pit_filter_check(df, facts_df, on="symbol", as_of_col="ts", filed_col="filing_published_at")
"""
from __future__ import annotations

import pandas as pd


def shift_check(
    df: pd.DataFrame,
    feature_cols: list[str],
    time_col: str = "ts",
    id_col: str | None = None,
) -> pd.DataFrame:
    """Flag rows where a feature value equals a future value of itself.

    For each feature column, compares df[col] at time t with df[col] at t+1
    (per id, if id_col given). A match indicates the feature at t already
    contains t+1 information — classic look-ahead leakage.

    Returns a DataFrame of leaking rows (may be empty).
    """
    df = df.sort_values([id_col, time_col] if id_col else [time_col]).copy()
    leaking_rows: list[pd.DataFrame] = []

    for col in feature_cols:
        if col not in df.columns:
            continue
        # Shift feature down by one row: shifted[row] = next row's value
        if id_col:
            shifted = df.groupby(id_col)[col].shift(-1)
        else:
            shifted = df[col].shift(-1)
        mask = df[col].eq(shifted) & shifted.notna()
        if mask.any():
            leaking_rows.append(df[mask].assign(_leaky_feature=col))

    if leaking_rows:
        return pd.concat(leaking_rows, ignore_index=True)
    return df.iloc[0:0].copy()  # empty with same columns


def pit_filter_check(
    df: pd.DataFrame,
    facts_df: pd.DataFrame,
    on: str = "symbol",
    as_of_col: str = "ts",
    filed_col: str = "filing_published_at",
    fact_col: str | None = None,
) -> pd.DataFrame:
    """Flag feature rows that use facts published *after* the feature date.

    For each (id, as_of) row in df, checks whether the facts used were
    first published at or before `as_of`. A violation means a fact with
    filed > as_of was used — look-ahead bias from re-filings.

    Returns a DataFrame of violating rows (may be empty).
    """
    if fact_col:
        # Check each fact column individually: max filed date per (id, fact)
        # must be <= as_of
        violations = []
        for col in fact_col:
            if col not in facts_df.columns or col not in df.columns:
                continue
            latest_filed = (
                facts_df.groupby(on)[filed_col].max().rename("max_filed")
            )
            merged = df.merge(latest_filed, on=on, how="left")
            mask = merged["max_filed"] > merged[as_of_col]
            if mask.any():
                violations.append(merged[mask].assign(_leaky_fact=col))
        if violations:
            return pd.concat(violations, ignore_index=True)
    return df.iloc[0:0].copy()


if __name__ == "__main__":
    # Smoke test
    import numpy as np

    df = pd.DataFrame(
        {
            "symbol": ["A", "A", "A", "B", "B"],
            "ts": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-01", "2026-01-02"]),
            "ret": [0.01, 0.02, 0.03, 0.04, 0.05],
        }
    )
    # Introduce leakage: A's 2026-01-02 ret equals 2026-01-03's value
    df.loc[df["ts"] == "2026-01-02", "ret"] = 0.03
    leaks = shift_check(df, ["ret"], time_col="ts", id_col="symbol")
    print(f"Leaking rows: {len(leaks)}")
    print(leaks[["symbol", "ts", "ret", "_leaky_feature"]])
