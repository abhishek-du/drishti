"""Track B: the pre-P6 power-check projection and the EXPLORATORY label on every output."""

import datetime as _dt
import math

import numpy as np
import pandas as pd

from drishti.judge import power, scorecard
from drishti.replay.load import LABEL


def test_sessions_to_look():
    assert power.sessions_to_look(0.0, 0.3) == math.inf
    assert power.sessions_to_look(10.0, 0.3) == 40 + 5            # the 40-session floor
    assert power.sessions_to_look(1.0, 0.3) == 100 + 5            # 100 closed binds
    assert power.sessions_to_look(1.0, 0.2) == 150 + 5            # 30 confirmed binds


def _panel():
    d = [_dt.date(2030, 1, 1) + _dt.timedelta(days=i) for i in range(10)]
    rows = []
    for i, day in enumerate(d):
        for k in range(2):
            sig = i < 5 and k == 0
            rows.append({"session_date": day, "signal": sig, "spec_gate": i < 5,
                         "rs_score": 1.0, "S1a": sig, "S1b": False, "S2": False, "S3": False,
                         "o_status": "TP" if sig else "SL", "p_status": "SL", "c_status": "SL",
                         "vix_spike": False, "vix_normal": True, "sector": "X",
                         **{f"{v}_{c}": 1.0 for v in "opc" for c in
                            ("entry", "stop", "trigger", "net_r_5", "net_r_10", "net_r_20",
                             "net_inr_5", "net_inr_10", "net_inr_20")}})
    reg = pd.DataFrame({"spec_gate": [i < 5 for i in range(10)], "n500": 1.0,
                        "n500_sma200": 1.0}, index=d)
    reg.loc[d[-1], ["n500", "n500_sma200", "spec_gate"]] = [np.nan, np.nan, False]  # no bar yet
    return pd.DataFrame(rows), reg, d


def test_power_build_flags_when_current_regime_is_off():
    panel, reg, d = _panel()
    pw = power.build(panel, reg, d, d[-1])
    assert pw["label"] == LABEL and pw["share_on"] == 0.5
    assert pw["events_per_session"]["spec_gate_ON"] == 1.0
    # the last 20 sessions with an index bar = the first 9 (5 ON)
    assert pw["projections"]["current"]["on_share"] == 5 / 9 and pw["current_window"][1] == str(d[8])
    # 20 more sessions after C with the gate OFF: the current share is 0, so the flag fires
    later = d + [d[-1] + _dt.timedelta(days=i + 1) for i in range(20)]
    reg3 = pd.concat([reg, pd.DataFrame({"spec_gate": False, "n500": 1.0, "n500_sma200": 2.0},
                                        index=later[10:])])
    pw3 = power.build(panel, reg3, later, d[-1])
    assert pw3["projections"]["current"]["on_share"] == 0.0
    assert pw3["flag"] == "FLAG_CURRENT_REGIME_OVER_120"


def test_every_output_carries_the_label():
    panel, reg, d = _panel()
    m = {"cutoff": "x", "data_end": "y", "git_sha": "abcdef0", "draft": True, "notes": []}
    sc = scorecard.build(panel)
    assert sc["label"] == LABEL and LABEL in scorecard.markdown(sc, m)
    pw = power.build(panel, reg, d, d[-1])
    assert LABEL in power.markdown(pw, m)
    assert np.isclose(sc["set_b_events"]["rules_only"]["hit_rate"], 1.0)
