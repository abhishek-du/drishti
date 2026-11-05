"""Paper portfolio jobs, pf-v1 Phase 1 (PAPER ONLY).

    .venv/bin/python -m drishti.paper.run init [--start YYYY-MM-DD]   # create the 5 portfolios
    .venv/bin/python -m drishti.paper.run mtm                         # mark open positions
    .venv/bin/python -m drishti.paper.run snapshot [--date D]         # pf_equity rows of D

Prices for the mark: Drishti's own 15:40 EOD quotes of the latest session (last price of the
day, eod_quote), else the official 1d close (bar_1d). No scheduling yet: the cron lines come
with the Phase 3 loop.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import sys

from sqlalchemy import text

from drishti.core import runlog
from drishti.core.clock import IST, now, today
from drishti.core.db import sessionmaker
from drishti.paper import ledger


async def latest_prices(s, keys: list[str]) -> dict[str, tuple[float, _dt.datetime]]:
    if not keys:
        return {}
    q = dict((k, (float(p), t)) for k, p, t in (await s.execute(text("""
        with r as (select r.run_id from run r join run_end e using (run_id)
                   where r.kind = 'P1_EOD' and e.status = 'OK'
                   order by r.session_date desc, r.started_at limit 1)
        select instrument_key, last_price, quote_ts from eod_quote
        where run_id = (select run_id from r) and instrument_key = any(:k)
          and last_price is not null"""), {"k": keys})).all())
    rest = [k for k in keys if k not in q]
    if rest:
        for k, c, d in (await s.execute(text("""select distinct on (instrument_key)
            instrument_key, close, session_date from bar_1d where instrument_key = any(:k)
            order by instrument_key, session_date desc, fetched_at desc"""), {"k": rest})).all():
            q[k] = (float(c), _dt.datetime.combine(d, _dt.time(15, 30), IST))
    return q


async def main(cmd: str, args: list[str]) -> dict:
    async with sessionmaker()() as s:
        rid = await runlog.start(s, f"PF_{cmd.upper()}", today(), {"args": args,
                                                                   "version": ledger.VERSION})
        try:
            if cmd == "init":
                start = _dt.date.fromisoformat(args[args.index("--start") + 1]) \
                    if "--start" in args else today()
                b = (await s.execute(text("""select close, session_date from index_bar
                    where index_key = :k and session_date <= :d
                    order by session_date desc, fetched_at desc limit 1"""),
                    {"k": ledger.BENCH_KEY, "d": start})).first()
                made = await ledger.init_portfolios(s, start, float(b[0]) if b else None,
                                                    b[1] if b else None)
                out = {"created": made, "benchmark_base": [float(b[0]), str(b[1])] if b else None}
            elif cmd == "mtm":
                keys = list((await s.execute(text("""select distinct instrument_key
                    from pf_position"""))).scalars())
                px = await latest_prices(s, keys)
                out = {}
                for pid in (await s.execute(text("select id from pf_portfolio order by id"))
                            ).scalars().all():
                    out[pid] = await ledger.mark_to_market(s, pid, px, now())
            elif cmd == "snapshot":
                day = _dt.date.fromisoformat(args[args.index("--date") + 1]) \
                    if "--date" in args else today()
                out = await ledger.snapshot(s, day, now())
            else:
                raise SystemExit(__doc__)
        except BaseException as e:
            await s.rollback()
            await runlog.end(s, rid, "FAILED", error=f"{type(e).__name__}: {e}"[:500])
            raise
        await runlog.end(s, rid, "OK", out)
        return out


if __name__ == "__main__":
    print(json.dumps(asyncio.run(main(sys.argv[1], sys.argv[2:])), indent=1, default=str))
