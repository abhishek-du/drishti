"""Re-point check (SWITCH_TO_B part 4): Drishti's reads on the D1 copy must equal its reads on
Prajna's live DB, before DRISHTI_HISTORY_SOURCE is flipped to 'copy' at the switch.

    .venv/bin/python -m drishti.switch.repoint verify --target drishti --out DIR [--n 50]
        [--months 2026-09] [--day YYYY-MM-DD]

For --n random stocks plus every stock with an EXACT corporate-action factor, bars.official()
(adjusted daily bars, CA factors applied point in time) is run on both sources with the same
as_of = the copy's watermark (copy_state.json), and the frames must be identical. --months
limits the comparison to the copied months (a partial dry-run copy). Read only on both sides.
The report goes to OUT/repoint_verify_<stamp>.json.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import random
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from drishti.core.clock import IST
from drishti.core.config import get_settings
from drishti.core.db import copy_engine
from drishti.data import bars

COLS = ["instrument_key", "session_date", "open", "high", "low", "close", "volume"]


def _norm(df: pd.DataFrame, months: list[str] | None) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=COLS)
    d = df[COLS].copy()
    if months:
        d = d[pd.to_datetime(d.session_date).dt.strftime("%Y-%m").isin(months)]
    for c in ("open", "high", "low", "close", "volume"):
        d[c] = d[c].astype(float).round(6)
    return d.sort_values(["instrument_key", "session_date"]).reset_index(drop=True)


async def verify(target: str, out: Path, n: int, months: list[str] | None,
                 day: _dt.date | None) -> dict:
    cfg = get_settings()
    state = json.loads((out / "copy_state.json").read_text())
    w = _dt.datetime.fromisoformat(state["runs"][-1]["w"].replace(" ", "T"))
    url = cfg.DRISHTI_DATABASE_URL.rsplit("/", 1)[0] + f"/{target}"
    live_e = create_async_engine(cfg.DRISHTI_PRAJNA_RO_URL, pool_pre_ping=True)
    copy_e = copy_engine(url)
    try:
        async with async_sessionmaker(live_e)() as lv, async_sessionmaker(copy_e)() as cp:
            day = day or w.astimezone(IST).date()
            if months:
                day = min(day, max(pd.Period(m).end_time.date() for m in months))
            keys_all = sorted((await cp.execute(text("""select instrument_key
                from canon_instrument where included and instrument_key like 'NSE_EQ|%'"""))
                ).scalars())
            ca = sorted(set((await cp.execute(text("""select distinct instrument_key
                from canon_ca_factor where status = 'EXACT' and ex_date is not null"""))
                ).scalars()) & set(keys_all))
            rnd = random.Random(20261112).sample(keys_all, min(n, len(keys_all)))
            keys = sorted(set(rnd) | set(ca))
            mism, rows = [], 0
            for i in range(0, len(keys), 200):
                chunk = keys[i:i + 200]
                a = _norm(await bars.official(lv, day, 450, as_of=w, keys=chunk), months)
                b = _norm(await bars.official(cp, day, 450, as_of=w, keys=chunk), months)
                rows += len(a)
                if not a.equals(b):
                    m = a.merge(b, on=["instrument_key", "session_date"], how="outer",
                                suffixes=("_live", "_copy"), indicator=True)
                    bad = m[(m._merge != "both") | (m[[f"{c}_live" for c in COLS[2:]]].to_numpy()
                                                   != m[[f"{c}_copy" for c in COLS[2:]]].to_numpy()
                                                   ).any(axis=1)]
                    mism += sorted(bad.instrument_key.unique().tolist())
    finally:
        await live_e.dispose()
        await copy_e.dispose()
    rep = {"target": target, "as_of": w.isoformat(), "day": str(day), "months": months,
           "keys_checked": len(keys), "random": len(rnd), "with_ca": len(ca), "rows": rows,
           "mismatch_keys": mism[:50], "mismatches": len(mism), "ok": not mism and rows > 0}
    stamp = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    (out / f"repoint_verify_{stamp}.json").write_text(json.dumps(rep, indent=1))
    return rep


def main(argv: list[str]) -> dict:
    if not argv or argv[0] != "verify":
        raise SystemExit(__doc__)
    a = dict(zip(argv[1::2], argv[2::2]))
    return asyncio.run(verify(a["--target"], Path(a["--out"]), int(a.get("--n", 50)),
                              a["--months"].split(",") if "--months" in a else None,
                              _dt.date.fromisoformat(a["--day"]) if "--day" in a else None))


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1:]), indent=1, default=str))
