"""Paper portfolio ledger, pf-v1 Phase 1 (user 2026-11-05; docs/PREREG_PAPER_PORTFOLIO.md).

PAPER ONLY: this module writes only pf_* tables; there is no broker call and no order path to
a real exchange. Deterministic: the same fills give the same ledger.

Portfolios (pf_portfolio, MUTABLE per DEVIATIONS D7):
  main         ₹10 lakh, swing (CNC) + intraday (MIS) books share the cash
  placebo      ₹10 lakh, the same gate-pass candidates with random next-bar entries
  benchmark    ₹10 lakh, Nifty 500 price index buy-and-hold (excludes dividends)
  obs_all      RESEARCH ONLY: every alert by its rule entry, same caps
  obs_placebo  RESEARCH ONLY: obs_all's random-entry twin

Every fill writes pf_order (final status) + pf_fill (cost breakdown from core.costs, base
slippage as a separate cost line) and changes cash and the position (pf_position, plus an
append-only pf_position_event row); a sell writes a pf_trade row for the qty it closes.
Phase 1 enforces cash only; the risk gate and the caps are Phase 2.
"""

from __future__ import annotations

import datetime as _dt
import json

from sqlalchemy import text

from drishti.core import costs

VERSION = "pf-v1"
START_CAPITAL = 1_000_000.0
LABEL = "Paper portfolio — decision support, not a validated edge. Paper only, no real orders."
BENCH_KEY = "NSE_INDEX|Nifty 500"
PORTFOLIOS = (
    ("main", "main", "Main paper portfolio (swing + intraday)", False),
    ("placebo", "placebo", "Placebo book: same gate-pass candidates, random next-bar entry",
     False),
    ("benchmark", "benchmark", "Benchmark: Nifty 500 price index buy-and-hold (no dividends)",
     False),
    ("obs_all", "observational", "RESEARCH ONLY: all alerts, rule entry", True),
    ("obs_placebo", "observational", "RESEARCH ONLY: all alerts, random entry", True),
)


class PaperError(ValueError):
    """A fill the ledger refuses (no cash, selling more than held, ...)."""


def fill_costs(side: str, product: str, price: float, qty: int,
               slippage_bps: float = costs.BASE_SLIPPAGE_BPS) -> dict:
    """The cost lines of one fill, from core.costs (delivery for CNC, intraday for MIS)."""
    turnover = price * qty
    lg = costs.leg(side, product, turnover)
    slip = round(turnover * slippage_bps / 1e4, 2)
    total = round(lg.total + slip, 2)
    return {"brokerage": lg.brokerage, "stt": lg.stt, "exchange_fee": lg.txn, "gst": lg.gst,
            "stamp": lg.stamp, "sebi_fee": lg.sebi, "dp": lg.dp, "slippage": slip,
            "total_costs": total,
            "net_cash": round(-turnover - total if side == "BUY" else turnover - total, 2),
            "cost_model": (costs.MODEL_MIS if product == "MIS" else costs.MODEL)
            + f"; slippage {slippage_bps} bps"}


async def init_portfolios(s, start: _dt.date, bench_close: float | None,
                          bench_date: _dt.date | None) -> list[str]:
    """Create the five portfolios once (idempotent: existing rows are left as they are)."""
    made = []
    for pid, kind, label, research in PORTFOLIOS:
        meta = {"index_key": BENCH_KEY, "base_close": bench_close,
                "base_date": str(bench_date) if bench_date else None} if pid == "benchmark" else {}
        r = (await s.execute(text("""insert into pf_portfolio(id, kind, label, research_only,
            version, start_date, starting_capital, cash, holdings_value, equity, peak_equity, meta,
            as_of) values (:i, :k, :l, :ro, :v, :d, :c, :c, 0, :c, :c, cast(:m as jsonb),
            clock_timestamp()) on conflict (id) do nothing returning id"""),
            {"i": pid, "k": kind, "l": label, "ro": research, "v": VERSION, "d": start,
             "c": START_CAPITAL, "m": json.dumps(meta)})).first()
        if r:
            made.append(pid)
    await s.commit()
    return made


async def _portfolio(s, pid: str) -> dict:
    p = (await s.execute(text("select * from pf_portfolio where id = :i for update"),
                         {"i": pid})).mappings().first()
    if p is None:
        raise PaperError(f"no portfolio {pid}")
    return dict(p)


