"""P1 data jobs (read-only market data; PAPER ONLY system).

    .venv/bin/python -m drishti.run.p1 holidays
    .venv/bin/python -m drishti.run.p1 indices [--from 2019-01-01]
    .venv/bin/python -m drishti.run.p1 manual
    .venv/bin/python -m drishti.run.p1 eod            # after 15:35 IST on a trading day
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import sys

from drishti.broker.upstox.rest import Upstox
from drishti.core import runlog
from drishti.core.clock import now, today
from drishti.core.config import get_settings
from drishti.core.db import prajna_sessionmaker, sessionmaker
from drishti.data import eod_quotes, holidays, indices, manual


async def main(cmd: str, args: list[str]) -> dict:
    async with sessionmaker()() as s:
        rid = await runlog.start(s, f"P1_{cmd.upper()}", today(), {"args": args})
        try:
            if cmd == "holidays":
                d = Upstox().get("/v2/market/holidays")
                n = await holidays.store(s, rid, holidays.parse(d.get("data") or []))
                out = {"holidays": n}
            elif cmd == "indices":
                frm = _dt.date.fromisoformat(args[args.index("--from") + 1]) if "--from" in args \
                    else _dt.date(2019, 1, 1)
                api, last, out = Upstox(), await indices.last_dates(s), {}
                for k in indices.INDEX_KEYS:
                    start = (last[k] + _dt.timedelta(days=1)) if k in last else frm
                    rows = [r for r in indices.fetch_history(api, k, start, today())
                            if r["session_date"] >= start]
                    out[k] = await indices.store(s, rid, k, rows, "UPSTOX_HIST")
            elif cmd == "manual":
                out = {"files": await manual.load_dir(s, rid, get_settings().DRISHTI_DATA_DIR
                                                      / "manual")}
            elif cmd == "eod":
                t = now()
                if t.hour * 60 + t.minute < 15 * 60 + 35:
                    raise SystemExit("REFUSED: eod quotes are taken after 15:35 IST")
                async with prajna_sessionmaker()() as ps:
                    keys = await eod_quotes.universe_keys(ps)
                rows = eod_quotes.fetch(Upstox(), keys)
                out = {"keys": len(keys), "stored": await eod_quotes.store(s, rid, today(), rows)}
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
