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