async def _refresh(s, pid: str, ts: _dt.datetime) -> dict:
    """Recompute holdings and equity from cash + positions; keep the peak."""
    hv = float((await s.execute(text("""select coalesce(sum(last_price * qty), 0)
        from pf_position where portfolio_id = :i"""), {"i": pid})).scalar())
    p = await _portfolio(s, pid)
    eq = float(p["cash"]) + hv
    await s.execute(text("""update pf_portfolio set holdings_value = :h, equity = :e,
        peak_equity = greatest(peak_equity, :e), as_of = :t, updated_at = clock_timestamp()
        where id = :i"""), {"h": hv, "e": eq, "t": ts, "i": pid})
    return {"cash": float(p["cash"]), "holdings_value": hv, "equity": eq}


async def execute(s, pid: str, book: str, *, instrument_key: str, symbol: str | None,
                  side: str, qty: int, price: float, ts: _dt.datetime, reason_code: str,
                  reason_text: str, product: str | None = None, order_type: str = "MARKET",
                  trigger_price: float | None = None, limit_price: float | None = None,
                  stop: float | None = None, target: float | None = None,
                  source_alert_id: int | None = None, decision_id: int | None = None,
                  slippage_bps: float = costs.BASE_SLIPPAGE_BPS,
                  session_count: int | None = None) -> dict:
    """One paper order filled in full at `price` (Phase 2 decides price and time from known
    bars). A refused order is stored with status 'rejected' and raises PaperError."""
    product = product or ("MIS" if book == "intraday" else "CNC")
    if qty <= 0 or price <= 0:
        raise PaperError("qty and price must be > 0")
    p = await _portfolio(s, pid)
    pos = (await s.execute(text("""select * from pf_position where portfolio_id = :i
        and book = :b and instrument_key = :k for update"""),
        {"i": pid, "b": book, "k": instrument_key})).mappings().first()
    c = fill_costs(side, product, price, qty, slippage_bps)
    why = None
    if side == "BUY" and float(p["cash"]) + c["net_cash"] < 0:
        why = f"cash {float(p['cash']):.2f} < cost {-c['net_cash']:.2f}"
    if side == "SELL" and (pos is None or pos["qty"] < qty):
        why = f"sell {qty} > held {pos['qty'] if pos else 0} (no short selling)"
    order = {"i": pid, "b": book, "t": ts, "k": instrument_key, "sym": symbol, "sd": side,
             "pr": product, "ot": order_type, "q": qty, "lp": limit_price, "tp": trigger_price,
             "a": source_alert_id, "d": decision_id, "rc": reason_code, "rt": reason_text,
             "st": stop, "tg": target}
    oid = (await s.execute(text("""insert into pf_order(portfolio_id, book, ts, instrument_key,
        symbol, side, product, order_type, qty, limit_price, trigger_price, status,
        source_alert_id, decision_id, reason_code, reason_text, stop, target) values (:i, :b,
        :t, :k, :sym, :sd, :pr, :ot, :q, :lp, :tp, :status, :a, :d, :rc, :rt, :st, :tg)
        returning id"""), {**order, "status": "rejected" if why else "filled"})).scalar()
    if why:
        await s.commit()
        raise PaperError(f"order {oid} rejected: {why}")
    fid = (await s.execute(text("""insert into pf_fill(order_id, portfolio_id, ts,
        instrument_key, symbol, side, product, qty, price, brokerage, stt, exchange_fee, gst,
        stamp, sebi_fee, dp, slippage, total_costs, net_cash, cost_model) values (:o, :i, :t, :k,
        :sym, :sd, :pr, :q, :p, :brokerage, :stt, :exchange_fee, :gst, :stamp, :sebi_fee, :dp,
        :slippage, :total_costs, :net_cash, :cost_model) returning id"""),
        {"o": oid, "i": pid, "t": ts, "k": instrument_key, "sym": symbol, "sd": side,
         "pr": product, "q": qty, "p": price, **c})).scalar()
    await s.execute(text("update pf_portfolio set cash = cash + :n where id = :i"),
                    {"n": c["net_cash"], "i": pid})
    ev = {"i": pid, "b": book, "k": instrument_key, "f": fid, "p": price, "t": ts}
    trade_id = None
    if side == "BUY":
        if pos is None:
            await s.execute(text("""insert into pf_position(portfolio_id, book, instrument_key,
                symbol, product, qty, avg_price, entry_costs, opened_ts, last_price,
                last_price_at, unrealized_pnl, stop, target, source_alert_id, reason_in) values
                (:i, :b, :k, :sym, :pr, :q, :p, :ec, :t, :p, :t, :u, :st, :tg, :a, :rt)"""),
                {**ev, "sym": symbol, "pr": product, "q": qty, "ec": c["total_costs"],
                 "u": -c["total_costs"], "st": stop, "tg": target, "a": source_alert_id,
                 "rt": reason_text})
            await _event(s, ev, "OPEN", 0, qty, None, price)
        else:
            nq = pos["qty"] + qty
            avg = (float(pos["avg_price"]) * pos["qty"] + price * qty) / nq
            await s.execute(text("""update pf_position set qty = :q, avg_price = :a,
                entry_costs = entry_costs + :ec, last_price = :p, last_price_at = :t,
                updated_at = clock_timestamp() where id = :id"""),
                {"q": nq, "a": avg, "ec": c["total_costs"], "p": price, "t": ts,
                 "id": pos["id"]})
            await _event(s, ev, "ADD", pos["qty"], nq, float(pos["avg_price"]), avg)
    else:
        avg, held = float(pos["avg_price"]), pos["qty"]
        entry_costs = float(pos["entry_costs"]) * qty / held       # the closed share
        gross = (price - avg) * qty
        cst = entry_costs + c["total_costs"]
        net = gross - cst
        risk = (avg - float(pos["stop"])) * qty if pos["stop"] is not None and \
            float(pos["stop"]) < avg else None
        trade_id = (await s.execute(text("""insert into pf_trade(portfolio_id, book,
            instrument_key, symbol, product, qty, entry_ts, exit_ts, entry_price, exit_price,
            gross_pnl, costs, net_pnl, stop, r_multiple, holding_sessions, reason_in, reason_out,
            source_alert_id, exit_order_id) values (:i, :b, :k, :sym, :pr, :q, :et, :t, :ep, :p,
            :g, :c, :n, :st, :r, :hs, :ri, :ro, :a, :o) returning id"""),
            {**ev, "sym": pos["symbol"], "pr": pos["product"], "q": qty, "et": pos["opened_ts"],
             "ep": avg, "g": gross, "c": cst, "n": net, "st": pos["stop"],
             "r": net / risk if risk else None, "hs": session_count, "ri": pos["reason_in"],
             "ro": reason_text, "a": pos["source_alert_id"], "o": oid})).scalar()
        if qty == held:
            await s.execute(text("delete from pf_position where id = :id"), {"id": pos["id"]})
            await _event(s, ev, "CLOSE", held, 0, avg, None)
        else:
            await s.execute(text("""update pf_position set qty = :q,
                entry_costs = entry_costs - :ec, last_price = :p, last_price_at = :t,
                updated_at = clock_timestamp() where id = :id"""),
                {"q": held - qty, "ec": entry_costs, "p": price, "t": ts, "id": pos["id"]})
            await _event(s, ev, "REDUCE", held, held - qty, avg, avg)
    await _mark_unrealized(s, pid)
    snap = await _refresh(s, pid, ts)
    await s.commit()
    return {"order_id": oid, "fill_id": fid, "trade_id": trade_id, "costs": c, **snap}


