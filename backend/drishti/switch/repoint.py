"""Re-point check (SWITCH_TO_B part 4): Drishti's reads on the D1 copy must equal its reads on
Prajna's live DB, before DRISHTI_HISTORY_SOURCE is flipped to 'copy' at the switch.

    .venv/bin/python -m drishti.switch.repoint history --target drishti [--cutover D]  # step 3
    .venv/bin/python -m drishti.switch.repoint verify --target drishti --out DIR [--n 50]
        [--months 2026-09] [--day YYYY-MM-DD] [--tol 0.001]

For --n random stocks plus every stock with an EXACT corporate-action factor, bars.official()
(adjusted daily bars, CA factors applied point in time) is run on both sources with the same
as_of = the copy's watermark (copy_state.json), and the frames must be identical. --months
limits the comparison to the copied months (a partial dry-run copy). Read only on both sides.
--tol (the P4 union check only): prices and volume may differ by this relative amount; a
vendor-adjusted bar fetched after an ex-date is rounded by the vendor (581.8 vs 581.75 computed
from the raw bar). The switch steps run without it (exact). The report goes to
OUT/repoint_verify_<stamp>.json.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from drishti.core.clock import IST
from drishti.core.config import get_settings
from drishti.core.db import copy_engine
from drishti.data import bars

COLS = ["instrument_key", "session_date", "open", "high", "low", "close", "volume"]


def _norm(df: pd.DataFrame, months: list[str] | None) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=COLS)
    d = df[COLS].copy()
    if months:
        d = d[pd.to_datetime(d.session_date).dt.strftime("%Y-%m").isin(months)]
    for c in ("open", "high", "low", "close", "volume"):
        d[c] = d[c].astype(float).round(6)
    return d.sort_values(["instrument_key", "session_date"]).reset_index(drop=True)


async def verify(target: str, out: Path, n: int, months: list[str] | None,
                 day: _dt.date | None, tol: float = 0.0) -> dict:
    cfg = get_settings()
    state = json.loads((out / "copy_state.json").read_text())
    w = _dt.datetime.fromisoformat(state["runs"][-1]["w"].replace(" ", "T"))
    url = cfg.DRISHTI_DATABASE_URL.rsplit("/", 1)[0] + f"/{target}"
    live_e = create_async_engine(cfg.DRISHTI_PRAJNA_RO_URL, pool_pre_ping=True)
    copy_e = copy_engine(url)
    try:
        async with async_sessionmaker(live_e)() as lv, async_sessionmaker(copy_e)() as cp:
            day = day or w.astimezone(IST).date()
            if months:
                day = min(day, max(pd.Period(m).end_time.date() for m in months))
            keys_all = sorted((await cp.execute(text("""select instrument_key
                from canon_instrument where included and instrument_key like 'NSE_EQ|%'"""))
                ).scalars())
            ca = sorted(set((await cp.execute(text("""select distinct instrument_key
                from canon_ca_factor where status = 'EXACT' and ex_date is not null"""))
                ).scalars()) & set(keys_all))
            rnd = random.Random(20261112).sample(keys_all, min(n, len(keys_all)))
            keys = sorted(set(rnd) | set(ca))
            mism, rows, worst = [], 0, 0.0
            for i in range(0, len(keys), 200):
                chunk = keys[i:i + 200]
                a = _norm(await bars.official(lv, day, 450, as_of=w, keys=chunk), months)
                b = _norm(await bars.official(cp, day, 450, as_of=w, keys=chunk), months)
                rows += len(a)
                if not a.equals(b):
                    m = a.merge(b, on=["instrument_key", "session_date"], how="outer",
                                suffixes=("_live", "_copy"), indicator=True)
                    lv_ = m[[f"{c}_live" for c in COLS[2:]]].to_numpy(dtype=float)
                    cp_ = m[[f"{c}_copy" for c in COLS[2:]]].to_numpy(dtype=float)
                    rel = np.abs(lv_ - cp_) / np.maximum(np.abs(lv_), 1e-9)
                    off = (rel > tol).any(axis=1) | (m._merge != "both").to_numpy()
                    within = rel[~off & (m._merge == "both").to_numpy()]
                    if within.size:
                        worst = max(worst, float(within.max()))
                    bad = m[off]
                    mism += sorted(bad.instrument_key.unique().tolist())
    finally:
        await live_e.dispose()
        await copy_e.dispose()
    rep = {"target": target, "as_of": w.isoformat(), "day": str(day), "months": months,
           "keys_checked": len(keys), "random": len(rnd), "with_ca": len(ca), "rows": rows,
           "mismatch_keys": mism[:50], "mismatches": len(mism), "ok": not mism and rows > 0,
           "tol": tol, "max_rel_diff_within_tol": worst}
    stamp = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    (out / f"repoint_verify_{stamp}.json").write_text(json.dumps(rep, indent=1))
    return rep


HISTORY_SQL = """
create schema if not exists history;
create table if not exists history.cutover (d date not null);
-- the cutover is always the copy's last session (or --cutover): after the switch-night delta
-- copy it moves to S, so a session is never in both the copy and Drishti's own bars
delete from history.cutover;
insert into history.cutover(d) select coalesce(cast(:cut as date),
    (select max(session_date) from prajna_copy.ohlcv_bar_1d));
