"""pf-v1 Phase 3 loop: sessions in order from the start date, a take fills on the next
session's official bar, a session is never processed twice, a missing bar stops the loop."""

import asyncio
import datetime as dt
import uuid

import pandas as pd
from sqlalchemy import text

from drishti.paper import loop, trader


def test_loop_orders_catches_up_and_is_idempotent(monkeypatch):
    from drishti.core import db
    tag = uuid.uuid4().hex[:6]
    books = {"main": f"l{tag}m", "placebo": f"l{tag}p", "obs_all": f"l{tag}a",
             "obs_placebo": f"l{tag}q"}
    kind = f"PF_T{tag}"
    d0, d1, d2 = dt.date(2033, 3, 7), dt.date(2033, 3, 8), dt.date(2033, 3, 9)   # Mon-Wed
    key = f"NSE_EQ|L{tag}"
    series = {d0: (99, 100, 98, 99.5), d1: (99, 101, 98, 100.5)}

    async def fake_official(ps, day, days=450, as_of=None, keys=None):
        # every requested key gets the series: earlier runs of this test (same dates) left
        # alerts that the ungated obs books take, and they need bars too
        rows = [dict(instrument_key=k, session_date=d, open=o, high=h, low=lo, close=c,
                     volume=1000) for k in (keys or [key])
                for d, (o, h, lo, c) in series.items() if d <= day]
        return pd.DataFrame(rows)

    monkeypatch.setattr(loop.bars, "official", fake_official)

    async def go():
        try:
            async with db.sessionmaker()() as s, db.prajna_sessionmaker()() as ps:
                for pid in books.values():
                    await s.execute(text("""insert into pf_portfolio(id, kind, label, version,
                        start_date, starting_capital, cash, equity, peak_equity, as_of) values
                        (:i, 'main', 'test', 'pf-v1', :d, 1000000, 1000000, 1000000, 1000000,
                        now())"""), {"i": pid, "d": d0})
                rid = (await s.execute(text("""insert into run(run_id, kind, code_sha, params)
                    values (gen_random_uuid(), 'TEST_PF3', 't', '{}') returning run_id"""))).scalar()
                a = dict((await s.execute(text("""insert into scan_alert(run_id, session_date,
                    tf, bar_time, instrument_key, symbol, detector, version, entry_ref, stop, r1,
                    r2, rr_r1, qty, capital, risk_pct, cost_r, atr, context) values (:r, :d, '1d',
                    :bt, :k, 'LOOPT', 'BREAKOUT_52W', 'scan-v1', 100, 95, null, null, null, 2000,
                    1000000, 1, 0.05, 3, '{}') returning *"""),
                    {"r": rid, "d": d0, "k": key,
                     "bt": dt.datetime(2033, 3, 7, 10, 0, tzinfo=dt.timezone.utc)})).mappings().one())
                await s.commit()
                ok_g = {"regime": {"pass": True, "code": None, "why": "on"},
                        "odds": {"pass": True, "code": None, "avg_r_net": 0.1, "replay_n": 500,
                                 "why": "odds pass"},
                        "results": {"pass": True, "code": None, "why": "none"}}
                await trader.decide_alert(s, a, ok_g, d0, books)        # the 19:45 decision
                await s.commit()
                out = await loop.run(s, ps, d1, telegram=False, books=books, kind=kind)
                assert list(out["sessions"]) == [str(d0), str(d1)], out
                assert out["sessions"][str(d1)]["fills"]["filled"] >= 1
                n = (await s.execute(text("""select count(*) from pf_position
                    where portfolio_id = :p"""), {"p": books["main"]})).scalar()
                assert n == 1
                again = await loop.run(s, ps, d1, telegram=False, books=books, kind=kind)
                assert again["sessions"] == {} and again["done_before"] == str(d1)
                nr = await loop.run(s, ps, d2, telegram=False, books=books, kind=kind)
                assert nr.get("not_ready") == str(d2) and nr["sessions"] == {}
                series[d2] = (101, 102, 100, 101.5)
                ok = await loop.run(s, ps, d2, telegram=False, books=books, kind=kind)
                assert list(ok["sessions"]) == [str(d2)]
                txt = await loop.summary_text(s, d2, ok["sessions"], books)
                assert "PAPER" in txt and "main: ₹" in txt and "1 open" in txt
        finally:
            await db.engine().dispose()
            db.engine.cache_clear()
            await db.prajna_engine().dispose()
            db.prajna_engine.cache_clear()

    asyncio.run(go())


def test_loop_does_nothing_before_the_forward_start():
    from drishti.core import db

    async def go():
        try:
            async with db.sessionmaker()() as s, db.prajna_sessionmaker()() as ps:
                out = await loop.run(s, ps, dt.date(2031, 3, 4), telegram=False,
                                     books={"main": "nonexistent_x"}, kind="PF_T_NONE")
                assert out == {"skipped": "portfolios not initialised (forward start not made)"}
        finally:
            await db.engine().dispose()
            db.engine.cache_clear()
            await db.prajna_engine().dispose()
            db.prajna_engine.cache_clear()

    asyncio.run(go())
