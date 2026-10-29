"""Scout: the daily swing watchlist (Screener B of second-module.md section 5, thresholds from
the frozen spec where it has them). A watchlist is context for the Chart Reader and the chart
UI; it is not a signal and never gates the model's universe (spec section 2 does that).

Version scout-b-v1. Every threshold is in PARAMS and is stored with each run.
"""

from __future__ import annotations

import datetime as _dt

import numpy as np
import pandas as pd

from drishti.features import candles, indicators

VERSION = "scout-b-v1"
PARAMS = {
    "min_sessions": 200, "min_tv20_inr": 20e7, "min_close": 50.0, "top_by_tv20": 500,
    "trend": "close > SMA50 > SMA200 and SMA200 rising over 20 sessions",
    "rs_top_quantile": 0.75, "rs_3m_beats_nifty500": True,
    "min_close_vs_52w_high": 0.85,
    "atr_pct": [0.02, 0.06],
    "max_listed": 300,
    "regime": "Nifty 500 close > SMA200 and India VIX < 20 (recorded; the list is kept when OFF)",
    "not_applied": ["sector index > 50 DMA (needs a sector-index map: P3)",
                    "delivery % (no source)", "2/5 % price band (no source)"],
}


def per_stock(bars: pd.DataFrame) -> pd.DataFrame:
    """Indicators on the last bar of each stock, plus its candle flags (bars sorted by stock,
    then date)."""
    b = bars.reset_index(drop=True)
    ind = indicators.basic(b, by="instrument_key")
    full = pd.concat([b, ind], axis=1)
    tail = full.groupby("instrument_key", sort=False).tail(8)       # flags need T-4..T
    fl = candles.flags(tail, by="instrument_key")
    last = full.groupby("instrument_key", sort=False).tail(1).index
    out = full.loc[last].set_index("instrument_key")
    out["n"] = b.groupby("instrument_key", sort=False).size()
    names = np.array(candles.FLAGS)
    out["candle_flags"] = [list(names[fl.loc[i].to_numpy()]) for i in last]
    return out


def regime(idx: pd.DataFrame, day: _dt.date) -> dict:
    """idx: index bars (index_key, session_date, close), short sessions already dropped."""
    def series(k):
        s = idx[(idx.index_key == k) & (idx.session_date <= day)].sort_values("session_date")
        return s.drop_duplicates("session_date", keep="last").set_index("session_date").close
    n5, vix = series("NSE_INDEX|Nifty 500"), series("NSE_INDEX|India VIX")
    out: dict = {"nifty500_date": str(n5.index[-1]) if len(n5) else None,
                 "vix_date": str(vix.index[-1]) if len(vix) else None}
    if len(n5) >= 200:
        out.update(nifty500=float(n5.iloc[-1]), nifty500_sma200=float(n5.iloc[-200:].mean()),
                   nifty500_ret63=float(n5.iloc[-1] / n5.iloc[-64] - 1))
    if len(vix) >= 6:
        out.update(vix=float(vix.iloc[-1]), vix_5d_change=float(vix.iloc[-1] / vix.iloc[-6] - 1))
    up = out.get("nifty500", 0) > out.get("nifty500_sma200", np.inf)
    calm = out.get("vix", np.inf) < 20
    spike = out.get("vix", 0) >= 25 or out.get("vix_5d_change", 0) >= 0.20
    out["status"] = "ON" if up and calm else "OFF"
    out["vix_spike"] = bool(spike)
    out["stale"] = out["nifty500_date"] != str(day)
    return out


def screen(st: pd.DataFrame, eq_isins: set[str], blocked_isins: set[str], reg: dict,
           day: _dt.date) -> tuple[pd.DataFrame, dict]:
    """Return (listed rows ranked by RS, funnel counts)."""
    P, f = PARAMS, {}
    st = st[st.session_date == day]
    f["with_bar_on_T"] = len(st)
    isin = st.index.str.split("|").str[1]
    st = st[isin.isin(eq_isins)]
    f["eq_series"] = len(st)
    st = st[(st.n >= P["min_sessions"] + 1) & (st.tv20 >= P["min_tv20_inr"])
            & (st.close >= P["min_close"])]
    st = st.sort_values("tv20", ascending=False).iloc[:P["top_by_tv20"]]
    st = st[~st.index.str.split("|").str[1].isin(blocked_isins)]
    f["eligible"] = len(st)
    st = st.assign(rs_score=indicators.rs_score(st.mr6, st.mr12))
    cut = st.rs_score.quantile(P["rs_top_quantile"])
    st = st[(st.close > st.sma50) & (st.sma50 > st.sma200) & (st.sma200_slope20 > 0)]
    f["trend_up"] = len(st)
    st = st[st.rs_score >= cut]
    f["rs_top"] = len(st)
    if P["rs_3m_beats_nifty500"] and "nifty500_ret63" in reg:
        st = st[st.ret63 > reg["nifty500_ret63"]]
    f["beats_nifty500_3m"] = len(st)
    st = st[st.close >= P["min_close_vs_52w_high"] * st.hi52]
    f["near_52w_high"] = len(st)
    lo, hi = P["atr_pct"]
    st = st[(st.atr_pct >= lo) & (st.atr_pct <= hi)]
    f["atr_pct_ok"] = len(st)
    st = st.sort_values("rs_score", ascending=False).iloc[:P["max_listed"]]
    f["listed"] = len(st)
    return st.assign(rank=np.arange(1, len(st) + 1)), f


def metrics(r: pd.Series) -> dict:
    keys = ("close", "sma50", "sma200", "ema20", "atr", "atr_pct", "rsi", "hi52", "volr50",
            "ret63", "tv20", "mr6", "mr12")
    out = {k: round(float(r[k]), 6) for k in keys if pd.notna(r[k])}
    out["dist_52w_high_pct"] = round(100 * (1 - r.close / r.hi52), 3)
    return out
