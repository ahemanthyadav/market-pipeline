"""ML-0 experiment infrastructure — walk-forward splitter with purge/embargo.

Returns train/test index pairs for expanding-window backtests on a time index.
All splits are time-based only; no look-ahead. Reused by ML-3, ML-5, ML-6.

Usage:
    from ml0_splitter import walk_forward
    for train_idx, test_idx in walk_forward(n_samples, n_splits=5, purge=5, embargo=5):
        ...
"""
from __future__ import annotations

import numpy as np


def walk_forward(
    n_samples: int,
    n_splits: int = 5,
    purge: int = 5,
    embargo: int = 5,
    min_train: int | None = None,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Expanding-window walk-forward splits.

    Each split trains on [0, split_end) and tests on [split_end, split_end +
    test_size). Purge removes `purge` samples from the end of train (samples
    whose features may leak test information), and embargo removes `embargo`
    samples from the start of test (samples whose labels may leak train
    information).

    Parameters
    ----------
    n_samples : total number of time-ordered samples.
    n_splits : number of backtest folds.
    purge : samples trimmed from the train end of each fold.
    embargo : samples trimmed from the test start of each fold.
    min_train : minimum training samples required (default: n_samples // (n_splits + 1)).

    Returns
    -------
    list of (train_idx, test_idx) numpy index arrays.
    """
    if min_train is None:
        min_train = max(1, n_samples // (n_splits + 1))
    if n_samples < min_train + purge + embargo + 1:
        raise ValueError("n_samples too small for requested splits")

    fold_size = n_samples // n_splits
    splits = []
    for i in range(n_splits):
        split_end = min_train + i * fold_size
        test_start = split_end
        test_end = min(split_end + fold_size, n_samples)
        if test_end <= test_start:
            break
        train_end = split_end - purge
        if train_end < min_train:
            continue
        test_slice = slice(test_start + embargo, test_end)
        if test_slice.start >= test_end:
            continue
        train_idx = np.arange(0, train_end)
        test_idx = np.arange(*test_slice.indices(n_samples))
        splits.append((train_idx, test_idx))
    return splits


def expanding_window(
    n_samples: int,
    initial_train: int,
    step: int = 1,
    purge: int = 5,
    embargo: int = 5,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Expanding-window splits with fixed initial train size, stepping forward.

    Each fold trains on [0, train_end) and tests on [train_end, train_end +
    step). Train end grows by `step` each fold.
    """
    splits = []
    train_end = initial_train
    while train_end + step <= n_samples:
        train_idx = np.arange(0, train_end - purge)
        test_idx = np.arange(train_end + embargo, min(train_end + step, n_samples))
        if len(train_idx) > 0 and len(test_idx) > 0:
            splits.append((train_idx, test_idx))
        train_end += step
    return splits


if __name__ == "__main__":
    # Smoke test: 100 samples, 5 splits
    splits = walk_forward(100, n_splits=5, purge=5, embargo=5)
    for i, (tr, te) in enumerate(splits):
        print(f"fold {i}: train={len(tr)} [{tr[0]}-{tr[-1]}], test={len(te)} [{te[0]}-{te[-1]}]")
