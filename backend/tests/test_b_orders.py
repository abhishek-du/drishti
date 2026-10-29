"""Track B: the section 6 order/level/exit path and the EXPLORATORY close-confirm variant."""

import numpy as np
import pytest

from drishti.core import costs
from drishti.strategies import orders


def _arr(rows):
    a = np.array(rows, dtype=float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]


SIG = (99.0, 100.0, 98.0, 99.5)      # signal bar t = 0; ATR 2 -> trigger 100.05, stop 97.8


def test_levels_stop_is_the_wider_rule_and_target_capped_by_resistance():
    lv = orders.levels_at(100.0, 98.0, 2.0, [])
    assert lv["trigger"] == pytest.approx(100.05)
    assert lv["stop"] == pytest.approx(min(98.0 - 0.2, 100.05 - 2.0))
    assert lv["stop"] == pytest.approx(97.8)                    # low - 0.1 ATR is the wider
    assert lv["tp"] == pytest.approx(100.05 + 2 * 2.25) and lv["rrr"] == pytest.approx(2)
    z = [{"bottom": 103.5, "top": 104.5, "touches": 2}]
    assert orders.levels_at(100.0, 98.0, 2.0, z)["tp"] == pytest.approx(103.5)
    z = [{"bottom": 102.5, "top": 103.5, "touches": 2}]
    lv = orders.levels_at(100.0, 98.0, 2.0, z)
    assert lv["rrr"] < 1.5
    o, h, lo, c = _arr([SIG, (100, 105, 99.5, 104)])
    assert orders.spec_order(o, h, lo, c, 0, 2.0, z)["status"] == "SKIP_RRR"


def test_not_triggered_gap_fill_and_gap_chase():
    o, h, lo, c = _arr([SIG, (99.6, 100.0, 99.0, 99.8)])
    assert orders.spec_order(o, h, lo, c, 0, 2.0, [])["status"] == "NOT_TRIGGERED"
    o, h, lo, c = _arr([SIG, (101.2, 101.5, 100.5, 101)])
    assert orders.spec_order(o, h, lo, c, 0, 2.0, [])["status"] == "GAP_CHASE"   # > trig + 1.0
    rows = [SIG, (100.3, 106, 100.1, 105)] + [(105, 106, 104, 105)] * 4
    r = orders.spec_order(*_arr(rows), 0, 2.0, [])
    assert r["entry"] == pytest.approx(100.3) and r["status"] == "TP"            # gap fill
    r = orders.spec_order(*_arr([SIG, (100.8, 106, 100.5, 105)]), 0, 2.0, [])
    assert r["status"] == "CANCEL_RRR_AT_FILL"       # same S and TP, RRR 3.75 / 3 < 1.5


def test_cancel_rrr_at_fill():
    z = [{"bottom": 103.6, "top": 104, "touches": 2}]      # RRR at trigger 1.58; at 100.8 < 1.5
    r = orders.spec_order(*_arr([SIG, (100.8, 101, 100.5, 101)]), 0, 2.0, z)
    assert r["status"] == "CANCEL_RRR_AT_FILL"


def test_stop_first_on_the_same_bar_and_fill_day_stop():
    rows = [SIG, (99.9, 100.5, 99.5, 100.2), (100.2, 105, 97.0, 101)]
    r = orders.spec_order(*_arr(rows), 0, 2.0, [])
    assert r["status"] == "SL" and r["exit"] == pytest.approx(97.8)
    rows = [SIG, (99.9, 100.5, 97.7, 100.2)]
    r = orders.spec_order(*_arr(rows), 0, 2.0, [])
    assert r["status"] == "SL" and r["exit_i"] == 1


def test_gap_through_stop_and_gap_above_target_exit_at_open():
    rows = [SIG, (99.9, 100.5, 99.5, 100.2), (97.0, 98.0, 96.0, 97.5)]
    r = orders.spec_order(*_arr(rows), 0, 2.0, [])
    assert r["status"] == "SL" and r["exit"] == 97.0
    rows = [SIG, (99.9, 100.5, 99.5, 100.2), (106.0, 107, 105.5, 106.5)]
    r = orders.spec_order(*_arr(rows), 0, 2.0, [])
    assert r["status"] == "TP" and r["exit"] == 106.0


def test_time_out_at_session_five_close_and_open_when_bars_run_out():
    rows = [SIG] + [(100.0, 100.6, 99.5, 100.1 + 0.1 * i) for i in range(5)] + [(90, 90, 90, 90)]
    r = orders.spec_order(*_arr(rows), 0, 2.0, [])
    assert r["status"] == "TIME" and r["exit_i"] == 5 and r["exit"] == pytest.approx(100.5)
    r = orders.spec_order(*_arr(rows[:4]), 0, 2.0, [])
    assert r["status"] == "OPEN"


def test_net_r_and_qty():
    assert orders.qty(100.0, 98.0) == 5000 and orders.qty(100.0, 98.0, spike=True) == 2500
    n = orders.net(100.0, 104.0, 98.0, 5000)
    cost = costs.round_trip(100.0, 104.0, 5000, 10)
    assert n["net_r_10"] == pytest.approx(2 - cost / 5000 / 2)
    assert n["net_inr_10"] == pytest.approx(4 * 5000 - cost)
    assert n["net_r_5"] > n["net_r_10"] > n["net_r_20"]


def test_close_confirm_variant():
    rows = [SIG, (100, 100.4, 99.0, 100.2), (100.3, 101, 100, 100.6)] + \
        [(100.7, 101, 100.5, 100.8)] * 5
    r = orders.close_confirm_order(*_arr(rows), 0, 2.0, [])
    assert r["status"] == "TIME" and r["entry"] == 100.3 and r["fill_i"] == 2   # T+1 confirms
    assert r["tp"] == pytest.approx(100.3 + 2 * (100.3 - 97.8))
    rows2 = [SIG, (99.5, 99.8, 97.5, 99.6)]
    assert orders.close_confirm_order(*_arr(rows2), 0, 2.0, [])["status"] == \
        "STOP_BEFORE_CONFIRM"
    rows3 = [SIG, (99.5, 99.9, 99, 99.6), (99.6, 99.9, 99, 99.7), (99, 99, 99, 99)]
    assert orders.close_confirm_order(*_arr(rows3), 0, 2.0, [])["status"] == "NO_CONFIRM"
    rows4 = [SIG, (100, 100.4, 99.0, 100.2), (101.5, 102, 101, 101.8)]
    assert orders.close_confirm_order(*_arr(rows4), 0, 2.0, [])["status"] == "GAP_CHASE"


def test_placebo_uses_the_seeded_draw():
    rows = [SIG, (100, 102, 99, 101)] + [(101, 101.5, 100.5, 101)] * 5
    r = orders.placebo_order(*_arr(rows), 0, 2.0, [], 0.25)
    assert r["entry"] == pytest.approx(99.75)
    assert r["stop"] == pytest.approx(min(98 - 0.2, 99.75 - 2))
