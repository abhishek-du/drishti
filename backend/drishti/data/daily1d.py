"""Drishti's own official daily bars (P1c, Option B): Upstox V3 historical 1d for the equity
universe of Drishti's master, one request per key for the window (T - LOOKBACK, T].

Every response is kept (bar_1d_payload: status, sha256, basis_as_of = fetch date) and every
candle of it (bar_1d), so a re-served value (a split applied by the vendor, a late correction)
is a new row, never an overwrite. Before the switch (DRISHTI_UPSTOX_LOGIN_ENABLED false =
decision A still in force) the historical API is used only 22:00-05:30 IST, fraction <= 0.5.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
from urllib.parse import quote

from sqlalchemy import text

from drishti.core.clock import now
from drishti.core.config import get_settings
from drishti.data import indices, master

SERIES = ("EQ", "BE", "BZ", "SM", "ST", "IV")      # = Prajna's NSE_EQ 1d coverage set
LOOKBACK = 8                                      # calendar days per request
A_WINDOW = ((22, 0), (5, 30))                     # decision A: historical API only at night


def a_rule_ok(t: _dt.datetime) -> bool:
    """True when the historical API may be called now under decision A (pre-switch)."""
    if get_settings().DRISHTI_UPSTOX_LOGIN_ENABLED:
        return True                               # after the switch Drishti owns the budget
    m = t.hour * 60 + t.minute
    return m >= A_WINDOW[0][0] * 60 + A_WINDOW[0][1] or m <= A_WINDOW[1][0] * 60 + A_WINDOW[1][1]


async def universe(s) -> list[str]:
    st = await master.state(s)
    return sorted(k for k, a in st.items()
                  if a.get("segment") == "NSE_EQ" and a.get("instrument_type") in SERIES)


async def fetch_store(s, rid, api, keys: list[str], to: _dt.date) -> dict:
    frm = to - _dt.timedelta(days=LOOKBACK)
    basis = now().date()
    c = {"keys": len(keys), "ok": 0, "empty": 0, "errors": 0, "candles": 0}
    for i, k in enumerate(keys):
        r = api.get_response(f"/v3/historical-candle/{quote(k, safe='')}/days/1/{to}/{frm}")
        body = r.content
        cs = (r.json().get("data") or {}).get("candles") or [] if r.status_code == 200 else []
        pid = (await s.execute(text("""insert into bar_1d_payload(run_id, instrument_key, req_from,
            req_to, http_status, sha256, n_candles, basis_as_of, error) values (:r, :k, :a, :b,
            :st, :h, :n, :ba, :e) returning id"""),
            {"r": rid, "k": k, "a": frm, "b": to, "st": r.status_code,
             "h": hashlib.sha256(body).hexdigest(), "n": len(cs), "ba": basis,
             "e": None if r.status_code == 200 else body[:300].decode(errors="replace")})).scalar()
        ts = (await s.execute(text("select fetched_at from bar_1d_payload where id = :i"),
                              {"i": pid})).scalar()
        for row in indices.parse_candles(cs):
            raw = next(x for x in cs if x[0][:10] == str(row["session_date"]))
            await s.execute(text("""insert into bar_1d(payload_id, instrument_key, session_date,
                open, high, low, close, volume, oi, fetched_at) values (:p, :k, :d, :o, :h, :l,
                :c, :v, :oi, :t)"""),
                {"p": pid, "k": k, "d": row["session_date"], "o": row["open"], "h": row["high"],
                 "l": row["low"], "c": row["close"], "v": raw[5], "oi": raw[6] if len(raw) > 6
                 else None, "t": ts})
        c["ok" if r.status_code == 200 and cs else "empty" if r.status_code == 200
          else "errors"] += 1
        c["candles"] += len(cs)
        if i % 200 == 199:
            await s.commit()
    await s.commit()
    return c


async def compare_with_prajna(s, ps, day: _dt.date) -> dict:
    """Continuity check: Drishti's latest 1d of `day` against Prajna's latest stored 1d of the
    same (key, date). Same vendor, so raw OHLCV must be equal unless the vendor re-served the
    bar (a split/bonus applied after Prajna's fetch, or a correction)."""
    ours = {k: v for k, *v in (await s.execute(text("""
        select distinct on (instrument_key) instrument_key, open, high, low, close, volume
        from bar_1d where session_date = :d order by instrument_key, fetched_at desc"""),
        {"d": day})).all()}
    theirs = {k: v for k, *v in (await ps.execute(text("""
        select distinct on (instrument_key) instrument_key, open, high, low, close, volume
        from ohlcv_bar where timeframe = '1d' and session_date = :d
          and split_part(instrument_key, '|', 1) = 'NSE_EQ' order by instrument_key, fetched_at desc"""),
        {"d": day})).all()}
    both = ours.keys() & theirs.keys()
    diff = sorted(k for k in both if [float(x or 0) for x in ours[k]]
                  != [float(x or 0) for x in theirs[k]])
    return {"session": str(day), "drishti": len(ours), "prajna": len(theirs), "both": len(both),
            "equal": len(both) - len(diff), "differ": len(diff), "differ_keys": diff[:30],
            "only_drishti": len(ours.keys() - theirs.keys()),
            "only_prajna": sorted(theirs.keys() - ours.keys())[:30],
            "only_prajna_count": len(theirs.keys() - ours.keys())}