async def _event(s, ev: dict, kind: str, qb: int, qa: int, ab, aa) -> None:
    await s.execute(text("""insert into pf_position_event(portfolio_id, book, instrument_key,
        event, fill_id, qty_before, qty_after, avg_before, avg_after, price, ts) values (:i, :b,
        :k, :e, :f, :qb, :qa, :ab, :aa, :p, :t)"""),
        {**ev, "e": kind, "qb": qb, "qa": qa, "ab": ab, "aa": aa})


async def _mark_unrealized(s, pid: str) -> None:
    await s.execute(text("""update pf_position set unrealized_pnl =
        (last_price - avg_price) * qty - entry_costs where portfolio_id = :i"""), {"i": pid})


async def mark_to_market(s, pid: str, prices: dict[str, tuple[float, _dt.datetime]],
                         ts: _dt.datetime) -> dict:
    """Update each open position's last price from `prices` (key -> (price, price time)),
    log a MARK event per changed position, refresh the snapshot."""
    pos = (await s.execute(text("""select id, book, instrument_key, qty, avg_price, last_price
        from pf_position where portfolio_id = :i for update"""), {"i": pid})).mappings().all()
    marked, missing = 0, []
    for p in pos:
        px = prices.get(p["instrument_key"])
        if px is None:
            missing.append(p["instrument_key"])
            continue
        price, at = px
        if float(p["last_price"]) == float(price):
            continue
        await s.execute(text("""update pf_position set last_price = :p, last_price_at = :a,
            updated_at = clock_timestamp() where id = :id"""), {"p": price, "a": at,
                                                                 "id": p["id"]})
        await _event(s, {"i": pid, "b": p["book"], "k": p["instrument_key"], "f": None,
                         "p": price, "t": at}, "MARK", p["qty"], p["qty"],
                     float(p["avg_price"]), float(p["avg_price"]))
        marked += 1
    await _mark_unrealized(s, pid)
    snap = await _refresh(s, pid, ts)
    await s.commit()
    return {"portfolio": pid, "positions": len(pos), "marked": marked, "missing": missing, **snap}


