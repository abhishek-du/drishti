"""Pivots and support/resistance zones (spec section 3)."""

from __future__ import annotations

import numpy as np


def pivots(h: np.ndarray, lo: np.ndarray, w: int = 5, lookback: int = 250
           ) -> list[tuple[float, int, str]]:
    """(price, index, 'H'|'L') of the 5-each-side pivots in the last `lookback` bars. A pivot at
    i is known at i + w, so the last w bars hold none."""
    n, out = len(h), []
    for i in range(max(w, n - lookback), n - w):
        if h[i] == h[i - w:i + w + 1].max():
            out.append((float(h[i]), i, "H"))
        if lo[i] == lo[i - w:i + w + 1].min():
            out.append((float(lo[i]), i, "L"))
    return out


def zones(h: np.ndarray, lo: np.ndarray, atr: float, w: int = 5) -> list[dict]:
    """Pivots clustered greedily within 0.5 ATR; a zone needs >= 2 pivots >= 10 sessions apart;
    price = cluster mean, band = mean +- 0.25 ATR."""
    return cluster(pivots(h, lo, w), atr)


def cluster(piv: list[tuple[float, int, str]], atr: float) -> list[dict]:
    """The zones of `zones` from a list of pivots (ascending price)."""
    piv = sorted(piv)
    out, cl = [], []
    for p in piv + [(np.inf, 0, "")]:
        if cl and p[0] - cl[0][0] > 0.5 * atr:
            idx = [i for _, i, _ in cl]
            if len(cl) >= 2 and max(idx) - min(idx) >= 10:
                m = float(np.mean([x for x, _, _ in cl]))
                out.append({"price": m, "bottom": m - 0.25 * atr, "top": m + 0.25 * atr,
                            "touches": len(cl), "first": min(idx), "last": max(idx)})
            cl = []
        cl.append(p)
    return out


def wilder_atr(h: np.ndarray, lo: np.ndarray, c: np.ndarray, n: int = 14) -> float:
    """ATR(14) of the last bar (numpy; same seeding as indicators.atr)."""
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - lo, np.maximum(abs(h - pc), abs(lo - pc)))
    atr = tr[:n].mean()
    for x in tr[n:]:
        atr = (atr * (n - 1) + x) / n
    return float(atr)


def pivot_flags(h: np.ndarray, lo: np.ndarray, w: int = 5) -> tuple[np.ndarray, np.ndarray]:
    """(is pivot high, is pivot low) for every bar of a whole series, as `pivots` marks them; a
    flag at i is only usable from bar i + w on."""
    n = len(h)
    ph, pl = np.zeros(n, bool), np.zeros(n, bool)
    if n >= 2 * w + 1:
        from numpy.lib.stride_tricks import sliding_window_view as win
        ph[w:n - w] = h[w:n - w] == win(h, 2 * w + 1).max(axis=1)
        pl[w:n - w] = lo[w:n - w] == win(lo, 2 * w + 1).min(axis=1)
    return ph, pl


def zones_at(h: np.ndarray, lo: np.ndarray, ph: np.ndarray, pl: np.ndarray, t: int, atr: float,
             w: int = 5, lookback: int = 250) -> list[dict]:
    """The zones known on bar t (= `zones(h[:t+1], lo[:t+1], atr)`), from `pivot_flags`."""
    a, b = max(w, t + 1 - lookback), t + 1 - w
    if b <= a:
        return []
    i = np.arange(a, b)
    piv = [(float(h[k]), int(k), "H") for k in i[ph[a:b]]] + \
          [(float(lo[k]), int(k), "L") for k in i[pl[a:b]]]
    return cluster(piv, atr)


def resistance_above(zs: list[dict], x: float) -> dict | None:
    """The lowest zone with band bottom > x (spec section 3); `zs` ascending by price."""
    return next((z for z in zs if z["bottom"] > x), None)


def support_below(zs: list[dict], x: float) -> dict | None:
    """The highest zone with band top < x."""
    return next((z for z in reversed(zs) if z["top"] < x), None)


def at_support(zs: list[dict], low: float, atr: float) -> dict | None:
    """The highest zone whose band holds `low`, or that `low` is at most 0.25 ATR below (S3; the
    hammer's "at support")."""
    return next((z for z in reversed(zs) if z["bottom"] - 0.25 * atr <= low <= z["top"]), None)


def nearest(zs: list[dict], x: float) -> tuple[dict | None, dict | None]:
    """(support below x, resistance above x) of spec section 3; `zs` ascending by price."""
    return support_below(zs, x), resistance_above(zs, x)


def last_two_pivots(price: np.ndarray, flag: np.ndarray, w: int = 5
                    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(latest, previous, latest index) of the flagged pivots as known on each bar (a pivot at i
    is known from i + w), over the whole history before the bar."""
    import pandas as pd
    n = len(price)
    p1, p2, i1 = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
    idx = np.flatnonzero(flag)
    prev = np.r_[np.nan, price[idx][:-1]]
    for k, i in enumerate(idx):
        if i + w < n:
            p1[i + w], p2[i + w], i1[i + w] = price[i], prev[k], i
    f = lambda a: pd.Series(a).ffill().to_numpy()  # noqa: E731
    return f(p1), f(p2), f(i1)


def trend(df, ind) -> str:
    """Spec section 3 trend class on the last row of one stock's bars (`ind` = indicators.basic
    of the same bars): 'UP' | 'DOWN' | 'SIDE'. Same rule as the f1 column `trend`."""
    from drishti.features.indicators import trend_class
    if len(df) == 0:
        return "SIDE"
    h, lo = df.high.to_numpy(dtype=float), df.low.to_numpy(dtype=float)
    ph, pl = pivot_flags(h, lo)
    ph1, ph2, _ = last_two_pivots(h, ph)
    pl1, pl2, _ = last_two_pivots(lo, pl)
    t = trend_class(df.close.to_numpy(dtype=float)[-1:], ind.sma50.to_numpy()[-1:],
                    ind.sma200.to_numpy()[-1:], ph1[-1:], ph2[-1:], pl1[-1:], pl2[-1:])[0]
    return {1: "UP", -1: "DOWN", 0: "SIDE"}[int(t)]
