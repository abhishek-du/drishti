"""Time Machine odds (drishti.judge.odds): barrier rule, slip-through, net break-even, point in
time, seed, purge/embargo, calibration. Pure, no DB."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from drishti.judge import odds


def P(*rows):
    a = np.array(rows, float)          # rows of (o, h, l, c) for one path
    return a[None, :, 0], a[None, :, 1], a[None, :, 2], a[None, :, 3]


def test_barrier_cases():
    # stop and target on the same bar: stop first
    k, px = odds.barrier(*P((100, 111, 94, 100)), 95, 110)
    assert k[0] == -1 and px[0] == 95
    # gap through the stop: the open
    k, px = odds.barrier(*P((100, 101, 99, 100), (90, 92, 89, 91)), 95, 110)
    assert k[0] == -1 and px[0] == 90
    # gap above the target: the open
    k, px = odds.barrier(*P((100, 101, 99, 100), (115, 116, 114, 115)), 95, 110)
    assert k[0] == 1 and px[0] == 115
    # target touched
    k, px = odds.barrier(*P((100, 110.5, 99, 104)), 95, 110)
    assert k[0] == 1 and px[0] == 110
    # neither: the close of the last session
    k, px = odds.barrier(*P((100, 103, 97, 102), (102, 104, 99, 101)), 95, 110)
    assert k[0] == 0 and px[0] == 101


def test_slip_through_and_costs():
    k = np.array([-1, -1, 1])
    px = np.array([95.0, 90.0, 110.0])
    net, _ = odds.net_r(k, px, 100, 95, 200, 1.2, 0)
    assert net[0] < -1.2 and net[0] > -1.35         # -1.2 R plus costs, not -1 R
    assert net[1] < -2.0                            # a gap to 90 is -2 R, kept
    assert net[2] < 2.0 and net[2] > 1.85           # target net of costs


def bars(n=600, seed=1, start=dt.date(2022, 1, 3)):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.015, n)))
    o = c * np.exp(rng.normal(0, 0.004, n))
    h = np.maximum(o, c) * np.exp(np.abs(rng.normal(0, 0.008, n)))
    lo = np.minimum(o, c) * np.exp(-np.abs(rng.normal(0, 0.008, n)))
    d = pd.bdate_range(start, periods=n).date
    return pd.DataFrame({"session_date": d, "open": o, "high": h, "low": lo, "close": c})


def plan(b, k=500):
    e = float(b.close.iloc[k - 1])
    return odds.Plan("NSE_EQ|X", b.session_date.iloc[k], e, e * 0.96, e * 1.08, 5)


SMALL = odds.Params(n_paths=2000)


def test_point_in_time():
    b = bars()
    pl = plan(b)
    r1 = odds.odds(pl, b, SMALL)
    fut = b.copy()
    m = fut.session_date >= pl.as_of
    fut.loc[m, ["open", "high", "low", "close"]] *= 3.0     # rewrite the future
    r2 = odds.odds(pl, fut, SMALL)
    assert r1["mc"] == r2["mc"] and r1["replay"] == r2["replay"]
    assert r1["pool"]["last"] < str(pl.as_of)


def test_seed_reproducible():
    b = bars()
    pl = plan(b)
    a, c = odds.odds(pl, b, SMALL), odds.odds(pl, b, SMALL)
    assert a["mc"] == c["mc"] and a["seed"] == odds.SEED
    d = odds.odds(pl, b, odds.Params(n_paths=2000, seed=7))
    assert d["mc"] != a["mc"] and d["seed"] == 7


def test_net_breakeven_and_label():
    b = bars()
    r = odds.odds(plan(b), b, SMALL)
    assert r["net"]["breakeven_win_rate"] > 1 / 3          # gross 2:1 would be 33.3 %
    assert r["label"].startswith("Odds from the stock's own history")
    assert "NOT a prediction" in r["label"] and "Stationarity" in r["caveat"]
    m = r["mc"]
    assert abs(m["target_first"] + m["stop_first"] + m["neither"] - 1) < 1e-9
    assert m["p05_r_net"] <= m["median_r_net"] <= m["p95_r_net"]
    assert r["replay"]["n"] == sum(r["replay"]["counts"].values())


def test_replay_purge_embargo():
    b = bars(400)
    e0, o, h, lo, c, dates = odds.replay_paths(b, 5, 5)
    assert len(e0) == len(b) - 5 - 5
    # the last window ends 5 sessions before the last bar
    assert c[-1, -1] == b.close.iloc[len(b) - 1 - 5]


def test_bootstrap_blocks_contiguous():
    pool = odds.day_table(bars(300))
    o, h, lo, c = odds.bootstrap_paths(pool, 100.0, 5, 50, 1e12, np.random.default_rng(0))
    # no jumps: each path is a run of consecutive pool days, so the day-2 gap matches the pool
    g = np.log(o[:, 1] / c[:, 0])
    assert np.isin(np.round(g, 10), np.round(pool.g.to_numpy(), 10)).all()


def test_short_history_and_long_only():
    b = bars(100)
    r = odds.odds(plan(b, 90), b, SMALL)
    assert r["mc"] == {"n": 0} and r["verdict"].startswith("NO VERDICT")
    with pytest.raises(ValueError):
        odds.odds(odds.Plan("k", dt.date(2023, 1, 2), 100, 105, 110), bars(), SMALL)


def test_calibration():
    d = pd.DataFrame({"p_target_mc": [0.2, 0.3, 0.8, 0.5],
                      "realized": ["STOP", "R1", "R1", "OPEN"]})
    c = odds.calibration(d)
    assert c["n"] == 3 and abs(c["realized_target_rate"] - 2 / 3) < 1e-12
    assert odds.calibration(d.iloc[3:])["n"] == 0


def test_verdict_branches():
    mc = {"n": 1, "target_first": 0.9, "stop_first": 0.1, "neither": 0.0, "avg_r_net": -0.1}
    assert odds.verdict(mc, -0.05, 1 / 0.95).startswith("BROKEN GEOMETRY")
    mc2 = {**mc, "target_first": 0.02, "stop_first": 0.03, "neither": 0.95, "avg_r_net": 0.0}
    assert odds.verdict(mc2, 1.5, 0.4).startswith("MOSTLY NEITHER")
    mc3 = {**mc, "target_first": 0.25, "stop_first": 0.55, "neither": 0.2}
    assert "LOSE" in odds.verdict(mc3, 1.45, 1 / 2.45)
