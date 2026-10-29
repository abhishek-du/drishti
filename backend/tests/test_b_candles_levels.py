"""Track B: candle flags on the spec section 4 shapes, S/R zones, trend and regime."""

import datetime as _dt

import numpy as np
import pandas as pd

from drishti.features import candles, indicators, levels


def _bars(rows, atr=2.0):
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    return df.assign(atr=atr)


def _flags(rows, atr=2.0, **kw):
    return candles.flags(_bars(rows, atr), **kw).iloc[-1]


DECLINE = [(110, 111, 107, 108), (108, 109, 105, 106), (106, 107, 103, 104), (104, 105, 101, 102)]


def test_hammer_after_three_lower_closes_and_not_without_them():
    hammer = (100.5, 101.0, 97.0, 100.9)          # B 0.4, Lw 3.5, Uw 0.1, body in the top third
    assert _flags(DECLINE + [hammer]).hammer
    flat = [(100, 101, 99, 100.2)] * 4
    assert not _flags(flat + [hammer]).hammer
    sup = pd.Series([False] * 4 + [True])
    assert _flags(flat + [hammer], at_support=sup).hammer        # "or at support"
    rise = [(90, 91, 89, 90.5), (91, 92, 90, 91.5), (92, 93, 91, 92.5), (93, 94, 92, 93.5)]
    f = _flags(rise + [hammer])
    assert f.hanging_man and not f.hammer


def test_engulfing_bull_and_bear():
    f = _flags([(100, 101, 97, 98), (97.5, 101.5, 97, 101)])
    assert f.engulfing_bull and not f.engulfing_bear
    assert _flags([(98, 101, 97, 100), (100.5, 101, 97, 97.5)]).engulfing_bear


def test_morning_star():
    rows = [(110, 110.5, 103.5, 104), (102, 102.6, 101, 102.3), (102.5, 108.5, 102.4, 108)]
    f = _flags(rows, atr=3.0)
    assert f.morning_star and not f.evening_star


def test_doji_variants():
    assert _flags([(100, 102, 98, 100.1)]).doji
    assert _flags([(100, 100.1, 96, 100.05)]).dragonfly_doji
    assert _flags([(100, 104, 99.95, 100.02)]).gravestone_doji


def test_bullish_candle_top_40_percent():
    b = _bars([(100, 101, 99, 100.5), (100, 110, 100, 106.5), (100, 110, 100, 105)])
    out = candles.bullish_candle(b, candles.flags(b))
    assert out.tolist() == [True, True, False]


def _series_with_double_top(n=120, top=110.0):
    """Flat 100 +- 1 with two clean pivot highs at `top` 30 sessions apart and one pivot low."""
    h, lo = np.full(n, 101.0), np.full(n, 99.0)
    for i in range(n):
        h[i] += 0.01 * (i % 3)
        lo[i] -= 0.01 * (i % 3)
    h[40], h[70] = top, top + 0.2
    lo[55] = 95.0
    return h, lo


def test_zones_need_two_pivots_ten_sessions_apart():
    h, lo = _series_with_double_top()
    z = [x for x in levels.zones(h, lo, atr=2.0) if x["price"] > 105]
    assert len(z) == 1 and z[0]["touches"] == 2
    assert abs(z[0]["price"] - 110.1) < 1e-9 and abs(z[0]["top"] - 110.6) < 1e-9
    h2 = h.copy()
    h2[70] = 101.0
    h2[47] = 110.2                                   # 7 sessions apart: no zone
    assert not [x for x in levels.zones(h2, lo, atr=2.0) if x["price"] > 105]


def test_zones_at_equals_zones_on_the_prefix():
    r = np.random.default_rng(5)
    c = 100 + np.cumsum(r.normal(0, 1, 400))
    h, lo = c + r.random(400), c - r.random(400)
    ph, pl = levels.pivot_flags(h, lo)
    for t in (30, 120, 260, 399):
        assert levels.zones_at(h, lo, ph, pl, t, 1.5) == levels.zones(h[:t + 1], lo[:t + 1], 1.5)


def test_nearest_resistance_support_and_at_support():
    zs = [{"price": 90, "bottom": 89, "top": 91, "touches": 2},
          {"price": 100, "bottom": 99, "top": 101, "touches": 3},
          {"price": 120, "bottom": 119, "top": 121, "touches": 2}]
    assert levels.resistance_above(zs, 100)["price"] == 120     # band bottom > x
    assert levels.resistance_above(zs, 98)["price"] == 100
    assert levels.support_below(zs, 100)["price"] == 90         # band top < x
    assert levels.at_support(zs, 98.6, atr=2.0)["price"] == 100  # <= 0.25 ATR below the band
    assert levels.at_support(zs, 98.4, atr=2.0) is None
    assert levels.resistance_above(zs, 130) is None


