"""Split/bonus-adjusted daily bars of all NSE equities, read only from Prajna, plus Drishti's
provisional bar of T.

Provenance: the adjustment is Prajna's `app/canon/pit.bars_adjusted` (Prajna commit 25712fc),
rewritten in bulk for one as_of (the copy rule: Drishti never imports Prajna code):
  net = product of EXACT factors F (canon_ca_factor) with bar_date < ex_date <= as_of day and
        knowable_at < as_of
      / product of F the vendor already applied to the stored payload
        (VENDOR_ADJUSTED, vendor_applied = APPLIED, bar_date < ex_date <= basis_as_of), except a
        payload the vendor later re-served adjusted by exactly F (CA_ADJUSTMENT observation);
  price / net, volume * net.
Rows with net < 1 (an action not yet knowable at as_of is baked in: RECONSTRUCTED) are dropped,
as Prajna does by default.

NOT applied here: Prajna's LOW-confidence refusal (vendor-adjusted rows older than the
corporate-action horizon, 2025-09-26). For the live Scout and the chart the vendor's own
adjustment is the best basis there is. P3 backtests must decide this explicitly (SURVIVORSHIP-
BIASED, EXPLORATORY already applies).
"""

from __future__ import annotations

import datetime as _dt

import numpy as np
import pandas as pd
from sqlalchemy import text

from drishti.core.clock import IST
from drishti.data.holidays import SHORT_SESSIONS

COLS = ["open", "high", "low", "close", "volume"]


async def official(ps, day: _dt.date, days: int = 450, as_of: _dt.datetime | None = None,
                   keys: list[str] | None = None) -> pd.DataFrame:
    """Adjusted official 1d bars with market_date in (day - days, day], knowable before as_of
    (default: now). Columns: instrument_key, session_date, OHLCV, net."""
    as_of = as_of or _dt.datetime.now(tz=IST)
    rows = (await ps.execute(text("""
        select instrument_key, market_date, open, high, low, close, volume, payload_sha256
        from canon_market_bar
        where timeframe = '1d' and segment = 'NSE_EQ' and knowable_at < :a
          and market_date > :d0 and market_date <= :d
          and (cast(:k as text[]) is null or instrument_key = any(:k))"""),
        {"a": as_of, "d0": day - _dt.timedelta(days=days), "d": day, "k": keys})).all()
    df = pd.DataFrame(rows, columns=["instrument_key", "session_date", *COLS, "payload"])
    if df.empty:
        return df
    df = df[~df.session_date.isin(SHORT_SESSIONS)]
    basis = pd.DataFrame((await ps.execute(text("""select payload_sha256, price_basis, basis_as_of
        from ohlcv_payload_basis where payload_sha256 = any(:h)"""),
        {"h": df.payload.unique().tolist()})).all(), columns=["payload", "pb", "bas"])
    ev = pd.DataFrame((await ps.execute(text("""select ca_id, instrument_key, ex_date,
        factor_price, knowable_at, vendor_applied from canon_ca_factor
        where status = 'EXACT' and ex_date is not null"""))).all(),
        columns=["ca_id", "instrument_key", "ex_date", "f", "kn", "va"])
    # only stocks with an EXACT factor can need it: the query stays a small index lookup
    # (unfiltered it was a parallel hash join over ohlcv_bar on every chart request, which
    # filled the container's 64 MB /dev/shm - "could not resize shared memory", 2026-11-02)
    ca_keys = sorted(set(ev.instrument_key) & set(df.instrument_key.unique()))
    raw_for = {(p, int(c)) for p, c in (await ps.execute(text("""
        select distinct b.payload_sha256, jsonb_array_elements_text(o.explained_by -> 'ca_ids')
        from ohlcv_observation o
        join ohlcv_bar b on b.instrument_id = o.instrument_id and b.timeframe = o.timeframe
         and b.bar_start_utc = o.bar_start_utc and o.fetched_at > b.fetched_at
        where o.timeframe = '1d' and o.classification = 'CA_ADJUSTMENT'
          and o.instrument_key = any(:k)"""), {"k": ca_keys})).all()} if ca_keys else set()
    df = df.merge(basis, on="payload", how="left")
    for c in COLS:
        df[c] = df[c].astype(float)
    df["net"] = 1.0
    as_day = as_of.astimezone(IST).date()
    for key, e in ev[ev.instrument_key.isin(df.instrument_key.unique())].groupby("instrument_key"):
        m = df.instrument_key == key
        sub = df.loc[m]
        net = np.ones(len(sub))
        for r in e.itertuples():
            before = (sub.session_date < r.ex_date).to_numpy()
            if r.ex_date <= as_day and r.kn < as_of:
                net[before] *= float(r.f)
            if r.va == "APPLIED":
                baked = before & (sub.pb == "VENDOR_ADJUSTED").to_numpy() \
                    & (sub.bas.to_numpy() >= r.ex_date) \
                    & ~sub.payload.map(lambda p: (p, r.ca_id) in raw_for).to_numpy()
                net[baked] /= float(r.f)
        df.loc[m, "net"] = net
    df = df[df.net >= 1 - 1e-9].copy()
    for c in ("open", "high", "low", "close"):
        df[c] = df[c] / df.net
    df["volume"] = df.volume * df.net
    return df.drop(columns=["payload", "pb", "bas"]).sort_values(
        ["instrument_key", "session_date"]).reset_index(drop=True)