create or replace view history.ohlcv_bar as
  -- the copy up to the cutover only: a session is never taken from both sides
  select * from prajna_copy.ohlcv_bar_1d where session_date <= (select d from history.cutover)
  union all
  -- Drishti's own nightly 1d after the cutover; the newest fetch per (stock, session) (the
  -- nightly job re-fetches T-5..T, so a bar can appear several times in bar_1d)
  select ci.instrument_id, cast('1d' as varchar(8)), b.session_date,
         (b.session_date::timestamp at time zone 'Asia/Kolkata'),
         cast('DRISHTI_UPSTOX_V3' as varchar(32)), cast(b.instrument_key as varchar(80)),
         cast(b.open as numeric(18,4)), cast(b.high as numeric(18,4)),
         cast(b.low as numeric(18,4)), cast(b.close as numeric(18,4)),
         cast(b.volume as numeric(22,0)), cast(b.oi as numeric(22,0)), ''::text, p.run_id,
         p.sha256, b.fetched_at, b.fetched_at, true,
         cast('drishti fetch time (own 1d ingest)' as varchar(200))
  from (select distinct on (instrument_key, session_date) * from public.bar_1d
        where session_date > (select d from history.cutover)
        order by instrument_key, session_date, fetched_at desc, id desc) b
  join public.bar_1d_payload p on p.id = b.payload_id
  join prajna_copy.canon_instrument ci on ci.instrument_key = b.instrument_key;
create or replace view history.ohlcv_payload_basis as
  select * from prajna_copy.ohlcv_payload_basis
  union all
  select distinct p.sha256, cast('historical' as varchar(16)), cast('VENDOR_ADJUSTED' as varchar(20)),
         p.basis_as_of, cast('HIGH' as varchar(8)), cast('drishti-own-v1' as varchar(32)),
         '{}'::jsonb
  from public.bar_1d_payload p
  where exists (select 1 from public.bar_1d b where b.payload_id = p.id
                and b.session_date > (select d from history.cutover));
create or replace view history.canon_ca_factor as
  -- the copy's factors up to the cutover only (2026-11-12 P4 finding: an announced CA with a
  -- later ex-date was in both the copy and Drishti's own table, so it was applied twice)
  select * from prajna_copy.canon_ca_factor
  where ex_date is null or ex_date <= (select d from history.cutover)
  union all
  select f.id + 1000000000, f.ca_id + 1000000000, cast(c.instrument_key as varchar(80)),
         cast(c.action_type as varchar(16)), c.ex_date, cast(f.status as varchar(12)),
         cast(f.method as varchar(16)), cast(f.factor_price as numeric(24,12)),
         cast(f.factor_volume as numeric(24,12)), greatest(c.knowable_at, f.derived_at),
         cast(f.vendor_applied as varchar(12)), f.vendor_evidence, f.reason, f.inputs,
         cast(f.method_version as varchar(32)), f.derived_at, f.run_id
  -- ca_factor is append-only (a re-derivation adds a row): the newest row per event, as
  -- corporate_actions.compare_with_prajna reads it. An event fetched under the old and the new
  -- ISIN of one issuer (ISIN change at a split; same content sha) counts once: the newest.
  from (select distinct on (ca_id) * from public.ca_factor
        order by ca_id, derived_at desc, id desc) f
  join (select distinct on (content_sha256, substr(isin, 1, 7)) * from public.corporate_action
        order by content_sha256, substr(isin, 1, 7), first_seen_at desc, id desc) c
    on c.id = f.ca_id
  where c.ex_date > (select d from history.cutover);
"""


def make_history_views(target: str, cutover: str | None = None) -> dict:
    """Re-point step 3 (2026-11-12): the `history` schema = the D1 copy up to the cutover date +
    Drishti's own 1d bars and CA factors after it (no event counted twice). canon_market_bar is
    recreated from Prajna's definition over these union views. In copy mode the engine's
    search_path is history, prajna_copy, so every reader sees one continuous series. Before
    the switch the cutover is the copy's last session: the result equals the copy."""
    from drishti.switch import d1copy as D
    sqltxt = HISTORY_SQL.replace(":cut", "NULL" if cutover is None else f"'{cutover}'")
    body = "\n".join("|".join(r) for r in D.sql(
        "prajna", "select pg_get_viewdef('public.canon_market_bar'::regclass, true)")).strip()
    sqltxt += ("set search_path = history, prajna_copy;\n"
               f"create or replace view history.canon_market_bar as {body.rstrip(';')};\n"
               "grant usage on schema history to drishti_app;\n"
               "grant select on all tables in schema history to drishti_app;\n")
    D.sql(target, sqltxt, False)
    cut = D.sql(target, "select d from history.cutover", False)[0][0]
    n = {v: int(D.sql(target, f"select count(*) from history.{v}", False)[0][0])
         for v in ("ohlcv_bar", "canon_ca_factor", "canon_market_bar")}
    return {"cutover": cut, **n}


def main(argv: list[str]) -> dict:
    if argv and argv[0] == "history":
        a = dict(zip(argv[1::2], argv[2::2]))
        return make_history_views(a["--target"], a.get("--cutover"))
    if not argv or argv[0] != "verify":
        raise SystemExit(__doc__)
    a = dict(zip(argv[1::2], argv[2::2]))
    return asyncio.run(verify(a["--target"], Path(a["--out"]), int(a.get("--n", 50)),
                              a["--months"].split(",") if "--months" in a else None,
                              _dt.date.fromisoformat(a["--day"]) if "--day" in a else None,
                              float(a.get("--tol", 0))))


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1:]), indent=1, default=str))
