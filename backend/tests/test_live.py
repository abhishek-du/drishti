import datetime as _dt

from drishti.core.clock import IST
from drishti.live import candles, recorder, store
from drishti.live.proto import MarketDataFeed_pb2 as pb

T0 = int(_dt.datetime(2026, 10, 30, 9, 21, tzinfo=IST).timestamp() * 1000)


def _frame(key, ltp, ltt, vtt, atp, cp=100.0, index=False):
    r = pb.FeedResponse()
    f = r.feeds[key]
    ff = f.fullFeed.indexFF if index else f.fullFeed.marketFF
    ff.ltpc.ltp, ff.ltpc.ltt, ff.ltpc.cp = ltp, ltt, cp
    if not index:
        ff.atp, ff.vtt = atp, vtt
    return r.SerializeToString()


def test_builder_minutes_volume_baseline_vwap_and_5m():
    b = candles.Builder()
    b.tick("K", 101, T0 + 1000, 5000, 100.5, 100)        # baseline: no volume booked
    b.tick("K", 102, T0 + 20000, 5100, 100.6, 100)
    b.tick("K", 99, T0 + 30000, 5150, 100.4, 100)
    b.tick("K", 98, T0 + 10000, 5200, 100.4, 100)        # older ltt: price ignored, volume kept
    b.tick("K", 103, T0 + 61000, 5300, 100.7, 100)       # next minute
    c = b.candles("K")
    assert len(c) == 2
    assert (c[0]["open"], c[0]["high"], c[0]["low"], c[0]["close"], c[0]["volume"]) == (101, 102, 99, 99, 200)
    assert c[1]["volume"] == 100 and c[1]["vwap"] == 100.7
    five = b.candles("K", 5)
    assert len(five) == 1 and five[0]["volume"] == 300 and five[0]["high"] == 103
    s = b.summary()["K"]
    assert s["ltp"] == 103 and s["chg_pct"] == 3.0 and s["vwap"] == 100.7


def test_tick_file_roundtrip_guard_and_prune(tmp_path):
    w = store.TickWriter(tmp_path / "ticks", guard_gb=0)
    t = _dt.datetime(2026, 10, 30, 9, 31, tzinfo=IST)
    msgs = [_frame("K", 100 + i, T0 + i * 1000, 10 + i, 100) for i in range(5)]
    for m in msgs:
        assert w.append(m, t)
    w.close()
    f = tmp_path / "ticks" / "2026-10-30" / "ticks_0930.zst"
    assert [d for _, d in store.read_frames(f)] == msgs
    w2 = store.TickWriter(tmp_path / "ticks", guard_gb=10 ** 9)      # nothing is that free
    assert w2.check_guard() and not w2.append(msgs[0], t)
    (tmp_path / "ticks" / "2026-09-01").mkdir()
    (tmp_path / "ticks" / "notes").mkdir()
    gone = store.prune(tmp_path / "ticks", _dt.date(2026, 10, 30), keep_days=30)
    assert gone == ["2026-09-01"] and (tmp_path / "ticks" / "notes").exists()
    assert (tmp_path / "ticks" / "2026-10-30").exists()


def test_recorder_on_frame_and_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(recorder, "LIVE_DIR", tmp_path / "live")
    monkeypatch.setattr(recorder, "HEALTH", tmp_path / "health.json")
    monkeypatch.setenv("DRISHTI_DATA_DIR", str(tmp_path / "data"))
    from drishti.core import config
    config.get_settings.cache_clear()
    rec = recorder.Recorder(["NSE_EQ|K"], _dt.datetime(2099, 1, 1, tzinfo=IST))
    rec.on_frame(_frame("NSE_EQ|K", 101, T0, 1000, 100.9))
    rec.on_frame(_frame("NSE_INDEX|Nifty 50", 24000, T0 + 500, 0, 0, index=True))
    rec.on_frame(b"not protobuf")
    rec.snapshot()
    rec.flush_bars(final=True)
    rec.ticks.close()
    rec.health(state="test")
    import json
    s = json.loads((tmp_path / "live" / "summary.json").read_text())
    assert s["label"].startswith("LIVE VIEW") and s["keys"]["NSE_EQ|K"]["ltp"] == 101
    assert s["keys"]["NSE_INDEX|Nifty 50"]["ltp"] == 24000
    assert rec.h["frames"] == 3 and rec.h.get("decode_errors") == 1
    assert list((tmp_path / "data" / "bars_1m").rglob("*.parquet"))
    assert list((tmp_path / "data" / "ticks").rglob("*.zst"))
    config.get_settings.cache_clear()


def test_window_before_and_after_switch(monkeypatch):
    from drishti.core import config
    monkeypatch.setenv("DRISHTI_UPSTOX_LOGIN_ENABLED", "false")
    config.get_settings.cache_clear()
    a, b = recorder.window(_dt.date(2026, 10, 30))
    assert (a.hour, a.minute, b.hour, b.minute) == (9, 21, 15, 35)
    monkeypatch.setenv("DRISHTI_UPSTOX_LOGIN_ENABLED", "true")
    config.get_settings.cache_clear()
    assert recorder.window(_dt.date(2026, 10, 30))[0].minute == 15
    config.get_settings.cache_clear()
