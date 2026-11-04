"""Track C: chart endpoints (read only), the drishti.features interface the chart uses, the
supertrend stub, 1m aggregation and the bias box. No database: the loaders are monkeypatched."""

import datetime as _dt
import inspect
import json
import re
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from drishti.api import chart_api, chart_bias, chart_data
from drishti.features import indicators, levels

API = Path(chart_api.__file__).parent
DAY0 = int(_dt.datetime(2026, 10, 29, 9, 15, tzinfo=_dt.timezone(_dt.timedelta(hours=5.5)))
           .timestamp())


def _bars(n: int, seed: int = 1, intraday: bool = False) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    c = 100 + np.cumsum(rng.normal(0, 1, n))
    o = c + rng.normal(0, 0.3, n)
    df = pd.DataFrame({"open": o, "close": c, "high": np.maximum(o, c) + rng.random(n),
                       "low": np.minimum(o, c) - rng.random(n),
                       "volume": rng.integers(1000, 5000, n).astype(float)})
    if intraday:
        df["time"] = DAY0 + 60 * np.arange(n) + 86400 * (np.arange(n) // 375)
        df["session_date"] = chart_data.session_of(df.time)
        df["vwap"] = np.nan
    else:
        d = pd.bdate_range("2025-01-01", periods=n)
        df["time"], df["session_date"], df["vwap"] = d.strftime("%Y-%m-%d"), d.date, np.nan
    return df[chart_data.COLS].assign(src="prajna")


# --- drishti.features as the chart uses it (Track B's code; formerly chart stubs) -------------------------------------------------------------

def test_feature_interface():
    sig = lambda f: list(inspect.signature(f).parameters)  # noqa: E731
    assert sig(indicators.bollinger) == ["df", "by", "n", "k"]
    assert sig(indicators.macd) == ["df", "by", "fast", "slow", "signal"]
    assert sig(indicators.vwap) == ["df", "by"]
    assert sig(indicators.supertrend) == ["df", "by", "n", "mult"]
    assert sig(levels.trend) == ["df", "ind"]
    assert sig(levels.nearest) == ["zs", "x"]
    df = _bars(60, intraday=True)
    assert list(indicators.bollinger(df).columns) == ["bb_mid", "bb_up", "bb_lo"]
    assert list(indicators.macd(df).columns) == ["macd", "macd_signal", "macd_hist"]


def test_feature_values():
    df = _bars(80)
    bb = indicators.bollinger(df)
    w = df.close.iloc[-20:]
    assert bb.bb_mid.iloc[-1] == pytest.approx(w.mean())
    assert bb.bb_up.iloc[-1] == pytest.approx(w.mean() + 2 * w.std(ddof=0))
    assert bb.bb_mid.iloc[:19].isna().all()
    m = indicators.macd(df)
    e = lambda n: df.close.ewm(span=n, adjust=False, min_periods=n).mean()  # noqa: E731
    assert m.macd.iloc[-1] == pytest.approx(e(12).iloc[-1] - e(26).iloc[-1])
    assert m.macd_hist.iloc[-1] == pytest.approx(m.macd.iloc[-1] - m.macd_signal.iloc[-1])
    v = indicators.vwap(_bars(400, intraday=True))      # two sessions: resets on the second
    d = _bars(400, intraday=True)
    tp = (d.high + d.low + d.close) / 3
    assert v.iloc[374] == pytest.approx((tp * d.volume)[:375].sum() / d.volume[:375].sum())
    assert v.iloc[375] == pytest.approx(tp.iloc[375])


def test_features_no_lookahead():
    df = _bars(120)
    a = pd.concat([indicators.bollinger(df), indicators.macd(df)], axis=1).iloc[:80]
    b = pd.concat([indicators.bollinger(df.iloc[:80]), indicators.macd(df.iloc[:80])], axis=1)
    pd.testing.assert_frame_equal(a, b)


def test_trend_rule():
    n = 300
    up = np.linspace(100, 200, n) + 3 * np.sin(np.arange(n) / 3)   # rising swings
    df = pd.DataFrame({"open": up, "close": up, "high": up + 1, "low": up - 1, "volume": 1.0})
    assert levels.trend(df, indicators.basic(df)) == "UP"
    dn = df[["open", "close", "high", "low"]].mul(-1).add(400).assign(volume=1.0)
    dn = dn.rename(columns={"high": "low", "low": "high"})
    assert levels.trend(dn, indicators.basic(dn)) == "DOWN"
    short = df.iloc[:100].reset_index(drop=True)                   # no SMA200 yet
    assert levels.trend(short, indicators.basic(short)) == "SIDE"


def test_nearest():
    zs = [{"price": p, "bottom": p - 1, "top": p + 1, "touches": 2} for p in (90, 98, 105, 120)]
    s, r = levels.nearest(zs, 100)
    assert s["price"] == 98 and r["price"] == 105        # band top 99 < 100
    s, r = levels.nearest(zs, 98.5)
    assert s["price"] == 90 and r["price"] == 105        # inside 98's band: neither side


# --- aggregation and sources -----------------------------------------------------------------

def test_bucket_grid_0915():
    t = DAY0 + np.array([0, 59, 14 * 60, 15 * 60, 59 * 60, 60 * 60, 6 * 3600 + 14 * 60])
    assert list(chart_data.bucket(t, 15) - DAY0) == [0, 0, 0, 900, 2700, 3600, 6 * 3600]
    assert list(chart_data.bucket(t, 60) - DAY0) == [0, 0, 0, 0, 0, 3600, 6 * 3600]
    assert list(chart_data.bucket(t, 5)[:3] - DAY0) == [0, 0, 600]


def test_aggregate_ohlcv():
    df = _bars(30, intraday=True)
    a = chart_data.aggregate(df.drop(columns="src"), 15)
    assert len(a) == 2 and a.time.iloc[1] - a.time.iloc[0] == 900
    first = df.iloc[:15]
    assert a.iloc[0][["open", "high", "low", "close", "volume"]].tolist() == [
        first.open.iloc[0], first.high.max(), first.low.min(), first.close.iloc[-1],
        first.volume.sum()]


def test_recorded_and_live_merge(tmp_path, monkeypatch):
    key = "NSE_EQ|TEST"
    d = tmp_path / "bars_1m" / "2026-10-29"
    d.mkdir(parents=True)
    rows = _bars(10, intraday=True).drop(columns=["session_date", "src"]).assign(
        instrument_key=key)
    rows.assign(volume=rows.volume.astype("int64")).to_parquet(d / "bars_152445.parquet")
    (tmp_path / "bars_1m" / "not-a-day").mkdir()
    live = tmp_path / "live"
    (live / "c1m").mkdir(parents=True)
    lv = [{"time": int(DAY0 + 60 * i), "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5,
           "volume": 10, "vwap": 1.2} for i in range(8, 12)]
    (live / "c1m" / f"{quote(key, safe='')}.json").write_text(json.dumps(lv))
    monkeypatch.setattr(chart_data, "bars_1m_dir", lambda: tmp_path / "bars_1m")
    monkeypatch.setattr(chart_data, "LIVE_DIR", live)
    rec = chart_data.recorded_1m(key, _dt.date(2026, 10, 28))
    assert len(rec) == 10 and (rec.src == "drishti_1m").all()
    assert chart_data.recorded_1m(key, _dt.date(2026, 10, 29)).empty
    m = chart_data.merge(rec, chart_data.live_1m(key))
    assert len(m) == 12 and m.time.is_monotonic_increasing
    assert (m.src.iloc[8:] == "live").all()                  # the later part wins on a tie
    src = chart_data.sources(pd.concat([rec, chart_data.live_1m(key)]),
                             {"drishti_1m": "a", "live": "b"})
    assert [s["bars"] for s in src] == [10, 4] and src[1]["last_time"] == "2026-10-29 09:26"


# --- bias box ----------------------------------------------------------------------------------

def test_bias_wording():
    z = {"price": 95.0, "bottom": 94.0, "top": 96.0, "touches": 3}
    b = chart_bias.bias(100.0, "UP", {"ema20": 98, "sma50": 101, "vwap": None, "rsi": 72},
                        z, None, 2.0)
    assert b["label"] == "NOT a prediction - rule summary only; not used by drishti-swing-v1"
    assert "above EMA20" in b["text"] and "below SMA50" in b["text"] and "VWAP" not in b["text"]
    assert "above 70" in b["text"] and "2.5 ATR" in b["text"] and "No resistance" in b["text"]
    assert not re.search(r"\b(buy|sell|probab\w*|predict(?!ion -))", b["text"], re.I)


# --- endpoints -----------------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    async def load(key, tf):
        df = _bars(500, intraday=tf != "1d")
        return df, [{"source": "test", "bars": len(df), "last_time": "x"}]

    async def meta(key, tf):
        return {"symbol": "TEST", "name": "Test", "sector": None, "watchlist_dates": []}
    monkeypatch.setattr(chart_api, "load", load)
    monkeypatch.setattr(chart_api, "_meta", meta)
    from drishti.api.app import app
    return TestClient(app)


@pytest.mark.parametrize("tf", ["1m", "5m", "15m", "1h", "1d"])
def test_bars_shape(client, tf):
    r = client.get("/api/chart2/bars", params={"key": "NSE_EQ|TEST", "tf": tf})
    assert r.status_code == 200
    j = r.json()
    assert {"key", "tf", "label", "adjusted", "sources", "symbol", "bars", "lines", "panes",
            "candles", "bullish_flags", "zones", "trend", "last", "bias"} <= j.keys()
    assert j["label"] == ("LIVE VIEW - not used by drishti-swing-v1" if tf in ("1m", "5m")
                          else None)
    assert j["adjusted"] == (tf == "1d")
    assert len(j["bars"]) == 500 and {"chg_pct", "volume"} <= j["bars"][-1].keys()
    assert set(j["lines"]) == {"ema20", "sma50", "sma200", "vwap", "bb_mid", "bb_up", "bb_lo"}
    assert set(j["panes"]) == {"rsi", "macd", "macd_signal", "macd_hist"}
    assert bool(j["lines"]["vwap"]) == (tf != "1d")
    assert isinstance(j["bars"][0]["time"], str if tf == "1d" else int)
    assert j["trend"] in ("UP", "DOWN", "SIDE")
    assert j["bias"]["label"].startswith("NOT a prediction")
    assert not {"pnl", "outcome", "hit_rate", "signals"} & j.keys()


def test_bad_tf_and_trends(client):
    assert client.get("/api/chart2/bars", params={"key": "K", "tf": "2h"}).status_code == 400
    j = client.get("/api/chart2/trends", params={"key": "K"}).json()
    assert set(j["trends"]) == {"1m", "5m", "15m", "1h", "1d"}
    assert all(v in ("UP", "DOWN", "SIDE") for v in j["trends"].values())


def test_read_only():
    for r in chart_api.router.routes:
        assert r.methods == {"GET"}
    for f in API.glob("chart_*.py"):
        src = f.read_text().lower()
        assert not re.search(r"\b(insert|update|delete|commit|upstox|truncate|drop table)\b",
                             src.replace("update(", "")), f.name


# --- indicators menu, Heikin Ashi, Supertrend ---------------------------------------------------

@pytest.mark.parametrize("kind,keys", [("ema", {"ema"}), ("sma", {"sma"}), ("vwap", {"vwap"}),
                                       ("bb", {"bb_mid", "bb_up", "bb_lo"}), ("rsi", {"rsi"}),
                                       ("macd", {"macd", "macd_signal", "macd_hist"}),
                                       ("atr", {"atr"}), ("supertrend", {"st", "st_dir"})])
def test_indicator_endpoint(client, kind, keys):
    r = client.get("/api/chart2/indicator", params={"key": "K", "tf": "5m", "kind": kind, "n": 14})
    assert r.status_code == 200, r.text
    j = r.json()
    assert set(j["series"]) == keys and j["label"].startswith("LIVE VIEW")
    assert all(isinstance(p["time"], int) for p in next(iter(j["series"].values())))


def test_indicator_values_match_features(client):
    """The menu's default instances give the same numbers as /bars (one implementation)."""
    b = client.get("/api/chart2/bars", params={"key": "K", "tf": "1d"}).json()
    get = lambda **p: client.get("/api/chart2/indicator", params={"key": "K", "tf": "1d", **p}).json()["series"]  # noqa: E731
    assert get(kind="ema", n=20)["ema"] == b["lines"]["ema20"]
    assert get(kind="sma", n=200)["sma"] == b["lines"]["sma200"]
    assert get(kind="rsi", n=14)["rsi"] == b["panes"]["rsi"]
    assert get(kind="bb", n=20, k=2)["bb_up"] == b["lines"]["bb_up"]
    assert get(kind="macd")["macd_hist"] == b["panes"]["macd_hist"]


def test_indicator_bad_params(client):
    q = {"key": "K", "tf": "1d"}
    assert client.get("/api/chart2/indicator", params={**q, "kind": "kdj"}).status_code == 400
    assert client.get("/api/chart2/indicator", params={**q, "kind": "vwap"}).status_code == 400
    assert client.get("/api/chart2/indicator", params={**q, "kind": "ema", "n": 0}).status_code == 422
    assert client.get("/api/chart2/indicator",
                      params={**q, "kind": "macd", "fast": 30, "slow": 26}).status_code == 400


def test_heikin_ashi(client):
    df = _bars(5)
    ha = chart_api.heikin_ashi(df)
    o, c = df.open.to_numpy(), df.close.to_numpy()
    assert ha.close.iloc[0] == pytest.approx((df.open + df.high + df.low + df.close).iloc[0] / 4)
    assert ha.open.iloc[0] == pytest.approx((o[0] + c[0]) / 2)
    assert ha.open.iloc[1] == pytest.approx((ha.open.iloc[0] + ha.close.iloc[0]) / 2)
    assert (ha.high >= ha[["open", "close"]].max(axis=1)).all()
    j = client.get("/api/chart2/bars", params={"key": "K", "tf": "1d", "ha": True}).json()
    plain = client.get("/api/chart2/bars", params={"key": "K", "tf": "1d"}).json()
    assert j["heikin_ashi"] and j["bars"][0]["open"] != plain["bars"][0]["open"]
    assert j["lines"] == plain["lines"] and j["trend"] == plain["trend"]   # on the real bars


def test_no_stubs_remain():
    assert not (API / "chart_stubs.py").exists(), "chart_stubs.py was not deleted"
    src = Path(chart_api.__file__).read_text()
    assert "chart_stubs" not in src and "stubs." not in src


def test_supertrend():
    up = np.linspace(100, 200, 120)
    df = pd.DataFrame({"open": up, "close": up, "high": up + 1, "low": up - 1, "volume": 1.0})
    s = indicators.supertrend(df, n=10, mult=3.0)
    assert s.st.iloc[:9].isna().all() and s.st_dir.iloc[-1] == 1
    assert (s.st.iloc[20:] < df.close.iloc[20:]).all()          # below price in an uptrend
    a = indicators.supertrend(pd.concat([df, df.iloc[::-1]], ignore_index=True), n=10).iloc[:120]
    pd.testing.assert_frame_equal(a, s)                          # no look-ahead


def test_hindsight_confirmation():
    df = pd.DataFrame({"open": [10, 11, 12, 13, 12, 11, 10], "high": [11, 12.5, 13.5, 13.2, 12.2, 11.2, 10.5],
                       "low": [9.5, 10.8, 11.8, 11.5, 10.5, 9.8, 9.6], "close": [10.8, 12.4, 13.2, 11.6, 10.6, 10.0, 10.2]})
    h = chart_api.hindsight(df)
    assert [x["dir"] for x in h] == ["bull", "bull", "bull", "bear", "bear", "bear", "bull"]
    assert h[0] == {"dir": "bull", "confirm": "confirmed", "follow": "follow-through"}   # 12.4 > 11, 13.2 > 12.4
    assert h[1] == {"dir": "bull", "confirm": "confirmed", "follow": "no follow-through"}  # 13.2 > 12.5, 11.6 < 13.2
    assert h[2]["confirm"] == "not confirmed" and h[2]["follow"] is None                 # 11.6 <= 13.5
    assert h[3] == {"dir": "bear", "confirm": "confirmed", "follow": "follow-through"}    # 10.6 < 11.5, 10.0 < 10.6
    assert h[4] == {"dir": "bear", "confirm": "confirmed", "follow": "no follow-through"}  # 10.0 < 10.5, 10.2 > 10.0
    assert h[5]["confirm"] == "not confirmed"                                             # 10.2 >= 9.8
    assert h[6] == {"dir": "bull", "confirm": "pending", "follow": None}                  # no next bar
    flat = chart_api.hindsight(pd.DataFrame({"open": [1.0, 1.0], "high": [1.1, 1.2], "low": [0.9, 0.9], "close": [1.0, 1.1]}))
    assert flat[0] == {"dir": "flat", "confirm": None, "follow": None} and flat[1]["confirm"] == "pending"


def test_bars_carry_hindsight(client):
    b = client.get("/api/chart2/bars", params={"key": "K", "tf": "5m"}).json()["bars"]
    assert {"dir", "confirm", "follow"} <= b[0].keys() and b[-1]["confirm"] in ("pending", None)


def test_trend_detail_matches_levels_trend():
    """The reasons shown reproduce levels.trend's class (frozen spec rule) and the MA chip uses
    close vs SMA50 vs SMA200 only."""
    n = 300
    up = np.linspace(100, 200, n) + 3 * np.sin(np.arange(n) / 3)
    df = pd.DataFrame({"open": up, "close": up, "high": up + 1, "low": up - 1, "volume": 1.0})
    for d in (df, df.iloc[:120].reset_index(drop=True),
              df[["open", "close", "high", "low"]].mul(-1).add(400).rename(
                  columns={"high": "low", "low": "high"}).assign(volume=1.0)):
        ind = indicators.basic(d)
        t = chart_api.trend_detail(d, ind)
        assert t["class"] == levels.trend(d, ind)
        assert t["ma_trend"] in ("UP", "DOWN", "FLAT") and len(t["conditions"]) == 3
        assert t["text"].startswith(t["class"] + " — ") and "drishti-swing-v1" in t["note"]
        if t["class"] == "UP":
            assert all(x["ok"] for x in t["conditions"]) and t["ma_trend"] == "UP"
    flat = df.copy(); flat.loc[flat.index[-1], "close"] = 0.5          # close below both SMAs
    ind = indicators.basic(flat)
    assert chart_api.trend_detail(flat, ind)["ma_trend"] == "FLAT"   # SMA50 > SMA200: not DOWN either


def test_bars_carry_trend_detail(client):
    j = client.get("/api/chart2/bars", params={"key": "K", "tf": "1d"}).json()
    assert j["trend_detail"]["class"] == j["trend"] and "conditions" in j["trend_detail"]


# --- pattern markers (server-side, causal) ------------------------------------------------------

def _markers(df):
    from drishti.api import chart_markers
    from drishti.features import candles
    df = df.reset_index(drop=True)
    ind = indicators.basic(df)
    fl = candles.flags(pd.concat([df, ind[["atr"]]], axis=1))
    return chart_markers.markers(df, ind, fl), ind


def test_markers_never_repaint():
    """A marker on bar j is identical in every prefix that holds bar j (zones known on bar j,
    not today's), and no prefix has a marker the full series lacks."""
    total = 0
    for seed in (2, 13):          # seeds where today's zones would change markers (mutation-checked)
        df = _bars(320, seed=seed)
        full, _ = _markers(df)
        total += len(full)
        key = lambda ms: {(m["i"], m["kind"], tuple(m["flags"]), m["stop"]) for m in ms}  # noqa: E731
        for k in range(30, len(df)):
            pre, _ = _markers(df.iloc[:k + 1])
            assert key(pre) == {x for x in key(full) if x[0] <= k}, (seed, k)
    assert total > 0


def test_markers_stop_and_caution():
    from drishti.api.chart_markers import BEARISH
    from drishti.features import candles
    seen = set()
    for seed in range(1, 9):
        df = _bars(400, seed=seed)
        ms, ind = _markers(df)
        c = df.close.to_numpy()
        for m in ms:
            j, i = m["i"], m["i"] - 1
            seen.add(m["kind"])
            up = c[j] > ind.sma50.iloc[j] > ind.sma200.iloc[j]
            if m["kind"] == "bull":
                assert m["stop"] == df.low.iloc[i] and set(m["flags"]) <= set(candles.BULLISH)
                assert c[j] > df.high.iloc[i]
            else:
                assert m["stop"] is None and set(m["flags"]) <= set(BEARISH)
                assert (m["kind"] == "caution") == bool(up)     # no SELL marker in an uptrend
    assert "bull" in seen


def test_bars_carry_markers(client):
    j = client.get("/api/chart2/bars", params={"key": "K", "tf": "1d"}).json()
    mk = j["markers"]
    assert "not a prediction" in mk["label"] and "drishti-swing-v1" in mk["label"]
    assert "Scanner" in mk["note"]
    times = {b["time"] for b in j["bars"]}
    for m in mk["items"]:
        assert m["time"] in times and m["kind"] in ("bull", "bear", "caution")
        assert (m["stop"] is not None) == (m["kind"] == "bull")
        assert not re.search(r"\b(buy|sell)\b", " ".join(m["flags"]).lower())


def test_pattern_layers_wording(client):
    """Bullish / bearish groupings come from the API; the chart's pattern and hindsight layers
    carry no buy / sell verbs (only the scanner alert levels are actionable)."""
    from drishti.api.chart_markers import BEARISH
    from drishti.features import candles
    j = client.get("/api/chart2/bars", params={"key": "K", "tf": "1d"}).json()
    assert j["bearish_flags"] == list(BEARISH) and not set(BEARISH) & set(candles.BULLISH)
    kc = API.parents[2] / "web" / "components" / "kchart"
    for f in kc.glob("*.ts*"):
        src = f.read_text()
        assert not re.search(r"\b(BUY|SELL)\b", src), f.name
    assert "the only actionable layer is the alert levels" in (kc / "KChart.tsx").read_text()


# --- alert panel: the alert layer is always on ----------------------------------------------------

def test_zones_as_of_point_in_time():
    """The alert panel's S/R (when an alert has no saved zones) are the zones known on the alert
    bar: equal to levels.zones on the bars up to it, unchanged when later bars arrive."""
    df = _bars(300)
    k = 240
    as_of = _dt.datetime.fromisoformat(f"{df.time[k]}T14:00:00+00:00")
    z = chart_api.zones_as_of(df, "1d", as_of)
    atr = indicators.atr(df.iloc[:k + 1]).iloc[-1]
    want = levels.zones(df.high.to_numpy()[:k + 1], df.low.to_numpy()[:k + 1], float(atr))
    assert z["bar"] == df.time[k] and len(z["zones"]) == len(want) > 0
    assert [round(x["price"], 4) for x in z["zones"]] == [round(x["price"], 4) for x in want]
    assert chart_api.zones_as_of(df.iloc[:k + 1], "1d", as_of) == z          # later bars: no effect
    early = _dt.datetime(2000, 1, 1, tzinfo=_dt.timezone.utc)
    assert chart_api.zones_as_of(df, "1d", early) is None
    di = _bars(500, intraday=True)
    zi = chart_api.zones_as_of(di, "15m", _dt.datetime.fromtimestamp(int(di.time[300]), _dt.timezone.utc))
    assert zi["bar"] == int(di.time[300])


def test_bars_as_of(client):
    j = client.get("/api/chart2/bars", params={"key": "K", "tf": "1d", "as_of": "2026-01-15T10:00:00+00:00"}).json()
    assert "zones_as_of" in j and j["zones_as_of"]["bar"] <= "2026-01-15"
    assert "zones_as_of" not in client.get("/api/chart2/bars", params={"key": "K", "tf": "1d"}).json()


def test_web_alert_layer_always_on():
    """web/tests/alertLayers.test.mjs: on an alert page with both pattern layers OFF the entry /
    stop / R1 / R2 lines, the ALERT marker and the S/R bands are present (node --test)."""
    import shutil
    import subprocess
    node = shutil.which("node") or str(Path.home() / ".local/bin/node")
    if not Path(node).exists():
        pytest.skip("node not installed")
    root = API.parents[2]
    r = subprocess.run([node, "--test", "web/tests/alertLayers.test.mjs"], cwd=root,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert "pass 5" in r.stdout
