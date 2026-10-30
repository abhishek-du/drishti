"""Track B: signal rules on hand-built rows, SC4 regime gate, VIX rule, results gate, and the
point-in-time property of the whole f1 pipeline (changing bars after T changes nothing on <= T).
"""

import datetime as _dt

import numpy as np
import pandas as pd

from drishti.features import candles, f1, indicators
from drishti.strategies import rules


def _row(**kw):
    base = {"instrument_key": "A", "session_date": _dt.date(2030, 1, 2), "n_prior": 300,
            "eligible": True, "rsi": 55.0, "spec_gate": True, "results_block": False,
            "vix_spike": False, "trend": 1, "rs_top": True, "brk_local": False,
            "brk_top": np.nan, "open": 100.2, "high": 101.0, "low": 99.6, "close": 100.8,
            "sma50": 95.0, "sma200": 90.0, "ema20": 100.0, "atr": 2.0, "volr20": 1.2,
            "at_support": False, "bullish_candle": False}
    base.update({f"{x}_0": False for x in candles.FLAGS})
    base.update(kw)
    return base


def _sig(rows):
    return rules.signals(pd.DataFrame(rows))


def test_s2_pullback_with_engulfing():
    s = _sig([_row(engulfing_bull_0=True)]).iloc[0]
    assert s.S2 and s.strategy == "S2" and not s.S1a and not s.S3
    assert not _sig([_row(engulfing_bull_0=True, low=100.6)]).iloc[0].S2   # no EMA20 touch
    assert not _sig([_row(engulfing_bull_0=True, close=98.9, open=98.5)]).iloc[0].S2


def test_regime_off_blocks_every_strategy_sc4():
    r = _row(engulfing_bull_0=True, hammer_0=True, at_support=True, brk_local=True,
             brk_top=100.0)
    on, off = _sig([r]).iloc[0], _sig([dict(r, spec_gate=False)]).iloc[0]
    assert on.signal and on.S1a and on.S2 and on.S3
    assert not off.signal and not any(off[s] for s in rules.STRATEGIES)


def test_common_gates_rsi_results_and_vix_spike_turns_s3_off():
    r = _row(hammer_0=True, at_support=True)
    assert _sig([r]).iloc[0].S3
    assert not _sig([dict(r, rsi=76)]).iloc[0].S3
    assert not _sig([dict(r, results_block=True)]).iloc[0].S3
    assert not _sig([dict(r, vix_spike=True)]).iloc[0].S3
    assert _sig([dict(r, vix_spike=True)]).iloc[0].S2            # only S3 is halted
    assert not _sig([dict(r, volr20=1.0)]).iloc[0].S3            # VolR20 > 1.0


def test_s1a_needs_rs_top_and_s1b_retest_two_to_ten_sessions_later():
    d = [_dt.date(2030, 1, 1) + _dt.timedelta(days=i) for i in range(8)]
    rows = [_row(session_date=d[i], n_prior=300 + i, close=103, low=102.5, high=103.5,
                 open=102.8) for i in range(8)]
    rows[0].update(brk_local=True, brk_top=101.0)
    rows[4].update(low=101.3, close=102.0, open=101.5, bullish_candle=True)  # retest, k = 4
    rows[1].update(low=101.2, close=101.8, open=101.3, bullish_candle=True)  # k = 1: too soon
    s = _sig(rows)
    assert s.S1a.tolist()[0] and not s.S1a.iloc[1:].any()
    assert s.S1b.tolist() == [False, False, False, False, True, False, False, False]
    assert s.brk_days.iloc[4] == 4 and s.brk_state.iloc[4] == 2
    rows[0].update(rs_top=False)
    assert not _sig(rows).S1b.any()                    # day B needs the S1a row incl. RS_top


def _universe(n_stocks=6, n=420, seed=7):
    r = np.random.default_rng(seed)
    days = [_dt.date(2030, 1, 1) + _dt.timedelta(days=i) for i in range(n)]
    out = []
    for s in range(n_stocks):
        c = 100 * np.exp(np.cumsum(0.001 + 0.02 * r.standard_normal(n)))
        o = c * (1 + 0.006 * r.standard_normal(n))
        h, lo = np.maximum(o, c) * (1 + 0.01 * r.random(n)), np.minimum(o, c) * (1 - 0.01 * r.random(n))
        out.append(pd.DataFrame({"instrument_key": f"NSE_EQ|INE{s:03d}", "session_date": days,
                                 "open": o, "high": h, "low": lo, "close": c,
                                 "volume": 3e6 * (1 + r.random(n))}))
    idx = pd.concat([pd.DataFrame({"index_key": "NSE_INDEX|Nifty 500", "session_date": days,
                                   "close": np.linspace(100, 150, n)}),
                     pd.DataFrame({"index_key": "NSE_INDEX|India VIX", "session_date": days,
                                   "close": 15.0})])
    return pd.concat(out, ignore_index=True), idx, days


def _pipeline(bars, idx, days, cutoff):
    reg = indicators.regime(idx)
    rows = f1.single_stock(bars, reg.vix_spike.to_dict(), cutoff)
    res = pd.DataFrame({"instrument_key": ["NSE_EQ|INE001"], "result_date": [days[380]]})
    return f1.cross_section(rows, reg, {f"INE{s:03d}" for s in range(6)}, res, {}, days)


def test_pit_changing_bars_after_t_changes_no_feature_or_signal_on_or_before_t():
    bars, idx, days = _universe()
    T = days[360]
    base = _pipeline(bars, idx, days, days[-1])
    rng = np.random.default_rng(99)
    alt = bars.copy()
    fut = alt.session_date > T
    k = rng.uniform(0.7, 1.3, fut.sum())
    for col in ("open", "high", "low", "close"):
        alt.loc[fut, col] *= k
    alt.loc[fut, "volume"] = alt.loc[fut, "volume"].sample(frac=1, random_state=1).to_numpy()
    shuf = _pipeline(alt, idx, days, days[-1])
    future_cols = [c for c in base.columns if c[:2] in ("o_", "p_", "c_")] + \
        ["t1_date", "t5_date"]
    a = base[base.session_date <= T].drop(columns=future_cols).reset_index(drop=True)
    b = shuf[shuf.session_date <= T].drop(columns=future_cols).reset_index(drop=True)
    assert len(a) > 500 and a.signal.sum() >= 0
    pd.testing.assert_frame_equal(a, b)


def test_results_gate_blocks_t_plus_1_to_t_plus_6():
    bars, idx, days = _universe()
    p = _pipeline(bars, idx, days, days[-1])
    a = p[p.instrument_key == "NSE_EQ|INE001"].set_index("session_date")
    blocked = a.index[a.results_block]
    assert list(blocked) == days[374:380]                    # result on 380: T = 374..379
    assert a.days_to_results.loc[days[374]] == 6
