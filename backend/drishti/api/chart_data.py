"""Bars for the chart's timeframes (read only). Track C.

1d       adjusted official bars + Drishti's provisional bar of today (as /api/chart).
15m, 1h  Prajna ohlcv_bar of that timeframe (latest fetch per bar), then Drishti's recorded 1m
         bars (Parquet, then the live snapshot) for the days after Prajna's last session,
         aggregated on the 09:15 IST grid.
1m, 5m   Prajna ohlcv_bar 1m for warm-up, then the recorded 1m bars and the live snapshot.
Intraday history is split/bonus adjusted point in time (bars.intraday, 2026-11-12; DEVIATIONS D5
closed); today's recorder bars need no adjustment. The recorder starts at 09:21 before the
switch, so its first 15m/1h bar of a day is partial.
"""

from __future__ import annotations

import datetime as _dt
import json
import time
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pandas as pd
from sqlalchemy import text

from drishti.core.clock import IST, today
from drishti.core.config import get_settings
from drishti.core.db import prajna_sessionmaker, sessionmaker
from drishti.data import bars
from drishti.data.holidays import SHORT_SESSIONS

TFS = ("1m", "5m", "15m", "1h", "1d")
MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}
SESSIONS = {"1m": 3, "5m": 10, "15m": 60, "1h": 250, "1d": 750}   # sessions (1d: calendar days)
LIVE_DIR = Path("/dev/shm/drishti_live")
IST_S, OPEN_S = 19800, 9 * 3600 + 15 * 60                           # IST offset; 09:15 IST
COLS = ["time", "session_date", "open", "high", "low", "close", "volume", "vwap"]
TTL_S = 30.0
TTL_LIVE_S = 10.0    # 1m/5m: the chart refreshes indicator values every 10 s
_cache: dict[tuple[str, str], tuple[float, tuple[pd.DataFrame, list[dict]]]] = {}
_quote_cache: dict[str, tuple[float, dict]] = {}


def _market_open(t: _dt.datetime | None = None) -> bool:
    t = (t or _dt.datetime.now(tz=IST)).astimezone(IST)
    m = t.hour * 60 + t.minute
    return t.weekday() < 5 and 9 * 60 + 15 <= m < 15 * 60 + 35


def get_live_quote(key: str) -> dict | None:
    now_t = time.monotonic()
    hit = _quote_cache.get(key)
    if hit and now_t - hit[0] < (10.0 if _market_open() else 60.0):
        return hit[1]

    # 1. Check summary.json: the recorder's live ticks, only while the market is open. After
    # 15:35 the recorder has stopped at its last tick, but NSE's official close (the weighted
    # last-30-minutes price) comes later; the REST quote carries it (2026-11-12: UNIMECH last
    # tick 1985.90, official close 1980.60; 1,049 of 3,460 stocks differed).
    try:
        if not _market_open():
            raise LookupError("market closed: use the REST quote")
        sum_file = LIVE_DIR / "summary.json"
        if sum_file.exists():
            s = json.loads(sum_file.read_text())
            q = s.get("keys", {}).get(key)
            if q and q.get("ltp") is not None:
                info = {
                    "ltp": float(q["ltp"]),
                    "net_change": float(q.get("chg") or 0.0),
                    "pct_change": float(q.get("chg_pct") or 0.0),
                    "vwap": float(q["vwap"]) if q.get("vwap") else None,
                    "open": None,
                    "high": None,
                    "low": None,
                    "volume": None,
                    "upper_circuit": None,
                    "lower_circuit": None,
                    "is_upper_circuit": False,
                    "is_lower_circuit": False,
                }
                _quote_cache[key] = (now_t, info)
                return info
    except Exception:
        pass

    # 2. Query live quote provider
    from drishti.data.live_quote import fetch_quote
    info = fetch_quote(key)
    if info:
        _quote_cache[key] = (now_t, info)
        return info
    return None


def bars_1m_dir() -> Path:
    return get_settings().DRISHTI_DATA_DIR / "bars_1m"


def bucket(t: np.ndarray | pd.Series, minutes: int):
    """Start (epoch s) of the `minutes` bar on the 09:15 IST grid that holds epoch second t."""
    m = minutes * 60
    return (t + IST_S - OPEN_S) // m * m + OPEN_S - IST_S


def session_of(t: pd.Series) -> pd.Series:
    return pd.to_datetime(t + IST_S, unit="s").dt.date


