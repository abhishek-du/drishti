"""Scanner + journal API (user request 2026-11-03). PAPER ONLY: nothing here places an order.

Every response carries LABEL: "Decision support - detections are NOT validated edges. Your
call. Paper only." The scanner is the user's discretionary decision-support tool, separate from
drishti-swing-v1 and every forward test; the blind rule of app.py covers those, not this
journal (the user asked to see their own hit rate vs the base rate vs a placebo).

Writes: only POST /api/journal/{alert_id} (journal_entry upsert + an append-only journal_event).
The web (Track C) reads; the journal form posts here.
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Literal

import numpy as np
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from drishti.core.clock import today
from drishti.core.db import sessionmaker
from drishti.scanner.detect import DETECTORS, LABEL, VERSION

router = APIRouter()

ALERT_COLS = """a.id, a.session_date, a.tf, a.bar_time, a.instrument_key, a.symbol, a.detector,
    a.version, a.entry_ref, a.stop, a.r1, a.r2, a.rr_r1, a.qty, a.capital, a.risk_pct, a.cost_r,
    a.atr, a.context, a.created_at, j.status, j.actual_entry, j.actual_qty, j.notes,
    j.updated_at as journal_updated_at"""

LATEST = """select distinct on (alert_id, basis) alert_id, basis, outcome, entry_used,
    exit_price, exit_bar, target_used, r_gross, r_net, bars_used, seed, graded_at
    from journal_outcome order by alert_id, basis, graded_at desc, id desc"""


def _j(v):
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.isoformat()
    if v is None or isinstance(v, (str, int, bool, dict, list)):
        return v
    return round(float(v), 4)


def _row(r) -> dict:
    return {k: _j(v) for k, v in dict(r).items()}


@router.get("/api/scan/alerts")
async def alerts(date: _dt.date | None = None, tf: Literal["1d", "15m"] | None = None,
                 detector: str | None = None) -> dict:
    """Alerts of one session (default: the latest session with alerts), newest bar first, with
    the journal status and the latest 'alert' grade."""
    async with sessionmaker()() as s:
        day = date or (await s.execute(text("select max(session_date) from scan_alert"))).scalar()
        rows = [] if day is None else (await s.execute(text(f"""
            select {ALERT_COLS}, o.outcome, o.r_net from scan_alert a
            left join journal_entry j on j.alert_id = a.id
            left join ({LATEST}) o on o.alert_id = a.id and o.basis = 'alert'
            where a.session_date = :d and (cast(:tf as text) is null or a.tf = :tf)
              and (cast(:det as text) is null or a.detector = :det)
            order by a.bar_time desc, a.detector, a.symbol"""),
            {"d": day, "tf": tf, "det": detector})).mappings().all()
        dates = [str(d) for d in (await s.execute(text("""select distinct session_date
            from scan_alert order by 1 desc limit 60"""))).scalars()]
    return {"label": LABEL, "version": VERSION, "session": _j(day), "dates": dates,
            "detectors": list(DETECTORS), "alerts": [_row(r) for r in rows]}


@router.get("/api/scan/alert/{alert_id}")
async def alert(alert_id: int) -> dict:
    """One alert: its levels, the journal entry and its edit history, the latest grade of each
    basis (alert / took / placebo)."""
    async with sessionmaker()() as s:
        a = (await s.execute(text(f"""select {ALERT_COLS} from scan_alert a
            left join journal_entry j on j.alert_id = a.id where a.id = :i"""),
            {"i": alert_id})).mappings().first()
        if a is None:
            raise HTTPException(404, "no such alert")
        g = (await s.execute(text(f"select * from ({LATEST}) o where alert_id = :i"),
                             {"i": alert_id})).mappings().all()
        ev = (await s.execute(text("""select change, at from journal_event where alert_id = :i
            order by at"""), {"i": alert_id})).mappings().all()
    a = _row(a)
    risk = a["entry_ref"] - a["stop"]
    a["risk_per_share"] = round(risk, 4)
    a["risk_rupees"] = round(risk * (a["qty"] or 0), 2)
    a["r2_rr"] = round((a["r2"] - a["entry_ref"]) / risk, 2) if a["r2"] and risk > 0 else None
    return {"label": LABEL, "alert": a, "grades": {r["basis"]: _row(r) for r in g},
            "journal_history": [_row(e) for e in ev]}


class JournalIn(BaseModel):
    status: Literal["watching", "took", "skipped"] | None = None
    actual_entry: float | None = Field(None, gt=0)
    actual_qty: int | None = Field(None, gt=0)
    notes: str | None = Field(None, max_length=4000)


@router.post("/api/journal/{alert_id}")
async def journal(alert_id: int, body: JournalIn) -> dict:
    """Mark an alert watching / took / skipped, set the actual entry and qty, add notes. Fields
    left out keep their value. Every change is also appended to journal_event (audit)."""
    ch = body.model_dump(exclude_unset=True)
    if not ch:
        raise HTTPException(400, "nothing to change")
    async with sessionmaker()() as s:
        a = (await s.execute(text("select stop from scan_alert where id = :i"),
                             {"i": alert_id})).first()
        if a is None:
            raise HTTPException(404, "no such alert")
        if ch.get("actual_entry") is not None and ch["actual_entry"] <= float(a[0]):
            raise HTTPException(400, "actual entry must be above the alert's stop")
        cur = (await s.execute(text("select * from journal_entry where alert_id = :i"),
                               {"i": alert_id})).mappings().first()
        new = {"status": "watching", "actual_entry": None, "actual_qty": None, "notes": None,
               **({k: cur[k] for k in ("status", "actual_entry", "actual_qty", "notes")}
                  if cur else {}), **ch}
        if new["status"] == "took" and new["actual_entry"] is None:
            raise HTTPException(400, "status 'took' needs actual_entry")
        await s.execute(text("""insert into journal_entry(alert_id, status, actual_entry,
            actual_qty, notes) values (:i, :st, :e, :q, :n)
            on conflict (alert_id) do update set status = excluded.status,
              actual_entry = excluded.actual_entry, actual_qty = excluded.actual_qty,
              notes = excluded.notes, updated_at = clock_timestamp()"""),
            {"i": alert_id, "st": new["status"], "e": new["actual_entry"],
             "q": new["actual_qty"], "n": new["notes"]})
        await s.execute(text("""insert into journal_event(alert_id, change)
            values (:i, cast(:c as jsonb))"""), {"i": alert_id, "c": json.dumps(ch)})
        await s.commit()
    return {"label": LABEL, "alert_id": alert_id, "journal": {k: _j(v) for k, v in new.items()}}


def _summary(r: np.ndarray, outcomes: list[str]) -> dict:
    n = len(r)
    wins, losses = r[r > 0], r[r <= 0]
    return {"closed": n,
            "hit_rate_r1": round(outcomes.count("R1") / n, 4) if n else None,
            "win_rate": round(len(wins) / n, 4) if n else None,
            "expectancy_r_net": round(float(r.mean()), 4) if n else None,
            "avg_win_r": round(float(wins.mean()), 4) if len(wins) else None,
            "avg_loss_r": round(float(losses.mean()), 4) if len(losses) else None,
            "payoff_ratio": round(float(wins.mean() / -losses.mean()), 3)
            if len(wins) and len(losses) and losses.mean() < 0 else None,
            "total_r_net": round(float(r.sum()), 3),
            "by_outcome": {o: outcomes.count(o) for o in sorted(set(outcomes))}}


@router.get("/api/journal/stats")
async def stats(since: _dt.date | None = None, until: _dt.date | None = None,
                tf: Literal["1d", "15m"] | None = None) -> dict:
    """Closed trades (R1 / STOP / TIMEOUT; net of costs, in R), by detector and overall:
      took      the user's trades (status 'took', graded from the actual entry)
      took_base the same alerts graded from the alert's own entry rule (did the user's timing
                help?)
      alerts    every alert that filled (the base rate of the detectors)
      placebo   every alert, seeded random entry in the next bar's range, same stop/target
    """
    # 2026-11-03 = the first live (cron) EOD scan; the 11-02 batch was a one-off re-run on 11-03
    # with the official bar of 11-02 (not knowable at 19:45 on 11-02; 1 of its 14 alerts would
    # not have fired live), so it is left out by default
    since = since or _dt.date(2026, 11, 3)
    until = until or today()
    async with sessionmaker()() as s:
        rows = (await s.execute(text(f"""select a.id, a.detector, o.basis, o.outcome, o.r_net,
                j.status from scan_alert a join ({LATEST}) o on o.alert_id = a.id
            left join journal_entry j on j.alert_id = a.id
            where a.session_date between :a and :b and (cast(:tf as text) is null or a.tf = :tf)
            """), {"a": since, "b": until, "tf": tf})).mappings().all()
        counts = dict((await s.execute(text("""select coalesce(j.status, 'none'), count(*)
            from scan_alert a left join journal_entry j on j.alert_id = a.id
            where a.session_date between :a and :b and (cast(:tf as text) is null or a.tf = :tf)
            group by 1"""), {"a": since, "b": until, "tf": tf})).all())
    closed = [r for r in rows if r["outcome"] in ("R1", "STOP", "TIMEOUT")]
    took_ids = {r["id"] for r in closed if r["basis"] == "took" and r["status"] == "took"}

    def pick(det: str | None) -> dict:
        sel = [r for r in closed if det is None or r["detector"] == det]
        groups = {
            "took": [r for r in sel if r["basis"] == "took" and r["id"] in took_ids],
            "took_base": [r for r in sel if r["basis"] == "alert" and r["id"] in took_ids],
            "alerts": [r for r in sel if r["basis"] == "alert"],
            "placebo": [r for r in sel if r["basis"] == "placebo"],
        }
        return {k: _summary(np.array([float(r["r_net"]) for r in v], float),
                            [r["outcome"] for r in v]) for k, v in groups.items()}

    open_ = sum(1 for r in rows if r["outcome"] == "OPEN" and r["basis"] == "alert")
    nofill = sum(1 for r in rows if r["outcome"] == "NO_FILL" and r["basis"] == "alert")
    return {"label": LABEL, "version": VERSION, "since": str(since), "until": str(until),
            "tf": tf, "journal_counts": counts, "alerts_open": open_, "alerts_no_fill": nofill,
            "overall": pick(None), "by_detector": {d: pick(d) for d in DETECTORS},
            "note": "Small samples mean nothing: a few dozen trades cannot separate skill from "
                    "luck. Compare 'took' with 'placebo', not with zero."}
