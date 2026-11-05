"""Paper trader + risk manager (pf-v1 Phase 2): sizing and caps, the frozen odds gate, entry
fills, and the decide -> fill -> exit flow of the four books on drishti_test."""

import asyncio
import datetime as dt
import json
import uuid

import pytest
from sqlalchemy import text

from drishti.paper import gates, risk, trader

S0 = dict(equity=1_000_000, cash=1_000_000, open_positions=0, open_risk=0,
          day_open_equity=1_000_000, starting_capital=1_000_000)


def st(**kw):
    return risk.State(**{**S0, **kw})


def test_sizing_and_caps():
    r = risk.size(500, 490, st())                     # 1 % risk / 10 = 1000 sh; 20 % = 400 sh
    assert r["ok"] and r["qty"] == 400 and r["binding"] == "position_20pct"
    r = risk.size(500, 450, st())                     # 10,000 / 50 = 200 sh: risk binds
    assert r["qty"] == 200 and r["binding"] == "risk_1pct" and r["risk_rupees"] == 10_000
    r = risk.size(500, 450, st(open_risk=25_000))     # 3 % cap leaves 5,000 -> 100 sh
    assert r["qty"] == 100 and r["binding"] == "open_risk_3pct"
    r = risk.size(500, 450, st(cash=150_000))         # cash above the 10 % buffer: 50,000
    assert r["qty"] == 99 and r["binding"] == "cash_buffer_10pct"
    r = risk.size(500, 450, st(open_risk=30_000))
    assert not r["ok"] and r["reason_code"] == "SKIP_CAP_OPEN_RISK_3PCT"
    assert risk.size(500, 450, st(open_positions=5))["reason_code"] == "SKIP_MAX_POSITIONS"
    assert risk.size(500, 450, st(equity=979_000))["reason_code"] == "SKIP_DAILY_LOSS"
    assert risk.size(500, 450, st(equity=899_000, day_open_equity=899_000))["reason_code"] \
        == "SKIP_KILL_SWITCH"
    assert risk.size(500, 450, st(paused=True))["reason_code"] == "SKIP_KILL_SWITCH"
    assert risk.size(500, 500, st())["reason_code"] == "SKIP_BAD_LEVELS"


def test_odds_gate_is_the_frozen_definition():
    ok = {"mc_avg_r_net": 0.06, "replay_n": 30, "thin_sample": False, "mc_target_first": 0.01}
    assert gates.odds_check(ok)["pass"]                          # target-first is display only
    assert not gates.odds_check({**ok, "mc_avg_r_net": 0.049})["pass"]
    assert not gates.odds_check({**ok, "replay_n": 29})["pass"]
    assert not gates.odds_check({**ok, "thin_sample": True})["pass"]
    assert gates.odds_check(None)["code"] == "SKIP_ODDS_MISSING"


def test_entry_prices():
    assert trader.entry_price("main", 1, 100.0, (99, 101, 98, 100))[0] == 100.0
    assert trader.entry_price("main", 1, 100.0, (102, 104, 101, 103))[0] == 102   # gap
    assert trader.entry_price("main", 1, 100.0, (98, 99.9, 97, 99))[0] is None    # no trigger
    p1 = trader.entry_price("placebo", 7, 100.0, (99, 101, 98, 100))[0]
    assert p1 == trader.entry_price("placebo", 7, 100.0, (99, 101, 98, 100))[0]
    assert 98 <= p1 <= 101


