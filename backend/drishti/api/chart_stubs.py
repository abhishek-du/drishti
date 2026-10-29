"""STUBS for the chart (Track C) until Track B lands these in drishti.features; then this file is
deleted and chart_api imports the real ones. Same style as drishti.features.indicators: `df`
oldest first, optional `by` = the stock column when `df` holds many stocks. Each value on row i
uses rows <= i only.

Interface proposed to Track B:
  indicators.bollinger(df, by=None, n=20, k=2.0) -> DataFrame[bb_mid, bb_up, bb_lo]
  indicators.macd(df, by=None, fast=12, slow=26, signal=9) -> DataFrame[macd, macd_signal, macd_hist]
  indicators.vwap(df, by=None) -> Series            (session VWAP, reset per session_date)
  levels.trend(df, ind) -> 'UP' | 'DOWN' | 'SIDE'   (spec section 3, on the last row)
  levels.nearest(zones, x) -> (support | None, resistance | None)
  indicators.supertrend(df, by=None, n=10, mult=3.0) -> DataFrame[st, st_dir]
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from drishti.features.indicators import _grp, ema, roll
from drishti.features.indicators import atr as indicators_atr
from drishti.features.levels import pivots


def bollinger(df: pd.DataFrame, by: str | None = None, n: int = 20, k: float = 2.0
              ) -> pd.DataFrame:
    """STUB until Track B lands indicators.bollinger. Population std (ddof 0)."""
    g, c = _grp(df, by), df.close
    mid = roll(c, g, n, "mean")
    sd = c.groupby(g.to_numpy()).rolling(n, min_periods=n).std(ddof=0) \
        .reset_index(level=0, drop=True).reindex(c.index)
    return pd.DataFrame({"bb_mid": mid, "bb_up": mid + k * sd, "bb_lo": mid - k * sd},
                        index=df.index)


def macd(df: pd.DataFrame, by: str | None = None, fast: int = 12, slow: int = 26,
         signal: int = 9) -> pd.DataFrame:
    """STUB until Track B lands indicators.macd. EMAs as indicators.ema (span, adjust=False)."""
    g = _grp(df, by)
    m = ema(df.close, g, fast) - ema(df.close, g, slow)
    s = ema(m, g, signal)
    return pd.DataFrame({"macd": m, "macd_signal": s, "macd_hist": m - s}, index=df.index)


def vwap(df: pd.DataFrame, by: str | None = None) -> pd.Series:
    """STUB until Track B lands indicators.vwap. Session VWAP of the typical price, reset per
    session_date (and per stock)."""
    keys = [df[by], df.session_date] if by else [df.session_date]
    tp = (df.high + df.low + df.close) / 3
    pv = (tp * df.volume).groupby(keys).cumsum()
    v = df.volume.groupby(keys).cumsum()
    return pv / v.where(v > 0)


def trend(df: pd.DataFrame, ind: pd.DataFrame) -> str:
    """STUB until Track B lands the trend class. Spec section 3 on the last row: UP when
    close > SMA50 > SMA200 and the last two pivot highs and pivot lows are rising; DOWN when
    the mirror holds; else SIDE."""
    if df.empty:
        return "SIDE"
    c, s50, s200 = df.close.iloc[-1], ind.sma50.iloc[-1], ind.sma200.iloc[-1]
    piv = pivots(df.high.to_numpy(dtype=float), df.low.to_numpy(dtype=float))
    hs = [p for p, _, t in piv if t == "H"][-2:]
    ls = [p for p, _, t in piv if t == "L"][-2:]
    if len(hs) < 2 or len(ls) < 2 or np.isnan(s50) or np.isnan(s200):
        return "SIDE"
    if c > s50 > s200 and hs[1] > hs[0] and ls[1] > ls[0]:
        return "UP"
    if c < s50 < s200 and hs[1] < hs[0] and ls[1] < ls[0]:
        return "DOWN"
    return "SIDE"


def nearest(zones: list[dict], x: float) -> tuple[dict | None, dict | None]:
    """STUB until Track B lands levels.nearest. Spec section 3: support below x = the highest
    zone with band top < x; resistance above x = the lowest zone with band bottom > x."""
    sup = [z for z in zones if z["top"] < x]
    res = [z for z in zones if z["bottom"] > x]
    return (max(sup, key=lambda z: z["price"]) if sup else None,
            min(res, key=lambda z: z["price"]) if res else None)


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
