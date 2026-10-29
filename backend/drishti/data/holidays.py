"""NSE trading holidays from the Upstox holidays API (user P0 decision 7). Every fetch is kept."""

from __future__ import annotations

import datetime as _dt

from sqlalchemy import text

from drishti.core.clock import IST


def parse(rows: list[dict]) -> list[dict]:
    out = []
    for h in rows:
        nse_open = next((o for o in h.get("open_exchanges") or [] if o.get("exchange") == "NSE"),
                        None)
        ms = lambda v: _dt.datetime.fromtimestamp(v / 1000, tz=IST) if v else None  # noqa: E731
        out.append({"holiday_date": _dt.date.fromisoformat(h["date"]),
                    "description": h.get("description"),
                    "holiday_type": h.get("holiday_type") or "UNKNOWN",
                    "nse_closed": "NSE" in (h.get("closed_exchanges") or []),
                    "nse_open_from": ms(nse_open and nse_open.get("start_time")),
                    "nse_open_to": ms(nse_open and nse_open.get("end_time"))})
    return out


async def store(s, rid, rows: list[dict]) -> int:
    for r in rows:
        await s.execute(text("""insert into trading_holiday(run_id, holiday_date, description,
            holiday_type, nse_closed, nse_open_from, nse_open_to) values (:run, :holiday_date,
            :description, :holiday_type, :nse_closed, :nse_open_from, :nse_open_to)"""),
            {"run": rid, **r})
    await s.commit()
    return len(rows)


# Spec clarification SC1 (docs/SPEC_CLARIFICATIONS.md): a special session shorter than this
# (Muhurat trading, the Saturday DR test sessions) is not a session of drishti-swing-v1: no
# provisional bar, no signal, and its 1d bar is kept out of every daily series.
SHORT_SESSION = _dt.timedelta(hours=3)

# Short sessions before Drishti's holidays feed, as seen in the stored history (each ~1 hour of
# 15m bars in Prajna's ohlcv_bar, and a 1d bar on the date; 2019-2021 from the index series).
SHORT_SESSIONS = {
    _dt.date(2019, 10, 27): "Muhurat", _dt.date(2020, 11, 14): "Muhurat",
    _dt.date(2021, 11, 4): "Muhurat", _dt.date(2022, 10, 24): "Muhurat",
    _dt.date(2023, 11, 12): "Muhurat", _dt.date(2024, 3, 2): "DR test session",
    _dt.date(2024, 5, 18): "DR test session", _dt.date(2024, 11, 1): "Muhurat",
    _dt.date(2025, 10, 21): "Muhurat",
}


def is_session(d: _dt.date, holiday: dict | None) -> bool:
    """A regular NSE session of drishti-swing-v1. `holiday` is the latest holidays-API row for d
    (or None). A full-length special session on a weekend (Budget day) counts; a short one does
    not, whatever the weekday."""
    if d in SHORT_SESSIONS:
        return False
    if holiday is None:
        return d.weekday() < 5
    if holiday["nse_closed"]:
        return False
    f, t = holiday.get("nse_open_from"), holiday.get("nse_open_to")
    if holiday.get("holiday_type") == "SPECIAL_TIMING" and f and t:
        return t - f >= SHORT_SESSION
    return d.weekday() < 5


async def latest(s, d: _dt.date) -> dict | None:
    r = (await s.execute(text("""select holiday_type, nse_closed, nse_open_from, nse_open_to
        from trading_holiday where holiday_date = :d order by fetched_at desc limit 1"""),
        {"d": d})).mappings().first()
    return dict(r) if r else None