def test_trend_class():
    t = indicators.trend_class(np.array([110, 90, 105]), np.array([105, 95, 100]),
                               np.array([100, 100, 101]), np.array([12, 8, 12]),
                               np.array([11, 9, 11]), np.array([10, 6, 9]),
                               np.array([9, 7, 10]))
    assert t.tolist() == [1, -1, 0]


def test_regime_spec_gate_and_vix_states():
    d = [_dt.date(2030, 1, 1) + _dt.timedelta(days=i) for i in range(230)]
    n5 = np.r_[np.full(200, 100.0), np.full(30, 120.0)]
    n5[-1] = 90.0
    vix = np.full(230, 15.0)
    vix[-3], vix[-2] = 26.0, 22.0
    idx = pd.concat([pd.DataFrame({"index_key": "NSE_INDEX|Nifty 500", "session_date": d,
                                   "close": n5}),
                     pd.DataFrame({"index_key": "NSE_INDEX|India VIX", "session_date": d,
                                   "close": vix})])
    r = indicators.regime(idx)
    assert not r.spec_gate.iloc[198] and r.spec_gate.iloc[-2] and not r.spec_gate.iloc[-1]
    assert r.vix_spike.iloc[-3]                            # >= 25
    assert r.vix_spike.iloc[-2]                            # 22 vs 15: +46 % in 5 sessions
    assert not r.vix_normal.iloc[-2] and r.vix_normal.iloc[-10]


def _one(seed=11, n=400):
    r = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(0.001 + 0.015 * r.standard_normal(n)))
    o = c * (1 + 0.004 * r.standard_normal(n))
    d = [_dt.date(2030, 1, 1) + _dt.timedelta(days=i) for i in range(n)]
    return pd.DataFrame({"session_date": d, "open": o, "high": np.maximum(o, c) * 1.01,
                         "low": np.minimum(o, c) * 0.99, "close": c,
                         "volume": 1e6 * (1 + r.random(n))})


def test_track_c_interfaces_bollinger_macd_vwap():
    df = _one()
    bb = indicators.bollinger(df)
    assert list(bb.columns) == ["bb_mid", "bb_up", "bb_lo"]
    w = df.close.iloc[-20:]
    assert np.isclose(bb.bb_mid.iloc[-1], w.mean())
    assert np.isclose(bb.bb_up.iloc[-1] - bb.bb_mid.iloc[-1], 2 * w.std(ddof=0))
    m = indicators.macd(df)
    assert list(m.columns) == ["macd", "macd_signal", "macd_hist"]
    e = lambda s, n: s.ewm(span=n, adjust=False, min_periods=n).mean()  # noqa: E731
    assert np.isclose(m.macd.iloc[-1], (e(df.close, 12) - e(df.close, 26)).iloc[-1])
    assert np.allclose(m.macd_hist.dropna(), (m.macd - m.macd_signal).dropna())
    v = indicators.vwap(df)                                # one bar per session: typical price
    assert np.allclose(v, (df.high + df.low + df.close) / 3)
    intraday = pd.DataFrame({"session_date": [1, 1, 2], "high": [11, 13, 20.0],
                             "low": [9, 11, 18.0], "close": [10, 12, 19.0],
                             "volume": [100, 300, 50.0]})
    assert np.allclose(indicators.vwap(intraday), [10, 11.5, 19])


def test_track_c_trend_and_nearest_match_the_f1_columns():
    df = _one(n=420)
    ind = indicators.basic(df)
    h, lo = df.high.to_numpy(), df.low.to_numpy()
    ph, pl = levels.pivot_flags(h, lo)
    a1, a2, _ = levels.last_two_pivots(h, ph)
    b1, b2, _ = levels.last_two_pivots(lo, pl)
    col = indicators.trend_class(df.close.to_numpy(), ind.sma50.to_numpy(), ind.sma200.to_numpy(),
                                 a1, a2, b1, b2)
    name = {1: "UP", -1: "DOWN", 0: "SIDE"}
    for t in (250, 330, 419):
        assert levels.trend(df.iloc[:t + 1], ind.iloc[:t + 1]) == name[int(col[t])]
    zs = [{"price": 90, "bottom": 89, "top": 91}, {"price": 100, "bottom": 99, "top": 101}]
    s, r = levels.nearest(zs, 95)
    assert s["price"] == 90 and r["price"] == 100
    assert levels.nearest(zs, 100) == (zs[0], None)
