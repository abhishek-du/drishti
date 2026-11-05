"""Paper trader + risk manager, pf-v1 Phase 2 (docs/PREREG_PAPER_PORTFOLIO.md). PAPER ONLY:
every order is simulated from known bars and written to pf_* tables; there is no broker call.
Deterministic and seeded. Nothing here schedules itself or initialises the portfolios: the
autonomous loop and the forward start are Phase 3, at the 2026-11-20 switch.

Books (ids in BOOKS; tests pass their own):
  main         gated (regime -> odds -> risk -> results), rule entry (the alert's buy-stop)
  placebo      the same gate-pass candidates, seeded random entry in the next bar's range
  obs_all      RESEARCH ONLY: every 1d alert, no regime/odds/results gate, rule entry
  obs_placebo  RESEARCH ONLY: every 1d alert, random entry
Each book has its own capital and its own risk gate (same caps).

Session flow (run_session, after the official bar of `day` exists):
  1. fill_pending: 'take' decisions of the previous session fill on `day`'s bar (rule: high >=
     trigger -> max(open, trigger), else cancelled; random: low + u * (high - low), seed =
     20261103 + alert id; at/below the stop -> cancelled).
  2. manage_exits: regime OFF on the previous session -> sell at `day`'s open; else the frozen
     exit_path over the bars since the fill (stop first, gap-through, target, time stop at the
     close of session 5).
  3. mark to market at `day`'s close.
  4. decide: the 1d alerts of `day` -> one pf_decision per book (take / skip + every gate);
     15m alerts -> 'observe' decisions in the main book only (intraday is in observe mode until
     the 15m odds engine; the would-do size and the MIS flat-by-15:20 rule are recorded).
"""

from __future__ import annotations

import datetime as _dt
import json

import numpy as np
from sqlalchemy import text

from drishti.core.clock import IST
from drishti.paper import gates, ledger, risk
from drishti.scanner import grade
from drishti.scanner.detect import choose_target
from drishti.strategies.orders import exit_path

BOOKS = {"main": {"gated": True, "entry": "rule"},
         "placebo": {"gated": True, "entry": "random"},
         "obs_all": {"gated": False, "entry": "rule"},
         "obs_placebo": {"gated": False, "entry": "random"}}
HORIZON = grade.HOLD["1d"]                    # 5 sessions, the alert grader's time-out
SEED = grade.SEED                             # 20261103 (+ alert id), as the grader's placebo


def _ts(day: _dt.date, hh: int, mm: int) -> _dt.datetime:
    return _dt.datetime.combine(day, _dt.time(hh, mm), IST)


# ---- state ----------------------------------------------------------------------------------

async def book_state(s, pid: str, day: _dt.date) -> risk.State:
    """Equity/cash from the snapshot; open positions and open risk include pending entries
    (decided 'take', no order yet), whose planned cost is reserved from cash."""
    p = (await s.execute(text("select * from pf_portfolio where id = :i"), {"i": pid})
         ).mappings().one()
    pos = (await s.execute(text("""select count(*), coalesce(sum(greatest(avg_price - stop, 0)
        * qty), 0) from pf_position where portfolio_id = :i"""), {"i": pid})).one()
    pend = (await s.execute(text("""select count(*),
        coalesce(sum((gate_results -> 'risk' ->> 'risk_rupees')::numeric), 0),
        coalesce(sum((gate_results -> 'risk' ->> 'position_value')::numeric), 0)
        from pf_decision d where d.portfolio_id = :i and d.decision = 'take'
          and not exists (select 1 from pf_order o where o.decision_id = d.id)"""),
        {"i": pid})).one()
    dopen = (await s.execute(text("""select equity from pf_equity_latest where portfolio_id = :i
        and book = 'all' and date < :d order by date desc limit 1"""), {"i": pid, "d": day})
    ).scalar()
    return risk.State(equity=float(p["equity"]), cash=float(p["cash"]) - float(pend[2]),
                      open_positions=int(pos[0]) + int(pend[0]),
                      open_risk=float(pos[1]) + float(pend[1]),
                      day_open_equity=float(dopen) if dopen is not None
                      else float(p["starting_capital"]),
                      starting_capital=float(p["starting_capital"]),
                      paused=bool((p["meta"] or {}).get("paused")))


# ---- decisions ------------------------------------------------------------------------------

