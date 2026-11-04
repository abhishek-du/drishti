"""Live Radar (radar-v1): Scout parity of the criteria, the point-in-time partial bar, the
recorder's first-tick day OHLC."""

import datetime as _dt
import json

import numpy as np
import pandas as pd
import pytest

from drishti.core.clock import IST
from drishti.live import recorder
from drishti.live.proto import MarketDataFeed_pb2 as pb
from drishti.scanner import radar
from drishti.scout import screener_b as sb


def _universe(n_stocks=40, n=320, seed=3):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2025-07-01", periods=n).date
    out = []
    for i in range(n_stocks):
        drift = rng.normal(0.0008, 0.0015)
        c = 200 * np.exp(np.cumsum(drift + 0.018 * rng.standard_normal(n)))
        o = np.r_[c[0], c[:-1]]
        out.append(pd.DataFrame({"instrument_key": f"NSE_EQ|INE{i:06d}01", "session_date": days,
                                 "open": o, "high": np.maximum(o, c) * 1.01,
                                 "low": np.minimum(o, c) * 0.99, "close": c,
                                 "volume": 2e6 + rng.integers(0, 1e6, n)}))
    return pd.concat(out, ignore_index=True), days[-1]


def test_criteria_match_scout_screen_on_the_same_bars():
    b, day = _universe()
    st = sb.per_stock(b)
    eq = {k.split("|")[1] for k in st.index}
    reg = {"nifty500_ret63": 0.0}
    listed, _ = sb.screen(st, eq, set(), reg, day, {})
    c = radar.criteria(st, set(st.index), reg, {})
    assert set(listed.index) == set(c.index[c["all"]])
    assert len(listed) > 0                                   # the test is not vacuous


def test_partial_bar_uses_only_minutes_closed_by_the_bar_end():
    day = _dt.date(2026, 11, 4)
    t0 = pd.Timestamp("2026-11-04 09:21", tz="Asia/Kolkata").tz_convert("UTC")
    m1 = pd.DataFrame({"instrument_key": "K", "t": [t0 + pd.Timedelta(minutes=i) for i in range(30)],
                       "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10})
    m1.loc[m1.index[-1], ["high", "close"]] = [150.0, 149.0]     # 09:50: after the bar end
    end = pd.Timestamp("2026-11-04 09:45", tz="Asia/Kolkata").tz_convert("UTC")
    seen = pd.Timestamp("2026-11-04 09:21:01", tz="Asia/Kolkata").timestamp()
    d0 = {"K": {"date": str(day), "open": 98.0, "high": 103.0, "low": 97.0, "close": 100,
                "volume": 5000, "seen_at": seen}}
    p = radar.partial_bars(m1, d0, end, day).set_index("instrument_key").loc["K"]
    assert (p.open, p.high, p.low, p.close) == (98.0, 103.0, 97.0, 100.5)
    assert p.volume == 5000 + 24 * 10                            # 09:21 .. 09:44
    late = {"K": {**d0["K"], "seen_at": end.timestamp() + 60}}   # first tick after the bar end
    q = radar.partial_bars(m1, late, end, day).set_index("instrument_key").loc["K"]
    assert (q.open, q.high, q.volume) == (100.0, 101.0, 240.0)


def test_recorder_keeps_the_first_tick_day_ohlc_and_live_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(recorder, "LIVE_DIR", tmp_path)
    monkeypatch.setattr(recorder, "HEALTH", tmp_path / "h.json")
    monkeypatch.setenv("DRISHTI_DATA_DIR", str(tmp_path / "data"))   # never the real ticks dir
    from drishti.core import config
    config.get_settings.cache_clear()
    rec = recorder.Recorder(["NSE_EQ|K", "NSE_EQ|R"], _dt.datetime(2099, 1, 1, tzinfo=IST),
                            live_keys={"NSE_EQ|K"})

    def frame(key, ltp, high):
        r = pb.FeedResponse()
        ff = r.feeds[key].fullFeed.marketFF
        ff.ltpc.ltp, ff.ltpc.ltt, ff.ltpc.cp = ltp, int(_dt.datetime.now().timestamp() * 1000), 99
        ff.vtt = 1000
        o = ff.marketOHLC.ohlc.add()
        o.interval, o.open, o.high, o.low, o.close, o.vol = "1d", 100, high, 95, ltp, 1000
        return r.SerializeToString()

    rec.on_frame(frame("NSE_EQ|K", 101, 105))
    rec.on_frame(frame("NSE_EQ|R", 50, 55))
    rec.on_frame(frame("NSE_EQ|K", 110, 112))            # later ticks never move day0
    rec.snapshot()
    d0 = json.loads((tmp_path / "day0.json").read_text())
    assert d0["NSE_EQ|K"]["high"] == 105 and d0["NSE_EQ|R"]["open"] == 100
    assert [p.name for p in (tmp_path / "c1m").iterdir()] == ["NSE_EQ%7CK.json"]
    s = json.loads((tmp_path / "summary.json").read_text())
    assert set(s["keys"]) == {"NSE_EQ|K"}
    rec.ticks.close()
    assert str(rec.ticks.root).startswith(str(tmp_path))
    config.get_settings.cache_clear()
