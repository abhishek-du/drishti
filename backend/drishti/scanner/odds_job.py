"""Nightly odds for the day's 1d scanner alerts -> odds_result (user 2026-11-04). RESEARCH /
SIMULATION, NOT a signal (Track B's engine: drishti.judge.odds, odds-1d-v1).

    .venv/bin/python -m drishti.scanner.odds_job [--session D] [--n 10000]

Point in time: each alert's bars and CA factors are those knowable at the alert's own
knowledge cut (context.as_of = the 19:45 EOD scan of its session, else 19:45 IST of the
session), so a run tonight, tomorrow or next month gives the same result. The plan is the one
the grader uses: entry = entry_ref, stop, target = detect.choose_target (R1 if its net R:R
>= 1, else R2, else 2R), horizon 5 sessions, the alert's qty; plus the user's 'took' entry when
the journal has one. An alert (basis) that already has a row of this version is skipped, except
a 'took' row whose entry changed. Engine and seeds: Track B's (seed = SEED + alert id).

This wraps the engine instead of `python -m drishti.judge.odds --alerts --store`, which writes
JSONL only and cuts the data at the next morning 09:00 (not reproducible when run the same
night).
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import sys
from sqlalchemy import text

from drishti.core import runlog
from drishti.core.clock import IST, today
from drishti.core.db import prajna_sessionmaker, sessionmaker
from drishti.data import bars
from drishti.judge import odds as O
from drishti.scanner.detect import choose_target


def verdict_kind(v: str) -> str:
    if v.startswith("Net R:R"):
        return "BASE RATES LOSE" if "LOSE" in v else "ABOUT BREAK-EVEN" if "about break-even" \
            in v else "CLEARS BREAK-EVEN"
    return v.split(":")[0]


def data_cut(a: dict) -> _dt.datetime:
    c = (a.get("context") or {}).get("as_of")
    if c:
        return _dt.datetime.fromisoformat(c)
    return _dt.datetime.combine(a["session_date"], _dt.time(19, 45), IST)


def plans_for(a: dict) -> list[O.Plan]:
    e, st = float(a["entry_ref"]), float(a["stop"])
    if not st < e:
        return []
    t = choose_target(e, st, a["r1"], a["r2"], a["qty"] or 0)["target"]
    as_of = a["session_date"] + _dt.timedelta(days=1)   # the engine reads bars < as_of
    out = [O.Plan(a["instrument_key"], as_of, e, st, t, 5, a["qty"], a["symbol"], a["id"],
                  "alert")]
    ae = a.get("actual_entry")
    if a.get("status") == "took" and ae and st < float(ae) < t:
        out.append(O.Plan(a["instrument_key"], as_of, float(ae), st, t, 5,
                          a.get("actual_qty") or a["qty"], a["symbol"], a["id"], "took"))
    return out


async def run(s, ps, rid, session: _dt.date, n_paths: int = 10_000) -> dict:
    alerts = [dict(r) for r in (await s.execute(text("""
        select a.*, j.status, j.actual_entry, j.actual_qty from scan_alert a
        left join journal_entry j on j.alert_id = a.id
        where a.tf = '1d' and a.session_date = :d order by a.id"""), {"d": session})).mappings()]
    done = {(r[0], r[1]): r[2] for r in (await s.execute(text("""
        select distinct on (alert_id, basis) alert_id, basis, entry from odds_result
        where version = :v and alert_id = any(:i) order by alert_id, basis, created_at desc"""),
        {"v": O.VERSION, "i": [a["id"] for a in alerts]})).all()}
    sha = O.code_sha()
    out = {"session": str(session), "alerts": len(alerts), "stored": 0, "skipped": 0,
           "verdicts": {}}
    for a in alerts:
        cut = data_cut(a)
        b = None
        for pl in plans_for(a):
            prev = done.get((pl.alert_id, pl.basis))
            if prev is not None and (pl.basis == "alert" or float(prev) == pl.entry):
                out["skipped"] += 1
                continue
            if b is None:
                b = await bars.official(ps, a["session_date"], int(750 * 1.6) + 60, as_of=cut,
                                        keys=[a["instrument_key"]])
            prm = O.Params(n_paths=n_paths, seed=O.SEED + a["id"],
                           extra={"data_as_of": cut.isoformat()})
            res = O.odds(pl, b, prm)
            res["code_sha"] = sha
            r = O.row(res)
            await s.execute(text("""insert into odds_result(alert_id, basis, instrument_key,
                symbol, as_of, tf, entry, stop, target, horizon, qty, version, seed, code_sha,
                params, pool, mc, replay, p_target_mc, breakeven, verdict, label, caveat,
                data_as_of) values (:alert_id, :basis, :instrument_key, :symbol, :as_of, :tf,
                :entry, :stop, :target, :horizon, :qty, :version, :seed, :code_sha,
                cast(:params as jsonb), cast(:pool as jsonb), cast(:mc as jsonb),
                cast(:replay as jsonb), :p_target_mc, :breakeven, :verdict, :label, :caveat,
                :cut)"""),
                {**r, "as_of": _dt.date.fromisoformat(r["as_of"]),
                 **{k: json.dumps(r[k], default=str) for k in ("params", "pool", "mc", "replay")},
                 "cut": cut})
            out["stored"] += 1
            v = verdict_kind(r["verdict"])
            out["verdicts"][v] = out["verdicts"].get(v, 0) + 1
        await s.commit()
    return out


async def main(args: list[str]) -> dict:
    async with sessionmaker()() as s, prajna_sessionmaker()() as ps:
        if "--session" in args:
            day = _dt.date.fromisoformat(args[args.index("--session") + 1])
        else:
            day = (await s.execute(text("""select max(session_date) from scan_alert
                where tf = '1d' and session_date <= :t"""), {"t": today()})).scalar()
        if day is None:
            return {"skipped": "no 1d alerts"}
        n = int(args[args.index("--n") + 1]) if "--n" in args else 10_000
        rid = await runlog.start(s, "SCAN_ODDS", day, {"args": args, "version": O.VERSION})
        try:
            out = await run(s, ps, rid, day, n)
        except BaseException as e:
            await s.rollback()
            await runlog.end(s, rid, "FAILED", error=f"{type(e).__name__}: {e}"[:500])
            raise
        await runlog.end(s, rid, "OK", out)
        return out


if __name__ == "__main__":
    print(json.dumps(asyncio.run(main(sys.argv[1:])), indent=1, default=str))