def aggregate(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """1m bars (COLS, sorted by time) -> `minutes` bars; vwap = the last minute's."""
    if minutes == 1 or df.empty:
        return df.reset_index(drop=True)
    g = df.assign(time=bucket(df.time, minutes)).groupby("time", sort=True)
    out = g.agg(session_date=("session_date", "first"), open=("open", "first"),
                high=("high", "max"), low=("low", "min"), close=("close", "last"),
                volume=("volume", "sum"), vwap=("vwap", "last")).reset_index()
    return out[COLS]


def _frame(rows, src: str) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume", "vwap"])
    if df.empty:
        return pd.DataFrame(columns=COLS + ["src"])
    df["time"] = df.time.astype("int64")
    for c in ("open", "high", "low", "close", "volume", "vwap"):
        df[c] = df[c].astype(float)
    df["session_date"] = session_of(df.time)
    df["src"] = src
    return df[COLS + ["src"]]


def recorded_1m(key: str, after: _dt.date | None) -> pd.DataFrame:
    """Drishti's 1m Parquet of the days after `after`."""
    root, parts = bars_1m_dir(), []
    if root.is_dir():
        for d in sorted(p for p in root.iterdir() if p.is_dir()):
            try:
                day = _dt.date.fromisoformat(d.name)
            except ValueError:
                continue
            if after is not None and day <= after:
                continue
            for f in sorted(d.glob("*.parquet")):
                x = pd.read_parquet(f, filters=[("instrument_key", "==", key)])
                if not x.empty:
                    parts.append(x)
    if not parts:
        return _frame([], "drishti_1m")
    x = pd.concat(parts, ignore_index=True)
    return _frame(x[["time", "open", "high", "low", "close", "volume", "vwap"]].to_numpy(),
                  "drishti_1m")


def live_1m(key: str) -> pd.DataFrame:
    """Today's 1m candles from the recorder's live snapshot (LIVE VIEW)."""
    try:
        c = json.loads((LIVE_DIR / "c1m" / f"{quote(key, safe='')}.json").read_text())
    except (OSError, ValueError):
        c = []
    return _frame([[b["time"], b["open"], b["high"], b["low"], b["close"], b["volume"],
                    b.get("vwap")] for b in c], "live")


def today_ohlc(key: str, day: _dt.date, ltp: float) -> dict:
    """Today's daily OHLC so far from the recorder (no REST call): the day's OHLC at the key's
    first tick (day0.json, the exchange's own day values) merged with the 1m candles since then
    (Parquet + the live snapshot) and the latest price. Falls back to the LTP alone."""
    o = h = lo = None
    vol = 0.0
    try:
        d0 = json.loads((LIVE_DIR / "day0.json").read_text()).get(key)
    except (OSError, ValueError):
        d0 = None
    if d0 and d0.get("date") == str(day):
        o, h, lo, vol = d0["open"], d0["high"], d0["low"], float(d0.get("volume") or 0)
    m = merge(recorded_1m(key, day - _dt.timedelta(days=1)), live_1m(key))
    if not m.empty:
        m = m[m.session_date == day]
    if not m.empty:
        o = o if o is not None else float(m.open.iloc[0])
        h = max(x for x in (h, float(m.high.max())) if x is not None)
        lo = min(x for x in (lo, float(m.low.min())) if x is not None)
        vol += float(m.volume.fillna(0).sum())
    h = max(x for x in (h, ltp) if x is not None)
    lo = min(x for x in (lo, ltp) if x is not None)
    return {"open": o if o is not None else ltp, "high": h, "low": lo, "close": ltp,
            "volume": vol, "from_recorder": d0 is not None or not m.empty}


def merge(*parts: pd.DataFrame) -> pd.DataFrame:
    """Concatenate 1m (or same-timeframe) frames; on the same time the later part wins."""
    parts = [p for p in parts if not p.empty]
    if not parts:
        return pd.DataFrame(columns=COLS + ["src"])
    df = pd.concat(parts, ignore_index=True)
    return df.drop_duplicates("time", keep="last").sort_values("time").reset_index(drop=True)


def sources(df: pd.DataFrame, names: dict[str, str]) -> list[dict]:
    out = []
    for src, name in names.items():
        sub = df[df.src == src] if "src" in df else df.iloc[:0]
        last = None
        if not sub.empty:
            t = sub.time.iloc[-1]
            last = str(t) if not isinstance(t, (int, np.integer)) else \
                _dt.datetime.fromtimestamp(int(t), IST).strftime("%Y-%m-%d %H:%M")
        out.append({"source": name, "bars": int(len(sub)), "last_time": last})
    return out


async def _prajna(ps, key: str, tf: str, n_sessions: int) -> pd.DataFrame:
    """Prajna's intraday history of `key` (bars.intraday: split/bonus adjusted point in time,
    from Prajna's live DB or the D1 copy per DRISHTI_HISTORY_SOURCE)."""
    d0 = today() - _dt.timedelta(days=int(n_sessions * 1.5) + 7)
    x = await bars.intraday(ps, [key], tf, d0 + _dt.timedelta(days=1))
    rows = [(int(t.timestamp()), o, h, lo, c, v, None) for t, o, h, lo, c, v in
            zip(x.t, x.open, x.high, x.low, x.close, x.volume)]
    df = _frame(rows, "prajna")
    df = df[~df.session_date.isin(SHORT_SESSIONS)]
    keep = sorted(df.session_date.unique())[-n_sessions:]
    return df[df.session_date.isin(keep)]


async def _load_intraday(key: str, tf: str) -> tuple[pd.DataFrame, list[dict]]:
    n = SESSIONS[tf]
    native = tf in ("15m", "1h")
    async with prajna_sessionmaker()() as ps:
        pj = await _prajna(ps, key, tf if native else "1m", n)
    last = pj.session_date.max() if not pj.empty else None
    live = live_1m(key)
    if last is not None:
        live = live[live.session_date > last]
    stored = recorded_1m(key, last)
    rec = merge(stored, live)
    names = {"prajna": f"Prajna ohlcv_bar {tf if native else '1m'}",
             "drishti_1m": "Drishti recorded 1m (Parquet)", "live": "Drishti live snapshot"}
    src = sources(pd.concat([pj, stored, live]), names)
    m = MINUTES[tf]
    if native:
        df = merge(pj, aggregate(rec.drop(columns="src"), m).assign(src="drishti"))
    else:
        df = merge(pj, rec)
        if m > 1:
            df = aggregate(df.drop(columns="src"), m).assign(src="mixed")
    df = df[df.session_date.isin(sorted(df.session_date.unique())[-n:])]
    return df.reset_index(drop=True), src


async def _load_1d(key: str) -> tuple[pd.DataFrame, list[dict]]:
    day = today()
    async with sessionmaker()() as s, prajna_sessionmaker()() as ps:
        df = await bars.official(ps, day, SESSIONS["1d"], keys=[key])
        prov = await bars.provisional(s, day)
    prov = prov[prov.instrument_key == key]
    df = df.assign(src="official")
    if not df.empty:                    # a late official bar (Prajna missed a morning job):
        # fill the sessions in between with Drishti's own 15:40 provisional bar
        async with sessionmaker()() as s2:
            gap = await bars.provisional_range(s2, key, df.session_date.max(), day)
        if not gap.empty:
            df = pd.concat([df, gap.assign(src="provisional")[df.columns]], ignore_index=True)
    if not prov.empty and (df.empty or df.session_date.max() < day):
        df = pd.concat([df, prov.assign(src="provisional")[df.columns] if not df.empty
                        else prov.assign(src="provisional")], ignore_index=True)
    elif df.empty or df.session_date.max() < day:
        # Before 15:40 EOD sync, today's provisional bar doesn't exist yet.
        # Append today's live candle from real-time quote so the chart reflects today's session!
        q = get_live_quote(key)
        if q and q.get("ltp") is not None:
            t = today_ohlc(key, day, float(q["ltp"]))
            if not t["from_recorder"] and q.get("open"):     # a REST quote carries the day OHLC
                t = {"open": q["open"], "high": max(q["high"] or 0, q["ltp"]),
                     "low": min(q["low"] or q["ltp"], q["ltp"]), "close": q["ltp"],
                     "volume": q.get("volume") or 0.0}
            live_bar = pd.DataFrame([{
                "instrument_key": key,
                "session_date": day,
                "open": t["open"], "high": t["high"], "low": t["low"], "close": t["close"],
                "volume": t["volume"],
                "vwap": q.get("vwap") or np.nan,
                "src": "live_quote",
            }])
            df = pd.concat([df, live_bar], ignore_index=True)

    if df.empty:
        return pd.DataFrame(columns=COLS + ["src"]), []
    df = df.sort_values("session_date").reset_index(drop=True)
    df["time"] = df.session_date.astype(str)
    if "vwap" not in df.columns:
        df["vwap"] = np.nan
    src = sources(df, {"official": "Prajna official 1d (adjusted)",
                       "provisional": "Drishti provisional 1d (EOD quote)",
                       "live_quote": "Real-time market quote (Live)"})
    return df[COLS + ["src"]], src


async def load(key: str, tf: str) -> tuple[pd.DataFrame, list[dict]]:
    """(bars with COLS + src, oldest first; sources with their last bar time). Cached TTL_S
    (TTL_LIVE_S for 1m/5m)."""
    hit = _cache.get((key, tf))
    if hit and time.monotonic() - hit[0] < (TTL_LIVE_S if tf in ("1m", "5m") else TTL_S):
        return hit[1]
    out = await (_load_1d(key) if tf == "1d" else _load_intraday(key, tf))
    _cache[(key, tf)] = (time.monotonic(), out)
    if len(_cache) > 256:
        _cache.pop(next(iter(_cache)))
    return out
