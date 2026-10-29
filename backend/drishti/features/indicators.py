"""Spec section 3 indicators on adjusted daily bars, oldest first. Every value on row i uses
rows <= i of the same stock only. `by` (optional) names the stock column when `df` holds many
stocks (sorted by stock, then date); without it `df` is one stock."""

from __future__ import annotations

import numpy as np
import pandas as pd


def _grp(df: pd.DataFrame, by: str | None):
    return df[by] if by else pd.Series(0, index=df.index)


def shift(x: pd.Series, g: pd.Series, k: int = 1) -> pd.Series:
    return x.groupby(g.to_numpy()).shift(k)


def roll(x: pd.Series, g: pd.Series, n: int, how: str) -> pd.Series:
    r = x.groupby(g.to_numpy()).rolling(n, min_periods=n)
    return getattr(r, how)().reset_index(level=0, drop=True).reindex(x.index)


def ema(x: pd.Series, g: pd.Series, n: int) -> pd.Series:
    return x.groupby(g.to_numpy()).transform(
        lambda s: s.ewm(span=n, adjust=False, min_periods=n).mean())


def wilder(x: pd.Series, g: pd.Series, n: int) -> pd.Series:
    """Wilder smoothing seeded with the mean of each stock's first n non-missing values."""
    v, gg = x.to_numpy(dtype=float), g.to_numpy()
    out = np.full(len(v), np.nan)
    start = np.r_[0, np.flatnonzero(gg[1:] != gg[:-1]) + 1, len(v)]
    for a, b in zip(start[:-1], start[1:]):
        ok = a + np.flatnonzero(~np.isnan(v[a:b]))
        if len(ok) < n:
            continue
        i0 = ok[0] + n - 1
        prev = v[ok[0]:i0 + 1].mean()
        out[i0] = prev
        for i in range(i0 + 1, b):
            prev = (prev * (n - 1) + v[i]) / n
            out[i] = prev
    return pd.Series(out, index=x.index)


def atr(df: pd.DataFrame, by: str | None = None, n: int = 14) -> pd.Series:
    g = _grp(df, by)
    pc = shift(df.close, g)
    tr = pd.concat([df.high - df.low, (df.high - pc).abs(), (df.low - pc).abs()], axis=1).max(
        axis=1)                                     # first bar of a stock: high - low
    return wilder(tr, g, n)


def rsi(df: pd.DataFrame, by: str | None = None, n: int = 14) -> pd.Series:
    g = _grp(df, by)
    d = df.close - shift(df.close, g)
    up, dn = wilder(d.clip(lower=0), g, n), wilder((-d).clip(lower=0), g, n)
    r = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    return r.mask((dn == 0) & (up > 0), 100.0)


def basic(df: pd.DataFrame, by: str | None = None) -> pd.DataFrame:
    """The columns Scout and the chart need."""
    g, c = _grp(df, by), df.close
    out = pd.DataFrame(index=df.index)
    out["sma50"], out["sma200"] = roll(c, g, 50, "mean"), roll(c, g, 200, "mean")
    out["ema20"] = ema(c, g, 20)
    out["atr"] = atr(df, by)
    out["atr_pct"] = out.atr / c
    out["rsi"] = rsi(df, by)
    out["hi52"] = roll(df.high, g, 250, "max")
    out["volr50"] = df.volume / roll(df.volume, g, 50, "mean")
    out["volr20"] = df.volume / roll(df.volume, g, 20, "mean")
    out["sma200_slope20"] = out.sma200 / shift(out.sma200, g, 20) - 1
    sig = roll(np.log(c) - np.log(shift(c, g)), g, 250, "std") * np.sqrt(250)
    for L, name in ((125, "mr6"), (250, "mr12")):
        out[name] = (c / shift(c, g, L) - 1) / sig
    out["ret63"] = c / shift(c, g, 63) - 1
    out["tv20"] = roll(c * df.volume, g, 20, "mean")
    return out


def rs_score(mr6: pd.Series, mr12: pd.Series) -> pd.Series:
    """Nifty200 Momentum 30 method across one day's universe (spec section 3)."""
    z = lambda s: (s - s.mean()) / s.std(ddof=0)  # noqa: E731
    zz = 0.5 * z(mr6) + 0.5 * z(mr12)
    return pd.Series(np.where(zz >= 0, 1 + zz, 1 / (1 - zz)), index=zz.index)
