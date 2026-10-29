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
    piv = sorted(pivots(h, lo, w))
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
