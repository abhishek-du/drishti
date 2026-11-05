"""Paper portfolio pf-v1 Phase 1: costs wiring, the fill -> position -> trade ledger, cash
conservation, refusals, append-only logs, snapshot and the read API (drishti_test)."""

import asyncio
import datetime as dt
import uuid

import pytest
from sqlalchemy import text

from drishti.core import costs
from drishti.paper import ledger


def test_fill_costs_reuse_core_costs():
    c = ledger.fill_costs("BUY", "CNC", 500.0, 100)
    lg = costs.buy(50_000)
    assert c["stt"] == lg.stt and c["stamp"] == lg.stamp and c["brokerage"] == lg.brokerage
    assert c["slippage"] == 50.0                                  # 10 bps of 50,000
    assert c["total_costs"] == pytest.approx(lg.total + 50.0)
    assert c["net_cash"] == pytest.approx(-50_000 - c["total_costs"])
    s = ledger.fill_costs("SELL", "CNC", 500.0, 100)
    assert s["dp"] > 0 and s["net_cash"] == pytest.approx(50_000 - s["total_costs"])
    m = ledger.fill_costs("SELL", "MIS", 500.0, 100)
    assert m["stt"] == 12.5 and m["dp"] == 0 and m["stamp"] == 0       # 0.025 % sell side
    mb = ledger.fill_costs("BUY", "MIS", 500.0, 100)
    assert mb["stt"] == 0 and mb["stamp"] == 1.5 and mb["brokerage"] == 20.0
    assert "MIS" in mb["cost_model"] and "ASSUMED" in mb["cost_model"]


def test_ledger_flow_cash_conservation_and_refusals():
    from drishti.core import db

    async def go():
        try:
            async with db.sessionmaker()() as s:
                pid = "t" + uuid.uuid4().hex[:12]
                await s.execute(text("""insert into pf_portfolio(id, kind, label, version,
                    start_date, starting_capital, cash, equity, peak_equity, as_of) values
                    (:i, 'main', 'test', 'pf-v1', '2026-11-05', 1000000, 1000000, 1000000,
                    1000000, now())"""), {"i": pid})
                await s.commit()
                t0 = dt.datetime(2026, 11, 6, 9, 20, tzinfo=dt.timezone.utc)
                kw = dict(instrument_key="NSE_EQ|TPF", symbol="TPF", reason_code="TEST",
                          reason_text="test fill")
                a = await ledger.execute(s, pid, "swing", side="BUY", qty=100, price=500.0,
                                         ts=t0, stop=490.0, target=530.0, **kw)
                b = await ledger.execute(s, pid, "swing", side="BUY", qty=100, price=520.0,
                                         ts=t0 + dt.timedelta(days=1), **kw)
                pos = (await s.execute(text("""select qty, avg_price, entry_costs from pf_position
                    where portfolio_id = :i"""), {"i": pid})).one()
                assert pos.qty == 200 and float(pos.avg_price) == pytest.approx(510.0)
                ec = a["costs"]["total_costs"] + b["costs"]["total_costs"]
                assert float(pos.entry_costs) == pytest.approx(ec)
                m = await ledger.mark_to_market(s, pid, {"NSE_EQ|TPF": (530.0, t0)}, t0)
                assert m["holdings_value"] == pytest.approx(106_000)
                un = (await s.execute(text("""select unrealized_pnl from pf_position
                    where portfolio_id = :i"""), {"i": pid})).scalar()
                assert float(un) == pytest.approx(20 * 200 - ec)
                c = await ledger.execute(s, pid, "swing", side="SELL", qty=150, price=540.0,
                                         ts=t0 + dt.timedelta(days=2), session_count=3, **kw)
                tr = (await s.execute(text("select * from pf_trade where id = :t"),
                                      {"t": c["trade_id"]})).mappings().one()
                assert float(tr["gross_pnl"]) == pytest.approx(30 * 150)
                assert float(tr["costs"]) == pytest.approx(ec * 0.75 + c["costs"]["total_costs"],
                                                           abs=0.02)
                assert float(tr["r_multiple"]) == pytest.approx(float(tr["net_pnl"]) / (20 * 150), abs=1e-4)
                with pytest.raises(ledger.PaperError):                # no short selling
                    await ledger.execute(s, pid, "swing", side="SELL", qty=60, price=540.0,
                                         ts=t0 + dt.timedelta(days=2), **kw)
                with pytest.raises(ledger.PaperError):                # no cash
                    await ledger.execute(s, pid, "swing", side="BUY", qty=10_000, price=500.0,
                                         ts=t0 + dt.timedelta(days=2),
                                         **{**kw, "instrument_key": "NSE_EQ|TPG"})
                st = (await s.execute(text("""select status, count(*) from pf_order
                    where portfolio_id = :i group by 1"""), {"i": pid})).all()
                assert dict(st) == {"filled": 3, "rejected": 2}
                await ledger.execute(s, pid, "swing", side="SELL", qty=50, price=535.0,
                                     ts=t0 + dt.timedelta(days=3), **kw)
                assert (await s.execute(text("""select count(*) from pf_position
                    where portfolio_id = :i"""), {"i": pid})).scalar() == 0
                p = (await s.execute(text("select * from pf_portfolio where id = :i"),
                                     {"i": pid})).mappings().one()
                net = (await s.execute(text("""select sum(net_cash) from pf_fill
                    where portfolio_id = :i"""), {"i": pid})).scalar()
                assert float(p["cash"]) == pytest.approx(1_000_000 + float(net))
                assert float(p["equity"]) == pytest.approx(float(p["cash"]))
                tsum = (await s.execute(text("""select sum(net_pnl) from pf_trade
                    where portfolio_id = :i"""), {"i": pid})).scalar()
                assert float(p["cash"]) - 1_000_000 == pytest.approx(float(tsum), abs=0.05)
                ev = (await s.execute(text("""select event from pf_position_event
                    where portfolio_id = :i order by id"""), {"i": pid})).scalars().all()
                assert ev == ["OPEN", "ADD", "MARK", "REDUCE", "CLOSE"]
                with pytest.raises(Exception):                       # logs are append-only
                    await s.execute(text("update pf_fill set price = 1 where portfolio_id = :i"),
                                    {"i": pid})
                await s.rollback()
                await ledger.init_portfolios(s, dt.date(2026, 11, 5), 22769.0,
                                             dt.date(2026, 11, 4))
                snap = await ledger.snapshot(s, dt.date(2026, 11, 6), t0)
                assert pid in snap["equity"] and "main" in snap["equity"]
                from drishti.api import pf_api
                r = await pf_api.portfolios()
                assert r["label"] == ledger.LABEL and r["prereg"]["risk"]["max_open_positions"] == 5
        finally:
            await db.engine().dispose()
            db.engine.cache_clear()

    asyncio.run(go())
