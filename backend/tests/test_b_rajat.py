"""Track B: the Rajat setup (EXPLORATORY): signal, levels/skips and the X1/X2 paths."""

import numpy as np

from drishti.replay import rajat


def _A(rows, vol=None, vavg=100.0, atr=2.0):
    a = np.array(rows, dtype=float)
    n = len(a)
    return {"o": a[:, 0], "h": a[:, 1], "lo": a[:, 2], "c": a[:, 3],
            "v": np.full(n, 100.0) if vol is None else np.array(vol, float),
            "vavg": np.full(n, vavg), "atr": np.full(n, atr), "n": n,
            "ph": np.zeros(n, bool), "pl": np.zeros(n, bool),
            "shape": np.zeros(n, bool), "fall3": np.zeros(n, bool)}


LV = {"T1": 110.0, "R1_top": 111.0, "T2": 120.0, "R": 4.0, "q": 2500, "status": "OK"}


def _exit(rows, mode, vol=None, e=100.0, s=96.0, first=0, last=None):
    A = _A(rows, vol)
    return rajat.path(A, first, last if last is not None else len(rows) - 1, e, s, LV, mode,
                      False)


def test_x1_rejection_exits_at_the_close_of_the_rejection_bar():
    p = _exit([(101, 105, 100, 104), (104, 110.5, 103, 108)], "X1")
    assert p["fills"] == [(108.0, 1.0, 1, "R1_REJECT")] and p["hit_T1"]


def test_x1_confirmed_breakout_moves_stop_to_r1_and_targets_r2():
    rows = [(101, 112, 100, 111.5), (111.6, 114, 111, 113.5), (113.5, 121, 113, 119)]
    p = _exit(rows, "X1", vol=[100, 200, 100])
    assert p["fills"] == [(120.0, 1.0, 2, "T2")] and p["hit_T2"]
    rows2 = [(101, 112, 100, 111.5), (111.6, 114, 111, 113.5), (112, 112.5, 109, 110)]
    p2 = _exit(rows2, "X1", vol=[100, 200, 100])
    assert p2["fills"] == [(110.0, 1.0, 2, "STOP_MOVED")]                 # stop now = T1


def test_x1_unconfirmed_breakout_exits_at_first_close_below_r1():
    rows = [(101, 112, 100, 111.5), (111.6, 112, 110.5, 111), (111, 111.5, 108, 109)]
    p = _exit(rows, "X1", vol=[100, 100, 100])                           # no volume
    assert p["fills"] == [(109.0, 1.0, 2, "BACK_BELOW_R1")]


def test_x2_sells_half_at_t1_then_breakeven_stop():
    rows = [(101, 110.5, 100, 108), (108, 109, 99.5, 100.5)]
    p = _exit(rows, "X2")
    assert p["fills"] == [(110.0, 0.5, 0, "T1_HALF"), (100.0, 0.5, 1, "STOP_MOVED")]


def test_stop_first_and_time_exit():
    p = _exit([(101, 111, 95, 110)], "X1")
    assert p["fills"] == [(96.0, 1.0, 0, "SL")]
    p = _exit([(101, 103, 99, 102), (102, 104, 101, 103)], "X1")
    assert p["fills"] == [(103.0, 1.0, 1, "TIME")]
    assert _exit([(101, 103, 99, 102)], "X1", last=3) is None             # bars run out


def test_skip_rules_rr_and_cost():
    A = _A([(100, 101, 99, 100)] * 40)
    z = [{"price": 115, "bottom": 114.5, "top": 115.5, "touches": 2},
         {"price": 130, "bottom": 129.5, "top": 130.5, "touches": 2}]
    rajat.zones = lambda A_, d: z                                        # noqa: E731
    try:
        assert rajat.levels_for(A, 30, 100.0, 92.0, "daily")["status"] == "SKIP_RR"  # 14.5 < 16
        lv = rajat.levels_for(A, 30, 100.0, 95.0, "daily")               # 14.5 >= 2 x 5
        assert lv["status"] == "OK" and lv["T1"] == 114.5 and lv["T2"] == 129.5
        # R = 2 % of price: delivery STT + 10 bps slippage per side > 0.15 R
        assert rajat.levels_for(A, 30, 100.0, 98.0, "daily")["status"] == "SKIP_COST"
        assert rajat.levels_for(A, 30, 140.0, 138.0, "daily")["status"] == "NO_R1"
    finally:
        import importlib
        importlib.reload(rajat)


def test_confirmation_candle_needs_green_close_above_hammer_high_and_volume():
    rows = [(100, 101, 97, 100.8), (100.9, 102, 100.5, 101.5)]
    A = _A(rows, vol=[100, 160])
    A["shape"][0] = True
    A["fall3"][0] = True
    assert rajat.is_signal(A, 0, True)
    A["v"][1] = 140                                                      # < 1.5 x 100
    assert not rajat.is_signal(A, 0, True)
    A["v"][1] = 160
    A["c"][1] = 100.95                                                   # below hammer high
    assert not rajat.is_signal(A, 0, True)


def test_intraday_costs_have_no_dp_and_stt_on_the_sell_only():
    b, s = rajat.intraday_leg(100000, "buy"), rajat.intraday_leg(100000, "sell")
    assert round(s - b, 2) == round(25.0 - 3.0, 2)                      # STT 25 vs stamp 3
