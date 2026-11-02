"""Live recorder: Upstox V3 market-data WebSocket, ONE connection, full mode, the current
watchlist (<= 300 keys) + Nifty 50 / Nifty 500 / India VIX.

LIVE VIEW + tick archive only: nothing here is read by drishti-swing-v1, signals, Scout or the
forward test. PAPER ONLY; no order path.

Window: decision A (before the switch): from 09:21 IST, after Prajna's pre-open recorder
(both connections, 08:55-09:20) has closed. After the switch (DRISHTI_UPSTOX_LOGIN_ENABLED):
from 09:15. Stop 15:35. Regular sessions only (SC1).

Token: read only (token_reader); the authorized WebSocket URL is single use and carries a
credential, so only its host is ever logged. Design follows Prajna's
`app/sources/upstox_preopen_ws.py` (archive first, stale watchdog on subscribed data,
backoff) - re-written, not imported.

Outputs:
  ticks      /windows/drishti_data/ticks/YYYY-MM-DD/ticks_HHMM.zst   (store.TickWriter)
  1m bars    /windows/drishti_data/bars_1m/YYYY-MM-DD/bars_HHMMSS.parquet (every 5 min)
  live view  /dev/shm/drishti_live/summary.json and c1m/<key>.json (<= 200 ms after a tick)
  health     backend/var/run/live_health.json (every 5 s)

    .venv/bin/python -m drishti.live.recorder [--until HH:MM] [--keys K1,K2]
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import os
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
from sqlalchemy import text

from drishti.core import runlog
from drishti.core.clock import IST, now
from drishti.core.config import BACKEND, get_settings
from drishti.core.db import sessionmaker
from drishti.data import holidays
from drishti.live import store
from drishti.live.candles import Builder

AUTHORIZE = "https://api.upstox.com/v3/feed/market-data-feed/authorize"
INDEX_KEYS = ("NSE_INDEX|Nifty 50", "NSE_INDEX|Nifty 500", "NSE_INDEX|India VIX")
MAX_KEYS = 300
LIVE_DIR = Path("/dev/shm/drishti_live")
HEALTH = BACKEND / "var" / "run" / "live_health.json"
STALE_S = 60
SNAP_S = 0.2           # a key's live snapshot is written <= 200 ms after its tick (Track C ask
                       # 2026-10-30); summary.json with it, and at least once a second
BACKOFF = (2, 5, 10, 30, 60)
LABEL = "LIVE VIEW - not used by drishti-swing-v1"


def window(day: _dt.date) -> tuple[_dt.datetime, _dt.datetime]:
    after_switch = get_settings().DRISHTI_UPSTOX_LOGIN_ENABLED
    start = _dt.time(9, 15) if after_switch else _dt.time(9, 21)
    return (_dt.datetime.combine(day, start, IST),
            _dt.datetime.combine(day, _dt.time(15, 35), IST))


async def watchlist_keys(s) -> list[str]:
    rows = (await s.execute(text("""
        with r as (select run_id from watchlist_run order by session_date desc, created_at desc
                   limit 1)
        select instrument_key from watchlist_event where run_id = (select run_id from r)
          and event <> 'DROPPED' order by rank nulls last limit :n"""),
        {"n": MAX_KEYS})).scalars().all()
    return list(rows)


def _write_json(p: Path, obj) -> None:
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":"), default=str))
    os.replace(tmp, p)


class Recorder:
    def __init__(self, keys: list[str], until: _dt.datetime):
        cfg = get_settings()
        self.keys, self.until = keys, until
        self.ticks = store.TickWriter(cfg.DRISHTI_DATA_DIR / "ticks")
        self.bars_root = cfg.DRISHTI_DATA_DIR / "bars_1m"
        self.b = Builder()
        self.h = {"label": LABEL, "state": "starting", "keys": len(keys), "connections": 0,
                  "reconnects": 0, "frames": 0, "last_frame_at": None, "last_data_at": None,
                  "started_at": now().isoformat(), "until": until.isoformat(), "alerts": [],
                  "tick_bytes_raw": 0, "tick_files": 0, "bars_rows": 0, "endpoint": None,
                  "reconnect_log": [], "max_loop_gap_s": 0.0}
        self._changed: set[str] = set()
        self._bar_rows = 0

    # ---- outputs ---------------------------------------------------------------------
    def health(self, **kw) -> None:
        self.h.update(kw, frames=self.ticks.frames if self.ticks.frames else self.h["frames"],
                      tick_bytes_raw=self.ticks.raw_bytes, tick_files=len(self.ticks.files),
                      tick_writing=not self.ticks.stopped_by_guard,
                      disk_free_gb=round(store.free_gb(self.ticks.root.parent), 2),
                      updated_at=now().isoformat())
        HEALTH.parent.mkdir(parents=True, exist_ok=True)
        _write_json(HEALTH, self.h)

    def alert(self, msg: str) -> None:
        self.h["alerts"].append({"at": now().isoformat(), "msg": msg})
        log = BACKEND / "var" / "logs" / "cron.log"
        with open(log, "a") as f:
            f.write(f"[{now():%Y-%m-%d %H:%M:%S} IST] DRISHTI_LIVE_ALERT {msg}\n")

    def snapshot(self) -> None:
        LIVE_DIR.joinpath("c1m").mkdir(parents=True, exist_ok=True)
        _write_json(LIVE_DIR / "summary.json", {"label": LABEL, "updated_at": now().isoformat(),
                                                "keys": self.b.summary()})
        for k in self._changed:
            _write_json(LIVE_DIR / "c1m" / f"{quote(k, safe='')}.json", self.b.candles(k, 1))
        self._changed.clear()

    def flush_bars(self, final: bool = False) -> None:
        if final:
            closed = [(k, b) for k, st in self.b.keys.items() for m, b in sorted(st.bars.items())
                      if m >= st.flushed_upto]
        else:
            closed = self.b.closed_bars(time.time())
        rows = [{"instrument_key": k, **b.as_dict()} for k, b in closed]
        if store.write_bars(self.bars_root, now().date(), rows, now()):
            self._bar_rows += len(rows)
            self.h["bars_rows"] = self._bar_rows

    # ---- feed ------------------------------------------------------------------------
    def on_frame(self, data: bytes) -> None:
        from drishti.live.proto import MarketDataFeed_pb2 as pb
        recv = now()
        self.ticks.append(data, recv)                       # archive first
        self.h["frames"] = self.h.get("frames", 0) + 1
        self.h["last_frame_at"] = recv.isoformat()
        try:
            r = pb.FeedResponse.FromString(data)
        except Exception:
            self.h["decode_errors"] = self.h.get("decode_errors", 0) + 1
            return
        for k, f in r.feeds.items():
            w = f.WhichOneof("FeedUnion")
            if w == "ltpc":
                lt, atp, vtt = f.ltpc, None, None
            elif w == "fullFeed":
                inner = f.fullFeed.WhichOneof("FullFeedUnion")
                ff = getattr(f.fullFeed, inner)
                lt = ff.ltpc
                atp = getattr(ff, "atp", None) or None
                vtt = getattr(ff, "vtt", None) if inner == "marketFF" else None
            else:
                continue
            self.b.tick(k, lt.ltp, lt.ltt, vtt, atp, lt.cp)
            self._changed.add(k)
            self.h["last_data_at"] = recv.isoformat()

    async def authorize(self) -> str:
        from drishti.broker.upstox.token_reader import read_token
        tok = read_token()
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(AUTHORIZE, headers={"Authorization": f"Bearer {tok}",
                                                "Accept": "application/json"})
        if r.status_code in (401, 403):
            raise PermissionError(f"feed authorize rejected: HTTP {r.status_code}")
        r.raise_for_status()
        url = (r.json().get("data") or {}).get("authorized_redirect_uri") or ""
        self.h["endpoint"] = urlsplit(url).netloc          # never the query (credential)
        return url

    async def session(self, url: str) -> None:
        from websockets.asyncio.client import connect
        async with connect(url, open_timeout=20, ping_interval=20, ping_timeout=20,
                           max_size=2 ** 24, compression=None) as ws:
            self.h["connections"] += 1
            self.health(state="connected")
            for i in range(0, len(self.keys), 500):
                await ws.send(json.dumps({"guid": uuid.uuid4().hex, "method": "sub", "data": {
                    "mode": "full", "instrumentKeys": self.keys[i:i + 500]}}).encode())
            last_data = time.monotonic()
            next_snap = next_health = next_guard = last_snap = time.monotonic()
            next_bars = time.monotonic() + 300
            prev_t = time.monotonic()
            while now() < self.until:
                gap = time.monotonic() - prev_t      # > ~1 s means our loop blocked (diagnosis
                prev_t = time.monotonic()             # of the keepalive ping timeouts)
                if gap > self.h["max_loop_gap_s"]:
                    self.h["max_loop_gap_s"] = round(gap, 3)
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=SNAP_S)
                    if isinstance(msg, bytes):
                        before = self.h.get("last_data_at")
                        self.on_frame(msg)
                        if self.h.get("last_data_at") != before:
                            last_data = time.monotonic()
                except TimeoutError:
                    pass
                t = time.monotonic()
                if t - last_data > STALE_S:
                    self.health(state="stale")
                    raise TimeoutError("stale: no subscribed data for 60 s")
                if (self._changed and t >= next_snap) or t >= last_snap + 1:
                    self.snapshot()
                    next_snap, last_snap = t + SNAP_S, t
                if t >= next_guard:
                    if not self.ticks.stopped_by_guard and self.ticks.check_guard():
                        self.alert(f"disk guard: < {store.GUARD_GB} GB free, tick writing stopped")
                    next_guard = t + 60
                if t >= next_bars:
                    self.flush_bars()
                    next_bars = t + 300
                if t >= next_health:
                    self.health(state="connected")
                    next_health = t + 5
            await ws.close()

    async def run(self) -> dict:
        failures = 0
        while now() < self.until:
            try:
                await self.session(await self.authorize())
                failures = 0
            except PermissionError as e:
                self.alert(f"auth: {e}")
                self.health(state="auth_failed")
                break
            except Exception as e:                           # closed, stale, network
                failures += 1
                self.h["reconnects"] += 1
                self.h["reconnect_log"] = (self.h["reconnect_log"] + [{
                    "at": now().isoformat(timespec="seconds"),
                    "error": f"{type(e).__name__}: {e}"[:160],
                    "last_data_at": self.h.get("last_data_at"),
                    "max_loop_gap_s": self.h["max_loop_gap_s"]}])[-20:]
                self.health(state="reconnecting", last_error=f"{type(e).__name__}: {e}"[:200])
                if failures > 20:
                    self.alert("gave up after 20 consecutive failed connections")
                    break
                await asyncio.sleep(BACKOFF[min(failures - 1, len(BACKOFF) - 1)])
        self.flush_bars(final=True)
        self.snapshot()
        self.ticks.close()
        day = now().date()
        self.health(state="stopped", tick_gb_today=round(store.day_gb(self.ticks.root, day), 4))
        return {k: self.h.get(k) for k in ("keys", "connections", "reconnects", "frames",
                                           "tick_bytes_raw", "tick_files", "bars_rows",
                                           "tick_gb_today", "state")}


async def main(args: list[str]) -> int:
    t = now()
    async with sessionmaker()() as s:
        if not holidays.is_session(t.date(), await holidays.latest(s, t.date())):
            print(json.dumps({"skipped": "not a session"}))
            return 0
        start, stop = window(t.date())
        if "--until" in args:
            hh, mm = args[args.index("--until") + 1].split(":")
            stop = min(stop, t.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0))
        if t < start or t >= stop:
            print(json.dumps({"skipped": f"outside the window {start:%H:%M}-{stop:%H:%M}"}))
            return 0
        keys = args[args.index("--keys") + 1].split(",") if "--keys" in args \
            else await watchlist_keys(s)
        keys = list(dict.fromkeys(keys[:MAX_KEYS] + list(INDEX_KEYS)))
        cfg = get_settings()
        gone = store.prune(cfg.DRISHTI_DATA_DIR / "ticks", t.date())
        rid = await runlog.start(s, "LIVE_RECORDER", t.date(),
                                 {"keys": len(keys), "until": stop.isoformat(), "pruned": gone})
        rec = Recorder(keys, stop)
        import signal
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):     # stop cleanly: flush bars, close ticks
            loop.add_signal_handler(sig, lambda: setattr(rec, "until", now()))
        try:
            out = await rec.run()
        except BaseException as e:
            await runlog.end(s, rid, "FAILED", error=f"{type(e).__name__}: {e}"[:500])
            raise
        await runlog.end(s, rid, "OK" if out["frames"] else "PARTIAL", out)
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
