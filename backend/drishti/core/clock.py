from __future__ import annotations

import datetime as _dt
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def now() -> _dt.datetime:
    return _dt.datetime.now(tz=IST)


def today() -> _dt.date:
    return now().date()
