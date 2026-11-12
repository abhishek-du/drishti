"""Paper portfolio daily loop, pf-v1 Phase 3 (docs/PREREG_PAPER_PORTFOLIO.md). PAPER ONLY.

    .venv/bin/python -m drishti.paper.run session [--until YYYY-MM-DD] [--no-telegram]

Runs every morning (cron, Tue-Sat) after the official 1d of the previous session exists. For
each session after the last one processed (PF_DAY run OK) and on/after the portfolios'
start date, in order, it calls trader.run_session:
  fills of the previous session's 'take' decisions on this session's official bar, exits,
  mark to market at the close, the decisions for this session's alerts (inputs as stored at
  the 19:45 scan and the 20:15 odds job), then the pf_equity snapshot.
A session whose official bar is not in yet stops the loop (NOT_READY; the next cron run picks it
up), so a missed day is caught up in order and a session is never processed twice.
Before `paper.run init` (the forward start) it does nothing.
"""

from __future__ import annotations

import datetime as _dt

from sqlalchemy import text

from drishti.core import runlog
from drishti.core.clock import IST, now, today
from drishti.data import bars, holidays
from drishti.paper import gates, ledger, trader

BOOKS = {r: r for r in trader.BOOKS}            # role -> portfolio id (pf-v1: the same names)


async def is_session(s, d: _dt.date) -> bool:
    return holidays.is_session(d, await holidays.latest(s, d))


async def sessions_between(s, a: _dt.date, b: _dt.date) -> list[_dt.date]:
    """Regular sessions in [a, b]."""
    out, d = [], a
    while d <= b:
        if await is_session(s, d):
            out.append(d)
        d += _dt.timedelta(days=1)
    return out


async def next_sessions(s, d: _dt.date, n: int) -> list[_dt.date]:
    out = []
    while len(out) < n:
        d += _dt.timedelta(days=1)
        if await is_session(s, d):
            out.append(d)
    return out


async def last_done(s, kind: str = "PF_DAY") -> _dt.date | None:
    return (await s.execute(text("""select max(r.session_date) from run r join run_end e
        using (run_id) where r.kind = :k and e.status = 'OK'"""), {"k": kind})).scalar()


def ready(inp: dict, day: _dt.date, late: bool) -> bool:
    """The official bar of `day` is in for >= 90 % of the stocks the session acts on (open and
    pending positions). With none it is trivially ready. A session >= 2 sessions old proceeds
    anyway (a suspended stock must not block the loop; trader treats a missing bar as no fill /
    no exit evaluation)."""
    need = inp["keys"]
    if not need or late:
        return True
    have = sum(1 for k in need if day in inp["bars"].get(k, {}))
    return have >= 0.9 * len(need)


async def inputs(s, ps, day: _dt.date, books: dict[str, str] = BOOKS) -> dict:
    """Everything run_session needs for `day`, from stored data only."""
    pos = (await s.execute(text("""select instrument_key, opened_ts from pf_position
        where book = 'swing' and portfolio_id = any(:p)"""), {"p": list(books.values())})).all()
    pend = (await s.execute(text("""select d.instrument_key, d.session_date from pf_decision d
        where d.decision = 'take' and d.tf = '1d' and d.portfolio_id = any(:p)
          and not exists (select 1 from pf_order o where o.decision_id = d.id)"""),
        {"p": list(books.values())})).all()
    keys = sorted({k for k, _ in pos} | {k for k, _ in pend})
    since = min([t.astimezone(IST).date() for _, t in pos] + [d for _, d in pend] + [day])
    bars_: dict = {}
    if keys:
        df = await bars.official(ps, day, (day - since).days + 10, keys=keys)
        for r in df.itertuples():
            bars_.setdefault(r.instrument_key, {})[r.session_date] = (
                float(r.open), float(r.high), float(r.low), float(r.close))
    span = await sessions_between(s, since - _dt.timedelta(days=10), day)
    nxt = {a: b for a, b in zip(span, span[1:])}
    prev = span[-2] if len(span) > 1 and span[-1] == day else None
    reg_prev = await gates.regime(s, prev) if prev else {"code": None}
    return {"bars": bars_, "next_session": nxt, "keys": keys,
            "window": await next_sessions(s, day, trader.HORIZON),
            # UNKNOWN (no Scout run) is not OFF: only a recorded OFF forces the regime exit
            "regime_off_prev": reg_prev.get("code") == "SKIP_REGIME_OFF"}


