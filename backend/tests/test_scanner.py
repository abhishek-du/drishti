"""Scanner + journal (decision support, PAPER ONLY): detectors, levels, sizing, grading, 15m
aggregation, notify fallback, journal API on drishti_test."""

import asyncio
import datetime as _dt
import json

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import text

from drishti.scanner import detect, grade, notify
from drishti.scanner.run import agg15


def _trend(n=300, seed=1):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(0.002 + 0.01 * rng.standard_normal(n)))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * 1.006
    lo = np.minimum(o, c) * 0.994
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c,
                         "volume": rng.integers(1e5, 2e5, n).astype(float)})


def test_breakout_52w_fires_once_with_levels_and_size():
    b = _trend()
    prior = b.high.iloc[-251:-1].max()
    b.loc[len(b) - 2, ["close", "high"]] = [prior * 0.98, prior * 0.985]   # t-1 below
    b.loc[len(b) - 1, ["open", "close", "high", "low"]] = [prior * 0.99, prior * 1.02,
                                                           prior * 1.025, prior * 0.985]
    hs = {h["detector"]: h for h in detect.hits(detect.prepare(b))}
    assert "BREAKOUT_52W" in hs
    h = hs["BREAKOUT_52W"]
    assert h["entry_ref"] == pytest.approx(prior * 1.025 * 1.0005)
    assert h["stop"] <= h["entry_ref"] - h["atr"] + 1e-9          # >= 1 ATR wide
    assert h["r1"] is None or h["r1"] > h["entry_ref"]
    z = detect.size(h, 1_000_000, 1.0)
    risk = z["entry_ref"] - z["stop"]
    assert z["qty"] * risk <= 10_000 + 1e-6 and (z["qty"] + 1) * risk > 10_000
    assert z["cost_r"] > 0


def test_hammer_confirm_and_no_hit_on_short_history():
    b = _trend(120, 3)
    t = len(b) - 1
    c = b.close.iloc[t - 6]
    for i, k in enumerate((t - 5, t - 4, t - 3, t - 2)):           # 4 lower closes first
        x = c * (1 - 0.01 * (i + 1))
        b.loc[k, ["open", "high", "low", "close"]] = [x * 1.005, x * 1.008, x * 0.997, x]
    c = b.close.iloc[t - 2]
    # a hammer on t-1 (long lower wick, small body near the high), then t closes above its high
    b.loc[t - 1, ["open", "high", "low", "close"]] = [c, c * 1.0015, c * 0.96, c * 1.001]
    b.loc[t, ["open", "high", "low", "close"]] = [c * 1.001, c * 1.02, c * 0.999, c * 1.015]
    df = detect.prepare(b)
    assert bool(df.hammer.iloc[t - 1])
    names = [h["detector"] for h in detect.hits(df)]
    assert "HAMMER_CONFIRM" in names
    h = [h for h in detect.hits(df) if h["detector"] == "HAMMER_CONFIRM"][0]
    assert h["stop"] < c * 0.96                                      # below the hammer low
    assert detect.hits(detect.prepare(b.iloc[-50:])) == []


def test_levels_r1_r2_are_the_next_zones_above_entry():
    zs = [{"bottom": 90, "top": 91, "price": 90.5, "touches": 3},
          {"bottom": 110, "top": 111, "price": 110.5, "touches": 2},
          {"bottom": 120, "top": 121, "price": 120.5, "touches": 2}]
    h = detect.levels_for("X", 100.0, 97.0, 2.0, zs)
    assert (h["r1"], h["r2"]) == (110, 120)
    assert h["stop"] == pytest.approx(min(97 - 0.2, 100.05 - 2))
    assert h["rr_r1"] == pytest.approx((110 - 100.05) / (100.05 - h["stop"]))


A = {"id": 7, "tf": "1d", "entry_ref": 100.0, "stop": 95.0, "r1": 110.0, "qty": 2000}


def _bars(rows):
    a = np.array(rows, float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]


