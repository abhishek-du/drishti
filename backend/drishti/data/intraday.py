"""Drishti's own nightly intraday bars (P1c, SWITCH_TO_B D3): 1m / 15m / 1h of one session for
the top 500 equities (by 20-session traded value on Drishti's own bar_1d) plus the index keys,
Upstox V3 historical. Files only (no Postgres):

    /windows/drishti_data/intraday/<tf>/<YYYY-MM-DD>/part_<stamp>.parquet
    /windows/drishti_data/intraday/manifest/<YYYY-MM-DD>_<stamp>.json   (per request: status,
                                                                        sha256, candles)
A re-run writes a new part; readers keep the latest fetched_at per (key, bar). Before the switch
decision A applies (historical API only 22:00-05:30, fraction <= 0.5). Read-only on Prajna,
which is used only for the overlap check.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from pathlib import Path
from urllib.parse import quote

import pandas as pd
from sqlalchemy import text

from drishti.core.clock import IST, now
from drishti.core.config import get_settings
from drishti.data import indices

TFS = {"1m": "minutes/1", "15m": "minutes/15", "1h": "hours/1"}
TOP_N = 500


def root() -> Path:
    return get_settings().DRISHTI_DATA_DIR / "intraday"


async def top_keys(s, day: _dt.date, n: int = TOP_N) -> list[str]:
    """Top n NSE_EQ keys by mean close*volume over the 20 sessions up to `day` (Drishti bar_1d,
    latest fetch per key/date), plus the index keys."""
    rows = (await s.execute(text("""
        with b as (select distinct on (instrument_key, session_date) instrument_key,
                          session_date, close * volume as tv
                   from bar_1d where session_date > cast(:d as date) - 40 and session_date <= :d
                   order by instrument_key, session_date, fetched_at desc),
             r as (select instrument_key, tv, row_number() over (partition by instrument_key
                   order by session_date desc) as k from b)
        select instrument_key from r where k <= 20 group by 1
        order by avg(tv) desc nulls last limit :n"""), {"d": day, "n": n})).scalars().all()
    return list(rows) + list(indices.INDEX_KEYS)


def parse(cs: list[list], key: str, fetched: _dt.datetime, sha: str) -> list[dict]:
    """V3 candles [ts, o, h, l, c, vol, oi] -> rows; ts is the bar start with an IST offset."""
    out = []
    for c in cs:
        t = _dt.datetime.fromisoformat(c[0]).astimezone(_dt.timezone.utc)
        out.append({"instrument_key": key, "bar_start_utc": t, "open": float(c[1]),
                    "high": float(c[2]), "low": float(c[3]), "close": float(c[4]),
                    "volume": int(c[5] or 0), "oi": float(c[6]) if len(c) > 6 and c[6] is not None
                    else None, "fetched_at": fetched, "payload_sha256": sha})
    return out


def write(tf: str, day: _dt.date, rows: list[dict], stamp: str) -> Path | None:
    if not rows:
        return None
    p = root() / tf / str(day) / f"part_{stamp}.parquet"
    p.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).sort_values(["instrument_key", "bar_start_utc"]).to_parquet(
        p, compression="zstd", index=False)
    return p


def read(tf: str, day: _dt.date) -> pd.DataFrame:
    parts = sorted((root() / tf / str(day)).glob("part_*.parquet"))
    if not parts:
        return pd.DataFrame()
    df = pd.concat([pd.read_parquet(p) for p in parts])
    return df.sort_values("fetched_at").drop_duplicates(["instrument_key", "bar_start_utc"],
                                                         keep="last")


def missing(day: _dt.date, tfs=tuple(TFS)) -> dict[str, list[str]]:
    """Per tf, the keys of `day` that no manifest has candles for yet. Upstox fills the previous
    session's intraday history over the night (2026-11-04 01:00: 1m only 18 of 524 keys), so a
    later pass fetches only these."""
    have: dict[str, set[str]] = {tf: set() for tf in tfs}
    asked: dict[str, set[str]] = {tf: set() for tf in tfs}
    for f in sorted((root() / "manifest").glob(f"{day}_*.json")):
        for x in json.loads(f.read_text()):
            if x["tf"] in have:
                asked[x["tf"]].add(x["key"])
                if x["candles"] > 0:
                    have[x["tf"]].add(x["key"])
    return {tf: sorted(asked[tf] - have[tf]) for tf in tfs}


def fetch(api, keys: list[str] | dict[str, list[str]], day: _dt.date, tfs=tuple(TFS),
          allowed=lambda: True) -> dict:
    """keys: one list for every tf, or {tf: keys}. `allowed()` is checked before every request
    (decision A's window): the run stops cleanly when it turns false."""
    stamp = now().strftime("%Y%m%dT%H%M%S")
    per_tf = keys if isinstance(keys, dict) else {tf: keys for tf in tfs}
    man, out = [], {"session": str(day), "keys": {tf: len(per_tf.get(tf, [])) for tf in tfs}}
    for tf in tfs:
        rows, c = [], {"ok": 0, "empty": 0, "errors": 0, "candles": 0}
        for k in per_tf.get(tf, []):
            if not allowed():
                out["stopped"] = f"window closed at {now():%H:%M} during {tf}"
                break
            r = api.get_response(f"/v3/historical-candle/{quote(k, safe='')}/{TFS[tf]}/{day}/{day}")
            sha = hashlib.sha256(r.content).hexdigest()
            cs = (r.json().get("data") or {}).get("candles") or [] if r.status_code == 200 else []
            got = [x for x in parse(cs, k, now(), sha)
                   if x["bar_start_utc"].astimezone(IST).date() == day]
            rows += got
            man.append({"tf": tf, "key": k, "status": r.status_code, "sha256": sha,
                        "candles": len(got),
                        "error": None if r.status_code == 200 else r.text[:200]})
            c["ok" if r.status_code == 200 and got else "empty" if r.status_code == 200
              else "errors"] += 1
            c["candles"] += len(got)
        p = write(tf, day, rows, stamp)
        c["bytes"] = p.stat().st_size if p else 0
        out[tf] = c
        if "stopped" in out:
            break
    m = root() / "manifest" / f"{day}_{stamp}.json"
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_text(json.dumps(man))
    return out


async def compare_with_prajna(ps, day: _dt.date, tfs=tuple(TFS)) -> dict:
    """Overlap check before the switch: our bars of `day` vs Prajna's stored bars (latest fetch
    per key/bar) for the same keys. Same vendor: OHLCV should be equal."""
    res = {}
    for tf in tfs:
        ours = read(tf, day)
        if ours.empty:
            res[tf] = {"ours": 0}
            continue
        keys = sorted(ours.instrument_key.unique())
        theirs = pd.DataFrame((await ps.execute(text("""
            select distinct on (instrument_key, bar_start_utc) instrument_key, bar_start_utc,
                   open, high, low, close, volume from ohlcv_bar
            where timeframe = :tf and session_date = :d and instrument_key = any(:k)
            order by instrument_key, bar_start_utc, fetched_at desc"""),
            {"tf": tf, "d": day, "k": keys})).all(),
            columns=["instrument_key", "bar_start_utc", "open", "high", "low", "close", "volume"])
        if theirs.empty:
            res[tf] = {"ours": len(ours), "prajna": 0}
            continue
        for col in ("open", "high", "low", "close", "volume"):
            theirs[col] = theirs[col].astype(float)
        m = ours.merge(theirs, on=["instrument_key", "bar_start_utc"], how="outer",
                       suffixes=("", "_p"), indicator=True)
        both = m[m._merge == "both"]
        diff = both[(both[["open", "high", "low", "close", "volume"]].astype(float).to_numpy()
                     != both[["open_p", "high_p", "low_p", "close_p", "volume_p"]].to_numpy()
                     ).any(axis=1)]
        res[tf] = {"ours": len(ours), "prajna": len(theirs), "both": len(both),
                   "equal": len(both) - len(diff), "differ": len(diff),
                   "differ_keys": sorted(diff.instrument_key.unique())[:20],
                   "only_ours": int((m._merge == "left_only").sum()),
                   "only_prajna": int((m._merge == "right_only").sum())}
    return res
