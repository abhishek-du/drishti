"""Decision-support scanner jobs (PAPER ONLY; "Decision support - detections are NOT validated
edges. Your call. Paper only.").

    .venv/bin/python -m drishti.scanner.run scan_eod [--date D]     # 19:40, after Scout
    .venv/bin/python -m drishti.scanner.run scan_intraday           # every 15 min, 09:31-15:31
    .venv/bin/python -m drishti.scanner.run scan_grade              # nightly, after daily1d

scan_eod: the eligible universe (spec section 2 proxy: EQ series, >= 200 sessions, 20-session
traded value >= 20 crore, close >= 50, top 500, not ASM/GSM) on Scout's bars (official history
+ the provisional bar of T). scan_intraday: the watchlist on 15m bars (history from Prajna's
15m until the switch, else the recorder's 1m Parquet; today from the live 1m snapshot), last
CLOSED bar only. scan_grade: every alert, triple barrier on real bars (scanner/grade.py).
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import sys
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pandas as pd
from sqlalchemy import text

from drishti.core import runlog
from drishti.core.clock import IST, now, today
from drishti.core.config import get_settings
from drishti.core.db import prajna_sessionmaker, sessionmaker
from drishti.data import bars, holidays, master
from drishti.scanner import detect, grade, notify

LIVE_C1M = Path("/dev/shm/drishti_live/c1m")
SESSION_OPEN = _dt.time(9, 15)


# ---- universes --------------------------------------------------------------------------

async def eligible(s, b: pd.DataFrame, day: _dt.date) -> list[str]:
    from drishti.run.p2 import manual_isins
    eq = await manual_isins(s, ("EQUITY_L",), day, "EQ")
    blocked = await manual_isins(s, ("ASM", "GSM"), day)
    g = b.groupby("instrument_key", sort=False)
    last = g.tail(1).set_index("instrument_key")
    n = g.size()
    tv = (b.close * b.volume).groupby(b.instrument_key).apply(lambda x: x.iloc[-21:-1].mean())
    ok = last[(last.session_date == day) & (n.reindex(last.index) >= 201)
              & (tv.reindex(last.index) >= 20e7) & (last.close >= 50)]
    isin = ok.index.str.split("|").str[1]
    ok = ok[isin.isin(eq) & ~isin.isin(blocked)]
    return list(tv.reindex(ok.index).sort_values(ascending=False).index[:500])


async def watchlist(s) -> list[str]:
    return list((await s.execute(text("""
        with r as (select run_id from watchlist_run order by session_date desc, created_at desc
                   limit 1)
        select instrument_key from watchlist_event where run_id = (select run_id from r)
          and event <> 'DROPPED' order by rank nulls last limit 300"""))).scalars())


# ---- 15m bars ---------------------------------------------------------------------------

def _bucket15(ts: pd.Series) -> pd.Series:
    """Bar start (UTC) of the NSE 15m bar holding each timestamp (bars start 09:15 IST)."""
    t = ts.dt.tz_convert(IST)
    base = t.dt.normalize() + pd.Timedelta(hours=9, minutes=15)
    k = ((t - base) // pd.Timedelta(minutes=15)).clip(lower=0)
    return (base + k * pd.Timedelta(minutes=15)).dt.tz_convert("UTC")


def agg15(m1: pd.DataFrame) -> pd.DataFrame:
    """1m candles (time = minute start, UTC) -> 15m bars."""
    if m1.empty:
        return m1
    m1 = m1.sort_values(["instrument_key", "t"]).assign(t15=lambda x: _bucket15(x.t))
    g = m1.groupby(["instrument_key", "t15"], sort=True)
    out = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                close=("close", "last"), volume=("volume", "sum")).reset_index()
    return out.rename(columns={"t15": "t"})


def live_1m(keys: list[str]) -> pd.DataFrame:
    rows = []
    for k in keys:
        try:
            for c in json.loads((LIVE_C1M / f"{quote(k, safe='')}.json").read_text()):
                rows.append({"instrument_key": k, "t": c["time"], "open": c["open"],
                             "high": c["high"], "low": c["low"], "close": c["close"],
                             "volume": c["volume"] or 0})
        except (OSError, ValueError):
            continue
    df = pd.DataFrame(rows)
    if not df.empty:
        df["t"] = pd.to_datetime(df.t, unit="s", utc=True)
    return df


def parquet_1m(keys: list[str], since: _dt.date) -> pd.DataFrame:
    root = get_settings().DRISHTI_DATA_DIR / "bars_1m"
    parts = []
    for d in sorted(p for p in root.glob("20*") if p.name >= str(since)):
        for f in d.glob("*.parquet"):
            parts.append(pd.read_parquet(f))
    if not parts:
        return pd.DataFrame()
    df = pd.concat(parts)
    df = df[df.instrument_key.isin(keys)].rename(columns={"time": "t"})
    df["t"] = pd.to_datetime(df.t, unit="s", utc=True)
    return df.drop_duplicates(["instrument_key", "t"], keep="last")[
        ["instrument_key", "t", "open", "high", "low", "close", "volume"]]


async def bars15(ps, keys: list[str], since: _dt.date) -> pd.DataFrame:
    """15m bars of `keys` since `since`: the adjusted 15m history for past days, the recorder's 1m
    (Parquet + the live snapshot) for days Prajna does not have (today; everything after the
    switch). Bars of different sources never mix within one day."""
    # split/bonus adjusted history (bars.intraday; Prajna live or the D1 copy, 2026-11-12)
    pj = (await bars.intraday(ps, keys, "15m", since))[
        ["instrument_key", "t", "open", "high", "low", "close", "volume"]]
    own = agg15(pd.concat([parquet_1m(keys, since), live_1m(keys)])
                .drop_duplicates(["instrument_key", "t"], keep="last"))
    if not pj.empty:
        for c in ("open", "high", "low", "close", "volume"):
            pj[c] = pj[c].astype(float)
        pj_days = set(zip(pj.instrument_key, pj.t.dt.tz_convert(IST).dt.date))
        if not own.empty:
            own = own[[(k, d) not in pj_days for k, d in
                       zip(own.instrument_key, own.t.dt.tz_convert(IST).dt.date)]]
    out = pd.concat([pj, own]) if not own.empty else pj
    return utc_t(out).sort_values(["instrument_key", "t"]).reset_index(drop=True)


def utc_t(df: pd.DataFrame) -> pd.DataFrame:
    """One datetime64[UTC] `t` column: Prajna's rows come as python datetimes, the recorder's
    as pandas timestamps, and their concat is an object column (scan_grade 2026-11-04 failed on
    `.dt`)."""
    return df.assign(t=pd.to_datetime(df.t, utc=True)) if len(df) else df


# ---- scans ------------------------------------------------------------------------------

async def _context(s, day: _dt.date) -> dict:
    r = (await s.execute(text("""select regime from watchlist_run where session_date <= :d
        order by session_date desc, created_at desc limit 1"""), {"d": day})).scalar()
    r = r or {}
    return {"regime": r.get("status"), "spec_gate": r.get("spec_gate"),
            "regime_date": r.get("nifty500_date")}


async def store_hits(s, rid, day, tf: str, bar_time, key: str, sym, hs: list[dict],
                     ctx: dict, stamps: list | None = None) -> list[int]:
    """`stamps`: the bar date/time of each row of the frame the hits came from, so the context
    records the dates of the zone pivots (audit: every pivot is <= the alert's bar)."""
    cfg = get_settings()
    ids = []
    for h in hs:
        z = detect.size(h, cfg.DRISHTI_SCAN_CAPITAL, cfg.DRISHTI_SCAN_RISK_PCT)
        zu = {n: {**v, "first": stamps[v["first"]], "last": stamps[v["last"]],
                  "confirmed": stamps[min(v["last"] + 5, len(stamps) - 1)]}
              for n, v in h.get("zones_used", {}).items()
              if v.get("first") is not None} if stamps is not None else {}
        hctx = {**ctx, "zones_used": zu, "target": z["target"], "target_basis": z["target_basis"],
                "net_rr_r1": z["net_rr_r1"], "net_rr_target": z["net_rr_target"],
                "low_rr": z["low_rr"], "broken_r1": z["broken_r1"], "net_r_at_r1": z["net_r_at_r1"],
                "tags": z["tags"]}
        i = (await s.execute(text("""insert into scan_alert(run_id, session_date, tf, bar_time,
            instrument_key, symbol, detector, version, entry_ref, stop, r1, r2, rr_r1, qty,
            capital, risk_pct, cost_r, atr, context) values (:r, :d, :tf, :bt, :k, :sym, :det,
            :v, :e, :st, :r1, :r2, :rr, :q, :cap, :rp, :cr, :atr, cast(:ctx as jsonb))
            on conflict (instrument_key, detector, tf, bar_time) do nothing returning id"""),
            {"r": rid, "d": day, "tf": tf, "bt": bar_time, "k": key, "sym": sym,
             "det": z["detector"], "v": detect.VERSION, "e": z["entry_ref"], "st": z["stop"],
             "r1": z["r1"], "r2": z["r2"], "rr": z["rr_r1"], "q": z["qty"],
             "cap": cfg.DRISHTI_SCAN_CAPITAL, "rp": cfg.DRISHTI_SCAN_RISK_PCT,
             "cr": z["cost_r"], "atr": z["atr"], "ctx": json.dumps(hctx, default=str)})).first()
        if i:
            ids.append(i[0])
    return ids


def _ctx_row(df: pd.DataFrame, base: dict) -> dict:
    r = df.iloc[-1]
    from drishti.features import levels
    ind = df[["sma50", "sma200"]]
    return {**base, "close": float(r.close), "rsi": None if pd.isna(r.rsi) else round(float(r.rsi), 1),
            "volr20": None if pd.isna(r.volr20) else round(float(r.volr20), 2),
            "trend": levels.trend(df, ind),
            "flags": [f for f in ("hammer", "engulfing_bull", "morning_star") if bool(r[f])]}


def eod_as_of(day: _dt.date, t: _dt.datetime) -> _dt.datetime:
    """What the 19:45 scan of `day` could know: now for the live run, else 19:45 IST of `day`
    (a re-run for a past day must not use bars or CA factors that became known later)."""
    return t if t.astimezone(IST).date() <= day else _dt.datetime.combine(day, _dt.time(19, 45), IST)


async def scan_eod(s, ps, rid, day: _dt.date) -> dict:
    as_of = eod_as_of(day, now())
    b, source = await bars.daily(s, ps, day, as_of=as_of)
    keys = await eligible(s, b, day)
    syms = {k: a.get("trading_symbol") for k, a in (await master.state(s)).items()}
    base = {**(await _context(s, day)), "bar_source": source, "as_of": as_of.isoformat(),
            "last_bar": str(day)}
    n_hits, ids = {}, []
    sub = b[b.instrument_key.isin(keys)]
    for k, g in sub.groupby("instrument_key", sort=False):
        df = detect.prepare(g[["open", "high", "low", "close", "volume"]])
        hs = detect.hits(df)
        if not hs:
            continue
        bt = _dt.datetime.combine(day, _dt.time(15, 30), IST)
        assert g.session_date.max() <= day                      # point in time
        ids += await store_hits(s, rid, day, "1d", bt, k, syms.get(k), hs, _ctx_row(df, base),
                                [str(d) for d in g.session_date])
        for h in hs:
            n_hits[h["detector"]] = n_hits.get(h["detector"], 0) + 1
    await s.commit()
    return {"session": str(day), "universe": len(keys), "bar_source": source, "hits": n_hits,
            "alerts_new": len(ids), "alert_ids": ids}


async def live_radar(s, ps, rid, t: _dt.datetime, last_closed: pd.Timestamp,
                     wl: list[str]) -> tuple[dict, list[str]]:
    """The Live Radar pass (scanner/radar.py) for the closed 15m bar, then today's radar keys.
    A radar failure never stops the watchlist scan."""
    from drishti.scanner import radar
    out: dict = {"label": radar.LABEL}
    try:
        keys, _ = await radar.universe(s, ps, t.date())
        out["flushed"] = radar.wait_for_flush(t.date(), last_closed + pd.Timedelta(minutes=15))
        m1 = parquet_1m(keys, t.date())
        out.update(await radar.step(s, ps, rid, t.date(), last_closed, m1, set(wl)))
    except Exception as e:
        await s.rollback()
        out["failed"] = f"{type(e).__name__}: {e}"[:300]
    return out, await radar.todays_keys(s, t.date())


async def scan_intraday(s, ps, rid, t: _dt.datetime) -> dict:
    wl = await watchlist(s)
    last_closed = _bucket15(pd.Series([pd.Timestamp(t)])).iloc[0] - pd.Timedelta(minutes=15)
    radar_out, radar_keys = await live_radar(s, ps, rid, t, last_closed, wl)
    keys = wl + [k for k in radar_keys if k not in set(wl)]
    df15 = await bars15(ps, keys, t.date() - _dt.timedelta(days=25))
    if df15.empty:
        return {"skipped": "no 15m bars", "radar": radar_out}
    syms = {k: a.get("trading_symbol") for k, a in (await master.state(s)).items()}
    base = await _context(s, t.date())
    from drishti.scanner.radar import CAVEAT, LABEL as RADAR_LABEL
    src = {k: "watchlist" for k in wl}
    src.update({k: "radar" for k in radar_keys if k not in src})
    ids, n_hits = [], {}
    for k, g in df15.groupby("instrument_key", sort=False):
        g = g[g.t <= last_closed]
        if g.empty or g.t.iloc[-1] != last_closed:
            continue
        df = detect.prepare(g[["open", "high", "low", "close", "volume"]])
        hs = detect.hits(df)
        if hs:
            extra = {"source": src.get(k)} if src.get(k) != "radar" else \
                {"source": "radar", "radar_label": RADAR_LABEL, "caveat": CAVEAT}
            ids += await store_hits(s, rid, t.date(), "15m", last_closed.to_pydatetime(), k,
                                    syms.get(k), hs, {**_ctx_row(df, base), **extra},
                                    [x.isoformat() for x in g.t])
            for h in hs:
                n_hits[h["detector"]] = n_hits.get(h["detector"], 0) + 1
    await s.commit()
    track = await live_track_15m(s, rid, t.date(), df15, ps)
    return {"bar": last_closed.isoformat(), "keys": len(keys), "watchlist": len(wl),
            "live_track": track,
            "radar_keys": len(radar_keys), "hits": n_hits, "alerts_new": len(ids),
            "alert_ids": ids, "radar": {k: v for k, v in radar_out.items() if k != "rows"},
            "radar_new": radar_out.get("rows", [])}


# ---- grading ----------------------------------------------------------------------------

async def scan_grade(s, ps, rid, regrade_all: bool = False) -> dict:
    """regrade_all: every alert again (append-only: the newest grade wins), e.g. after the
    target rule changed (2026-11-04: R2 / 2R when R1's net R:R < 1)."""
    alerts = [dict(r) for r in (await s.execute(text("""
        select a.*, j.status, j.actual_entry, j.actual_qty from scan_alert a
        left join journal_entry j on j.alert_id = a.id
        where cast(:all as boolean) or not exists (select 1 from journal_outcome o where o.alert_id = a.id)
           or exists (select 1 from (select distinct on (basis) outcome from journal_outcome o
                      where o.alert_id = a.id order by basis, graded_at desc, id desc) l
                      where l.outcome = 'OPEN')
           or (j.status = 'took' and j.actual_entry is not null and not exists (
               select 1 from journal_outcome o where o.alert_id = a.id and o.basis = 'took'
                 and o.outcome <> 'OPEN' and o.entry_used = j.actual_entry))"""),
        {"all": regrade_all})).mappings()]
    if not alerts:
        return {"graded": 0}
    live_final = set((await s.execute(text("""
        with l as (select distinct on (alert_id) alert_id, outcome, source from journal_outcome
                   where basis = 'alert' order by alert_id, graded_at desc, id desc)
        select alert_id from l where source = 'live' and outcome <> 'OPEN'"""))).scalars())
    cfg = get_settings()
    out = {"graded": 0, "outcomes": {}}
    d1 = [a for a in alerts if a["tf"] == "1d"]
    m15 = [a for a in alerts if a["tf"] == "15m"]
    frames = {}
    if d1:
        span = (today() - min(a["session_date"] for a in d1)).days + 15
        b = await bars.official(ps, today(), span, keys=sorted({a["instrument_key"] for a in d1}))
        for k, g in b.groupby("instrument_key"):
            g = g.assign(t=[_dt.datetime.combine(d, _dt.time(15, 30), IST) for d in g.session_date])
            frames[("1d", k)] = g
    if m15:
        since = min(a["session_date"] for a in m15)
        b = await bars15(ps, sorted({a["instrument_key"] for a in m15}), since)
        for k, g in b.groupby("instrument_key"):
            frames[("15m", k)] = g.assign(t=g.t.dt.tz_convert(IST))
    for a in alerts:
        g = frames.get((a["tf"], a["instrument_key"]))
        if g is None:
            continue
        if a["tf"] == "15m" and a["id"] in live_final:
            continue                       # the live tracker's final grade stands
        res = await grade_alert(s, rid, a, g, cfg, "nightly")
        for k in res:
            out["outcomes"][k] = out["outcomes"].get(k, 0) + 1
        out["graded"] += 1
    await s.commit()
    return out


async def grade_alert(s, rid, a: dict, g: pd.DataFrame, cfg, source: str,
                      finals_only: bool = False) -> list[str]:
    """Grade one alert on its bars `g` (t = bar start, IST) for every basis and store the rows.
    15m: only the alert's own session, flat at its end. finals_only: store nothing while the
    'alert' grade is still OPEN (the live tracker runs every 15 min)."""
    after = g[g.t > a["bar_time"].astimezone(IST)]
    hold = None
    if a["tf"] == "15m":
        after = after[after.t.dt.date == a["session_date"]]
        hold = grade.slots_left(a["bar_time"])
    o, h, lo, c = (after[x].to_numpy(float) for x in ("open", "high", "low", "close"))
    bases = ["alert", "placebo"]
    if a["status"] == "took" and a["actual_entry"] is not None:
        bases.append("took")
    res = {b: grade.grade(a, o, h, lo, c, b, float(a["actual_entry"]) if b == "took" else None,
                          cfg.DRISHTI_SCAN_CAPITAL, cfg.DRISHTI_SCAN_RISK_PCT, hold)
           for b in bases}
    if finals_only and res["alert"]["outcome"] == "OPEN":
        return []
    for basis, r in res.items():
        ix = r.get("exit_index")
        await s.execute(text("""insert into journal_outcome(alert_id, run_id, basis, outcome,
            entry_used, exit_price, exit_bar, target_used, r_gross, r_net, bars_used, seed,
            source) values (:a, :r, :b, :o, :e, :x, :xb, :tg, :rg, :rn, :n, :seed, :src)"""),
            {"a": a["id"], "r": rid, "b": basis, "o": r["outcome"], "e": r.get("entry_used"),
             "x": r.get("exit_price"), "xb": after.t.iloc[ix] if ix is not None else None,
             "tg": r.get("target_used"), "rg": r.get("r_gross"), "rn": r.get("r_net"),
             "n": r.get("bars_used"), "seed": r.get("seed"), "src": source})
    return [r["outcome"] for r in res.values()]


async def live_track_15m(s, rid, day: _dt.date, df15: pd.DataFrame, ps) -> dict:
    """Live outcome tracking of today's 15m alerts on the recorder's closed 15m bars (the same
    grade function and rules as the nightly grader). A final grade is stored once (source
    'live') and is not replaced by the nightly grader."""
    alerts = [dict(r) for r in (await s.execute(text("""
        with l as (select distinct on (alert_id) alert_id, outcome from journal_outcome
                   where basis = 'alert' order by alert_id, graded_at desc, id desc)
        select a.*, j.status, j.actual_entry, j.actual_qty from scan_alert a
        left join journal_entry j on j.alert_id = a.id left join l on l.alert_id = a.id
        where a.tf = '15m' and a.session_date = :d
          and (l.outcome is null or l.outcome = 'OPEN')"""), {"d": day})).mappings()]
    if not alerts:
        return {"tracked": 0}
    need = sorted({a["instrument_key"] for a in alerts} - set(df15.instrument_key.unique()))
    if need:
        df15 = pd.concat([df15, await bars15(ps, need, day - _dt.timedelta(days=1))])
    cfg = get_settings()
    out = {"tracked": len(alerts), "resolved": {}}
    for k, g in df15.groupby("instrument_key"):
        g = g.assign(t=g.t.dt.tz_convert(IST))
        for a in (x for x in alerts if x["instrument_key"] == k):
            res = await grade_alert(s, rid, a, g, cfg, "live", finals_only=True)
            if res:
                out["resolved"][res[0]] = out["resolved"].get(res[0], 0) + 1
    await s.commit()
    return out


# ---- CLI --------------------------------------------------------------------------------

async def main(cmd: str, args: list[str]) -> dict:
    t = now()
    day = _dt.date.fromisoformat(args[args.index("--date") + 1]) if "--date" in args else t.date()
    async with sessionmaker()() as s, prajna_sessionmaker()() as ps:
        if cmd in ("scan_eod", "scan_intraday") and \
                not holidays.is_session(day, await holidays.latest(s, day)):
            return {"skipped": "not a session"}
        if cmd == "scan_intraday" and not (_dt.time(9, 30) <= t.time() <= _dt.time(15, 35)):
            return {"skipped": "outside market hours"}
        rid = await runlog.start(s, cmd.upper(), day, {"args": args, "version": detect.VERSION})
        try:
            if cmd == "scan_eod":
                out = await scan_eod(s, ps, rid, day)
                out.pop("alert_ids")
                from drishti.scanner import odds_job      # odds first: the alert messages carry them
                try:
                    out["odds"] = {k: v for k, v in (await odds_job.run(s, ps, rid, day)).items()
                                   if k in ("stored", "skipped")}
                except Exception as e:                   # never block the scan or its messages
                    await s.rollback()
                    out["odds"] = {"failed": f"{type(e).__name__}: {e}"[:200]}
                out["telegram"] = {"scout": await notify.deliver_scout(s, day),
                                   "alerts": await notify.deliver_alerts(s, "1d"),
                                   "summary": await notify.flush_batched(s, rid)}
            elif cmd == "scan_intraday":
                out = await scan_intraday(s, ps, rid, t)
                out.pop("alert_ids", None)
                out["telegram"] = {"radar": await notify.deliver_radar(s, t.date()),
                                   "alerts": await notify.deliver_alerts(s, "15m"),
                                   "outcomes": await notify.deliver_outcomes(s)}
                if t.time() >= _dt.time(15, 30):        # after the last bar: the EOD digest
                    out["telegram"]["outcomes_15m"] = await notify.deliver_outcomes_15m(
                        s, t.date())
                out["telegram"]["summary"] = await notify.flush_batched(s, rid)
            elif cmd == "scan_grade":
                out = await scan_grade(s, ps, rid, "--all" in args)
                out["telegram"] = {"outcomes": await notify.deliver_outcomes(s),
                                   "alerts": await notify.deliver_alerts(s)}   # any left over
                prev = (await s.execute(text("""select max(session_date) from scan_alert
                    where tf = '15m' and session_date < :t"""), {"t": today()})).scalar()
                if prev:                                 # backstop for a missed 15:31 run
                    out["telegram"]["outcomes_15m"] = await notify.deliver_outcomes_15m(s, prev)
                out["telegram"]["summary"] = await notify.flush_batched(s, rid)
            else:
                raise SystemExit(__doc__)
        except BaseException as e:
            await s.rollback()
            await runlog.end(s, rid, "FAILED", error=f"{type(e).__name__}: {e}"[:500])
            raise
        await runlog.end(s, rid, "OK", out)
        return out


if __name__ == "__main__":
    print(json.dumps(asyncio.run(main(sys.argv[1], sys.argv[2:])), indent=1, default=str))