def test_grade_alert_bases():
    # no fill: next bar never trades through the entry
    assert grade.grade(A, *_bars([[98, 99.9, 97, 99]]), "alert")["outcome"] == "NO_FILL"
    # fill then target
    r = grade.grade(A, *_bars([[99, 101, 98, 100], [100, 111, 99, 110]]), "alert")
    assert r["outcome"] == "R1" and r["entry_used"] == 100 and r["r_gross"] == pytest.approx(2)
    assert r["r_net"] < r["r_gross"]
    # gap above the entry fills at the open; stop first when both are hit in one bar
    r = grade.grade(A, *_bars([[102, 103, 101, 102], [101, 112, 94, 100]]), "alert")
    assert r["entry_used"] == 102 and r["outcome"] == "STOP"
    # timeout after 5 sessions at the close; fewer bars = OPEN
    flat = [[100, 101, 99, 100.5]] * 5
    assert grade.grade(A, *_bars(flat), "alert")["outcome"] == "TIMEOUT"
    assert grade.grade(A, *_bars(flat[:3]), "alert")["outcome"] == "OPEN"
    # took uses the user's entry; placebo is seeded and inside bar 0's range
    r = grade.grade(A, *_bars([[99, 101, 98, 100], [100, 111, 99, 110]]), "took", 99.5)
    assert r["entry_used"] == 99.5 and r["outcome"] == "R1"
    p1 = grade.grade(A, *_bars([[99, 101, 98, 100], [100, 111, 99, 110]]), "placebo")
    p2 = grade.grade(A, *_bars([[99, 101, 98, 100], [100, 111, 99, 110]]), "placebo")
    assert p1 == p2 and 98 <= p1["entry_used"] <= 101 and p1["seed"] == grade.SEED + 7


def test_agg15_buckets_from_0915_ist():
    t0 = pd.Timestamp("2026-11-03 09:15", tz="Asia/Kolkata").tz_convert("UTC")
    m = pd.DataFrame({"instrument_key": "K", "t": [t0 + pd.Timedelta(minutes=i) for i in range(31)],
                      "open": np.arange(31.0), "high": np.arange(31.0) + 1,
                      "low": np.arange(31.0) - 1, "close": np.arange(31.0) + 0.5,
                      "volume": 1.0})
    b = agg15(m)
    assert len(b) == 3 and list(b.t) == [t0, t0 + pd.Timedelta(minutes=15),
                                         t0 + pd.Timedelta(minutes=30)]
    first = b.iloc[0]
    assert (first.open, first.high, first.low, first.close, first.volume) == (0, 15, -1, 14.5, 15)


def test_notify_texts_use_the_actionable_target_and_honest_wording():
    import datetime as dt
    a = {"id": 37, "tf": "15m", "symbol": "MARINE", "instrument_key": "K",
         "detector": "BREAKOUT_52W", "session_date": dt.date(2026, 11, 5),
         "bar_time": dt.datetime(2026, 11, 5, 4, 45, tzinfo=dt.timezone.utc),
         "entry_ref": 428.21, "stop": 414.69, "r1": 429.69, "r2": None, "qty": 739,
         "context": {"regime": "OFF", "spec_gate": "OFF"}}
    t = notify.alert_text(a, None)
    assert "Target 455.25 2R (R1 not actionable)" in t and "broken R1" in t
    assert "Odds: pending (15m engine)" in t and "regime and spec gate OFF" in t
    assert notify.LABEL in t and "D5" in t and "Risk/share 13.52" in t
    o = {"outcome": "R1", "target_used": 455.25, "exit_price": 455.25, "r_net": 1.39,
         "exit_bar": dt.datetime(2026, 11, 5, 5, 45, tzinfo=dt.timezone.utc)}
    t = notify.outcome_text(a, o, None, "took at 428.50")
    assert "target 455.25 reached 11:15–11:30 IST bar (net R 1.39)" in t
    assert "Your marked trade (took at 428.50)" in t
    assert "predict" not in t.lower().replace("not a prediction", "")
    t = notify.outcome_text(a, {**o, "outcome": "TIMEOUT", "exit_price": 431.2, "r_net": -0.6},
                            None, None)
    assert "expired, neither hit, closed 431.20" in t and "(net R -0.60)" in t
    t = notify.outcome_text(a, {**o, "outcome": "STOP", "exit_price": 414.69, "r_net": -1.07},
                            None, None)
    assert "stop 414.69 hit" in t


def test_15m_horizon_is_the_rest_of_the_session():
    import datetime as dt
    from drishti.core.clock import IST
    assert grade.slots_left(dt.datetime(2026, 11, 5, 10, 0, tzinfo=IST)) == 20   # 10:15..15:00
    assert grade.slots_left(dt.datetime(2026, 11, 5, 14, 45, tzinfo=IST)) == 1   # 15:00 bar
    assert grade.slots_left(dt.datetime(2026, 11, 5, 15, 0, tzinfo=IST)) == 0    # flat by 15:20
    assert grade.slots_left(dt.datetime(2026, 11, 5, 15, 15, tzinfo=IST)) == 0
    a = {"id": 1, "tf": "15m", "entry_ref": 100.0, "stop": 95.0, "r1": 120.0, "r2": None,
         "qty": 2000}
    assert grade.grade(a, *_bars([[99, 101, 98, 100]]), "alert", hold=0)["outcome"] == "NO_FILL"
    r = grade.grade(a, *_bars([[99, 101, 98, 100], [100, 102, 99, 101]]), "alert", hold=2)
    assert r["outcome"] == "TIMEOUT" and r["exit_price"] == 101


