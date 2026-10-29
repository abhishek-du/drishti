"""Inputs of the P3 replay, read only (Prajna through drishti_ro, prod drishti through
drishti_tracks), cached as Parquet under /windows/drishti_data/research/track-b/cache/.

SURVIVORSHIP-BIASED, EXPLORATORY: Prajna's history is close to survivor-only (spec section 2).
Labelled, not fixed, here:
- EQ series (EQUITY_L) and sector (canon_instrument.sector) are the current lists;
- ASM/GSM is not applied (no history, spec section 2);
- results dates (user 2026-10-29, "results-gate proxy"): Prajna results_event HISTORICAL_FILING
  event_at (IST date), assumed known at T, plus INTIMATION meeting dates;
- old corporate-action basis (user 2026-10-29): the vendor adjustment is accepted, as Scout
  does (drishti.data.bars docstring); the share of such stock-days is recorded.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from drishti.core.clock import IST
from drishti.core.config import get_settings
from drishti.data import bars
from drishti.data.holidays import SHORT_SESSIONS

LABEL = "SURVIVORSHIP-BIASED, EXPLORATORY"
CA_HORIZON = _dt.date(2025, 9, 26)     # Prajna's corporate-action horizon (bars.py docstring)


def research_dir() -> Path:
    return get_settings().DRISHTI_DATA_DIR / "research" / "track-b"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


async def fetch(s, ps, data_end: _dt.date, days: int = 2700) -> dict[str, pd.DataFrame | dict]:
    as_of = _dt.datetime.combine(data_end + _dt.timedelta(days=1), _dt.time(12, 0), tzinfo=IST)
    b = await bars.official(ps, data_end, days=days, as_of=as_of)
    idx = pd.DataFrame((await s.execute(text("""select index_key, session_date, close
        from index_bar where source = 'UPSTOX_HIST' and session_date <= :d
        and index_key in ('NSE_INDEX|Nifty 500', 'NSE_INDEX|India VIX')
        order by fetched_at"""), {"d": data_end})).all(),
        columns=["index_key", "session_date", "close"])
    idx = idx[~idx.session_date.isin(SHORT_SESSIONS)].assign(close=lambda x: x.close.astype(float))
    idx = idx.drop_duplicates(["index_key", "session_date"], keep="last")
    eq = pd.DataFrame({"isin": (await s.execute(text("""
        with f as (select file_id from manual_file where kind = 'EQUITY_L'
                   order by file_date desc, loaded_at desc limit 1)
        select r.isin from manual_row r join f using (file_id)
        where r.isin is not null and r.stage = 'EQ'"""))).scalars().all()})
    res = pd.DataFrame((await ps.execute(text("""
        select instrument_key, (event_at at time zone 'Asia/Kolkata')::date
        from results_event where kind = 'HISTORICAL_FILING' and event_at is not null
          and instrument_key is not null
        union
        select instrument_key, meeting_date from results_event
        where kind = 'INTIMATION' and meeting_date is not null
          and instrument_key is not null"""))).all(), columns=["instrument_key", "result_date"])
    sec = pd.DataFrame((await ps.execute(text("""select instrument_key, sector
        from canon_instrument where segment = 'NSE_EQ' and sector is not null"""))).all(),
        columns=["instrument_key", "sector"])
    low = (await ps.execute(text("""select count(*) filter (where p.price_basis =
        'VENDOR_ADJUSTED' and b.market_date < :h), count(*)
        from canon_market_bar b left join ohlcv_payload_basis p using (payload_sha256)
        where b.timeframe = '1d' and b.segment = 'NSE_EQ' and b.market_date <= :d
          and b.market_date > :d0"""),
        {"h": CA_HORIZON, "d": data_end, "d0": data_end - _dt.timedelta(days=days)})).one()
    meta = {"data_end": str(data_end), "as_of": as_of.isoformat(), "days": days,
            "vendor_adjusted_before_horizon": int(low[0]), "bars_total": int(low[1]),
            "label": LABEL}
    return {"bars": b, "index": idx, "eq": eq, "results": res, "sectors": sec, "meta": meta}


async def cached(data_end: _dt.date, days: int = 2700, refresh: bool = False) -> dict:
    """The inputs for `data_end`, from the cache when present."""
    d = research_dir() / "cache" / f"inputs_{data_end}"
    if not refresh and (d / "meta.json").exists():
        out = {k: pd.read_parquet(d / f"{k}.parquet")
               for k in ("bars", "index", "eq", "results", "sectors")}
        out["meta"] = json.loads((d / "meta.json").read_text())
        for k in ("bars", "index", "results"):
            col = "result_date" if k == "results" else "session_date"
            out[k][col] = pd.to_datetime(out[k][col]).dt.date
        return out
    from drishti.core.db import prajna_sessionmaker, sessionmaker
    async with sessionmaker()() as s, prajna_sessionmaker()() as ps:
        out = await fetch(s, ps, data_end, days)
    d.mkdir(parents=True, exist_ok=True)
    for k in ("bars", "index", "eq", "results", "sectors"):
        out[k].to_parquet(d / f"{k}.parquet", index=False)
    out["meta"]["sha256"] = {k: sha256(d / f"{k}.parquet")
                             for k in ("bars", "index", "eq", "results", "sectors")}
    (d / "meta.json").write_text(json.dumps(out["meta"], indent=1))
    return out
