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


def test_live_ws_pushes_on_change(tmp_path, monkeypatch):
    import json
    import time

    from fastapi.testclient import TestClient

    from drishti.api import app as A
    d = tmp_path / "live"
    (d / "c1m").mkdir(parents=True)
    monkeypatch.setattr(A, "LIVE_DIR", d)
    (d / "summary.json").write_text(json.dumps({"updated_at": "x", "keys": {"K": {"ltp": 1}}}))
    (d / "c1m" / "K.json").write_text(json.dumps([{"time": 60, "open": 1, "high": 1, "low": 1,
                                                   "close": 1, "volume": 0, "vwap": None}]))
    with TestClient(A.app).websocket_connect("/api/live/ws?key=K") as ws:
        first = ws.receive_json()
        assert first["summary"]["keys"]["K"]["ltp"] == 1 and len(first["candles"]) == 1
        time.sleep(0.05)
        (d / "c1m" / "K.json").write_text(json.dumps([{"time": 60, "open": 1, "high": 2, "low": 1,
                                                       "close": 2, "volume": 5, "vwap": None}]))
        t0 = time.monotonic()
        nxt = ws.receive_json()
        assert nxt["candles"][-1]["close"] == 2
        assert time.monotonic() - t0 < 1.0          # pushed on change, not on a 2 s heartbeat


def test_tick_files_survive_a_killed_writer(tmp_path):
    import zstandard

    root = tmp_path / "ticks"
    t = _dt.datetime(2026, 10, 30, 12, 1, tzinfo=IST)
    msgs = [_frame("K", 100 + i, T0 + i * 1000, 10 + i, 100) for i in range(50)]
    w = store.TickWriter(root, guard_gb=0)
    for m in msgs[:20]:
        w.append(m, t)
    w._z.flush(zstandard.FLUSH_BLOCK)                 # a killed writer: no frame end
    w._f.flush()
    w._f.close()
    w._z = w._f = None
    w2 = store.TickWriter(root, guard_gb=0)           # the restarted recorder, same slot
    for m in msgs[20:]:
        w2.append(m, t)
    w2.close()
    files = sorted((root / "2026-10-30").glob("*.zst"))
    assert [f.name for f in files] == ["ticks_1200.zst", "ticks_1200_a.zst"]
    got = [d for f in files for _, d in store.read_frames(f)]
    assert got == msgs                                # nothing lost, nothing corrupt


def test_live_candles_for_15m_1h_and_today_daily(tmp_path, monkeypatch):
    """User 2026-11-12: every chart timeframe live within ~1 s (WebSocket candles)."""
    import json
    from urllib.parse import quote
    from drishti.api import app as A, chart_data
    monkeypatch.setattr(A, "LIVE_DIR", tmp_path)
    monkeypatch.setattr(chart_data, "LIVE_DIR", tmp_path)
    monkeypatch.setattr(chart_data, "bars_1m_dir", lambda: tmp_path / "none")
    key = "NSE_EQ|TLIVE"
    ist = lambda h, m: int(_dt.datetime(2026, 11, 12, h, m, tzinfo=IST).timestamp())  # noqa
    c1 = [{"time": ist(9, 15) + 60 * i, "open": 100 + i, "high": 101 + i, "low": 99 + i,
           "close": 100.5 + i, "volume": 10, "vwap": None} for i in range(75)]   # 09:15-10:29
    (tmp_path / "c1m").mkdir()
    (tmp_path / "c1m" / f"{quote(key, safe='')}.json").write_text(json.dumps(c1))
    (tmp_path / "day0.json").write_text(json.dumps({key: {"date": "2026-11-12", "open": 98.0,
        "high": 120.0, "low": 95.0, "close": 99, "volume": 500, "seen_at": 0}}))
    c15 = A._live_candles(key, 15)
    assert [c["time"] for c in c15][:2] == [ist(9, 15), ist(9, 30)] and len(c15) == 5
    assert c15[0]["volume"] == 150 and c15[0]["high"] == 115 and c15[0]["close"] == 114.5
    c60 = A._live_candles(key, 60)
    assert [c["time"] for c in c60] == [ist(9, 15), ist(10, 15)]                # NSE hours
    d = A._live_candles(key, 1440)
    assert d[0]["time"] == ist(0, 0) and (d[0]["open"], d[0]["high"], d[0]["low"]) == (98.0, 175, 95.0)
    assert d[0]["close"] == 174.5
    from fastapi.testclient import TestClient
    cl = TestClient(A.app)
    assert cl.get(f"/api/live/candles?key={quote(key)}&tf=15").json()["candles"][0]["time"] == ist(9, 15)
    assert cl.get(f"/api/live/candles?key={quote(key)}&tf=7").status_code == 400
    assert (tmp_path / "want" / quote(key, safe="")).exists()                  # asked the recorder


def test_recorder_writes_live_json_for_wanted_keys(tmp_path, monkeypatch):
    import os
    import time
    from urllib.parse import quote
    monkeypatch.setattr(recorder, "LIVE_DIR", tmp_path)
    monkeypatch.setattr(recorder, "HEALTH", tmp_path / "h.json")
    monkeypatch.setenv("DRISHTI_DATA_DIR", str(tmp_path / "data"))
    from drishti.core import config
    config.get_settings.cache_clear()
    try:
        rec = recorder.Recorder(["NSE_EQ|K", "NSE_EQ|R"], _dt.datetime(2099, 1, 1, tzinfo=IST),
                                live_keys={"NSE_EQ|K"})
        rec.on_frame(_frame("NSE_EQ|R", 50, T0, 1000, 50.1))
        rec.snapshot()
        assert not (tmp_path / "c1m" / quote("NSE_EQ|R", safe="")).with_suffix(".json").exists()
        (tmp_path / "want").mkdir()
        (tmp_path / "want" / quote("NSE_EQ|R", safe="")).touch()
        rec._wanted_at = 0.0
        rec.snapshot()                                   # newly wanted: written at once
        assert (tmp_path / "c1m" / f"{quote('NSE_EQ|R', safe='')}.json").exists()
        old = time.time() - 300                          # a stale want file stops it
        os.utime(tmp_path / "want" / quote("NSE_EQ|R", safe=""), (old, old))
        rec._wanted_at = 0.0
        rec.snapshot()
        assert rec._wanted == set()
        rec.ticks.close()
    finally:
        config.get_settings.cache_clear()
