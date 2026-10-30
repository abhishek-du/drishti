"""Signals S1a/S1b/S2/S3 of spec drishti-swing-v1 section 5 on the f1 panel (one row per
stock-day, sorted by stock then date, columns from `drishti.features.f1`).

Common gates on T (all must hold): eligible; RSI(14) in [40, 75]; regime OK = spec_gate
(Nifty 500 close > SMA200; SC4: OFF blocks every new entry); no results date in T+1..T+6; not
halted by the VIX rule (a spike turns S3 off).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

STRATEGIES = ("S1a", "S1b", "S2", "S3")


def common_gates(df: pd.DataFrame) -> pd.Series:
    return (df.eligible & df.rsi.between(40, 75) & df.spec_gate & ~df.results_block)


def breakout_state(df: pd.DataFrame) -> pd.DataFrame:
    """Per row: the latest S1a rule-row breakout B of that stock within 10 sessions before T
    (k = T - B by the stock's bar count), its band top, and the state 0 none / 1 broke (k <= 1,
    or not retesting) / 2 retesting (S1b's price condition on T with k in 2..10)."""
    s1a_row = df.brk_local & df.rs_top
    g = df.instrument_key
    k = pd.Series(np.nan, index=df.index)
    top = pd.Series(np.nan, index=df.index)
    for lag in range(10, -1, -1):          # the most recent breakout wins (smallest k)
        hit = s1a_row.groupby(g).shift(lag).astype("boolean").fillna(False).astype(bool)
        gap = df.n_prior - df.n_prior.groupby(g).shift(lag)
        ok = hit & (gap <= 10)
        k = k.mask(ok, gap)
        top = top.mask(ok, df.brk_top.groupby(g).shift(lag))
    retest = (k.between(2, 10) & (df.low <= top + 0.25 * df.atr) & (df.close > top))
    state = np.where(retest, 2, np.where(k.notna(), 1, 0))
    return pd.DataFrame({"brk_days": k, "brk_band_top": top, "brk_state": state,
                         "s1a_row": s1a_row, "retest": retest}, index=df.index)


def signals(df: pd.DataFrame) -> pd.DataFrame:
    df = df.join(breakout_state(df))
    gate = common_gates(df)
    up = df.trend == 1
    hammer, engulf, morning = df.hammer_0, df.engulfing_bull_0, df.morning_star_0
    df["gate"] = gate
    df["S1a"] = gate & df.s1a_row
    df["S1b"] = gate & df.retest & df.bullish_candle & up & df.rs_top
    df["S2"] = gate & (df.close > df.sma50) & (df.sma50 > df.sma200) \
        & (df.low <= df.ema20 * 1.005) & (df.close >= df.ema20 * 0.99) & (hammer | engulf)
    df["S3"] = gate & ~df.vix_spike & (df.close > df.sma200) & df.at_support \
        & (hammer | engulf | morning) & (df.volr20 > 1.0)
    for s in STRATEGIES:
        df[s] = df[s].astype(bool)
    df["signal"] = df[list(STRATEGIES)].any(axis=1)
    df["strategy"] = np.select([df[s] for s in STRATEGIES], STRATEGIES, default="none")
    return df
