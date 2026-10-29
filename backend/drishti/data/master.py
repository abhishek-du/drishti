"""Drishti's own instrument master (P1c, Option B): the Upstox public NSE master file, kept as
append-only events (ADDED / CHANGED / REMOVED) so the state on any date is a fold of the events
knowable by then. Public file, no API budget, no token.

Kept: NSE_EQ (every series: EQ, BE, BZ, SM, ST, IV, the debt series ...) and NSE_INDEX.
"""

from __future__ import annotations

import gzip
import hashlib
import json

import httpx
from sqlalchemy import text

URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
KEEP = ("NSE_EQ", "NSE_INDEX")
ATTRS = ("segment", "instrument_type", "trading_symbol", "name", "isin", "lot_size", "tick_size",
         "exchange_token", "security_type", "freeze_quantity")


def download(client: httpx.Client | None = None) -> bytes:
    c = client or httpx.Client(timeout=120)
    r = c.get(URL)
    r.raise_for_status()
    return r.content


def parse(raw: bytes) -> tuple[int, dict[str, dict]]:
    rows = json.loads(gzip.decompress(raw))
    kept = {r["instrument_key"]: {a: r.get(a) for a in ATTRS}
            for r in rows if r.get("segment") in KEEP and r.get("instrument_key")}
    return len(rows), kept


async def state(s, as_of=None) -> dict[str, dict]:
    """The master as known at as_of (default: now): last event per key, REMOVED dropped."""
    rows = (await s.execute(text("""
        select distinct on (instrument_key) instrument_key, event, attrs from instrument_event
        where (cast(:a as timestamptz) is null or knowable_at <= :a)
        order by instrument_key, knowable_at desc, id desc"""), {"a": as_of})).all()
    return {k: a for k, e, a in rows if e != "REMOVED"}


def diff(old: dict[str, dict], new: dict[str, dict]) -> list[tuple[str, str, dict, dict]]:
    out = []
    for k, a in new.items():
        if k not in old:
            out.append((k, "ADDED", a, {}))
        else:
            ch = {f: [old[k].get(f), a.get(f)] for f in ATTRS if old[k].get(f) != a.get(f)}
            if ch:
                out.append((k, "CHANGED", a, ch))
    out += [(k, "REMOVED", old[k], {}) for k in old.keys() - new.keys()]
    return out


async def store(s, rid, raw: bytes, total: int, kept: dict[str, dict]) -> dict:
    ev = diff(await state(s), kept)
    fetched = (await s.execute(text("""insert into instrument_master_file(run_id, url, sha256,
        rows_total, rows_kept) values (:r, :u, :h, :t, :k) returning fetched_at"""),
        {"r": rid, "u": URL, "h": hashlib.sha256(raw).hexdigest(), "t": total,
         "k": len(kept)})).scalar()
    for k, e, a, ch in ev:
        await s.execute(text("""insert into instrument_event(run_id, instrument_key, event, attrs,
            changed, knowable_at) values (:r, :k, :e, cast(:a as jsonb), cast(:c as jsonb), :t)"""),
            {"r": rid, "k": k, "e": e, "a": json.dumps(a), "c": json.dumps(ch), "t": fetched})
    await s.commit()
    n = {e: sum(1 for _, x, _, _ in ev if x == e) for e in ("ADDED", "CHANGED", "REMOVED")}
    return {"rows_total": total, "kept": len(kept), **n}


async def compare_with_prajna(s, ps) -> dict:
    """Continuity check against Prajna's master (read only): same NSE_EQ key set and series."""
    ours = {k: a for k, a in (await state(s)).items() if a.get("segment") == "NSE_EQ"}
    theirs = dict((await ps.execute(text("""select instrument_key, instrument_type from instrument
        where segment = 'NSE_EQ' and valid_to = 'infinity'
          and lifecycle_status = 'ACTIVE'"""))).all())
    both = ours.keys() & theirs.keys()
    series_diff = sorted(k for k in both if ours[k].get("instrument_type") != theirs[k])
    return {"drishti": len(ours), "prajna_active": len(theirs), "both": len(both),
            "only_drishti": len(ours.keys() - theirs.keys()),
            "only_prajna": sorted(theirs.keys() - ours.keys())[:20],
            "only_prajna_count": len(theirs.keys() - ours.keys()),
            "series_differ": series_diff[:20], "series_differ_count": len(series_diff)}
