"""One-shot probe (read only, run by hand or one cron line, 08:40-09:10 IST before the open):
does the Upstox full quote show the NEW day's circuit limits before 09:15?

It compares, for every key of the latest 15:40 snapshot (session T), the circuit limits in the
quote now (T+1 morning) with T's 15:40 limits, and checks around which close they are centred:
  - limits changed and centred on T's close (last_price of T)  -> T+1 limits are available;
  - limits unchanged (centred on T-1's close)                   -> not available before the open.
No login, the token is never printed; ~8 quote requests. Report: var/reports/probe_t1_band_<T+1>.json

    cd /home/cis/windows/drishti/backend && .venv/bin/python ../ops/probes/probe_t1_band.py
"""

from __future__ import annotations

import asyncio
import collections
import json
import sys

from sqlalchemy import text

from drishti.broker.upstox.rest import Upstox
from drishti.core.clock import now
from drishti.core.config import BACKEND
from drishti.core.db import sessionmaker
from drishti.data import eod_quotes


def centred(u: float, lo: float, ref: float) -> float:
    """How far the band midpoint is from ref, in % of ref."""
    return abs((u + lo) / 2 / ref - 1) * 100


async def main() -> int:
    t = now()
    async with sessionmaker()() as s:
        day = (await s.execute(text("""select max(r.session_date) from run r join run_end e
            using (run_id) where r.kind = 'P1_EOD' and e.status = 'OK'"""))).scalar()
        snap = {k: (u, lo, lp, nc) for k, u, lo, lp, nc in (await s.execute(text("""
            with r as (select r.run_id from run r join run_end e using (run_id)
                       where r.kind = 'P1_EOD' and r.session_date = :d and e.status = 'OK'
                       order by r.started_at limit 1)
            select instrument_key, upper_circuit, lower_circuit, last_price, net_change
            from eod_quote where run_id = (select run_id from r)
              and upper_circuit is not null"""), {"d": day})).all()}
    if not snap:
        print(json.dumps({"error": f"no circuit limits in the {day} snapshot"}))
        return 2
    rows = eod_quotes.fetch(Upstox(), list(snap))
    c, sample = collections.Counter(), []
    for r in rows:
        k = r["instrument_key"]
        u0, l0, lp0, nc0 = (float(x) for x in snap[k])
        if r["upper_circuit"] is None or r["lower_circuit"] is None:
            c["missing_now"] += 1
            continue
        u1, l1 = float(r["upper_circuit"]), float(r["lower_circuit"])
        same = abs(u1 - u0) < 1e-6 and abs(l1 - l0) < 1e-6
        to_t = centred(u1, l1, lp0)              # T's close (provisional = last price of T)
        to_prev = centred(u1, l1, lp0 - nc0)     # T-1's close
        c["unchanged" if same else "changed"] += 1
        if not same:
            c["changed_centred_on_T" if to_t < to_prev else "changed_centred_on_T-1"] += 1
        if len(sample) < 10 and not same:
            sample.append({"key": k, "T_limits": [u0, l0], "now": [u1, l1],
                           "T_close": lp0, "T-1_close": round(lp0 - nc0, 4)})
    verdict = ("T+1 limits AVAILABLE before the open"
               if c["changed_centred_on_T"] > 0.5 * len(rows) else
               "T+1 limits NOT available (quote still shows T's limits)"
               if c["unchanged"] > 0.5 * len(rows) else "UNCLEAR")
    out = {"probed_at": t.isoformat(), "snapshot_session": str(day), "keys": len(rows),
           "counts": dict(c), "verdict": verdict, "sample_changed": sample}
    p = BACKEND / "var" / "reports" / f"probe_t1_band_{t.date()}_{t:%H%M}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
