"""The provisional daily bar of T: one Upstox full-quote snapshot of the equity universe at
15:40 IST (spec section 1). The official 1d bar comes from Prajna at T+1 07:00.

The provisional close is `last_price` (the last trade). NSE's official close is the
weighted average of the last 30 minutes, so the two differ; spec section 1 flags BAR_REVISED when
the official bar differs by > 0.1 %. The quote's own `ohlc` fields are stored as returned."""

from __future__ import annotations

import datetime as _dt

from sqlalchemy import text

from drishti.core.clock import IST

BATCH = 500                     # Upstox quote API: at most 500 keys a request
# Unannounced-holiday guard: below this share of quotes with a last trade today (IST) the day is
# taken as no session (SKIPPED_NO_SESSION) and nothing is stored. A normal day is ~95 %
# (2026-10-28: 3,416 of 3,576; the rest did not trade that day).
MIN_FRESH_SHARE = 0.5


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
                    "upper_circuit": v.get("upper_circuit_limit"),
                    "lower_circuit": v.get("lower_circuit_limit"),
                    "net_change": v.get("net_change"),
                    "quote_ts": _dt.datetime.fromtimestamp(int(ts) / 1000, tz=_dt.timezone.utc)
                    if ts and str(ts).isdigit() else None})
    return [r for r in out if r["instrument_key"]]


def split_fresh(rows: list[dict], day: _dt.date) -> tuple[list[dict], list[dict]]:
    """(rows whose last trade is on `day` in IST, the rest). Only the first become provisional
    bars of `day`: a quote with an older last trade is a previous session's bar."""
    fresh, stale = [], []
    for r in rows:
        ts = r["quote_ts"]
        (fresh if ts is not None and ts.astimezone(IST).date() == day else stale).append(r)
    return fresh, stale


def session_seen(fresh: list[dict], stale: list[dict]) -> bool:
    n = len(fresh) + len(stale)
    return n > 0 and len(fresh) / n >= MIN_FRESH_SHARE


def fetch(api, keys: list[str]) -> list[dict]:
    out = []
    for i in range(0, len(keys), BATCH):
        d = api.get("/v2/market-quote/quotes", params={"instrument_key": ",".join(keys[i:i + BATCH])})
        out += parse(d.get("data") or {})
    return out


async def store(s, rid, day: _dt.date, rows: list[dict]) -> int:
    for r in rows:
        await s.execute(text("""insert into eod_quote(run_id, instrument_key, session_date, open,
            high, low, close, last_price, volume, quote_ts, upper_circuit, lower_circuit,
            net_change) values (:run, :instrument_key, :d, :open, :high, :low, :close,
            :last_price, :volume, :quote_ts, :upper_circuit, :lower_circuit, :net_change)"""),
            {"run": rid, "d": day, "upper_circuit": None, "lower_circuit": None,
             "net_change": None, **r})
    await s.commit()
    return len(rows)


BANDS = (2, 5, 10, 20)


def band_pct(upper: float | None, lower: float | None, last: float | None,
             net_change: float | None) -> int | None:
    """NSE price band of the day in % (2/5/10/20) from the circuit limits around the previous
    close (= last_price - net_change). None when the limits are missing or match no band
    (F&O names have no band; NSE shows them a +-10 % operating range, which reads as 10)."""
    if None in (upper, lower, last, net_change):
        return None
    prev = float(last) - float(net_change)
    if prev <= 0:
        return None
    width = max(float(upper) / prev - 1, 1 - float(lower) / prev)
    b = min(BANDS, key=lambda x: abs(100 * width - x))
    return b if abs(100 * width - b) <= 0.25 * b else None
