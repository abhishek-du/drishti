"""Feature set f1 (spec drishti-swing-v1 section 9) and the section 6 order outcomes, one row per
stock-day, point in time: every feature on T uses bars <= T of that stock, plus same-day
cross-sections (RS, breadth, sector strength) of the universe on T.

Two steps:
- `stock_rows`: everything that needs only one stock's bars (indicators, candles, pivots,
  zones, levels, the breakout rule row without RS, and the order outcomes, which read bars > T
  for the fill and the exit only);
- `cross_section`: the universe of spec section 2, RS, regime, results gate proxy, breadth and
  sector strength, then the S1a/S1b/S2/S3 masks (`drishti.strategies.rules`).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from drishti.features import candles, indicators, levels
from drishti.strategies import orders

SEED = 20261028
MIN_PRIOR = 200                 # spec section 2: >= 200 sessions with bars before d
MIN_TV20 = 20e7                 # ₹20 crore
MIN_CLOSE = 50.0
TOP_N = 500
SHAPES = ("B", "Uw", "Lw", "Rg")
VARIANTS = ("o", "p", "c")      # o = frozen buy-stop (sets a/b), p = placebo, c = close-confirm
ORDER_COLS = ("status", "entry", "stop", "tp", "trigger", "exit", "fill_date", "exit_date",
              "net_r_5", "net_r_10", "net_r_20", "net_inr_5", "net_inr_10", "net_inr_20",
              "qty")


def stock_rows(df: pd.DataFrame, spike: dict, seed_i: int, cutoff) -> pd.DataFrame:
    """f1 single-stock columns and order outcomes for one stock's bars (sorted by date). Rows
    returned: >= 200 prior bars, tv20 >= ₹20 cr, close >= ₹50, T <= cutoff."""
    df = df.reset_index(drop=True)
    n = len(df)
    if n <= MIN_PRIOR:
        return pd.DataFrame()
    ind = pd.concat([indicators.basic(df), indicators.extra(df)], axis=1)
    o, h, lo, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    atr = ind.atr.to_numpy()
    ph, pl = levels.pivot_flags(h, lo)
    zs_all: list = [None] * n
    sup = np.zeros(n, bool)
    for t in range(MIN_PRIOR - 5, n):
        if np.isfinite(atr[t]) and atr[t] > 0:
            zs_all[t] = levels.zones_at(h, lo, ph, pl, t, float(atr[t]))
            sup[t] = levels.at_support(zs_all[t], lo[t], float(atr[t])) is not None
    bars = df.assign(atr=ind.atr)
    fl = candles.flags(bars, at_support=pd.Series(sup, index=df.index))
    bull = candles.bullish_candle(bars, fl)
    col: dict[str, np.ndarray] = {}
    for k in range(3):
        sh = fl.shift(k).fillna(False).astype(bool)
        col.update({f"{x}_{k}": sh[x].to_numpy() for x in candles.FLAGS})
    col["bullish_candle"] = bull.to_numpy()
    body_top, body_bot = np.maximum(o, c), np.minimum(o, c)
    shp = {"B": np.abs(c - o), "Uw": h - body_top, "Lw": body_bot - lo, "Rg": h - lo}
    for name, v in shp.items():
        s = pd.Series(v / atr)
        for k in range(5):
            col[f"{name}_atr_{k}"] = s.shift(k).to_numpy()
    ph1, ph2, iph = levels.last_two_pivots(h, ph)
    pl1, pl2, ipl = levels.last_two_pivots(lo, pl)
    col["trend"] = indicators.trend_class(c, ind.sma50.to_numpy(), ind.sma200.to_numpy(),
                                          ph1, ph2, pl1, pl2)
    up_swing = ipl < iph
    depth = np.where(up_swing & (ph1 > pl1), (ph1 - c) / (ph1 - pl1), np.nan)
    col["fib_depth"] = depth
    col["fib_zone"] = (depth >= 0.382) & (depth <= 0.618)
    col["close_sma50"], col["close_sma200"] = c / ind.sma50 - 1, c / ind.sma200 - 1
    col["sma50_sma200"] = ind.sma50 / ind.sma200 - 1
    col["ema20_dist_atr"] = (c - ind.ema20) / ind.atr
    col["dist_52w"] = c / ind.hi52 - 1
    col["n_prior"] = np.arange(n)
    out = pd.concat([df[["instrument_key", "session_date", "open", "high", "low", "close",
                         "volume"]], ind, pd.DataFrame(col, index=df.index)], axis=1)
    dates = df.session_date.to_numpy()
    cand = (out.n_prior >= MIN_PRIOR) & (ind.tv20 >= MIN_TV20) & (df.close >= MIN_CLOSE) \
        & (df.session_date <= cutoff) & np.isfinite(atr)
    rows = np.flatnonzero(cand.to_numpy())
    u = np.random.default_rng([SEED, seed_i]).random(n)
    rec: dict[str, list] = {k: [] for k in (
        "dist_res_atr", "res_touches", "dist_sup_atr", "sup_touches", "at_support",
        "brk_local", "brk_top", "t1_date", "t5_date")}
    for v in VARIANTS:
        for k in ORDER_COLS:
            rec[f"{v}_{k}"] = []
    for t in rows:
        zs, a = zs_all[t], float(atr[t])
        r = levels.resistance_above(zs, c[t])
        s = levels.support_below(zs, c[t])
        rec["dist_res_atr"].append((r["bottom"] - c[t]) / a if r else np.nan)
        rec["res_touches"].append(r["touches"] if r else 0)
        rec["dist_sup_atr"].append((c[t] - s["top"]) / a if s else np.nan)
        rec["sup_touches"].append(s["touches"] if s else 0)
        rec["at_support"].append(bool(sup[t]))
        # S1a rule row without RS_top (spec section 5; user 2026-10-29: S1b's day B needs this
        # row + RS_top, not the common gates): the nearest resistance above the previous close
        # (>= 2 touches by construction) is broken on the close
        rp = levels.resistance_above(zs, c[t - 1])
        brk = (rp is not None and c[t] > rp["top"] and c[t - 1] <= rp["top"]
               and out.trend.iat[t] == 1 and c[t] >= 0.85 * ind.hi52.iat[t]
               and ind.volr50.iat[t] >= 1.5)
        rec["brk_local"].append(bool(brk))
        rec["brk_top"].append(rp["top"] if brk else np.nan)
        rec["t1_date"].append(dates[t + 1] if t + 1 < n else None)
        rec["t5_date"].append(dates[t + 5] if t + 5 < n else None)
        sp = bool(spike.get(dates[t], False))
        for v, res in (("o", orders.spec_order(o, h, lo, c, t, a, zs)),
                       ("p", orders.placebo_order(o, h, lo, c, t, a, zs, u[t])),
                       ("c", orders.close_confirm_order(o, h, lo, c, t, a, zs))):
            if res["status"] in orders.CLOSED:
                res.update(orders.net(res["entry"], res["exit"], res["stop"],
                                      orders.qty(res["entry"], res["stop"], sp)))
            res["fill_date"] = dates[res["fill_i"]] if "fill_i" in res else None
            res["exit_date"] = dates[res["exit_i"]] if "exit_i" in res else None
            for k in ORDER_COLS:
                rec[f"{v}_{k}"].append(res.get(k, np.nan))
    sel = out.iloc[rows].reset_index(drop=True)
    return pd.concat([sel, pd.DataFrame(rec)], axis=1)


def _stock_job(args):
    return stock_rows(*args)


def single_stock(bars: pd.DataFrame, spike: dict, cutoff, jobs: int = 1) -> pd.DataFrame:
    """`stock_rows` for every stock of `bars` (sorted by stock, then date)."""
    groups = [(g, spike, i, cutoff) for i, (_, g) in
              enumerate(bars.groupby("instrument_key", sort=True))]
    if jobs > 1:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(jobs) as ex:
            parts = list(ex.map(_stock_job, groups, chunksize=8))
    else:
        parts = [_stock_job(a) for a in groups]
    parts = [p for p in parts if len(p)]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def cross_section(rows: pd.DataFrame, reg: pd.DataFrame, eq_isins: set[str],
                  results: pd.DataFrame, sectors: dict[str, str], calendar: list) -> pd.DataFrame:
    """Universe (spec section 2), RS, regime, results gate proxy, context, breakout state and
    the signal masks. results: instrument_key, result_date. calendar: the session dates."""
    from drishti.strategies import rules
    df = rows.copy()
    isin = df.instrument_key.str.split("|").str[1]
    df["eq_series"] = isin.isin(eq_isins)
    rk = df.tv20.where(df.eq_series).groupby(df.session_date).rank(ascending=False, method="first")
    df["eligible"] = df.eq_series & (rk <= TOP_N)
    df = df.join(indicators.rs_daily(df[df.eligible]))
    df["rs_top"] = df.rs_top.astype("boolean").fillna(False).astype(bool)
    r = reg[["n500_vs_sma200", "spec_gate", "vix", "vix_chg5", "vix_spike", "vix_normal"]]
    df = df.join(r, on="session_date")
    df["spec_gate"] = df.spec_gate.astype("boolean").fillna(False).astype(bool)
    df["vix_spike"] = df.vix_spike.astype("boolean").fillna(False).astype(bool)
    # results gate proxy (user 2026-10-29): a results date in sessions T+1..T+6 blocks
    pos = {d: i for i, d in enumerate(calendar)}
    cal = np.array(calendar, dtype="datetime64[D]")
    df["_p"] = df.session_date.map(pos)
    res = results.copy()
    res["_q"] = np.searchsorted(cal, np.array(res.result_date, dtype="datetime64[D]"))
    res = res.sort_values("_q")
    df = df.sort_values("_p")
    nx = pd.merge_asof(df[["_p", "instrument_key"]].reset_index(), res[["instrument_key", "_q"]],
                       left_on="_p", right_on="_q", by="instrument_key", direction="forward",
                       allow_exact_matches=False).set_index("index")._q
    df["days_to_results"] = nx.reindex(df.index) - df._p
    df["results_block"] = df.days_to_results <= 6
    df = df.sort_values(["instrument_key", "session_date"]).drop(columns="_p")
    # context on the eligible universe of T
    e = df.eligible
    df["breadth"] = (df.close > df.sma200).where(e).groupby(df.session_date).transform("mean")
    df["sector"] = df.instrument_key.map(sectors)
    med = df.ret20.where(e).groupby(df.session_date).transform("median")
    smed = df.ret20.where(e).groupby([df.session_date, df.sector]).transform("median")
    df["sector_strength"] = smed - med
    df["dow"] = pd.to_datetime(df.session_date).dt.day_of_week
    df["news_n72"], df["news_sent"] = np.nan, np.nan    # not loaded (allowed missing, spec 9)
    df = rules.signals(df)
    return df.reset_index(drop=True)