def test_deliver_claims_once_and_logs_without_credentials():
    from drishti.core import db

    async def go():
        try:
            async with db.sessionmaker()() as s:
                ref = f"test:{dt_now()}"
                assert await notify.deliver(s, "SUMMARY", ref, "x") == "logged"
                assert await notify.deliver(s, "SUMMARY", ref, "x") == "dup"     # never twice
                st = (await s.execute(text("select status from notify_event where ref = :r"),
                                      {"r": ref})).scalar()
                assert st == "logged"
        finally:
            await db.engine().dispose()
            db.engine.cache_clear()

    def dt_now():
        import time
        return time.time_ns()
    asyncio.run(go())


def test_journal_api_and_stats_on_test_db():
    from fastapi import HTTPException

    from drishti.api import scan_api
    from drishti.core import db

    async def go():
        try:
            async with db.sessionmaker()() as s:
                rid = (await s.execute(text("""insert into run(run_id, kind, code_sha, params)
                    values (gen_random_uuid(), 'TEST_SCAN', 'test', '{}') returning run_id"""))
                ).scalar()
                bt = _dt.datetime.now(_dt.timezone.utc)
                aid = (await s.execute(text("""insert into scan_alert(run_id, session_date, tf,
                    bar_time, instrument_key, symbol, detector, version, entry_ref, stop, r1, r2,
                    rr_r1, qty, capital, risk_pct, cost_r, atr, context) values (:r, :d, '1d',
                    :bt, 'NSE_EQ|TESTSCAN', 'TST', 'BREAKOUT_52W', 'scan-v1', 100, 95, 110, 120,
                    2, 2000, 1000000, 1, 0.05, 4, '{}') returning id"""),
                    {"r": rid, "d": bt.date(), "bt": bt})).scalar()
                for basis, out, rn in (("alert", "R1", 1.9), ("placebo", "STOP", -1.05),
                                       ("took", "R1", 2.0)):
                    await s.execute(text("""insert into journal_outcome(alert_id, run_id, basis,
                        outcome, entry_used, r_gross, r_net, bars_used) values
                        (:a, :r, :b, :o, 100, :rn, :rn, 2)"""),
                        {"a": aid, "r": rid, "b": basis, "o": out, "rn": rn})
                await s.commit()
                with pytest.raises(Exception):     # outcomes are append-only
                    await s.execute(text("update journal_outcome set r_net = 0 where alert_id = :a"),
                                    {"a": aid})
                await s.rollback()
            with pytest.raises(HTTPException):
                await scan_api.journal(aid, scan_api.JournalIn(status="took"))   # needs entry
            with pytest.raises(HTTPException):
                await scan_api.journal(aid, scan_api.JournalIn(actual_entry=90))  # below stop
            r = await scan_api.journal(aid, scan_api.JournalIn(status="took", actual_entry=100.2,
                                                               actual_qty=1500))
            assert r["journal"]["status"] == "took"
            r = await scan_api.journal(aid, scan_api.JournalIn(notes="clean breakout"))
            assert r["journal"]["actual_entry"] == 100.2 and r["journal"]["notes"]
            d = await scan_api.alert(aid)
            assert d["label"] == detect.LABEL and len(d["journal_history"]) == 2
            assert set(d["grades"]) == {"alert", "placebo", "took"}
            assert d["alert"]["risk_rupees"] == 10000
            lst = await scan_api.alerts(date=bt.date())
            assert any(a["id"] == aid and a["status"] == "took" for a in lst["alerts"])
            st = await scan_api.stats(since=bt.date(), until=bt.date())
            b = st["by_detector"]["BREAKOUT_52W"]
            assert b["took"]["closed"] >= 1 and b["placebo"]["closed"] >= 1
            assert json.dumps(st)
        finally:
            await db.engine().dispose()
            db.engine.cache_clear()

    asyncio.run(go())


