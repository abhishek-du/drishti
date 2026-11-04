"""Live Radar (radar-v1, user 2026-11-04). PAPER ONLY. LABEL everywhere:
"Intraday radar - decision support, not validated edge".

Every 15 min in market hours (inside scan_intraday) Scout's selection criteria (scout-b-v1
PARAMS, unchanged: trend, RS top quantile, beats Nifty 500 over 3 months, near the 52-week high,
ATR %, price band) are re-run on the liquid / F&O universe, on a PARTIAL daily bar of today
built only from data up to the last CLOSED 15m bar:
  open/high/low/volume = the day's OHLC at the recorder's first tick of the key (seen before the
  bar closed) merged with the recorder's closed 1m bars that end by the bar's close;
  close = the close of the last 1m bar inside the closed 15m bar.
History = the adjusted official 1d bars up to T-1 (bars.official, as of the morning).
Eligibility (EQ series, >= 200 sessions, 20-session traded value, close >= 50, not ASM/GSM) is
fixed for the day from T-1: the top 500 by traded value plus every eligible F&O stock.
The RS cut is the 0.75 quantile over the top-500 eligible set (Scout's spec universe), applied
to the F&O extras too. Regime and price bands are T-1's (the intraday index level is not used).

A stock that passes all criteria now, is not on the latest EOD watchlist and is not yet on
today's radar gets one ADDED row (radar_event) with the criteria that turned true since T-1;
scan_intraday then watches it for setups. The EOD watchlist and Scout are not changed.

Caveat (DEVIATIONS D5): the setup scan on radar names uses unadjusted 15m bars until the 15m
path is adjusted; do not rely on intraday setups for stocks with a split/bonus in ~25 days.
No REST call: the live data is the recorder's WebSocket feed.
"""

from __future__ import annotations

import datetime as _dt
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

from drishti.core.clock import IST, now
from drishti.core.config import BACKEND, get_settings
from drishti.data import bars, master
from drishti.data.holidays import SHORT_SESSIONS
from drishti.features import indicators
from drishti.scout import screener_b as sb

VERSION = "radar-v1"
LABEL = "Intraday radar - decision support, not validated edge"
CAVEAT = ("Intraday setups on radar names use unadjusted 15m bars (DEVIATIONS D5): unreliable "
          "for stocks with a split or bonus in the last ~25 days, until the 15m path is adjusted.")
CRITERIA = ("band_ok", "trend_up", "rs_top", "beats_nifty500_3m", "near_52w_high", "atr_pct_ok")
TOP_N = 500
LIVE_DIR = Path("/dev/shm/drishti_live")
DAY0 = LIVE_DIR / "day0.json"


def _cache(day: _dt.date, name: str) -> Path:
    return BACKEND / "var" / "run" / f"radar_{name}_{day}.parquet"


# ---- universe + history (once a day, cached) -------------------------------------------

async def _previous_session(s, d: _dt.date) -> _dt.date:
    from drishti.run.p1 import previous_session
    return await previous_session(s, d)


def eligible_frame(hist: pd.DataFrame, prev: _dt.date, eq: set[str], blocked: set[str],
                   fo: set[str]) -> pd.DataFrame:
    """Per key at T-1: n, tv20, close, and whether it is in the top-500 spec universe or an
    eligible F&O extra."""
    g = hist.groupby("instrument_key", sort=False)
    last = g.tail(1).set_index("instrument_key")
    tv = (hist.close * hist.volume).groupby(hist.instrument_key).apply(
        lambda x: x.iloc[-20:].mean())
    df = pd.DataFrame({"n": g.size(), "tv20": tv, "close": last.close,
                       "date": last.session_date})
    isin = df.index.str.split("|").str[1]
    ok = (df.date == prev) & (df.n >= sb.PARAMS["min_sessions"]) \
        & (df.tv20 >= sb.PARAMS["min_tv20_inr"]) & (df.close >= sb.PARAMS["min_close"]) \
        & isin.isin(eq) & ~isin.isin(blocked)
    el = df[ok].sort_values("tv20", ascending=False)
    el["main"] = np.arange(len(el)) < TOP_N
    el["fo"] = el.index.isin(list(fo))
    return el[el.main | el.fo]


