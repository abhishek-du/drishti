"""Entry gates, pf-v1 (docs/PREREG_PAPER_PORTFOLIO.md section 2), in order: regime -> odds ->
(risk, in risk.py) -> results veto. Deterministic; every gate returns
{'pass': bool, 'code': SKIP_... | None, ...evidence} for pf_decision.gate_results. No LLM.
"""

from __future__ import annotations

import datetime as _dt

from sqlalchemy import text

from drishti.data import manual

ODDS_MIN_AVG_R = 0.05
ODDS_MIN_REPLAY_N = 30


async def regime(s, day: _dt.date) -> dict:
    """The regime of session `day` = Scout's latest run of that session (Nifty 500 vs SMA200
    and the spec gate, both ON)."""
    r = (await s.execute(text("""select regime from watchlist_run where session_date = :d
        order by created_at desc limit 1"""), {"d": day})).scalar()
    if r is None:
        return {"pass": False, "code": "SKIP_REGIME_UNKNOWN", "why": f"no Scout run for {day}"}
    ok = r.get("status") == "ON" and r.get("spec_gate") == "ON"
    return {"pass": ok, "code": None if ok else "SKIP_REGIME_OFF", "status": r.get("status"),
            "why": "regime and spec gate ON" if ok else
            f"regime {r.get('status')} / spec gate {r.get('spec_gate')}: no new entries",
            "spec_gate": r.get("spec_gate"), "nifty500": r.get("nifty500"),
            "nifty500_sma200": r.get("nifty500_sma200")}


def odds_check(o: dict | None) -> dict:
    """gate_pass = mc_avg_r_net >= +0.05 AND replay_n >= 30 AND NOT thin_sample. target-first %
    is carried for display only."""
    if not o:
        return {"pass": False, "code": "SKIP_ODDS_MISSING", "why": "no odds row for the alert"}
    avg, n, thin = o.get("mc_avg_r_net"), o.get("replay_n"), o.get("thin_sample")
    fails = []
    if avg is None or float(avg) < ODDS_MIN_AVG_R:
        fails.append(f"avg net R {float(avg or 0):+.2f} < +{ODDS_MIN_AVG_R}")
    if n is None or int(n) < ODDS_MIN_REPLAY_N:
        fails.append(f"replay N {n} < {ODDS_MIN_REPLAY_N}")
    if thin:
        fails.append("thin sample")
    return {"pass": not fails, "code": "SKIP_ODDS_FAIL" if fails else None,
            "avg_r_net": None if avg is None else float(avg), "replay_n": n, "thin": bool(thin),
            "target_first_display": None if o.get("mc_target_first") is None
            else float(o["mc_target_first"]), "why": "; ".join(fails) or "odds pass"}


async def odds(s, alert_id: int) -> dict:
    o = (await s.execute(text("""select mc_avg_r_net, replay_n, thin_sample, mc_target_first,
        breakeven, data_as_of from odds_latest where alert_id = :a and basis = 'alert'"""),
        {"a": alert_id})).mappings().first()
    return odds_check(dict(o) if o else None)


async def results_veto(s, ps, key: str, symbol: str | None, window: list[_dt.date],
                       decided_at: _dt.datetime, on: _dt.date) -> dict:
    """Skip when a results / board meeting of the stock falls inside the holding window
    (`window` = the sessions the trade may be held). Sources: Prajna results_event INTIMATION
    (knowable before the decision) and the user's board-meetings CSV (BOARD_MEETINGS rows whose
    purpose/details mention results). A board-meetings CSV that is missing or older than 4 days
    makes the calendar UNKNOWN, which skips (PREREG section 2)."""
    fresh = await manual.board_meetings_freshness(s, on)
    st = fresh["BOARD_MEETINGS"]["status"]
    if st != "OK":
        return {"pass": False, "code": "SKIP_RESULTS_UNKNOWN",
                "why": f"board-meetings CSV {st} (file {fresh['BOARD_MEETINGS']['file_date']})"}
    a, b = window[0], window[-1]
    pj = (await ps.execute(text("""select min(meeting_date) from results_event
        where kind = 'INTIMATION' and instrument_key = :k and meeting_date between :a and :b
          and knowable_at < :t"""), {"k": key, "a": a, "b": b, "t": decided_at})).scalar()
    isin = key.split("|", 1)[1] if "|" in key else None
    bm = (await s.execute(text("""
        with f as (select file_id from manual_file where kind = 'BOARD_MEETINGS'
                   and file_date <= :on order by file_date desc, loaded_at desc limit 1)
        select min(r.event_date) from manual_row r join f using (file_id)
        where (r.isin = :i or (r.isin is null and r.symbol = :sym))
          and r.event_date between :a and :b
          and coalesce((r.extra ->> 'results')::boolean, false)"""),
        {"on": on, "i": isin, "sym": symbol, "a": a, "b": b})).scalar()
    hit = min(d for d in (pj, bm) if d is not None) if (pj or bm) else None
    if hit:
        return {"pass": False, "code": "SKIP_RESULTS_VETO", "meeting": str(hit),
                "source": "results_event" if hit == pj else "board_meetings_csv",
                "why": f"results/board meeting {hit} inside the hold {a}..{b}"}
    return {"pass": True, "code": None, "window": [str(a), str(b)], "why": "no results in hold"}
