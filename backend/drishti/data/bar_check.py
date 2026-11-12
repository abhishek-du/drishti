"""Provisional vs official daily bar of T (spec section 1, user check 2026-10-29: daily for the
first 5 sessions).

- provisional: Drishti's EOD quote snapshot (eod_quote, the newest OK P1_EOD run of T: 16:10
  after NSE's official close from 2026-11-12, else 15:40; only
  quotes whose last trade is on T);
- official: Upstox historical 1d of T as Prajna stores it (ohlcv_bar, read only, latest fetch).

A stock is REVISED when any of O/H/L/C differs by more than 0.1 %. For each revised stock the
report says whether its provisional bar sat within 0.5 ATR of a rule boundary of
S1a/S1b/S2/S3, or of its own trigger/stop, i.e. whether the revision could flip a signal or move
an order level.

The levels here are an ops approximation written before P3 (spec section 3 definitions, on
unadjusted bars; a name with a > 35 % one-day move in the window is left out as a likely
unadjusted split). They are not the frozen signal code and never feed a signal.
"""

from __future__ import annotations

import datetime as _dt

import numpy as np
import pandas as pd
from sqlalchemy import text

from drishti.data.holidays import SHORT_SESSIONS
from drishti.features.levels import wilder_atr, zones

TOL = 0.001                     # spec section 1: > 0.1 % in any OHLC field = BAR_REVISED
NEAR_ATR = 0.5                  # "near a boundary" = within 0.5 ATR
SPLIT_JUMP = 0.35
FIELDS = ("open", "high", "low", "close")


async def provisional(s, day: _dt.date) -> pd.DataFrame:
    rows = (await s.execute(text("""
        with r as (select r.run_id from run r join run_end e using (run_id)
                   where r.kind = 'P1_EOD' and r.session_date = :d and e.status = 'OK'
                   order by r.started_at desc limit 1)
        select instrument_key, open, high, low, close, last_price, volume from eod_quote
        where run_id = (select run_id from r) and session_date = :d
          and (quote_ts at time zone 'Asia/Kolkata')::date = :d"""), {"d": day})).mappings().all()
    return pd.DataFrame([dict(r) for r in rows]).set_index("instrument_key").astype(float) \
        if rows else pd.DataFrame()


async def official_history(ps, day: _dt.date, keys: list[str], days: int = 420) -> pd.DataFrame:
    """Prajna 1d bars of `keys` for (day - days, day], latest fetch per (key, date); short
    sessions (SC1) dropped."""
    rows = (await ps.execute(text("""
        select distinct on (instrument_key, session_date) instrument_key, session_date,
               open, high, low, close, volume
        from ohlcv_bar where timeframe = '1d' and session_date > :a and session_date <= :d
          and instrument_key = any(:k)
        order by instrument_key, session_date, fetched_at desc"""),
        {"a": day - _dt.timedelta(days=days), "d": day, "k": keys})).all()
    df = pd.DataFrame(rows, columns=["instrument_key", "session_date", *FIELDS, "volume"])
    df = df[~df.session_date.isin(SHORT_SESSIONS)]
    for c in (*FIELDS, "volume"):
        df[c] = df[c].astype(float)
    return df


def boundaries(hist: pd.DataFrame, bars: list[dict]) -> list[dict] | None:
    """Signed distances, in ATR, of each bar of T in `bars` (provisional, official) to the rule
    boundaries of S1a/S1b/S2/S3; `hist` = the stock's official bars before T. The levels (ATR,
    52-week high, zones) come from the first bar; EMA20/SMA50/SMA200 include each bar's close."""
    if len(hist) < 220:
        return None
    c = hist.close.to_numpy()
    if np.any(np.abs(np.diff(c) / c[:-1]) > SPLIT_JUMP):
        return None
    atr = wilder_atr(hist.high.to_numpy(), hist.low.to_numpy(), c)
    if not atr > 0:
        return None
    b0 = bars[0]
    h, lo = np.r_[hist.high.to_numpy(), b0["high"]], np.r_[hist.low.to_numpy(), b0["low"]]
    hi52 = h[-250:].max()
    zs = zones(h, lo, atr)
    tops = np.array([z["top"] for z in zs])
    bots = np.array([z["bottom"] for z in zs])
    out = []
    for b in bars:
        cc = np.r_[c, b["close"]]
        ema = pd.Series(cc).ewm(span=20, adjust=False).mean().iloc[-1]
        x, low = b["close"], b["low"]
        d = {"S1a_52w": x - 0.85 * hi52, "S2_ema_low": low - ema * 1.005,
             "S2_ema_close": x - ema * 0.99, "trend_sma50": x - cc[-50:].mean(),
             "S3_sma200": x - cc[-200:].mean()}
        if zs:
            i = int(np.abs(b0["close"] - tops).argmin())
            j = int(np.abs(b0["low"] - tops).argmin())
            d["S1_res_top"] = x - tops[i]
            d["S1b_retest"] = low - (tops[j] + 0.25 * atr)
            d["S3_sup_band_top"] = low - tops[j]
            k = int(np.abs(b0["low"] - (bots - 0.25 * atr)).argmin())
            d["S3_sup_band_floor"] = low - (bots[k] - 0.25 * atr)
        out.append({"atr": atr, **{n: float(v / atr) for n, v in d.items()}})
    return out


