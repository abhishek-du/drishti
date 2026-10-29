"""Purged + embargoed walk-forward CV (PREREG_M1, change 3): expanding train, test blocks of 3
calendar months; a training row is purged when its label window [T+1, T+5] overlaps the test
block, and rows of the 5 sessions after a test block are embargoed from training."""

from __future__ import annotations

import datetime as _dt

import numpy as np
import pandas as pd

EMBARGO = 5


def blocks(first: _dt.date, last: _dt.date) -> list[tuple[_dt.date, _dt.date]]:
    """3-calendar-month blocks [start, end] from the quarter start on or after `first`."""
    m = ((first.month - 1) // 3) * 3 + 1
    s = _dt.date(first.year, m, 1)
    if s < first:
        s = _dt.date(s.year + (m + 2) // 12, (m + 2) % 12 + 1, 1)
    out = []
    while s <= last:
        n = _dt.date(s.year + (s.month + 2) // 12, (s.month + 2) % 12 + 1, 1)
        out.append((s, min(n - _dt.timedelta(days=1), last)))
        s = n
    return out


def folds(t: pd.Series, t5: pd.Series, calendar: list, first_test: _dt.date,
          last: _dt.date, embargo: int = EMBARGO) -> list[dict]:
    """Per test block: train = rows with T before the block whose label window ends before the
    block starts (purge), minus rows with T in the `embargo` sessions after any earlier test
    block; test = rows with T in the block."""
    T, T5 = pd.to_datetime(t).dt.date.to_numpy(), pd.to_datetime(t5).dt.date.to_numpy()
    cal = sorted(calendar)
    out, emb = [], np.zeros(len(T), bool)
    for bs, be in blocks(first_test, last):
        test = (T >= bs) & (T <= be)
        if not test.any():
            continue
        train = (T < bs) & (T5 < bs) & ~emb
        out.append({"block": (bs, be), "train": np.flatnonzero(train),
                    "test": np.flatnonzero(test)})
        after = [d for d in cal if d > be][:embargo]
        if after:
            emb |= (T > be) & (T <= after[-1])
    return out