async def universe(s, ps, day: _dt.date) -> tuple[list[str], set[str]]:
    """(keys, main keys) of today's radar universe, cached per day."""
    f = _cache(day, "universe")
    if f.exists():
        u = pd.read_parquet(f)
        return list(u.index), set(u.index[u.main])
    from drishti.run.p2 import manual_isins
    prev = await _previous_session(s, day)
    # T-1's official bar normally lands ~08:30; bars.daily falls back to T-1's provisional bar
    hist, _src = await bars.daily(s, ps, prev, 450, as_of=_dt.datetime.combine(day, _dt.time(9, 0), IST))
    eq = await manual_isins(s, ("EQUITY_L",), day, "EQ")
    blocked = await manual_isins(s, ("ASM", "GSM"), day)
    fo = {k for k, a in (await master.state(s)).items() if a.get("fo")}
    u = eligible_frame(hist, prev, eq, blocked, fo)
    f.parent.mkdir(parents=True, exist_ok=True)
    u.to_parquet(f)
    hist[hist.instrument_key.isin(u.index)].to_parquet(_cache(day, "hist"), index=False)
    return list(u.index), set(u.index[u.main])


def history(day: _dt.date) -> pd.DataFrame:
    return pd.read_parquet(_cache(day, "hist"))


# ---- today's partial bar ---------------------------------------------------------------

def load_day0() -> dict:
    try:
        return json.loads(DAY0.read_text())
    except (OSError, ValueError):
        return {}


def partial_bars(m1: pd.DataFrame, day0: dict, bar_end: pd.Timestamp, day: _dt.date
                 ) -> pd.DataFrame:
    """Today's daily bar per key from data known at `bar_end` (the close of the last closed 15m
    bar): 1m bars (t = minute start, UTC) with t + 1 min <= bar_end, merged with the day OHLC of
    the first tick when that tick was seen by bar_end."""
    m = m1[m1.t + pd.Timedelta(minutes=1) <= bar_end].sort_values(["instrument_key", "t"])
    if m.empty:
        return pd.DataFrame()
    g = m.groupby("instrument_key", sort=False)
    out = pd.DataFrame({"open": g.open.first(), "high": g.high.max(), "low": g.low.min(),
                        "close": g.close.last(), "volume": g.volume.sum().astype(float)})
    cutoff = bar_end.timestamp()
    for k in out.index:
        d = day0.get(k)
        if d and d.get("date") == str(day) and d["seen_at"] <= cutoff:
            out.loc[k, "open"] = d["open"]
            out.loc[k, "high"] = max(out.loc[k, "high"], d["high"])
            out.loc[k, "low"] = min(out.loc[k, "low"], d["low"])
            out.loc[k, "volume"] += float(d.get("volume") or 0)
    out["session_date"] = day
    return out.rename_axis("instrument_key").reset_index()


# ---- criteria (Scout's, unchanged thresholds) ------------------------------------------

def criteria(st: pd.DataFrame, main: set[str], reg: dict, bands: dict) -> pd.DataFrame:
    """Booleans per key per criterion on the last bar of `st` (sb.per_stock output), in Scout's
    terms; the RS cut is taken over `main` (the spec universe)."""
    P = sb.PARAMS
    st = st.assign(rs_score=indicators.rs_score(st.mr6, st.mr12))
    cut = st.loc[st.index.isin(list(main)), "rs_score"].quantile(P["rs_top_quantile"])
    lo, hi = P["atr_pct"]
    band = st.index.map(lambda k: bands.get(k))
    c = pd.DataFrame(index=st.index)
    c["band_ok"] = ~band.isin(P["exclude_price_band_pct"])
    c["trend_up"] = (st.close > st.sma50) & (st.sma50 > st.sma200) & (st.sma200_slope20 > 0)
    c["rs_top"] = st.rs_score >= cut
    c["beats_nifty500_3m"] = (st.ret63 > reg["nifty500_ret63"]) \
        if P["rs_3m_beats_nifty500"] and "nifty500_ret63" in reg else True
    c["near_52w_high"] = st.close >= P["min_close_vs_52w_high"] * st.hi52
    c["atr_pct_ok"] = (st.atr_pct >= lo) & (st.atr_pct <= hi)
    c = c.fillna(False).astype(bool)
    c["all"] = c[list(CRITERIA)].all(axis=1)
    c["rs_score"], c["rs_cut"] = st.rs_score, cut
    return c


def _metrics(r: pd.Series, c: pd.Series) -> dict:
    keys = ("close", "sma50", "sma200", "hi52", "atr_pct", "ret63", "volume")
    out = {k: round(float(r[k]), 4) for k in keys if k in r and pd.notna(r[k])}
    out.update(rs_score=round(float(c.rs_score), 4), rs_cut=round(float(c.rs_cut), 4),
               dist_52w_high_pct=round(100 * (1 - r.close / r.hi52), 3) if r.hi52 else None)
    return out


# ---- the 15-min step -------------------------------------------------------------------

