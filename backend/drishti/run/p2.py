"""P2 jobs (PAPER ONLY system; no orders anywhere).

    .venv/bin/python -m drishti.run.p2 scout [--date 2026-10-28] [--dry]   # 19:30 IST
(--dry computes and prints the funnel; nothing is written)
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import sys

import pandas as pd
from sqlalchemy import text

from drishti.core import runlog
from drishti.core.clock import now, today
from drishti.core.db import prajna_sessionmaker, sessionmaker
from drishti.data import bars, eod_quotes, holidays, manual, master
from drishti.data.holidays import SHORT_SESSIONS
from drishti.scout import screener_b as sb


async def next_sessions(s, day: _dt.date, n: int) -> list[_dt.date]:
    out, d = [], day
    while len(out) < n:
        d += _dt.timedelta(days=1)
        if holidays.is_session(d, await holidays.latest(s, d)):
            out.append(d)
    return out


async def manual_isins(s, kinds: tuple[str, ...], day: _dt.date, stage: str | None = None
                       ) -> set[str]:
    """ISINs of the latest file of each kind with file_date <= day (point in time)."""
    rows = (await s.execute(text("""
        with f as (select distinct on (kind) file_id from manual_file
                   where kind = any(:k) and file_date <= :d order by kind, file_date desc,
                   loaded_at desc)
        select r.isin from manual_row r join f using (file_id)
        where r.isin is not null and (cast(:st as text) is null or r.stage = :st)"""),
        {"k": list(kinds), "d": day, "st": stage})).scalars().all()
    return set(rows)


async def scout(s, ps, day: _dt.date) -> dict:
    b, source = await bars.daily(s, ps, day)
    st = sb.per_stock(b)
    idx = pd.DataFrame((await s.execute(text("""select index_key, session_date, close, source
        from index_bar where session_date <= :d order by fetched_at"""), {"d": day})).all(),
        columns=["index_key", "session_date", "close", "source"])
    idx = idx[~idx.session_date.isin(SHORT_SESSIONS)].assign(close=lambda x: x.close.astype(float))
    reg = sb.regime(idx, day)
    eq = await manual_isins(s, ("EQUITY_L",), day, "EQ")
    blocked = await manual_isins(s, ("ASM", "GSM"), day)
    q = (await s.execute(text("""
        with r as (select r.run_id from run r join run_end e using (run_id)
                   where r.kind = 'P1_EOD' and r.session_date = :d and e.status = 'OK'
                   order by r.started_at desc limit 1)
        select instrument_key, upper_circuit, lower_circuit, last_price, net_change
        from eod_quote where run_id = (select run_id from r)"""), {"d": day})).all()
    fo = {k for k, a in (await master.state(s)).items() if a.get("fo")}
    # F&O stocks have no fixed price band; their circuit limits are a dynamic operating range
    # (and after the close Upstox shows them as +-5 %), so they are never band-excluded (SC5)
    bands = {k: None if k in fo else eod_quotes.band_pct(u, lo, lp, nc) for k, u, lo, lp, nc in q}
    listed, funnel = sb.screen(st, eq, blocked, reg, day, bands)
    funnel["band_fo_exempt"] = int(st.index.isin(list(fo)).sum())
    window = await next_sessions(s, day, 6)
    res = dict((await ps.execute(text("""select instrument_key, min(meeting_date) from results_event
        where kind = 'INTIMATION' and meeting_date between :a and :b and knowable_at < :t
        group by 1"""), {"a": window[0], "b": window[-1], "t": now()})).all())
    sym = dict((await ps.execute(text("""select instrument_key, trading_symbol from canon_instrument
        where instrument_key = any(:k)"""), {"k": list(listed.index)})).all())
    prev = (await s.execute(text("""select run_id from watchlist_run where session_date < :d
        order by session_date desc, created_at desc limit 1"""), {"d": day})).scalar()
    before = set() if prev is None else set((await s.execute(text("""select instrument_key
        from watchlist_event where run_id = :r and event <> 'DROPPED'"""), {"r": prev})).scalars())
    surv = await manual.surveillance_freshness(s, day)
    funnel.update(added=len(set(listed.index) - before), kept=len(set(listed.index) & before),
                  dropped=len(before - set(listed.index)),
                  results_ahead=int(listed.index.isin(list(res)).sum()))
    return {"listed": listed, "dropped": sorted(before - set(listed.index)), "before": before,
            "source": source, "regime": reg, "surveillance": surv, "funnel": funnel,
            "results": res, "symbols": sym, "window": window}


async def store(s, rid, day: _dt.date, r: dict) -> None:
    await s.execute(text("""insert into watchlist_run(run_id, session_date, version, params,
        bar_source, regime, surveillance, counts) values (:r, :d, :v, cast(:p as jsonb), :b,
        cast(:g as jsonb), cast(:sv as jsonb), cast(:c as jsonb))"""),
        {"r": rid, "d": day, "v": sb.VERSION, "p": json.dumps(sb.PARAMS), "b": r["source"],
         "g": json.dumps(r["regime"], default=str), "sv": json.dumps(r["surveillance"], default=str),
         "c": json.dumps(r["funnel"])})
    for k, row in r["listed"].iterrows():
        flags = {"candles": row.candle_flags, "results_meeting": str(r["results"][k])
                 if k in r["results"] else None, "results_window": [str(r["window"][0]),
                                                                    str(r["window"][-1])]}
        await s.execute(text("""insert into watchlist_event(run_id, session_date, instrument_key,
            symbol, event, rank, rs_score, metrics, flags) values (:r, :d, :k, :sym, :e, :rk,
            :rs, cast(:m as jsonb), cast(:f as jsonb))"""),
            {"r": rid, "d": day, "k": k, "sym": r["symbols"].get(k),
             "e": "KEPT" if k in r["before"] else "ADDED", "rk": int(row["rank"]),
             "rs": float(row.rs_score), "m": json.dumps(sb.metrics(row)), "f": json.dumps(flags)})
    for k in r["dropped"]:
        await s.execute(text("""insert into watchlist_event(run_id, session_date, instrument_key,
            event) values (:r, :d, :k, 'DROPPED')"""), {"r": rid, "d": day, "k": k})
    await s.commit()


async def main(cmd: str, args: list[str]) -> dict:
    if cmd != "scout":
        raise SystemExit(__doc__)
    day = _dt.date.fromisoformat(args[args.index("--date") + 1]) if "--date" in args else today()
    async with sessionmaker()() as s, prajna_sessionmaker()() as ps:
        if "--dry" in args:
            r = await scout(s, ps, day)
            top = r["listed"].head(15)
            return {"bar_source": r["source"], "funnel": r["funnel"], "regime": r["regime"],
                    "top": [f"{r['symbols'].get(k, k)} rs={v.rs_score:.2f}"
                            for k, v in top.iterrows()]}
        rid = await runlog.start(s, "P2_SCOUT", day, {"args": args, "version": sb.VERSION})
        try:
            if not holidays.is_session(day, await holidays.latest(s, day)):
                await runlog.end(s, rid, "NOOP", {"reason": "not a session"})
                return {"skipped": "not a session"}
            r = await scout(s, ps, day)
            await store(s, rid, day, r)
            out = {"session": str(day), "bar_source": r["source"], **r["funnel"],
                   "regime": r["regime"]["status"], "regime_stale": r["regime"]["stale"],
                   "surveillance_warning": r["surveillance"].get("warning")}
        except BaseException as e:
            await s.rollback()
            await runlog.end(s, rid, "FAILED", error=f"{type(e).__name__}: {e}"[:500])
            raise
        await runlog.end(s, rid, "OK", out)
        return out


if __name__ == "__main__":
    print(json.dumps(asyncio.run(main(sys.argv[1], sys.argv[2:])), indent=1, default=str))