def plan_of(a: dict) -> dict:
    ct = choose_target(float(a["entry_ref"]), float(a["stop"]), a["r1"], a["r2"], a["qty"] or 0)
    return {"entry_ref": float(a["entry_ref"]), "stop": float(a["stop"]),
            "target": ct["target"], "target_basis": ct["target_basis"], "tags": ct["tags"],
            "net_rr_target": ct["net_rr_target"], "horizon": HORIZON}


async def gather_gates(s, ps, a: dict, day: _dt.date, window: list[_dt.date],
                       reg: dict | None = None) -> dict:
    """The non-risk gates of one 1d alert, in the frozen order (all evaluated and recorded)."""
    reg = reg or await gates.regime(s, day)
    return {"regime": reg, "odds": await gates.odds(s, a["id"]),
            "results": await gates.results_veto(s, ps, a["instrument_key"], a["symbol"], window,
                                                a["created_at"], day)}


def first_fail(g: dict) -> str | None:
    for k in ("regime", "odds", "results"):
        if not g[k]["pass"]:
            return g[k]["code"]
    return None


async def decide_alert(s, a: dict, g: dict, day: _dt.date, books: dict[str, str]) -> list[dict]:
    """One pf_decision per book for a 1d alert. `books` maps the BOOKS role -> portfolio id.
    Idempotent: a book that already has a decision for this alert is left alone."""
    plan = plan_of(a)
    fail = first_fail(g)
    out = []
    for role, pid in books.items():
        cfg = BOOKS[role]
        if (await s.execute(text("""select 1 from pf_decision where portfolio_id = :p
            and source_alert_id = :a"""), {"p": pid, "a": a["id"]})).first():
            continue
        gr = {**({k: g[k] for k in ("regime", "odds", "results")} if cfg["gated"] else
                 {"note": "observational book: regime/odds/results not applied"}),
              "plan": plan, "entry": cfg["entry"], "book_role": role}
        if cfg["gated"] and fail:
            code, take = fail, False
            why = next(g[k].get("why", k) for k in ("regime", "odds", "results")
                       if not g[k]["pass"])
            gr["risk"] = None
        else:
            sz = risk.size(plan["entry_ref"], plan["stop"], await book_state(s, pid, day))
            gr["risk"] = sz
            take = sz["ok"]
            code = (f"ENTRY_{a['detector']}_ODDS_PASS" if cfg["gated"] else
                    f"ENTRY_{a['detector']}_OBS") if take else sz["reason_code"]
            why = sz["why"]
        if take and cfg["gated"]:
            why = (f"Plan {sz['qty']} {a['symbol']} {a['detector']} buy-stop "
                   f"{plan['entry_ref']:.2f}, stop {plan['stop']:.2f}, target "
                   f"{plan['target']:.2f} ({plan['target_basis']}); odds avg net R "
                   f"{g['odds']['avg_r_net']:+.2f} (N {g['odds']['replay_n']}); {sz['why']}")
        did = (await s.execute(text("""insert into pf_decision(portfolio_id, ts, session_date,
            instrument_key, symbol, candidate_from, source_alert_id, tf, decision, gate_results,
            reason_code, why) values (:p, :t, :d, :k, :sym, :cf, :a, '1d', :dec,
            cast(:g as jsonb), :rc, :w) returning id"""),
            {"p": pid, "t": a["created_at"], "d": day, "k": a["instrument_key"],
             "sym": a["symbol"], "cf": "radar" if (a.get("context") or {}).get("source") == "radar"
             else "scanner", "a": a["id"], "dec": "take" if take else "skip",
             "g": json.dumps(gr, default=str), "rc": code, "w": why[:1000]})).scalar()
        out.append({"portfolio": pid, "decision_id": did, "take": take, "code": code})
    await s.commit()
    return out


