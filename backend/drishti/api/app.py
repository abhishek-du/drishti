"""Drishti API (read only; 127.0.0.1:8700). PAPER ONLY: no endpoint places or changes anything.

Blind rule (user P0 decision 4): no outcome, P&L or hit-rate of any watchlist, signal or model
is served before the look; /api/health is the ops view (freshness, counts, errors).

    .venv/bin/uvicorn drishti.api.app:app --host 127.0.0.1 --port 8700
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import text

from drishti.api import chart_api, pf_api, scan_api
from drishti.core.clock import today
from drishti.core.config import BACKEND
from drishti.core.db import prajna_sessionmaker, sessionmaker
from drishti.data import bars, manual
from drishti.features import candles, indicators, levels
from drishti.scout import screener_b as sb

LIVE_DIR = Path("/dev/shm/drishti_live")
LIVE_HEALTH = BACKEND / "var" / "run" / "live_health.json"
LIVE_LABEL = "LIVE VIEW - not used by drishti-swing-v1"

app = FastAPI(title="Drishti", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.include_router(chart_api.router)  # Track C chart endpoints (read only)
app.include_router(scan_api.router)   # scanner + journal (decision support; the only writes)
app.include_router(pf_api.router)     # paper portfolio pf-v1 (read only)


def _f(x):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), 4)


@app.get("/api/health")
async def health() -> dict:
    async with sessionmaker()() as s:
        runs = (await s.execute(text("""
            select distinct on (r.kind) r.kind, r.session_date, r.started_at, e.finished_at,
                   e.status, e.error
            from run r left join run_end e using (run_id) where r.kind not like 'TEST%'
            order by r.kind, r.started_at desc"""))).mappings().all()
        fails = (await s.execute(text("""select r.kind, r.started_at, e.error from run r
            join run_end e using (run_id) where e.status = 'FAILED'
            and r.started_at > now() - interval '7 days' order by r.started_at desc limit 20"""))
        ).mappings().all()
        fresh = (await s.execute(text("""select
            (select max(session_date) from eod_quote) as eod_provisional,
            (select max(session_date) from index_bar where source = 'UPSTOX_HIST') as index_official,
            (select max(session_date) from index_bar where source = 'UPSTOX_QUOTE') as index_provisional,
            (select max(session_date) from watchlist_run) as watchlist"""))).mappings().one()
        surv = await manual.surveillance_freshness(s, today())
        wl = (await s.execute(text("""select session_date, count(*) filter (where event <> 'DROPPED')
            from watchlist_event group by 1 order by 1 desc limit 10"""))).all()
    async with prajna_sessionmaker()() as ps:
        off = (await ps.execute(text("""select max(session_date) from ohlcv_bar
            where timeframe = '1d' and session_date > current_date - 10"""))).scalar()
    live = None
    try:
        live = json.loads(LIVE_HEALTH.read_text())
    except (OSError, ValueError):
        pass
    return {"live": live, "today": str(today()), "runs": [dict(r) for r in runs], "failures_7d": [dict(f) for f in fails],
            "freshness": {**dict(fresh), "prajna_official_1d": off}, "surveillance": surv,
            "watchlist_sizes": [{"session": str(d), "listed": n} for d, n in wl]}


@app.get("/api/watchlist/dates")
async def watchlist_dates() -> list[str]:
    async with sessionmaker()() as s:
        return [str(d) for d in (await s.execute(text(
            "select distinct session_date from watchlist_run order by 1 desc"))).scalars()]


@app.get("/api/watchlist")
async def watchlist(date: _dt.date | None = None) -> dict:
    async with sessionmaker()() as s:
        run = (await s.execute(text("""select run_id, session_date, version, params, bar_source,
            regime, surveillance, counts, created_at from watchlist_run
            where (cast(:d as date) is null or session_date = :d)
            order by session_date desc, created_at desc limit 1"""), {"d": date})).mappings().first()
        if run is None:
            raise HTTPException(404, "no watchlist run")
        rows = (await s.execute(text("""select instrument_key, symbol, event, rank, rs_score,
            metrics, flags from watchlist_event where run_id = :r
            order by rank nulls last, instrument_key"""), {"r": run["run_id"]})).mappings().all()

    clean_rows = [dict(r, rs_score=_f(r["rs_score"])) for r in rows if r["event"] != "DROPPED"]
    alpha_rows, alpha_counts = sb.extract_alpha_watchlist(clean_rows)
    tagged_rows = [sb.tag_alpha(r) for r in clean_rows]

    return {
        "run": {**dict(run), "run_id": str(run["run_id"])},
        "rows": tagged_rows,
        "dropped": [r["instrument_key"] for r in rows if r["event"] == "DROPPED"],
        "alpha_rows": alpha_rows,
        "alpha_counts": alpha_counts,
    }


@app.get("/api/search")
async def search(q: str = Query(min_length=1, max_length=32)) -> list[dict]:
    async with prajna_sessionmaker()() as ps:
        rows = (await ps.execute(text("""select instrument_key, trading_symbol, name
            from canon_instrument where segment = 'NSE_EQ'
              and (trading_symbol ilike :p or name ilike :c)
            order by (trading_symbol ilike :p) desc, length(trading_symbol) limit 15"""),
            {"p": q + "%", "c": "%" + q + "%"})).mappings().all()
    return [dict(r) for r in rows]


@app.get("/api/chart")
async def chart(key: str, days: int = Query(500, ge=60, le=2000)) -> dict:
    """Adjusted daily bars (official, plus today's provisional bar when Prajna has none yet),
    EMA20 / SMA50 / SMA200, the S/R zones known on the last bar, candle flags and the dates the
    stock was on the watchlist. Signals: none until P3."""
    day = today()
    async with sessionmaker()() as s, prajna_sessionmaker()() as ps:
        df = await bars.official(ps, day, days, keys=[key])
        prov = await bars.provisional(s, day)
        prov = prov[prov.instrument_key == key]
        source = "official"
        if not prov.empty and (df.empty or df.session_date.max() < day):
            df = pd.concat([df, prov[df.columns] if not df.empty else prov], ignore_index=True)
            source = "provisional"
        sym = (await ps.execute(text("""select trading_symbol, name, sector from canon_instrument
            where instrument_key = :k"""), {"k": key})).mappings().first()
        wl = (await s.execute(text("""select distinct session_date from watchlist_event
            where instrument_key = :k and event <> 'DROPPED' order by 1"""), {"k": key})).scalars()
        wl = [str(d) for d in wl]
    if df.empty:
        raise HTTPException(404, "no bars")
    df = df.sort_values("session_date").reset_index(drop=True)
    ind = indicators.basic(df)
    fl = candles.flags(pd.concat([df, ind[["atr"]]], axis=1))
    atr = ind.atr.iloc[-1]
    zs = levels.zones(df.high.to_numpy(), df.low.to_numpy(), float(atr)) if atr > 0 else []
    t = df.session_date.astype(str).tolist()
    names, on = np.array(candles.FLAGS), fl.to_numpy()
    marks = [{"time": t[i], "flags": names[on[i]].tolist()} for i in range(len(df)) if on[i].any()]
    return {
        "key": key, "symbol": sym and sym["trading_symbol"], "name": sym and sym["name"],
        "sector": sym and sym["sector"], "last_bar_source": source,
        "bars": [{"time": t[i], "open": _f(r.open), "high": _f(r.high), "low": _f(r.low),
                  "close": _f(r.close), "volume": _f(r.volume)} for i, r in enumerate(df.itertuples())],
        "lines": {n: [{"time": t[i], "value": _f(v)} for i, v in enumerate(ind[n]) if pd.notna(v)]
                  for n in ("ema20", "sma50", "sma200")},
        "zones": [{k: (_f(v) if isinstance(v, float) else v) for k, v in z.items()}
                  | {"first": t[z["first"]], "last": t[z["last"]]} for z in zs],
        "candles": marks, "bullish_flags": list(candles.BULLISH),
        "last": {"atr": _f(atr), "rsi": _f(ind.rsi.iloc[-1]), "atr_pct": _f(ind.atr_pct.iloc[-1])},
        "watchlist_dates": wl, "signals": [],
    }


def _live_summary() -> dict:
    try:
        return json.loads((LIVE_DIR / "summary.json").read_text())
    except (OSError, ValueError):
        return {"label": LIVE_LABEL, "updated_at": None, "keys": {}}


def _live_candles(key: str, tf: int) -> list[dict]:
    try:
        c1 = json.loads((LIVE_DIR / "c1m" / f"{quote(key, safe='')}.json").read_text())
    except (OSError, ValueError):
        return []
    if tf == 1:
        return c1
    out: dict[int, dict] = {}
    for b in c1:
        t = b["time"] // (tf * 60) * tf * 60
        x = out.get(t)
        if x is None:
            out[t] = dict(b, time=t)
        else:
            x.update(high=max(x["high"], b["high"]), low=min(x["low"], b["low"]),
                     close=b["close"], volume=x["volume"] + b["volume"], vwap=b["vwap"])
    return [out[t] for t in sorted(out)]


@app.get("/api/live/summary")
async def live_summary() -> dict:
    return _live_summary()


@app.get("/api/live/candles")
async def live_candles(key: str, tf: int = Query(1, ge=1, le=5)) -> dict:
    return {"label": LIVE_LABEL, "key": key, "tf": tf, "candles": _live_candles(key, tf)}


def _live_mtimes(key: str | None) -> tuple[int, int]:
    def m(p: Path) -> int:
        try:
            return p.stat().st_mtime_ns
        except OSError:
            return 0
    return (m(LIVE_DIR / "summary.json"),
            m(LIVE_DIR / "c1m" / f"{quote(key, safe='')}.json") if key else 0)


@app.websocket("/api/live/ws")
async def live_ws(ws: WebSocket, key: str | None = None, tf: int = 1):
    """Push the live summary (and the candles of `key`) as soon as the recorder's snapshot
    changes (checked every 200 ms), and at least every 2 s as a heartbeat. Read only; nothing
    is accepted from the client. Same message format as before."""
    await ws.accept()
    tf = 5 if tf == 5 else 1
    seen, last = None, 0.0
    try:
        while True:
            cur = _live_mtimes(key)
            t = asyncio.get_running_loop().time()
            if cur != seen or t - last >= 2.0:
                msg = {"label": LIVE_LABEL, "summary": _live_summary()}
                if key:
                    msg["candles"] = _live_candles(key, tf)
                await ws.send_text(json.dumps(msg, separators=(",", ":")))
                seen, last = cur, t
            await asyncio.sleep(0.2)
    except (WebSocketDisconnect, RuntimeError):
        return