async def summary_text(s, day: _dt.date, results: dict, books: dict[str, str] = BOOKS) -> str:
    rows = {r["id"]: r for r in (await s.execute(text("""select id, equity, starting_capital,
        peak_equity from pf_portfolio order by id"""))).mappings()}
    eq = {r.portfolio_id: r for r in (await s.execute(text("""select portfolio_id, daily_pnl
        from pf_equity_latest where book = 'all' and date = :d"""), {"d": day})).all()}
    npos = dict((await s.execute(text("""select portfolio_id, count(*) from pf_position
        group by 1"""))).all())
    lines = [f"📒 PAPER portfolio · {day:%a %d %b} (pf-v1, paper only)"]
    for role, pid in (("main", books.get("main")), ("benchmark", "benchmark"),
                      ("placebo", books.get("placebo"))):
        r = rows.get(pid)
        if not r:
            continue
        e, c = float(r["equity"]), float(r["starting_capital"])
        dp = eq.get(pid).daily_pnl if eq.get(pid) is not None else None
        lines.append(f"{role}: ₹{e:,.0f} ({(e / c - 1) * 100:+.2f}%)"
                     + (f", today ₹{float(dp):+,.0f}" if dp is not None else "")
                     + (f", {npos.get(pid, 0)} open" if role != "benchmark" else ""))
    r = results.get(str(day), {})
    f, x, d = r.get("fills", {}), r.get("exits", {}).get("exits", {}), r.get("decisions", {})
    lines.append(f"fills {f.get('filled', 0)} · not filled {f.get('cancelled', 0)} · "
                 f"exits {sum(x.values())} {dict(x) if x else ''} · new takes {d.get('take', 0)}"
                 f" · skips {d.get('skip', 0)}")
    return "\n".join(lines)


async def run(s, ps, until: _dt.date | None = None, telegram: bool = True,
              books: dict[str, str] = BOOKS, kind: str = "PF_DAY") -> dict:
    start = (await s.execute(text("select min(start_date) from pf_portfolio where id = any(:p)"),
                             {"p": list(books.values())})).scalar()
    if start is None:
        return {"skipped": "portfolios not initialised (forward start not made)"}
    until = until or today() - _dt.timedelta(days=1)
    done = await last_done(s, kind)
    todo = await sessions_between(s, max(start, (done + _dt.timedelta(days=1)) if done else start),
                                  until)
    out: dict = {"done_before": str(done) if done else None, "sessions": {}}
    for i, day in enumerate(todo):
        inp = await inputs(s, ps, day, books)
        if not ready(inp, day, late=len(todo) - i > 2):
            out["not_ready"] = str(day)
            break
        rid = await runlog.start(s, kind, day, {"version": ledger.VERSION})
        try:
            res = await trader.run_session(s, ps, day, inp["bars"], books, inp["next_session"],
                                           inp["window"], inp["regime_off_prev"])
            res["snapshot"] = await ledger.snapshot(s, day, now())
        except BaseException as e:
            await s.rollback()
            await runlog.end(s, rid, "FAILED", error=f"{type(e).__name__}: {e}"[:500])
            raise
        await runlog.end(s, rid, "OK", res)
        out["sessions"][str(day)] = res
    if out["sessions"] and telegram:
        from drishti.scanner import notify
        last = max(out["sessions"])
        out["telegram"] = await notify.deliver(
            s, "PF_DAILY", last, await summary_text(s, _dt.date.fromisoformat(last),
                                                     out["sessions"], books))
    return out
