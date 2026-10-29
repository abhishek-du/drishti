"""Track B sprint (EXPLORATORY): finalist rules and helper metrics."""

import pandas as pd

from drishti.replay import sprint


def test_streak_and_drawdown():
    assert sprint._streak(pd.Series([True, True, False, True, True, True, False])) == 3
    assert abs(sprint._mdd(pd.Series([0.1, -0.5, 0.2])) + 0.5) < 1e-12


def test_event_verdict_needs_placebo_margin_and_four_positive_years():
    yrs = {y: {"n": 10, "net_r_10": 0.1} for y in (2020, 2021, 2022, 2023)}
    m = {"n": 40, "net_r_10": 0.10, "by_year": yrs | {2024: {"n": 10, "net_r_10": -0.2}}}
    assert sprint.verdict_events(m, {"net_r_10": 0.04})["finalist_ok"]
    assert not sprint.verdict_events(m, {"net_r_10": 0.06})["finalist_ok"]      # < +0.05 R
    m2 = dict(m, by_year=m["by_year"] | {2023: {"n": 10, "net_r_10": -0.1}})
    assert not sprint.verdict_events(m2, {"net_r_10": 0.0})["finalist_ok"]      # 3 of 5


def test_c1_verdict_needs_both_benchmarks():
    yr = {y: {"months": 12, "ex_n500": 0.01, "ex_ew": 0.01} for y in sprint.YEARS}
    m = {"excess_n500": 0.02, "excess_ew": 0.01, "by_year": yr}
    assert sprint.verdict_c1(m)["finalist_ok"]
    yr2 = yr | {2021: {"months": 12, "ex_n500": 0.1, "ex_ew": -0.01},
                2022: {"months": 12, "ex_n500": -0.1, "ex_ew": 0.1}}
    assert not sprint.verdict_c1(dict(m, by_year=yr2))["finalist_ok"]
