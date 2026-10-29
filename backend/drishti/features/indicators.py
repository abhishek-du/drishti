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


def bollinger(df: pd.DataFrame, by: str | None = None, n: int = 20, k: float = 2.0
              ) -> pd.DataFrame:
    """Bollinger bands: SMA(n) of close +- k population standard deviations (ddof 0)."""
    g, c = _grp(df, by), df.close
    mid = roll(c, g, n, "mean")
    sd = c.groupby(g.to_numpy()).rolling(n, min_periods=n).std(ddof=0) \
        .reset_index(level=0, drop=True).reindex(c.index)
    return pd.DataFrame({"bb_mid": mid, "bb_up": mid + k * sd, "bb_lo": mid - k * sd},
                        index=df.index)


def macd(df: pd.DataFrame, by: str | None = None, fast: int = 12, slow: int = 26,
         signal: int = 9) -> pd.DataFrame:
    """MACD = EMA(fast) - EMA(slow) of close, its EMA(signal), and the histogram (EMAs as
    `ema`: span, adjust=False, min_periods = span)."""
    g = _grp(df, by)
    m = ema(df.close, g, fast) - ema(df.close, g, slow)
    s = ema(m, g, signal)
    return pd.DataFrame({"macd": m, "macd_signal": s, "macd_hist": m - s}, index=df.index)


def vwap(df: pd.DataFrame, by: str | None = None) -> pd.Series:
    """Session VWAP of the typical price (high + low + close) / 3, reset per session_date (and
    per stock). On daily bars it is the bar's typical price; it is meant for intraday bars."""
    keys = [df[by], df.session_date] if by else [df.session_date]
    tp = (df.high + df.low + df.close) / 3
    pv = (tp * df.volume).groupby(keys).cumsum()
    v = df.volume.groupby(keys).cumsum()
    return pv / v.where(v > 0)


def extra(df: pd.DataFrame, by: str | None = None) -> pd.DataFrame:
    """The f1 momentum/volatility columns beyond `basic` (spec section 9): returns, Bollinger
    20/2 position and bandwidth percentile (250 sessions)."""
    g, c = _grp(df, by), df.close
    out = pd.DataFrame(index=df.index)
    for k in (1, 5, 20, 60):
        out[f"ret{k}"] = c / shift(c, g, k) - 1
    bb = bollinger(df, by)
    w = bb.bb_up - bb.bb_lo
    out["bb_pos"] = (c - bb.bb_lo) / w.replace(0, np.nan)
    bw = w / bb.bb_mid
    out["bb_bw_pct"] = bw.groupby(g.to_numpy()).transform(
        lambda s: s.rolling(250, min_periods=60).rank(pct=True))
    return out


def trend_class(close, sma50, sma200, ph1, ph2, pl1, pl2) -> np.ndarray:
    """Spec section 3: UP (1) when close > SMA50 > SMA200 and the last two pivot highs and pivot
    lows are rising (ph1/pl1 = latest known, ph2/pl2 = the one before); DOWN (-1) on the
    mirror; else SIDE (0)."""
    up = (close > sma50) & (sma50 > sma200) & (ph1 > ph2) & (pl1 > pl2)
    dn = (close < sma50) & (sma50 < sma200) & (ph1 < ph2) & (pl1 < pl2)
    return np.where(up, 1, np.where(dn, -1, 0))


def regime(idx: pd.DataFrame) -> pd.DataFrame:
    """Per session: spec_gate (Nifty 500 close > its SMA200, spec section 5 / SC4) and the VIX
    state (SC4: normal < 20; spike >= 25 or 5-session change >= +20 %; else neither).
    idx: index_key, session_date, close (short sessions already dropped; one row per day)."""
    def series(k):
        s = idx[idx.index_key == k].sort_values("session_date")
        return s.drop_duplicates("session_date", keep="last").set_index("session_date").close
    n5, vix = series("NSE_INDEX|Nifty 500"), series("NSE_INDEX|India VIX")
    out = pd.DataFrame({"n500": n5, "n500_sma200": n5.rolling(200, min_periods=200).mean()})
    out["n500_vs_sma200"] = out.n500 / out.n500_sma200 - 1
    out["spec_gate"] = out.n500 > out.n500_sma200
    v = vix.reindex(out.index)
    out["vix"], out["vix_chg5"] = v, v / v.shift(5) - 1
    out["vix_spike"] = (v >= 25) | (out.vix_chg5 >= 0.20)
    out["vix_normal"] = v < 20
    return out


def rs_daily(df: pd.DataFrame, date: str = "session_date") -> pd.DataFrame:
    """RS score per session on the rows given (the eligible universe), its rank percentile and
    RS_top (top 25 %, as Scout: score >= the 0.75 quantile)."""
    sc = df.groupby(date, sort=False, group_keys=False)[["mr6", "mr12"]].apply(
        lambda x: rs_score(x.mr6, x.mr12))
    sc = sc.where(df.mr6.notna() & df.mr12.notna())
    pct = sc.groupby(df[date]).rank(pct=True)
    cut = sc.groupby(df[date]).transform(lambda s: s.quantile(0.75))
    return pd.DataFrame({"rs_score": sc, "rs_pct": pct, "rs_top": sc >= cut}, index=df.index)
