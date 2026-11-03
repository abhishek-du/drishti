"""Track B: base-rate helpers (DESCRIPTIVE): confirmed swings, nearest swing, outcome, ADX."""

import numpy as np
import pandas as pd

from drishti.features import indicators
from drishti.replay import baserates as br


def test_swing_flags_are_strict_and_k_sided():
    h = np.array([1, 2, 3, 9, 3, 2, 1, 5, 5, 1, 0.0])
    f = br.swing_flags(h, 2)
    assert f.tolist() == [False, False, False, True, False, False, False, False, False, False,
                          False]                                   # 5,5 tie is not strict
    assert br.swing_flags(h, 3)[3]


def test_nearest_swing_uses_confirmed_pivots_only():
    h = np.array([1, 2, 3, 9, 3, 2, 1, 2, 3, 4.0])
    f = br.swing_flags(h, 2)
    assert np.isnan(br.nearest_swing(h, f, 2, 4, 5.0))           # pivot at 3 known from bar 5
    assert br.nearest_swing(h, f, 2, 5, 5.0) == 9.0
    assert np.isnan(br.nearest_swing(h, f, 2, 9, 9.5))           # nothing above 9.5


def test_outcome_stop_first_touch_break_and_mfe():
    h = np.array([10, 10.5, 12, 13.0])
    lo = np.array([9.5, 9.6, 10.8, 11.5])
    c = np.array([10, 10.2, 11.5, 12.5])
    k, i, broke, mfe, end = br.outcome(h, lo, c, 1, 3, 10.0, 9.0, 11.0)
    assert (k, i, broke) == ("TOUCH", 2, True) and mfe == 2.0 and end == 1.5
    k, i, broke, *_ = br.outcome(h, lo, c, 1, 3, 10.0, 9.0, 11.9)
    assert (k, broke) == ("TOUCH", False)                         # close 11.5 below the level
    lo2 = lo.copy()
    lo2[2] = 8.9
    assert br.outcome(h, lo2, c, 1, 3, 10.0, 9.0, 11.0)[0] == "STOP"   # stop first on the bar
    assert br.outcome(h, lo, c, 1, 3, 10.0, 9.0, 20.0)[0] == "TIME"


def test_adx_is_high_in_a_clean_trend_and_low_in_a_flat_range():
    n = 120
    up = pd.DataFrame({"high": np.arange(n) + 101.0, "low": np.arange(n) + 99.0,
                       "close": np.arange(n) + 100.5})
    flat = pd.DataFrame({"high": 101 + 0.5 * np.sin(np.arange(n)), "low": 99 - 0.5 *
                         np.sin(np.arange(n)), "close": 100 + 0.3 * np.sin(np.arange(n))})
    assert indicators.adx(up).iloc[-1] > 50 and indicators.adx(flat).iloc[-1] < 25
