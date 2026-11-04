"""Nightly intraday ingest (SWITCH_TO_B D3): parsing, the session filter, files, re-run dedup."""

import datetime as _dt
import json

import pytest

from drishti.core import config
from drishti.data import intraday


class Resp:
    def __init__(self, status, body):
        self.status_code, self._b = status, body
        self.content = json.dumps(body).encode()
        self.text = self.content.decode()

    def json(self):
        return self._b


class FakeApi:
    def __init__(self, close=100.0):
        self.close, self.paths = close, []

    def get_response(self, path, params=None):
        self.paths.append(path)
        if "BAD" in path:
            return Resp(500, {"status": "error"})
        c = self.close
        return Resp(200, {"data": {"candles": [
            ["2026-11-03T09:16:00+05:30", c, c + 1, c - 1, c + 0.5, 10, 0],
            ["2026-11-03T09:15:00+05:30", c, c + 2, c - 2, c, 20, 0],
            ["2026-11-02T15:29:00+05:30", c, c, c, c, 5, 0]]}})   # another session: dropped


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DRISHTI_DATA_DIR", str(tmp_path))
    config.get_settings.cache_clear()
    yield tmp_path
    config.get_settings.cache_clear()


def test_fetch_writes_one_session_and_rerun_keeps_latest(data_dir):
    day = _dt.date(2026, 11, 3)
    api = FakeApi()
    out = intraday.fetch(api, ["NSE_EQ|A", "NSE_EQ|BAD"], day, ("1m",))
    assert out["1m"]["ok"] == 1 and out["1m"]["errors"] == 1 and out["1m"]["candles"] == 2
    assert api.paths[0] == "/v3/historical-candle/NSE_EQ%7CA/minutes/1/2026-11-03/2026-11-03"
    df = intraday.read("1m", day)
    assert len(df) == 2 and str(df.bar_start_utc.min()) == "2026-11-03 03:45:00+00:00"
    import time
    time.sleep(1.1)                                 # a new stamp for the second part
    intraday.fetch(FakeApi(close=200.0), ["NSE_EQ|A"], day, ("1m",))
    df = intraday.read("1m", day)
    assert len(df) == 2 and set(df.open) == {200.0}
    assert len(list((data_dir / "intraday" / "manifest").glob("*.json"))) == 2


def test_missing_pass_and_window_stop(data_dir):
    day = _dt.date(2026, 11, 3)
    intraday.fetch(FakeApi(), ["NSE_EQ|A", "NSE_EQ|BAD"], day, ("1m",))
    assert intraday.missing(day, ("1m",)) == {"1m": ["NSE_EQ|BAD"]}
    api = FakeApi()
    out = intraday.fetch(api, {"1m": ["NSE_EQ|X", "NSE_EQ|Y"]}, day, ("1m",),
                         allowed=lambda: len(api.paths) < 1)
    assert len(api.paths) == 1 and "stopped" in out