async def provisional(s, day: _dt.date) -> pd.DataFrame:
    """Drishti's provisional bars of `day` (first OK EOD run; quotes that traded on `day`)."""
    rows = (await s.execute(text("""
        with r as (select r.run_id from run r join run_end e using (run_id)
                   where r.kind = 'P1_EOD' and r.session_date = :d and e.status = 'OK'
                   order by r.started_at limit 1)
        select instrument_key, open, high, low, close, volume from eod_quote
        where run_id = (select run_id from r)
          and (quote_ts at time zone 'Asia/Kolkata')::date = :d"""), {"d": day})).all()
    df = pd.DataFrame(rows, columns=["instrument_key", *COLS])
    for c in COLS:
        df[c] = df[c].astype(float)
    df["session_date"] = day
    df["net"] = 1.0
    return df


async def daily(s, ps, day: _dt.date, days: int = 450, as_of: _dt.datetime | None = None
                ) -> tuple[pd.DataFrame, str]:
    """Bars up to `day`: official where Prajna has `day`, else official < day + provisional of
    day. Returns (bars, bar_source of day). `as_of` (default now) limits the official bars and
    the CA factors to what was knowable then (a re-run for a past day)."""
    off = await official(ps, day, days, as_of=as_of)
    if (off.session_date == day).sum() > 0.5 * off.instrument_key.nunique():
        return off, "official"
    prov = await provisional(s, day)
    if prov.empty:
        raise RuntimeError(f"no official or provisional bars for {day}")
    off = off[off.session_date < day]
    return pd.concat([off, prov[off.columns]], ignore_index=True).sort_values(
        ["instrument_key", "session_date"]).reset_index(drop=True), "provisional"


async def provisional_range(s, key: str, after: _dt.date, before: _dt.date) -> pd.DataFrame:
    """Drishti's provisional (15:40 EOD quote) bars of `key` for the sessions strictly between
    `after` and `before`: a gap filler for a display when Prajna's official bar of a session is
    late (2026-11-12: its morning job missed 11-11). Raw prices (no CA adjustment); the official
    bar replaces it as soon as it is stored. Not used by Scout, the scanner or any test."""
    rows = (await s.execute(text("""
        with r as (select distinct on (r.session_date) r.run_id, r.session_date from run r
                   join run_end e using (run_id) where r.kind = 'P1_EOD' and e.status = 'OK'
                   and r.session_date > :a and r.session_date < :b
                   order by r.session_date, r.started_at)
        select q.session_date, q.open, q.high, q.low, q.close, q.volume from eod_quote q
        join r using (run_id) where q.instrument_key = :k
          and (q.quote_ts at time zone 'Asia/Kolkata')::date = q.session_date"""),
        {"a": after, "b": before, "k": key})).all()
    df = pd.DataFrame(rows, columns=["session_date", *COLS])
    for c in COLS:
        df[c] = df[c].astype(float)
    df["instrument_key"] = key
    df["net"] = 1.0
    return df
