"""D8: readers use the newest EOD snapshot of a session (16:10, the official close) that started
before as_of; after the market closes the page price is the REST quote, not the last tick."""
import asyncio
import datetime as dt
import json
import uuid

from sqlalchemy import text

from drishti.core.clock import IST


def test_provisional_takes_the_newest_snapshot_before_as_of():
    from drishti.core import db
    from drishti.data import bars
    u = uuid.uuid4()
    day = dt.date(2035, 1, 1) + dt.timedelta(days=u.int % 20000)   # a fresh session per run
    key = f"NSE_EQ|E{u.hex[:8]}"

    async def go():
        try:
            async with db.sessionmaker()() as s:
                for hh, mm, px in ((15, 40, 1985.9), (16, 10, 1980.6)):
                    st = dt.datetime.combine(day, dt.time(hh, mm), IST)
                    rid = (await s.execute(text("""insert into run(run_id, kind, session_date,
                        started_at, code_sha, params) values (gen_random_uuid(), 'P1_EOD', :d, :t,
                        't', '{}') returning run_id"""), {"d": day, "t": st})).scalar()
                    await s.execute(text("""insert into run_end(run_id, status, counts)
                        values (:r, 'OK', '{}')"""), {"r": rid})
                    await s.execute(text("""insert into eod_quote(run_id, instrument_key,
                        session_date, open, high, low, close, last_price, volume, quote_ts) values
                        (:r, :k, :d, 1949, 2025.6, 1927.6, :p, :p, 100, :q)"""),
                        {"r": rid, "k": key, "d": day, "p": px,
                         "q": dt.datetime.combine(day, dt.time(15, 29), IST)})
                await s.commit()
                p = await bars.provisional(s, day, dt.datetime.combine(day, dt.time(19, 45), IST))
                assert float(p[p.instrument_key == key].close.iloc[0]) == 1980.6
                p = await bars.provisional(s, day, dt.datetime.combine(day, dt.time(16, 0), IST))
                assert float(p[p.instrument_key == key].close.iloc[0]) == 1985.9   # PIT
        finally:
            await db.engine().dispose()
            db.engine.cache_clear()

    asyncio.run(go())


def test_after_close_the_quote_is_rest_not_the_last_tick(monkeypatch, tmp_path):
    from drishti.api import chart_data as C
    key = "NSE_EQ|INE0U3I01011"
    (tmp_path / "summary.json").write_text(json.dumps({"keys": {key: {"ltp": 1985.9}}}))
    monkeypatch.setattr(C, "LIVE_DIR", tmp_path)
    C._quote_cache.clear()
    import drishti.data.live_quote as LQ
    monkeypatch.setattr(LQ, "fetch_quote", lambda k: {"ltp": 1980.6, "open": 1949.0})
    monkeypatch.setattr(C, "_market_open", lambda t=None: False)
    assert C.get_live_quote(key)["ltp"] == 1980.6
    C._quote_cache.clear()
    monkeypatch.setattr(C, "_market_open", lambda t=None: True)
    assert C.get_live_quote(key)["ltp"] == 1985.9
    assert C._market_open.__name__  # noqa
    C._quote_cache.clear()


def test_market_open_window():
    from drishti.api.chart_data import _market_open
    f = lambda h, m, d=12: _market_open(dt.datetime(2026, 11, d, h, m, tzinfo=IST))  # noqa
    assert f(9, 15) and f(15, 34) and not f(15, 35) and not f(16, 10) and not f(9, 14)
    assert not f(11, 0, d=14)                                    # Saturday
