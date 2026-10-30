"""STUB for the chart (Track C) until Track B lands it in drishti.features; then this file is
deleted and chart_api imports the real one. Same style as drishti.features.indicators: `df`
oldest first, optional `by` = the stock column when `df` holds many stocks. Each value on row i
uses rows <= i only. (bollinger, macd, vwap, trend and nearest landed with Track B and are
imported from drishti.features.)

Interface proposed to Track B:
  indicators.supertrend(df, by=None, n=10, mult=3.0) -> DataFrame[st, st_dir]
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from drishti.features.indicators import _grp
from drishti.features.indicators import atr as indicators_atr


def supertrend(df: pd.DataFrame, by: str | None = None, n: int = 10, mult: float = 3.0
               ) -> pd.DataFrame:
    """STUB until Track B lands indicators.supertrend (not in the frozen spec; chart only).
    Bands hl2 +- mult * Wilder ATR(n) (indicators.atr), carried the usual way; st_dir = 1 while
    the close holds above the lower band, -1 below the upper band."""
    atr = indicators_atr(df, by, n).to_numpy()
    hl2 = ((df.high + df.low) / 2).to_numpy(float)
    c = df.close.to_numpy(float)
    g = _grp(df, by).to_numpy()
    up, lo = hl2 + mult * atr, hl2 - mult * atr
    st, dr = np.full(len(df), np.nan), np.zeros(len(df))
    fu, fl = up.copy(), lo.copy()
    for i in range(len(df)):
        if np.isnan(atr[i]):
            continue
        first = i == 0 or g[i] != g[i - 1] or np.isnan(atr[i - 1])
        if not first:
            fu[i] = up[i] if up[i] < fu[i - 1] or c[i - 1] > fu[i - 1] else fu[i - 1]
            fl[i] = lo[i] if lo[i] > fl[i - 1] or c[i - 1] < fl[i - 1] else fl[i - 1]
        d = 1 if first else dr[i - 1]
        d = 1 if c[i] > fu[i] else -1 if c[i] < fl[i] else d
        dr[i], st[i] = d, fl[i] if d == 1 else fu[i]
    return pd.DataFrame({"st": st, "st_dir": np.where(np.isnan(st), np.nan, dr)}, index=df.index)