async def benchmark_value(s, day: _dt.date) -> dict:
    """The benchmark book's equity on `day`: start capital x Nifty 500 close / base close."""
    p = await _portfolio(s, "benchmark")
    await s.rollback()                                   # only a read (release the row lock)
    base = (p["meta"] or {}).get("base_close")
    close = (await s.execute(text("""select close from index_bar where index_key = :k
        and session_date <= :d order by session_date desc, fetched_at desc limit 1"""),
        {"k": BENCH_KEY, "d": day})).scalar()
    if not base or close is None:
        return {"equity": None}
    return {"equity": round(START_CAPITAL * float(close) / float(base), 2),
            "close": float(close), "base_close": float(base)}


async def snapshot(s, day: _dt.date, ts: _dt.datetime) -> dict:
    """One pf_equity row per (portfolio, book) for `day` (append-only; the newest wins). The
    main portfolio's 'all' row carries the benchmark and placebo equity of the same day."""
    bench = await benchmark_value(s, day)
    out = {}
    if bench["equity"] is not None:
        await s.execute(text("""update pf_portfolio set cash = :e, equity = :e,
            peak_equity = greatest(peak_equity, :e), as_of = :t, updated_at = clock_timestamp()
            where id = 'benchmark'"""), {"e": bench["equity"], "t": ts})
    ports = (await s.execute(text("select * from pf_portfolio order by id"))).mappings().all()
    eq = {p["id"]: float(p["equity"]) for p in ports}
    for p in ports:
        prev = (await s.execute(text("""select equity from pf_equity_latest
            where portfolio_id = :i and book = 'all' and date < :d order by date desc limit 1"""),
            {"i": p["id"], "d": day})).scalar()
        prev = float(prev) if prev is not None else float(p["starting_capital"])
        rows = [("all", float(p["cash"]), float(p["holdings_value"]), float(p["equity"]))]
        for book in ("swing", "intraday"):
            hv = float((await s.execute(text("""select coalesce(sum(last_price * qty), 0)
                from pf_position where portfolio_id = :i and book = :b"""),
                {"i": p["id"], "b": book})).scalar())
            rows.append((book, None, hv, hv))
        for book, cash, hv, e in rows:
            await s.execute(text("""insert into pf_equity(portfolio_id, book, date, cash,
                holdings_value, equity, daily_pnl, drawdown, benchmark_equity, placebo_equity)
                values (:i, :b, :d, :c, :h, :e, :dp, :dd, :be, :pe)"""),
                {"i": p["id"], "b": book, "d": day, "c": cash, "h": hv, "e": e,
                 "dp": round(e - prev, 2) if book == "all" else None,
                 "dd": round(e / float(p["peak_equity"]) - 1, 5) if book == "all" else None,
                 "be": bench["equity"] if p["id"] == "main" and book == "all" else None,
                 "pe": eq.get("placebo") if p["id"] == "main" and book == "all" else None})
        out[p["id"]] = round(float(p["equity"]), 2)
    await s.commit()
    return {"date": str(day), "equity": out, "benchmark": bench}
