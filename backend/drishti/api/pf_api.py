"""Paper portfolio read API, pf-v1 Phase 1 (read only; PAPER ONLY).

Every response carries LABEL and the frozen pre-registration values (PREREG_PAPER_PORTFOLIO.md).
"""

from __future__ import annotations

import datetime as _dt

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from drishti.api.scan_api import _row
from drishti.core.db import sessionmaker
from drishti.paper.ledger import LABEL, VERSION

router = APIRouter()

PREREG = {
    "version": VERSION, "doc": "docs/PREREG_PAPER_PORTFOLIO.md", "frozen": "2026-11-05",
    "odds_gate": "mc_avg_r_net >= +0.05 AND replay_n >= 30 AND NOT thin_sample "
                 "(target-first % display only)",
    "risk": {"per_trade": "1% of equity", "max_position": "20% of equity",
             "max_open_positions": 5, "cash_buffer": "10%", "max_open_risk": "3%",
             "daily_loss_limit": "-2%", "kill_switch": "-10% drawdown from start"},
    "intraday": "observe mode until the 15m odds engine; MIS flat by 15:20",
    "benchmark": "Nifty 500 price index (excludes dividends)",
    "sector_cap": "deferred until the 2026-11-20 switch",
    "results_veto": "deterministic (results_event + board-meetings CSV); no LLM gate",
}


async def _rows(q: str, p: dict) -> list[dict]:
    async with sessionmaker()() as s:
        return [_row(r) for r in (await s.execute(text(q), p)).mappings().all()]


@router.get("/api/portfolio")
async def portfolios() -> dict:
    """All paper portfolios (snapshot), with total return, drawdown and the open-position count."""
    rows = await _rows("""select p.*, (select count(*) from pf_position x
        where x.portfolio_id = p.id) as open_positions from pf_portfolio p
        order by research_only, id""", {})
    for r in rows:
        sc = r["starting_capital"]
        r["total_return"] = round(r["equity"] / sc - 1, 5) if sc else None
        r["drawdown"] = round(r["equity"] / r["peak_equity"] - 1, 5) if r["peak_equity"] else None
    return {"label": LABEL, "prereg": PREREG, "portfolios": rows,
            "note": "Phase 1: the ledger only. No paper orders are placed until Phase 2/3."}


def _pid_ok(pid: str) -> str:
    if pid not in ("main", "placebo", "benchmark", "obs_all", "obs_placebo"):
        raise HTTPException(404, "no such portfolio")
    return pid


@router.get("/api/portfolio/{pid}/positions")
async def positions(pid: str) -> dict:
    return {"label": LABEL, "positions": await _rows("""select * from pf_position
        where portfolio_id = :i order by book, instrument_key""", {"i": _pid_ok(pid)})}


@router.get("/api/portfolio/{pid}/orders")
async def orders(pid: str, since: _dt.date | None = None, book: str | None = None,
                 limit: int = Query(200, le=2000)) -> dict:
    return {"label": LABEL, "orders": await _rows("""select o.*, f.price as fill_price,
        f.total_costs, f.net_cash from pf_order o left join pf_fill f on f.order_id = o.id
        where o.portfolio_id = :i and (cast(:s as date) is null or o.ts >= :s)
          and (cast(:b as text) is null or o.book = :b) order by o.ts desc, o.id desc limit :n""",
        {"i": _pid_ok(pid), "s": since, "b": book, "n": limit})}


@router.get("/api/portfolio/{pid}/fills")
async def fills(pid: str, limit: int = Query(200, le=2000)) -> dict:
    return {"label": LABEL, "fills": await _rows("""select * from pf_fill where portfolio_id = :i
        order by ts desc, id desc limit :n""", {"i": _pid_ok(pid), "n": limit})}


@router.get("/api/portfolio/{pid}/trades")
async def trades(pid: str, since: _dt.date | None = None, book: str | None = None,
                 limit: int = Query(200, le=2000)) -> dict:
    rows = await _rows("""select * from pf_trade where portfolio_id = :i
        and (cast(:s as date) is null or exit_ts >= :s) and (cast(:b as text) is null or book = :b)
        order by exit_ts desc, id desc limit :n""",
        {"i": _pid_ok(pid), "s": since, "b": book, "n": limit})
    n = len(rows)
    net = [r["net_pnl"] for r in rows]
    rr = [r["r_multiple"] for r in rows if r["r_multiple"] is not None]
    stats = {"trades": n, "net_pnl": round(sum(net), 2) if n else 0,
             "hit_rate": round(sum(1 for x in net if x > 0) / n, 4) if n else None,
             "avg_r": round(sum(rr) / len(rr), 4) if rr else None}
    return {"label": LABEL, "stats": stats, "trades": rows}


@router.get("/api/portfolio/{pid}/equity")
async def equity(pid: str, book: str = "all") -> dict:
    return {"label": LABEL, "equity": await _rows("""select date, book, cash, holdings_value,
        equity, daily_pnl, drawdown, benchmark_equity, placebo_equity from pf_equity_latest
        where portfolio_id = :i and book = :b order by date""", {"i": _pid_ok(pid), "b": book})}


@router.get("/api/portfolio/{pid}/decisions")
async def decisions(pid: str, date: _dt.date | None = None,
                    limit: int = Query(300, le=3000)) -> dict:
    return {"label": LABEL, "decisions": await _rows("""select * from pf_decision
        where portfolio_id = :i and (cast(:d as date) is null or session_date = :d)
        order by ts desc, id desc limit :n""", {"i": _pid_ok(pid), "d": date, "n": limit})}


@router.get("/api/portfolio/{pid}/position-events")
async def position_events(pid: str, key: str | None = None,
                          limit: int = Query(300, le=3000)) -> dict:
    return {"label": LABEL, "events": await _rows("""select * from pf_position_event
        where portfolio_id = :i and (cast(:k as text) is null or instrument_key = :k)
        order by ts desc, id desc limit :n""", {"i": _pid_ok(pid), "k": key, "n": limit})}