def wait_for_flush(day: _dt.date, bar_end: pd.Timestamp, timeout: float = 330.0) -> bool:
    """The recorder writes closed 1m bars to Parquet every 5 min; wait (<= timeout) for a file
    written after bar_end, so every minute of the closed 15m bar is on disk."""
    d = get_settings().DRISHTI_DATA_DIR / "bars_1m" / str(day)
    t0 = time.time()
    while True:
        files = sorted(d.glob("bars_*.parquet")) if d.exists() else []
        if files and files[-1].stat().st_mtime >= bar_end.timestamp() + 1:
            return True
        if time.time() - t0 > timeout:
            return False
        time.sleep(5)


async def regime_and_bands(s, prev: _dt.date) -> tuple[dict, dict]:
    from drishti.data import eod_quotes
    idx = pd.DataFrame((await s.execute(text("""select index_key, session_date, close
        from index_bar where session_date <= :d order by fetched_at"""), {"d": prev})).all(),
        columns=["index_key", "session_date", "close"])
    idx = idx[~idx.session_date.isin(SHORT_SESSIONS)].assign(close=lambda x: x.close.astype(float))
    reg = sb.regime(idx, prev)
    q = (await s.execute(text("""
        with r as (select r.run_id from run r join run_end e using (run_id)
                   where r.kind = 'P1_EOD' and r.session_date = :d and e.status = 'OK'
                   order by r.started_at limit 1)
        select instrument_key, upper_circuit, lower_circuit, last_price, net_change
        from eod_quote where run_id = (select run_id from r)"""), {"d": prev})).all()
    fo = {k for k, a in (await master.state(s)).items() if a.get("fo")}
    bands = {k: None if k in fo else eod_quotes.band_pct(u, lo, lp, nc) for k, u, lo, lp, nc in q}
    return reg, bands


async def step(s, ps, rid, day: _dt.date, bar_start: pd.Timestamp, m1: pd.DataFrame,
               on_watchlist: set[str]) -> dict:
    """One radar pass for the closed 15m bar starting at `bar_start`. Returns counts and the new
    rows."""
    bar_end = bar_start + pd.Timedelta(minutes=15)
    keys, main = await universe(s, ps, day)
    hist = history(day)
    prev = hist.session_date.max()
    part = partial_bars(m1[m1.instrument_key.isin(keys)], load_day0(), bar_end, day)
    if part.empty:
        return {"universe": len(keys), "with_live_bar": 0, "added": 0, "rows": []}
    reg, bands = await regime_and_bands(s, prev)
    cols = ["instrument_key", "session_date", "open", "high", "low", "close", "volume"]
    h = hist[cols]
    before = sb.per_stock(h.sort_values(["instrument_key", "session_date"]))
    nowb = sb.per_stock(pd.concat([h[h.instrument_key.isin(part.instrument_key)], part[cols]])
                        .sort_values(["instrument_key", "session_date"]))
    nowb = nowb[nowb.session_date == day]
    c1, c0 = criteria(nowb, main, reg, bands), criteria(before, main, reg, bands)
    already = set((await s.execute(text("""select instrument_key from radar_event
        where session_date = :d"""), {"d": day})).scalars())
    new = c1.index[c1["all"] & ~c1.index.isin(list(on_watchlist | already))]
    syms = {k: a.get("trading_symbol") for k, a in (await master.state(s)).items()}
    rows = []
    for k in new:
        was = c0.loc[k] if k in c0.index else pd.Series(False, index=c1.columns)
        crit = {n: {"t1": bool(was[n]), "now": bool(c1.loc[k, n])} for n in CRITERIA}
        reason = [n for n in CRITERIA if not crit[n]["t1"]] or ["met_at_T-1_not_on_watchlist"]
        rows.append({"k": k, "sym": syms.get(k), "reason": reason, "crit": crit,
                     "m": _metrics(nowb.loc[k], c1.loc[k])})
        await s.execute(text("""insert into radar_event(run_id, session_date, bar_time,
            instrument_key, symbol, version, event, reason, criteria, metrics) values (:r, :d,
            :bt, :k, :sym, :v, 'ADDED', cast(:re as jsonb), cast(:c as jsonb), cast(:m as jsonb))
            on conflict (session_date, instrument_key) do nothing"""),
            {"r": rid, "d": day, "bt": bar_start.to_pydatetime(), "k": k, "sym": syms.get(k),
             "v": VERSION, "re": json.dumps(reason), "c": json.dumps(crit),
             "m": json.dumps(rows[-1]["m"])})
    await s.commit()
    return {"universe": len(keys), "main": len(main), "with_live_bar": len(part),
            "passing_now": int(c1["all"].sum()), "added": len(rows),
            "rows": [{"key": r["k"], "symbol": r["sym"], "reason": r["reason"]} for r in rows]}


async def todays_keys(s, day: _dt.date) -> list[str]:
    return list((await s.execute(text("""select instrument_key from radar_event
        where session_date = :d order by bar_time, id"""), {"d": day})).scalars())