def test_mixed_15m_sources_give_a_datetime_column():
    import datetime as dt
    from drishti.scanner.run import utc_t
    pj = pd.DataFrame({"instrument_key": ["K"], "t": [dt.datetime(2026, 11, 2, 3, 45,
                                                                  tzinfo=dt.timezone.utc)]})
    own = pd.DataFrame({"instrument_key": ["K"], "t": [pd.Timestamp("2026-11-03 03:45",
                                                                    tz="UTC")]})
    out = utc_t(pd.concat([pj, own]))
    assert str(out.t.dtype) == "datetime64[ns, UTC]" or str(out.t.dtype).startswith("datetime64")
    assert out.t.dt.tz_convert("Asia/Kolkata").dt.hour.tolist() == [9, 9]


def test_eod_as_of_and_zone_dates_are_point_in_time():
    import datetime as dt
    from drishti.core.clock import IST
    from drishti.scanner.run import eod_as_of
    live = dt.datetime(2026, 11, 3, 19, 45, 5, tzinfo=IST)
    assert eod_as_of(dt.date(2026, 11, 3), live) == live            # the live run: now
    later = dt.datetime(2026, 11, 4, 12, 0, tzinfo=IST)
    assert eod_as_of(dt.date(2026, 11, 2), later) == dt.datetime(2026, 11, 2, 19, 45, tzinfo=IST)
    # future bars never change a hit: the frame ends at the detection bar
    b = _trend(320, 5)
    t = 300
    h1 = detect.hits(detect.prepare(b.iloc[:t + 1]))
    b2 = b.copy()
    b2.loc[t + 1:, ["high", "low", "close", "open"]] *= 3.0          # a wild future
    h2 = detect.hits(detect.prepare(b2.iloc[:t + 1]))
    assert h1 == h2
    for h in h1:
        for z in h["zones_used"].values():
            assert z["last"] + 5 <= t                                 # pivots confirmed by t


def test_low_rr_alert_moves_its_target_to_r2_or_2r():
    # JINDALPOLY 2026-11-02: entry 690.55, stop 653.16, R1 690.75 (0.2 above), R2 748.57
    ct = detect.choose_target(690.55, 653.16, 690.75, 748.57, 267)
    assert ct["low_rr"] and ct["net_rr_r1"] < 0 and ct["target_basis"] == "R2"
    assert ct["target"] == 748.57 and ct["net_rr_target"] >= 1
    ct = detect.choose_target(100.0, 95.0, 100.5, 102.0, 2000)        # both too close: 2R
    assert ct["target_basis"] == "2R" and ct["target"] == 110.0 and ct["low_rr"]
    ct = detect.choose_target(100.0, 95.0, 110.0, 120.0, 2000)        # a good R1 stays
    assert ct["target_basis"] == "R1" and not ct["low_rr"]
    ct = detect.choose_target(100.0, 95.0, None, None, 2000)          # no resistance: 2R
    assert ct["target_basis"] == "2R" and not ct["low_rr"]


def test_grade_uses_the_chosen_target():
    a = {"id": 9, "tf": "1d", "entry_ref": 100.0, "stop": 95.0, "r1": 100.5, "r2": None,
         "qty": 2000}
    r = grade.grade(a, *_bars([[99, 101, 98, 100], [100, 111, 99, 110]]), "alert")
    assert r["target_used"] == 110.0 and r["outcome"] == "R1"        # 2R, not R1 = 100.5


def test_odds_job_plan_and_data_cut_are_point_in_time():
    import datetime as dt
    from drishti.core.clock import IST
    from drishti.scanner import odds_job
    a = {"id": 8, "session_date": dt.date(2026, 11, 2), "instrument_key": "K", "symbol": "J",
         "entry_ref": 690.55, "stop": 653.16, "r1": 690.75, "r2": 748.57, "qty": 267,
         "context": {}, "status": "took", "actual_entry": 691.0, "actual_qty": 200}
    assert odds_job.data_cut(a) == dt.datetime(2026, 11, 2, 19, 45, tzinfo=IST)
    a2 = {**a, "context": {"as_of": "2026-11-03T19:45:05+05:30"}}
    assert odds_job.data_cut(a2).isoformat() == "2026-11-03T19:45:05+05:30"
    ps = odds_job.plans_for(a)
    assert [p.basis for p in ps] == ["alert", "took"] and all(p.target == 748.57 for p in ps)
    assert ps[0].as_of == dt.date(2026, 11, 3)                        # bars < as_of only
    assert odds_job.verdict_kind("Net R:R 1.1 needs ... Base rates LOSE on this") == \
        "BASE RATES LOSE"


