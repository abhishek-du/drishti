"""P1 data jobs (read-only market data; PAPER ONLY system).

    .venv/bin/python -m drishti.run.p1 holidays
    .venv/bin/python -m drishti.run.p1 indices [--from 2019-01-01]
    .venv/bin/python -m drishti.run.p1 manual
    .venv/bin/python -m drishti.run.p1 eod            # after 15:35 IST on a trading day
    .venv/bin/python -m drishti.run.p1 barcheck [--date 2026-10-28]   # T+1 after 09:00
    .venv/bin/python -m drishti.run.p1 master        # Upstox public NSE master (06:20)
    .venv/bin/python -m drishti.run.p1 daily1d [--to D] [--limit N]   # official 1d (P1c)
    .venv/bin/python -m drishti.run.p1 compare1d [--date D]           # vs Prajna (shadow)
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import sys

from drishti.broker.upstox.rest import Upstox
from drishti.core import runlog
from drishti.core.clock import IST, now, today
from drishti.core.config import BACKEND, get_settings
from drishti.core.db import prajna_sessionmaker, sessionmaker
from drishti.data import bar_check, daily1d, eod_quotes, holidays, indices, manual, master


async def trading_day(s, d: _dt.date) -> bool:
    """A regular NSE session (latest holidays fetch; short special sessions excluded, SC1)."""
    return holidays.is_session(d, await holidays.latest(s, d))


async def eod(s, rid, day: _dt.date, keys: list[str], rows: list[dict],
              index_rows: list[dict] = ()) -> dict:
    """Store the provisional bars of `day`: only quotes whose last trade is on `day`, and none at
    all when too few are (an unannounced holiday or a dead feed: SKIPPED_NO_SESSION). The index
    quotes become provisional index bars of `day` (index_bar, source UPSTOX_QUOTE), because the
    historical 1d of T is served only the next morning."""
    fresh, stale = eod_quotes.split_fresh(rows, day)
    ts = [r["quote_ts"] for r in rows if r["quote_ts"] is not None]
    out = {"keys": len(keys), "quotes": len(rows), "fresh": len(fresh), "stale": len(stale),
           "latest_trade": max(ts).astimezone(IST).isoformat() if ts else None}
    if not eod_quotes.session_seen(fresh, stale):
        out["skipped"] = "SKIPPED_NO_SESSION"
        await runlog.end(s, rid, "NOOP", out)
        return out
    out["stored"] = await eod_quotes.store(s, rid, day, fresh)
    ifresh, _ = eod_quotes.split_fresh(list(index_rows), day)
    out["index_stored"] = await indices.store(s, rid, [
        {"index_key": r["instrument_key"], "session_date": day,
         **{f: r[f] for f in ("open", "high", "low", "close")}} for r in ifresh], "UPSTOX_QUOTE")
    await runlog.end(s, rid, "OK", out)
    return out


async def barcheck(s, args: list[str]) -> dict:
    """Provisional vs official bar of T (default: the latest EOD session before today). The
    full report goes to var/reports/bar_check_<T>.json; the run row keeps the counts."""
    from sqlalchemy import text
    if "--date" in args:
        day = _dt.date.fromisoformat(args[args.index("--date") + 1])
    else:
        day = (await s.execute(text("""select max(r.session_date) from run r
            join run_end e using (run_id) where r.kind = 'P1_EOD' and e.status = 'OK'
            and r.session_date < :t"""), {"t": today()})).scalar()
    if day is None:
        return {"skipped": "no provisional session"}
    prov = await bar_check.provisional(s, day)
    if prov.empty:
        return {"session": str(day), "skipped": "no provisional bars"}
    async with prajna_sessionmaker()() as ps:
        hist = await bar_check.official_history(ps, day, list(prov.index))
    off = hist[hist.session_date == day].set_index("instrument_key")
    if off.empty:
        raise RuntimeError(f"official 1d bars of {day} not in Prajna yet")
    rep = bar_check.compare(prov, off, hist, day, bar_check.eligible_proxy(hist, off, day))
    path = BACKEND / "var" / "reports" / f"bar_check_{day}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rep, indent=1, default=str))
    return {k: v for k, v in rep.items() if k != "detail"} | {"report": str(path)}


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
                    out[k] = await indices.store(s, rid, [dict(r, index_key=k) for r in rows],
                                                 "UPSTOX_HIST")
            elif cmd == "manual":
                out = {"files": await manual.load_dir(s, rid, get_settings().DRISHTI_DATA_DIR
                                                      / "manual")}
            elif cmd == "eod":
                t = now()
                if t.hour * 60 + t.minute < 15 * 60 + 35:
                    raise SystemExit("REFUSED: eod quotes are taken after 15:35 IST")
                if not await trading_day(s, t.date()):
                    await runlog.end(s, rid, "NOOP", {"reason": "not a trading day"})
                    return {"skipped": "not a trading day"}
                async with prajna_sessionmaker()() as ps:
                    keys = await eod_quotes.universe_keys(ps)
                api = Upstox()
                return await eod(s, rid, t.date(), keys, eod_quotes.fetch(api, keys),
                                 eod_quotes.fetch(api, list(indices.INDEX_KEYS)))
            elif cmd == "master":
                raw = master.download()
                total, kept = master.parse(raw)
                out = await master.store(s, rid, raw, total, kept)
                async with prajna_sessionmaker()() as ps:
                    out["vs_prajna"] = await master.compare_with_prajna(s, ps)
            elif cmd == "daily1d":
                if not daily1d.a_rule_ok(now()):
                    raise SystemExit("REFUSED: decision A: historical API only 22:00-05:30 IST "
                                     "until the switch")
                to = _dt.date.fromisoformat(args[args.index("--to") + 1]) if "--to" in args \
                    else today()
                keys = await daily1d.universe(s)
                if "--limit" in args:
                    keys = keys[:int(args[args.index("--limit") + 1])]
                out = await daily1d.fetch_store(s, rid, Upstox(fraction=min(
                    0.5, get_settings().DRISHTI_UPSTOX_RATE_FRACTION)), keys, to)
            elif cmd == "compare1d":
                from sqlalchemy import text
                day = _dt.date.fromisoformat(args[args.index("--date") + 1]) if "--date" in args \
                    else (await s.execute(text("select max(session_date) from bar_1d"))).scalar()
                async with prajna_sessionmaker()() as ps:
                    out = await daily1d.compare_with_prajna(s, ps, day)
            elif cmd == "barcheck":
                out = await barcheck(s, args)
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