async def observe_15m(s, a: dict, reg: dict, pid: str, day: _dt.date) -> int | None:
    """Intraday is in observe mode: log what the main book WOULD do (no order)."""
    if (await s.execute(text("""select 1 from pf_decision where portfolio_id = :p
        and source_alert_id = :a"""), {"p": pid, "a": a["id"]})).first():
        return None
    plan = plan_of(a)
    sz = risk.size(plan["entry_ref"], plan["stop"], await book_state(s, pid, day))
    gr = {"regime": reg, "odds": {"pass": False, "code": "PENDING_15M_ENGINE",
                                  "why": "the daily odds engine is not used for 15m trades"},
          "risk": sz, "plan": {**plan, "horizon": "MIS flat by 15:20"}, "mode": "observe"}
    return (await s.execute(text("""insert into pf_decision(portfolio_id, ts, session_date,
        instrument_key, symbol, candidate_from, source_alert_id, tf, decision, gate_results,
        reason_code, why) values (:p, :t, :d, :k, :sym, :cf, :a, '15m', 'observe',
        cast(:g as jsonb), 'OBSERVE_15M_ODDS_PENDING', :w) returning id"""),
        {"p": pid, "t": a["created_at"], "d": day, "k": a["instrument_key"], "sym": a["symbol"],
         "cf": "radar" if (a.get("context") or {}).get("source") == "radar" else "scanner",
         "a": a["id"], "g": json.dumps(gr, default=str),
         "w": f"would size {sz['qty']} sh ({sz['reason_code']}); observe mode, no order"})).scalar()


# ---- fills ----------------------------------------------------------------------------------

def entry_price(role: str, alert_id: int, trigger: float, bar: tuple) -> tuple[float | None, str]:
    """(price, why) of an entry on the next bar (open, high, low, close), or (None, reason)."""
    o, h, lo, _c = bar
    if BOOKS[role]["entry"] == "rule":
        if h < trigger:
            return None, f"buy-stop {trigger:.2f} not triggered (high {h:.2f})"
        return (o, f"gap open {o:.2f} above the buy-stop") if o >= trigger else \
            (trigger, f"buy-stop {trigger:.2f} triggered")
    u = np.random.default_rng(SEED + alert_id).random()
    return lo + u * (h - lo), f"random entry (seed {SEED + alert_id}) in the bar's range"


async def fill_pending(s, day: _dt.date, bars: dict, books: dict[str, str],
                       next_session: dict[_dt.date, _dt.date]) -> dict:
    """Fill or cancel every 'take' decision without an order whose next session is `day`.
    `bars[key][date] = (o, h, l, c)`; `next_session[d]` = the session after d."""
    role_of = {v: k for k, v in books.items()}
    rows = (await s.execute(text("""select d.* from pf_decision d where d.decision = 'take'
        and d.tf = '1d' and d.portfolio_id = any(:p)
        and not exists (select 1 from pf_order o where o.decision_id = d.id)
        order by d.id"""), {"p": list(books.values())})).mappings().all()
    out = {"filled": 0, "cancelled": 0, "rejected": 0}
    for d in rows:
        pid, plan, rk = d["portfolio_id"], d["gate_results"]["plan"], d["gate_results"]["risk"]
        common = dict(instrument_key=d["instrument_key"], symbol=d["symbol"], side="BUY",
                      qty=int(rk["qty"]), stop=plan["stop"], target=plan["target"],
                      source_alert_id=d["source_alert_id"], decision_id=d["id"])
        nxt = next_session.get(d["session_date"])
        if nxt is None or nxt > day:
            continue                                    # not its session yet
        bar = bars.get(d["instrument_key"], {}).get(nxt)
        if nxt < day or bar is None:
            await ledger.record_unfilled(s, pid, "swing", ts=_ts(nxt, 15, 30), status="cancelled",
                                         reason_code="ENTRY_EXPIRED",
                                         reason_text=f"no bar on {nxt}: entry expired",
                                         trigger_price=plan["entry_ref"], **common)
            out["cancelled"] += 1
            continue
        px, why = entry_price(role_of[pid], d["source_alert_id"], plan["entry_ref"], bar)
        if px is not None and px <= plan["stop"]:
            px, why = None, f"entry {px:.2f} at/below the stop {plan['stop']:.2f}"
        if px is None:
            await ledger.record_unfilled(s, pid, "swing", ts=_ts(day, 15, 30), status="cancelled",
                                         reason_code="ENTRY_NOT_FILLED", reason_text=why,
                                         trigger_price=plan["entry_ref"], **common)
            out["cancelled"] += 1
            continue
        try:
            await ledger.execute(s, pid, "swing", price=round(px, 4), ts=_ts(day, 9, 15),
                                 order_type="SL" if BOOKS[role_of[pid]]["entry"] == "rule"
                                 else "MARKET", trigger_price=plan["entry_ref"],
                                 reason_code=d["reason_code"],
                                 reason_text=f"Bought {rk['qty']} @ {px:.2f} ({why}; daily-bar "
                                             f"fill, time of day unknown). {d['why'][:300]}",
                                 **common)
            out["filled"] += 1
        except ledger.PaperError:
            out["rejected"] += 1                        # stored as 'rejected' by the ledger
    return out