def test_broken_r1_tag_and_reference_levels_kept():
    # MARINE 2026-11-04 15m: entry 428.21, stop 414.69, R1 429.69 (0.11 R gross, 0.14 R costs)
    ct = detect.choose_target(428.21, 414.69, 429.69, None, 739)
    assert ct["broken_r1"] and ct["tags"] == ["broken R1"] and ct["net_r_at_r1"] <= 0
    assert ct["target_basis"] == "2R" and ct["target"] == pytest.approx(428.21 + 2 * 13.52)
    # a net-positive but thin R1 is "low R:R", not broken
    ct = detect.choose_target(100.0, 95.0, 104.0, 112.0, 2000)
    assert ct["tags"] == ["low R:R"] and ct["net_r_at_r1"] > 0 and ct["target_basis"] == "R2"
    assert ct["cost_model"].startswith("costs drishti-swing-v1") and ct["stop_slip_r"] == 1.2


def test_15m_alerts_are_not_scored_by_the_daily_engine():
    from drishti.api import scan_api
    from drishti.scanner import odds_job
    assert scan_api.ODDS_15M == {"status": "odds pending: 15m engine"}
    assert odds_job.HORIZON == grade.HOLD["1d"] == 5


def test_15m_alerts_and_outcomes_go_out_as_digests_once():
    import datetime as dt
    import random
    from drishti.core import db

    async def go():
        try:
            async with db.sessionmaker()() as s:
                day = dt.date(2031, 1, 1) + dt.timedelta(days=random.randint(0, 3000))
                bt = dt.datetime.combine(day, dt.time(4, 45), dt.timezone.utc)
                rid = (await s.execute(text("""insert into run(run_id, kind, code_sha, params)
                    values (gen_random_uuid(), 'TEST_NOTIFY', 'test', '{}') returning run_id"""))
                ).scalar()
                ids = []
                for i, (key, sym) in enumerate((("NSE_EQ|TN1", "AAA"), ("NSE_EQ|TN2", "BBB"),
                                               ("NSE_EQ|TN3", "CCC"))):
                    ids.append((await s.execute(text("""insert into scan_alert(run_id,
                        session_date, tf, bar_time, instrument_key, symbol, detector, version,
                        entry_ref, stop, r1, r2, rr_r1, qty, capital, risk_pct, cost_r, atr,
                        context) values (:r, :d, '15m', :bt, :k, :sym, 'EMA20_PULLBACK',
                        'scan-v1', 100, 95, 100.3, null, 0.06, 2000, 1000000, 1, 0.1, 2, '{}')
                        returning id"""), {"r": rid, "d": day, "bt": bt, "k": key,
                                          "sym": sym})).scalar())
                await s.commit()
                o1 = await notify.deliver_alerts(s, "15m")
                assert o1 == {"digest_logged": 1}                     # one digest, not three
                assert await notify.deliver_alerts(s, "15m") == {}   # never again
                txt = (await s.execute(text("""select text from notify_event
                    where kind = 'DIGEST_15M' and ref like :r"""),
                    {"r": f"digest15:{day}%"})).scalar()
                assert "AAA EMA20_PULLBACK" in txt and "(2R)" in txt and "broken R1" in txt
                assert notify.LABEL in txt and "D5" in txt
                for i, k in zip(ids, ("R1", "STOP", "TIMEOUT")):
                    await s.execute(text("""insert into journal_outcome(alert_id, run_id, basis,
                        outcome, entry_used, exit_price, target_used, r_net, bars_used, source)
                        values (:a, :r, 'alert', :o, 100, 101, 110, :rn, 3, 'live')"""),
                        {"a": i, "r": rid, "o": k, "rn": {"R1": 1.8, "STOP": -1.1,
                                                         "TIMEOUT": -0.3}[k]})
                await s.commit()
                await notify.deliver_outcomes(s)                      # 15m: no singles
                singles = (await s.execute(text("""select count(*) from notify_event
                    where kind = 'OUTCOME' and ref = any(:r)"""),
                    {"r": [f"outcome:{i}" for i in ids]})).scalar()
                assert singles == 0
                assert await notify.deliver_outcomes_15m(s, day) == "logged"
                assert await notify.deliver_outcomes_15m(s, day) == "nothing to send"
                t = (await s.execute(text("""select text from notify_event
                    where kind = 'OUTCOME_15M' and ref = :r"""), {"r": f"outcome15:{day}"})
                    ).scalar()
                assert "Target reached 1 · stop hit 1 · expired 1" in t and "+0.40 R" in t
        finally:
            await db.engine().dispose()
            db.engine.cache_clear()

    asyncio.run(go())
