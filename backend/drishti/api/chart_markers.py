"""Chart pattern markers (Track C; display only, decision support). NOT a prediction, NOT an
edge, not used by drishti-swing-v1. The actionable items are the scanner alerts (entry / stop /
R1 / R2), not these markers.

Every marker is causal: a marker on bar j uses rows <= j only (the candle flags, EMA20 / SMA50 /
SMA200, ATR and the S/R zones known on bar j via levels.zones_at), so a marker never appears,
moves or disappears when later bars arrive (no repaint). This replaces the old in-browser
BUY/SELL markers, which tested support/resistance against today's zones (look-ahead) and
alternated an always-in BUY/SELL (stop-and-reverse).

  bull  bar i carries a bullish trigger flag (candles.BULLISH), bar j = i + 1 closes above bar
        i's high, and on bar j the close is >= EMA20 * 0.995 or bar j's low is at a support zone
        known on bar j. Label = the flag name(s); stop = bar i's low (the signal bar).
  bear  mirror with BEARISH below, the low, EMA20 * 1.005 and resistance. When bar j is in an
        uptrend (close > SMA50 > SMA200) it is served as kind 'caution' (a faint note, not a
        trade marker). That is a display choice, not a tested rule.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from drishti.features import candles, levels

LABEL = "pattern - not a prediction; not used by drishti-swing-v1"
NOTE = ("Pattern markers only, not trade signals and not a validated edge. The actionable items "
        "are the Scanner alerts (entry / stop / R1 / R2).")
# display grouping of existing flag names (no pattern logic here: the flags come from candles)
BEARISH = ("engulfing_bear", "evening_star", "shooting_star", "dark_cloud", "three_black_crows")


def markers(df: pd.DataFrame, ind: pd.DataFrame, fl: pd.DataFrame) -> list[dict]:
    """`df` bars oldest first (open/high/low/close), `ind` = indicators.basic of the same bars,
    `fl` = candles.flags of the same bars. Returns [{i, kind, flags, stop}] where i is the
    marker bar (the confirming bar) and kind is 'bull' | 'bear' | 'caution'."""
    h, lo, c = (df[x].to_numpy(float) for x in ("high", "low", "close"))
    ema, s50, s200, atr = (ind[x].to_numpy(float) for x in ("ema20", "sma50", "sma200", "atr"))
    ph, pl = levels.pivot_flags(h, lo)
    bull = fl[list(candles.BULLISH)].to_numpy(bool)
    bear = fl[list(BEARISH)].to_numpy(bool)
    out = []

    def zones(j: int) -> list[dict]:
        return levels.zones_at(h, lo, ph, pl, j, float(atr[j])) if atr[j] > 0 else []

    for i in range(len(df) - 1):
        j = i + 1
        if bull[i].any() and c[j] > h[i]:
            ok = not np.isfinite(ema[j]) or c[j] >= ema[j] * 0.995
            if not ok:
                ok = any(lo[j] <= z["top"] * 1.005 and c[j] >= z["bottom"] * 0.995
                         for z in zones(j))
            if ok:
                out.append({"i": j, "kind": "bull", "stop": float(lo[i]),
                            "flags": [f for f, on in zip(candles.BULLISH, bull[i]) if on]})
        elif bear[i].any() and c[j] < lo[i]:
            ok = not np.isfinite(ema[j]) or c[j] <= ema[j] * 1.005
            if not ok:
                ok = any(h[j] >= z["bottom"] * 0.995 and c[j] <= z["top"] * 1.005
                         for z in zones(j))
            if ok:
                up = bool(c[j] > s50[j] > s200[j])     # NaN compares False: not an uptrend
                out.append({"i": j, "kind": "caution" if up else "bear", "stop": None,
                            "flags": [f for f, on in zip(BEARISH, bear[i]) if on]})
    return out
