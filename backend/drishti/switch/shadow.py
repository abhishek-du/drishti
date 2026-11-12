"""Shadow days before the switch (PLAN_TO_2026-11-20, Mon 11-16 .. Thu 11-19): Scout's
watchlist and the EOD scanner's 1d hits, computed on both history sources for the same session,
must be identical. Read only: nothing is stored in Drishti's tables; the report is a file.

    .venv/bin/python -m drishti.switch.shadow compare [--day YYYY-MM-DD] [--target drishti]

Source A = Prajna live (drishti_ro); source B = the D1 copy (+ history union), as the reads
after DRISHTI_HISTORY_SOURCE=copy. Both use the same Drishti session for everything else
(provisional bars, manual files, master, index bars), so a difference can only come from the
history source. The report goes to DATA_DIR/prajna_copy/shadow_<day>.json.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import sys

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from drishti.core.clock import now, today
from drishti.core.config import get_settings
from drishti.core.db import copy_engine, sessionmaker
from drishti.data import bars
from drishti.run import p2
from drishti.scanner import detect
from drishti.scanner import run as scan


async def scan_hits(s, ps, day: _dt.date) -> dict[str, list]:
    """scan_eod's 1d detector hits, without storing anything: {key: [(detector, entry, stop)]}."""
    b, _src = await bars.daily(s, ps, day, as_of=scan.eod_as_of(day, now()))
    keys = await scan.eligible(s, b, day)
    out = {}
    for k, g in b[b.instrument_key.isin(keys)].groupby("instrument_key", sort=False):
        hs = detect.hits(detect.prepare(g[["open", "high", "low", "close", "volume"]]))
        if hs:
            out[k] = sorted((h["detector"], round(float(h["entry_ref"]), 2),
                             round(float(h["stop"]), 2)) for h in hs)
    return out


async def compare(day: _dt.date, target: str) -> dict:
    cfg = get_settings()
    live_e = create_async_engine(cfg.DRISHTI_PRAJNA_RO_URL, pool_pre_ping=True)
    copy_e = copy_engine(cfg.DRISHTI_DATABASE_URL.rsplit("/", 1)[0] + f"/{target}")
    rep: dict = {"day": str(day), "target": target, "at": now().isoformat()}
    try:
        async with sessionmaker()() as s, async_sessionmaker(live_e)() as lv, \
                async_sessionmaker(copy_e)() as cp:
            a, b = await p2.scout(s, lv, day), await p2.scout(s, cp, day)
            la, lb = list(a["listed"].index), list(b["listed"].index)
            rep["scout"] = {"live": len(la), "copy": len(lb), "same_order": la == lb,
                            "only_live": sorted(set(la) - set(lb))[:30],
                            "only_copy": sorted(set(lb) - set(la))[:30],
                            "regime_same": a["regime"] == b["regime"],
                            "funnel_live": a["funnel"], "funnel_copy": b["funnel"]}
            ha, hb = await scan_hits(s, lv, day), await scan_hits(s, cp, day)
            diff = sorted(k for k in ha.keys() | hb.keys() if ha.get(k) != hb.get(k))
            rep["scan"] = {"live": sum(map(len, ha.values())), "copy": sum(map(len, hb.values())),
                           "differ_keys": diff[:30], "differ": len(diff)}
    finally:
        await live_e.dispose()
        await copy_e.dispose()
    rep["ok"] = rep["scout"]["same_order"] and rep["scout"]["regime_same"] and \
        rep["scan"]["differ"] == 0
    out = cfg.DRISHTI_DATA_DIR / "prajna_copy" / f"shadow_{day}.json"
    out.write_text(json.dumps(rep, indent=1, default=str))
    rep["report"] = str(out)
    return rep


def main(argv: list[str]) -> dict:
    if not argv or argv[0] != "compare":
        raise SystemExit(__doc__)
    a = dict(zip(argv[1::2], argv[2::2]))
    day = _dt.date.fromisoformat(a["--day"]) if "--day" in a else today()
    return asyncio.run(compare(day, a.get("--target", "drishti")))


if __name__ == "__main__":
    r = main(sys.argv[1:])
    print(json.dumps({k: r[k] for k in ("day", "ok", "scout", "scan", "report")}, default=str))