def compare(prov: pd.DataFrame, off: pd.DataFrame, hist: pd.DataFrame, day: _dt.date,
            eligible: set[str]) -> dict:
    both = prov.index.intersection(off.index)
    rel = (prov.loc[both, list(FIELDS)] - off.loc[both, list(FIELDS)]).abs() \
        / off.loc[both, list(FIELDS)]
    rev = rel.index[(rel > TOL).any(axis=1)]
    by = dict(tuple(hist[hist.session_date < day].groupby("instrument_key")))
    near, flip, shift, unknown, detail = [], [], [], 0, []
    for k in rev:
        bb = boundaries(by[k], [prov.loc[k].to_dict(), off.loc[k].to_dict()]) if k in by else None
        if bb is None:
            unknown += 1
            continue
        b, o = bb
        atr = b.pop("atr")
        # trigger = high x 1.0005 and stop = low - 0.1 ATR move with the revised high / low
        mv = {"trigger": abs(off.at[k, "high"] - prov.at[k, "high"]) / atr,
              "stop": abs(off.at[k, "low"] - prov.at[k, "low"]) / atr}
        hit = sorted(n for n, v in b.items() if abs(v) <= NEAR_ATR)
        crossed = sorted(n for n, v in b.items() if (v > 0) != (o[n] > 0))
        if hit:
            near.append(k)
        if crossed:
            flip.append(k)
        if max(mv.values()) >= 0.1:
            shift.append(k)
        detail.append({"key": k, "eligible": k in eligible,
                       "max_rel_diff_pct": round(100 * float(rel.loc[k].max()), 3),
                       "field": str(rel.loc[k].idxmax()), "near": hit, "crossed": crossed,
                       "level_move_atr": {n: round(v, 3) for n, v in mv.items()}})
    ev = [k for k in rev if k in eligible]
    return {
        "session": str(day), "provisional": len(prov), "official": len(off),
        "compared": len(both), "only_provisional": len(prov.index.difference(off.index)),
        "only_official": len(off.index.difference(prov.index)),
        "revised": len(rev), "revised_by_field": {f: int((rel[f] > TOL).sum()) for f in FIELDS},
        "close_vs_ltp_revised": int(((prov.loc[both, "last_price"] - off.loc[both, "close"]).abs()
                                     / off.loc[both, "close"] > TOL).sum()),
        "revised_near_boundary": len(near), "revised_crossed_boundary": len(flip),
        "revised_level_move_ge_0.1atr": len(shift),
        "revised_levels_unknown": unknown,
        "eligible_proxy": len(eligible & set(both)), "eligible_revised": len(ev),
        "eligible_revised_near_boundary": len([k for k in near if k in eligible]),
        "eligible_revised_crossed_boundary": len([k for k in flip if k in eligible]),
        "crossed_by_rule": {n: sum(n in r["crossed"] for r in detail)
                            for n in sorted({n for r in detail for n in r["crossed"]})},
        "eligible_revised_level_move_ge_0.1atr": len([k for k in shift if k in eligible]),
        "detail": sorted(detail, key=lambda r: -r["max_rel_diff_pct"]),
    }


def eligible_proxy(hist: pd.DataFrame, off: pd.DataFrame, day: _dt.date) -> set[str]:
    """Spec section 2 without the instrument-class and ASM rules: >= 200 sessions before T,
    20-session mean traded value >= 20 crore, close >= 50, top 500 by that value."""
    h = hist[hist.session_date < day]
    n = h.groupby("instrument_key").size()
    tv = h.assign(tv=h.close * h.volume).groupby("instrument_key").tv.apply(
        lambda s: s.iloc[-20:].mean())
    ok = tv[(n.reindex(tv.index) >= 200) & (tv >= 20e7)]
    ok = ok[ok.index.isin(off.index[off.close >= 50])]
    return set(ok.sort_values(ascending=False).index[:500])