def test_four_books_decide_fill_exit_and_caps():
    from drishti.core import db

    async def go():
        try:
            async with db.sessionmaker()() as s:
                tag = uuid.uuid4().hex[:6]
                books = {"main": f"t{tag}m", "placebo": f"t{tag}p", "obs_all": f"t{tag}a",
                         "obs_placebo": f"t{tag}q"}
                for pid in books.values():
                    await s.execute(text("""insert into pf_portfolio(id, kind, label, version,
                        start_date, starting_capital, cash, equity, peak_equity, as_of) values
                        (:i, 'main', 'test', 'pf-v1', '2026-11-20', 1000000, 1000000, 1000000,
                        1000000, now())"""), {"i": pid})
                rid = (await s.execute(text("""insert into run(run_id, kind, code_sha, params)
                    values (gen_random_uuid(), 'TEST_PF2', 't', '{}') returning run_id"""))).scalar()
                d0, d1, d2 = dt.date(2031, 3, 3), dt.date(2031, 3, 4), dt.date(2031, 3, 5)
                ok_g = {"regime": {"pass": True, "code": None, "why": "on"},
                        "odds": {"pass": True, "code": None, "avg_r_net": 0.1, "replay_n": 500,
                                 "why": "odds pass"},
                        "results": {"pass": True, "code": None, "why": "none"}}
                bad_g = {**ok_g, "odds": {"pass": False, "code": "SKIP_ODDS_FAIL",
                                          "why": "avg net R -0.10 < +0.05"}}
                alerts = []
                for i in range(7):
                    key = f"NSE_EQ|T{tag}{i}"
                    a = (await s.execute(text("""insert into scan_alert(run_id, session_date, tf,
                        bar_time, instrument_key, symbol, detector, version, entry_ref, stop, r1,
                        r2, rr_r1, qty, capital, risk_pct, cost_r, atr, context) values (:r, :d,
                        '1d', :bt, :k, :sym, 'BREAKOUT_52W', 'scan-v1', 100, 95, null, null, null,
                        2000, 1000000, 1, 0.05, 3, '{}') returning *"""),
                        {"r": rid, "d": d0, "bt": dt.datetime(2031, 3, 3, 10, 0,
                                                              tzinfo=dt.timezone.utc),
                         "k": key, "sym": f"S{i}"})).mappings().one()
                    alerts.append(dict(a))
                await s.commit()
                # alert 0 fails the odds gate: main/placebo skip, observational books take
                r0 = await trader.decide_alert(s, alerts[0], bad_g, d0, books)
                assert {r["portfolio"]: r["code"] for r in r0}[books["main"]] == "SKIP_ODDS_FAIL"
                assert {r["portfolio"]: r["take"] for r in r0}[books["obs_all"]]
                for a in alerts[1:]:
                    await trader.decide_alert(s, a, ok_g, d0, books)
                assert await trader.decide_alert(s, alerts[1], ok_g, d0, books) == []  # once
                dec = dict((await s.execute(text("""select reason_code, count(*) from pf_decision
                    where portfolio_id = :p group by 1"""), {"p": books["main"]})).all())
                # 1 % risk each: the 3 % open-risk cap allows 3 full-size entries (pending
                # entries count), so it binds before the 5-position cap
                assert dec == {"SKIP_ODDS_FAIL": 1, "ENTRY_BREAKOUT_52W_ODDS_PASS": 3,
                               "SKIP_CAP_OPEN_RISK_3PCT": 3}
                g = (await s.execute(text("""select gate_results from pf_decision
                    where portfolio_id = :p and decision = 'take' limit 1"""),
                    {"p": books["main"]})).scalar()
                assert g["risk"]["qty"] == 2000 and g["plan"]["target"] == 110.0     # 2R
                # T+1: alerts 1 and 2 trigger, 3 gaps up; obs books also hold 0 and 4..6
                bars = {alerts[i]["instrument_key"]: {d1: (99, 101, 98, 100.5)} for i in (0, 1, 2)}
                bars[alerts[3]["instrument_key"]] = {d1: (102, 103, 101, 102.5)}
                for i in (4, 5, 6):
                    bars[alerts[i]["instrument_key"]] = {d1: (98, 99.5, 97, 99)}
                f = await trader.fill_pending(s, d1, bars, books, {d0: d1})
                pos = dict((await s.execute(text("""select instrument_key, avg_price from
                    pf_position where portfolio_id = :p"""), {"p": books["main"]})).all())
                assert len(pos) == 3 and float(pos[alerts[3]["instrument_key"]]) == 102.0
                ob = (await s.execute(text("""select status, reason_code from pf_order
                    where portfolio_id = :p and instrument_key = :k"""),
                    {"p": books["obs_all"], "k": alerts[0]["instrument_key"]})).one()
                assert tuple(ob) == ("filled", "ENTRY_BREAKOUT_52W_OBS")
                assert f["filled"] >= 3
                # d2: alert 1 hits its 2R target, alert 2 its stop; regime still ON
                bars[alerts[1]["instrument_key"]][d2] = (105, 111, 104, 109)
                bars[alerts[2]["instrument_key"]][d2] = (97, 98, 94, 95)
                bars[alerts[3]["instrument_key"]][d2] = (101, 103, 100, 101)
                bars[alerts[0]["instrument_key"]][d2] = (101, 102, 100, 101)
                ex = await trader.manage_exits(s, d2, bars, books, regime_off_prev=False)
                tr = dict((await s.execute(text("""select instrument_key, reason_out from pf_trade
                    where portfolio_id = :p"""), {"p": books["main"]})).all())
                assert "target 110.00 reached" in tr[alerts[1]["instrument_key"]]
                assert "stop 95.00 hit" in tr[alerts[2]["instrument_key"]]
                r = (await s.execute(text("""select r_multiple, net_pnl from pf_trade
                    where portfolio_id = :p and instrument_key = :k"""),
                    {"p": books["main"], "k": alerts[1]["instrument_key"]})).one()
                assert 1.8 < float(r.r_multiple) < 2.0 and float(r.net_pnl) > 0   # net of costs
                assert ex["exits"]["EXIT_TARGET"] >= 1
                # regime OFF at d2's close: everything left exits at d3's open
                d3 = dt.date(2031, 3, 6)
                for k in (alerts[3]["instrument_key"], alerts[0]["instrument_key"]):
                    bars[k][d3] = (100.5, 101, 99, 100)
                await trader.manage_exits(s, d3, bars, books, regime_off_prev=True)
                left = (await s.execute(text("""select count(*) from pf_position
                    where portfolio_id = :p"""), {"p": books["main"]})).scalar()
                assert left == 0
                codes = dict((await s.execute(text("""select o.reason_code, count(*) from pf_order o
                    where o.portfolio_id = :p and o.side = 'SELL' group by 1"""),
                    {"p": books["main"]})).all())
                assert codes == {"EXIT_TARGET": 1, "EXIT_STOP": 1, "EXIT_REGIME_OFF": 1}
                p = (await s.execute(text("select cash, equity from pf_portfolio where id = :p"),
                                     {"p": books["main"]})).one()
                net = (await s.execute(text("""select sum(net_pnl) from pf_trade
                    where portfolio_id = :p"""), {"p": books["main"]})).scalar()
                assert float(p.cash) == pytest.approx(1_000_000 + float(net), abs=0.1)
                # placebo entries are seeded random prices inside the bar, not the buy-stop
                pl = (await s.execute(text("""select f.price from pf_fill f join pf_order o
                    on o.id = f.order_id where o.portfolio_id = :p and o.side = 'BUY'"""),
                    {"p": books["placebo"]})).scalars().all()
                assert pl and all(98 <= float(x) <= 103 for x in pl)
                # kill-switch pause: a recorded pause flag stops new entries
                await s.execute(text("""update pf_portfolio set meta = '{"paused": true}'
                    where id = :p"""), {"p": books["obs_all"]})
                await s.commit()
                a7 = dict((await s.execute(text("""insert into scan_alert(run_id, session_date,
                    tf, bar_time, instrument_key, symbol, detector, version, entry_ref, stop, r1,
                    r2, rr_r1, qty, capital, risk_pct, cost_r, atr, context) values (:r, :d,
                    '15m', :bt, :k, 'S7', 'EMA20_PULLBACK', 'scan-v1', 100, 98, null, null, null,
                    5000, 1000000, 1, 0.05, 1, '{}') returning *"""),
                    {"r": rid, "d": d3, "bt": dt.datetime(2031, 3, 6, 5, 0,
                                                          tzinfo=dt.timezone.utc),
                     "k": f"NSE_EQ|T{tag}7"})).mappings().one())
                await s.commit()
                res = await trader.decide_alert(s, {**a7, "tf": "1d"}, ok_g, d3,
                                                {"obs_all": books["obs_all"]})
                assert res[0]["code"] == "SKIP_KILL_SWITCH"
                did = await trader.observe_15m(s, a7, ok_g["regime"], books["main"], d3)
                ob = (await s.execute(text("select * from pf_decision where id = :i"),
                                      {"i": did})).mappings().one()
                assert ob["decision"] == "observe" and ob["reason_code"] == "OBSERVE_15M_ODDS_PENDING"
                assert ob["gate_results"]["plan"]["horizon"] == "MIS flat by 15:20"
                assert (await s.execute(text("""select count(*) from pf_order
                    where source_alert_id = :a"""), {"a": a7["id"]})).scalar() == 0
                await s.commit()
        finally:
            await db.engine().dispose()
            db.engine.cache_clear()

    asyncio.run(go())


def test_results_gate_unknown_without_a_fresh_board_csv_and_session_smoke():
    from drishti.core import db

    async def go():
        try:
            async with db.sessionmaker()() as s, db.prajna_sessionmaker()() as ps:
                day = dt.date(2031, 5, 5)
                v = await gates.results_veto(s, ps, "NSE_EQ|INE002A01018", "RELIANCE",
                                             [dt.date(2031, 5, 6), dt.date(2031, 5, 12)],
                                             dt.datetime(2031, 5, 5, 14, 15, tzinfo=dt.timezone.utc),
                                             day)
                assert v["code"] == "SKIP_RESULTS_UNKNOWN" and not v["pass"]
                assert (await gates.regime(s, day))["code"] == "SKIP_REGIME_UNKNOWN"
                out = await trader.run_session(s, ps, day, {}, {}, {}, [day],
                                               regime_off_prev=False)
                assert out["decisions"] == {"take": 0, "skip": 0, "observe": 0}
        finally:
            await db.engine().dispose()
            db.engine.cache_clear()
            await db.prajna_engine().dispose()
            db.prajna_engine.cache_clear()

    asyncio.run(go())
