"""Track B: labels from the section 6 path, purged + embargoed walk-forward CV, calibration."""

import datetime as _dt

import numpy as np
import pandas as pd

from drishti.ml import cv_purged, labels, registry, train


def _cal(n=400):
    d, out = _dt.date(2021, 1, 1), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += _dt.timedelta(days=1)
    return out


def test_blocks_are_three_calendar_months():
    b = cv_purged.blocks(_dt.date(2021, 2, 10), _dt.date(2021, 12, 31))
    assert b[0] == (_dt.date(2021, 4, 1), _dt.date(2021, 6, 30))
    assert b[-1] == (_dt.date(2021, 10, 1), _dt.date(2021, 12, 31))


def test_purged_cv_has_no_train_label_window_overlapping_a_test_block_and_embargo_holds():
    cal = _cal()
    T = pd.Series(cal[:-5])
    T5 = pd.Series(cal[5:])
    fl = cv_purged.folds(T, T5, cal, _dt.date(2021, 4, 1), cal[-6])
    assert len(fl) >= 3
    for i, f in enumerate(fl):
        bs, be = f["block"]
        t1 = np.array(cal)[1:][f["train"]]                 # T+1 of the training rows
        t5 = T5.to_numpy()[f["train"]]
        assert not ((t1 <= be) & (t5 >= bs)).any()        # [T+1, T+5] never touches the block
        assert (T.to_numpy()[f["test"]] >= bs).all() and (T.to_numpy()[f["test"]] <= be).all()
        assert (T.to_numpy()[f["train"]] < bs).all()
        for _, pbe in (g["block"] for g in fl[:i]):        # 5 sessions after each earlier block
            emb = [d for d in cal if d > pbe][:5]
            assert not set(T.to_numpy()[f["train"]]) & set(emb)
    # the last rows before a block are purged: T + 5 lands inside it
    bs = fl[1]["block"][0]
    near = [k for k, d in enumerate(T) if d < bs and T5[k] >= bs]
    assert near and not set(near) & set(fl[1]["train"])


def test_labels_classes_and_weights():
    p = pd.DataFrame({"o_status": ["TP", "SL", "TIME", "TIME", "SKIP_RRR", "TP"],
                      "o_net_r_10": [1.8, -1.1, 0.3, -0.2, np.nan, 1.5],
                      "t5_date": [1, 1, 1, 1, 1, None]})
    lab = labels.labels(p)
    assert lab.cls.tolist() == [1, -1, 1, -1, 0, 0]
    assert lab.y.tolist() == [1, 0, 1, 0, 0, 0] and lab.closed.tolist()[-2:] == [False, False]
    assert np.allclose(lab.weight.to_numpy()[:4], [1.8, 1.1, 0.3, 0.2])


def test_walk_forward_isotonic_uses_only_earlier_blocks():
    rng = np.random.default_rng(0)
    n = 4000
    p = rng.random(n)
    y = (rng.random(n) < p).astype(int)
    fl = [{"test": np.arange(k * 1000, (k + 1) * 1000)} for k in range(4)]
    cal = train.walk_forward_isotonic(p, y, fl)
    assert np.isnan(cal[:1000]).all() and not np.isnan(cal[1000:]).any()
    y2 = y.copy()
    y2[3000:] = 1 - y2[3000:]                     # change the last block's outcomes only
    assert np.allclose(train.walk_forward_isotonic(p, y2, fl)[:3000][1000:],
                       cal[:3000][1000:], equal_nan=True)
    assert np.allclose(train.walk_forward_isotonic(p, y2, fl)[3000:], cal[3000:])


def test_p_star_proposal_needs_share_and_count():
    t = [{"p_star": 0.5, "confirmed": 500, "confirm_share": 0.5, "mean_net_r": 0.01},
         {"p_star": 0.6, "confirmed": 150, "confirm_share": 0.25, "mean_net_r": 0.05},
         {"p_star": 0.7, "confirmed": 50, "confirm_share": 0.05, "mean_net_r": 0.3}]
    assert registry.propose_p_star(t)["p_star"] == 0.6
    assert registry.propose_p_star(t[2:]) is None