# ---- exits ----------------------------------------------------------------------------------

async def manage_exits(s, day: _dt.date, bars: dict, books: dict[str, str],
                       regime_off_prev: bool) -> dict:
    """Exit every open swing position whose rule fires by `day` (see the module doc)."""
    pos = (await s.execute(text("""select * from pf_position where book = 'swing'
        and portfolio_id = any(:p) order by id"""), {"p": list(books.values())})).mappings().all()
    out = {"exits": {}}
    for p in pos:
        opened = p["opened_ts"].astimezone(IST).date()
        ser = sorted((d, v) for d, v in bars.get(p["instrument_key"], {}).items()
                     if opened <= d <= day)
        if not ser or ser[-1][0] != day:
            continue                                     # no bar today: nothing to evaluate
        kw = dict(instrument_key=p["instrument_key"], symbol=p["symbol"], side="SELL",
                  qty=p["qty"])
        if regime_off_prev and opened < day:
            await ledger.execute(s, p["portfolio_id"], "swing", price=float(ser[-1][1][0]),
                                 ts=_ts(day, 9, 15), reason_code="EXIT_REGIME_OFF",
                                 reason_text=f"regime OFF at the previous close: sold at the "
                                             f"{day} open {ser[-1][1][0]:.2f}",
                                 session_count=len(ser), **kw)
            out["exits"]["EXIT_REGIME_OFF"] = out["exits"].get("EXIT_REGIME_OFF", 0) + 1
            continue
        o, h, lo, c = (np.array([v[i] for _, v in ser], float) for i in range(4))
        res = exit_path(o, h, lo, c, 0, float(p["stop"]), float(p["target"]), HORIZON)
        if res is None:
            continue
        kind, px, i = res
        code = {"SL": "EXIT_STOP", "TP": "EXIT_TARGET", "TIME": "EXIT_TIME"}[kind]
        text_ = {"SL": f"stop {float(p['stop']):.2f} hit, exit {px:.2f}",
                 "TP": f"target {float(p['target']):.2f} reached, exit {px:.2f}",
                 "TIME": f"time stop: session {HORIZON} close {px:.2f}"}[kind]
        await ledger.execute(s, p["portfolio_id"], "swing", price=float(px),
                             ts=_ts(ser[i][0], 15, 30), reason_code=code,
                             reason_text=f"{text_} (daily bar {ser[i][0]})",
                             session_count=i + 1, **kw)
        out["exits"][code] = out["exits"].get(code, 0) + 1
    return out


# ---- one session ----------------------------------------------------------------------------

async def run_session(s, ps, day: _dt.date, bars: dict, books: dict[str, str],
                      next_session: dict, window: list[_dt.date], regime_off_prev: bool) -> dict:
    """The Phase 3 loop calls this once per session after `day`'s official bar exists. Phase 2
    ships it with tests only; it is not scheduled and the portfolios are not initialised."""
    out = {"fills": await fill_pending(s, day, bars, books, next_session),
           "exits": await manage_exits(s, day, bars, books, regime_off_prev)}
    closes = {k: (float(v[day][3]), _ts(day, 15, 30)) for k, v in bars.items() if day in v}
    for pid in books.values():
        await ledger.mark_to_market(s, pid, closes, _ts(day, 15, 30))
    alerts = [dict(r) for r in (await s.execute(text("""select * from scan_alert
        where session_date = :d order by id"""), {"d": day})).mappings()]
    reg = await gates.regime(s, day)
    dec = {"take": 0, "skip": 0, "observe": 0}
    for a in alerts:
        if a["tf"] == "1d":
            for r in await decide_alert(s, a, await gather_gates(s, ps, a, day, window, reg),
                                        day, books):
                dec["take" if r["take"] else "skip"] += 1
        elif "main" in books and await observe_15m(s, a, reg, books["main"], day):
            dec["observe"] += 1
    await s.commit()
    out["decisions"] = dec
    return out
