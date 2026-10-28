"""The provisional daily bar of T: one Upstox full-quote snapshot of the equity universe at
15:40 IST (spec section 1). The official 1d bar comes from Prajna at T+1 07:00.

The provisional close is `last_price` (the last trade). NSE's official close is the
weighted average of the last 30 minutes, so the two differ; spec section 1 flags BAR_REVISED when
the official bar differs by > 0.1 %. The quote's own `ohlc` fields are stored as returned."""

from __future__ import annotations

import datetime as _dt

from sqlalchemy import text

BATCH = 500                     # Upstox quote API: at most 500 keys a request


async def universe_keys(ps) -> list[str]:
    """Active NSE equities in Prajna's instrument master (read only)."""
    rows = (await ps.execute(text("""select instrument_key from instrument
        where segment = 'NSE_EQ' and lifecycle_status = 'ACTIVE' and valid_to = 'infinity'
        order by instrument_key"""))).scalars().all()
    return list(rows)


def parse(data: dict) -> list[dict]:
    out = []
    for v in data.values():
        o = v.get("ohlc") or {}
        ts = v.get("last_trade_time") or v.get("timestamp")
        out.append({"instrument_key": v.get("instrument_token"), "open": o.get("open"),
                    "high": o.get("high"), "low": o.get("low"), "close": o.get("close"),
                    "last_price": v.get("last_price"), "volume": v.get("volume"),
                    "quote_ts": _dt.datetime.fromtimestamp(int(ts) / 1000, tz=_dt.timezone.utc)
                    if ts and str(ts).isdigit() else None})
    return [r for r in out if r["instrument_key"]]


def fetch(api, keys: list[str]) -> list[dict]:
    out = []
    for i in range(0, len(keys), BATCH):
        d = api.get("/v2/market-quote/quotes", params={"instrument_key": ",".join(keys[i:i + BATCH])})
        out += parse(d.get("data") or {})
    return out


async def store(s, rid, day: _dt.date, rows: list[dict]) -> int:
    for r in rows:
        await s.execute(text("""insert into eod_quote(run_id, instrument_key, session_date, open,
            high, low, close, last_price, volume, quote_ts) values (:run, :instrument_key, :d,
            :open, :high, :low, :close, :last_price, :volume, :quote_ts)"""),
            {"run": rid, "d": day, **r})
    await s.commit()
    return len(rows)
