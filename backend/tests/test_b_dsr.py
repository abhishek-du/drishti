"""Track B: Deflated Sharpe Ratio helper."""

import numpy as np

from drishti.judge.dsr import deflated_sharpe


def test_dsr_falls_with_more_trials_and_is_high_for_a_strong_series():
    r = np.random.default_rng(0).normal(0.02, 0.04, 120)        # monthly SR ~0.5
    one, many = deflated_sharpe(r, 1), deflated_sharpe(r, 100)
    assert one["sr0_period"] < 1e-9 + 0 or one["sr0_period"] <= many["sr0_period"]
    assert many["dsr"] < one["dsr"] and one["dsr"] > 0.99
    noise = np.random.default_rng(1).normal(0.0, 0.04, 60)
    assert deflated_sharpe(noise, 30)["dsr"] < 0.5
