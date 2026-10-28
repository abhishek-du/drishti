"""NSE index daily bars from Upstox (regime: Nifty 500; sector strength; VIX; benchmark).
Prajna stores only Nifty 50, Nifty Bank and India VIX, so Drishti keeps its own copy of all."""

from __future__ import annotations

import datetime as _dt

from sqlalchemy import text

INDEX_KEYS = (
    "NSE_INDEX|Nifty 500", "NSE_INDEX|Nifty 50", "NSE_INDEX|Nifty Bank", "NSE_INDEX|India VIX",
    "NSE_INDEX|Nifty200Momentm30", "NSE_INDEX|Nifty IT", "NSE_INDEX|Nifty Auto",
    "NSE_INDEX|Nifty FMCG", "NSE_INDEX|Nifty Pharma", "NSE_INDEX|Nifty Metal",
    "NSE_INDEX|Nifty Realty", "NSE_INDEX|Nifty Energy", "NSE_INDEX|Nifty Media",
    "NSE_INDEX|Nifty PSU Bank", "NSE_INDEX|Nifty Pvt Bank", "NSE_INDEX|Nifty Fin Service",
    "NSE_INDEX|Nifty Infra", "NSE_INDEX|Nifty500 Health", "NSE_INDEX|Nifty Commodities",
    "NSE_INDEX|Nifty Consumption", "NSE_INDEX|Nifty Cement")


def parse_candles(candles: list[list]) -> list[dict]:
    """Upstox V3 candles [ts, o, h, l, c, vol, oi] -> rows (index volume is ignored)."""
    out = []
    for c in candles:
        out.append({"session_date": _dt.date.fromisoformat(c[0][:10]), "open": c[1], "high": c[2],
                    "low": c[3], "close": c[4]})
    return sorted(out, key=lambda r: r["session_date"])


def fetch_history(api, key: str, frm: _dt.date, to: _dt.date) -> list[dict]:
    from urllib.parse import quote
    d = api.get(f"/v3/historical-candle/{quote(key, safe='')}/days/1/{to}/{frm}")
    return parse_candles((d.get("data") or {}).get("candles") or [])


async def store(s, rid, key: str, rows: list[dict], source: str) -> int:
    for r in rows:
        await s.execute(text("""insert into index_bar(run_id, index_key, session_date, open, high,
            low, close, source) values (:run, :k, :session_date, :open, :high, :low, :close,
            :src)"""), {"run": rid, "k": key, "src": source, **r})
    await s.commit()
    return len(rows)


async def last_dates(s) -> dict[str, _dt.date]:
    rows = (await s.execute(text("""select index_key, max(session_date) from index_bar
        where source = 'UPSTOX_HIST' group by 1"""))).all()
    return dict(rows)
