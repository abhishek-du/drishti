"""Research sprint step 2/3 (user 2026-10-29): candidates C1a-C1c (monthly momentum portfolio),
C2a/C2b (S1a breakout + trailing MA), C3a/C3b (PEAD). EXPLORATORY, SURVIVORSHIP-BIASED.
Definitions and the finalist rule are logged in
/windows/drishti_data/research/track-b/sprint/VARIANTS.md before the run.

    .venv/bin/python -m drishti.replay.sprint --replay <replay dir> --window dev
    .venv/bin/python -m drishti.replay.sprint --replay <replay dir> --window holdout --only C1c,C2a

dev = 2019-09 → 2024-12-31 (no bar after it is read); holdout = 2025-01-01 → the replay's C
(bars to its data end). The holdout runs once, for at most 2 finalists.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

from drishti.core import costs
from drishti.core.clock import IST
from drishti.replay import load
from drishti.replay.run import calendar
from drishti.strategies import orders

LABEL = "EXPLORATORY, SURVIVORSHIP-BIASED"
SEED = 20261028
DEV_END = _dt.date(2024, 12, 31)
HOLDOUT_START = _dt.date(2025, 1, 1)
BPS = (5, 10, 20)
CAPITAL = 10_00_000.0
TOP, BUFFER = 30, 60
PEAD_MIN_RET, PEAD_MIN_VOLR, PEAD_STOP_ATR = 0.04, 2.0, 3.0
YEARS = (2020, 2021, 2022, 2023, 2024)


# ---------- C1: monthly momentum portfolio ----------

def month_ends(cal: list) -> list:
    s = pd.Series(cal, index=pd.to_datetime(cal))
    return list(s.groupby(s.index.to_period("M")).last())


def c1(panel, bars, cal, n500: pd.Series, start, end, regime: bool, buffer: bool,
       bps: float) -> dict:
    """The book from the first month-end >= start - 1 month with RS, to the last period whose
    exit open is <= end."""
    el = panel[panel.eligible & panel.rs_score.notna()]
    rank = {d: g.sort_values("rs_score", ascending=False).instrument_key.tolist()
            for d, g in el.groupby("session_date")}
    gate = panel.groupby("session_date").spec_gate.first()
    elig = {d: g.instrument_key.tolist() for d, g in panel[panel.eligible].groupby("session_date")}
    op = bars.set_index(["instrument_key", "session_date"]).open
    cl = bars.set_index(["instrument_key", "session_date"]).close.sort_index()
    nxt = {d: cal[i + 1] for i, d in enumerate(cal[:-1])}
    me = [d for d in month_ends(cal) if d in rank and d in nxt and start <= nxt[d] <= end]
    periods = [(r, r2, nxt[r], nxt[r2]) for r, r2 in zip(me[:-1], me[1:])]

    def px(k, d):
        v = op.get((k, d))
        if v is not None and np.isfinite(v):
            return float(v)
        try:
            s = cl.loc[k].loc[:d]
            return float(s.iloc[-1]) if len(s) else None
        except KeyError:
            return None

    V, w, hold, rows = CAPITAL, {}, [], []
    for r, r2, e0, e1 in periods:
        if regime and not bool(gate.get(r, False)):
            target = []
        elif buffer:
            rk = {k: i for i, k in enumerate(rank[r])}
            keep = [k for k in hold if rk.get(k, 10 ** 9) < BUFFER]
            target = keep + [k for k in rank[r] if k not in keep][:TOP - len(keep)]
        else:
            target = rank[r][:TOP]
        target = [k for k in target if px(k, e0) is not None]
        wt = {k: 1 / len(target) for k in target} if target else {}
        names = set(wt) | set(w)
        cost, traded = 0.0, 0.0
        for k in names:
            d = (wt.get(k, 0.0) - w.get(k, 0.0)) * V
            if abs(d) < 1:
                continue
            leg = costs.buy(d) if d > 0 else costs.sell(-d, dp=True)
            cost += leg.total + abs(d) * bps / 1e4
            traded += abs(d)
        V0 = V
        V -= cost
        ret = {k: px(k, e1) / px(k, e0) - 1 for k in wt if px(k, e1) is not None}
        gross = sum(wt[k] * ret.get(k, 0.0) for k in wt)
        V *= 1 + gross
        tot = 1 + gross
        w = {k: wt[k] * (1 + ret.get(k, 0.0)) / tot for k in wt} if wt else {}
        hold = list(wt)
        ew = [px(k, e1) / px(k, e0) - 1 for k in elig.get(r, [])
              if px(k, e0) is not None and px(k, e1) is not None]
        nr, n_next = n500.get(r), n500.get(r2)
        rows.append({"rebalance": r, "entry": e0, "exit": e1, "year": e0.year,
                     "net": V / V0 - 1, "gross": gross, "cost_frac": cost / V0,
                     "turnover": traded / V0 / 2, "n_names": len(wt),
                     "ew": float(np.mean(ew)) if ew else 0.0,
                     "n500": float(n_next / nr - 1) if nr and n_next else np.nan})
    return {"periods": pd.DataFrame(rows), "final": V}


def _cagr(r: pd.Series) -> float:
    return float((1 + r).prod() ** (12 / len(r)) - 1) if len(r) else float("nan")


def _mdd(r: pd.Series) -> float:
    eq = (1 + r).cumprod()
    return float((eq / eq.cummax() - 1).min()) if len(r) else float("nan")


def _streak(neg: pd.Series) -> int:
    best = cur = 0
    for x in neg:
        cur = cur + 1 if x else 0
        best = max(best, cur)
    return best


def c1_metrics(p: pd.DataFrame) -> dict:
    r = p.net
    out = {"months": int(len(p)), "cagr": _cagr(r), "vol": float(r.std() * math.sqrt(12)),
           "sharpe": float(r.mean() / r.std() * math.sqrt(12)) if r.std() > 0 else None,
           "max_dd": _mdd(r), "worst_month": float(r.min()),
           "turnover_month": float(p.turnover.mean()),
           "cost_drag_year": float(p.cost_frac.sum() / (len(p) / 12)),
           "cagr_n500": _cagr(p.n500), "cagr_ew": _cagr(p.ew),
           "losing_streak_months": _streak(r < 0), "cash_months": int((p.n_names == 0).sum())}
    out["excess_n500"], out["excess_ew"] = out["cagr"] - out["cagr_n500"], out["cagr"] - out["cagr_ew"]
    yr = {}
    for y, g in p.groupby("year"):
        a, n5, ew = (1 + g.net).prod() - 1, (1 + g.n500).prod() - 1, (1 + g.ew).prod() - 1
        yr[int(y)] = {"months": int(len(g)), "ret": float(a), "n500": float(n5), "ew": float(ew),
                      "ex_n500": float(a - n5), "ex_ew": float(a - ew)}
    out["by_year"] = yr
    return out


# ---------- C2/C3: event trades ----------

def arrays(bars: pd.DataFrame) -> dict:
    out = {}
    for k, g in bars.groupby("instrument_key", sort=False):
        c = g.close.to_numpy(float)
        v = g.volume.to_numpy(float)
        out[k] = {"dates": g.session_date.tolist(), "pos": {d: i for i, d in enumerate(g.session_date)},
                  "o": g.open.to_numpy(float), "h": g.high.to_numpy(float),
                  "lo": g.low.to_numpy(float), "c": c, "v": v,
                  "ma20": pd.Series(c).rolling(20, min_periods=20).mean().to_numpy(),
                  "ma50": pd.Series(c).rolling(50, min_periods=50).mean().to_numpy(),
                  "vma20": pd.Series(v).rolling(20, min_periods=20).mean().to_numpy()}
    return out


def _trade(k, T, e, s, a, x, spike=False) -> dict | None:
    if x is None:
        return None
    n = orders.qty(e, s, spike)
    rec = {"instrument_key": k, "T": T, "year": T.year, "exit_kind": x[0], "exit_i": x[2],
           "gross_r": (x[1] - e) / (e - s)}
    for b in BPS:
        cost = costs.round_trip(e, x[1], n, b)
        rec[f"net_r_{b}"] = (x[1] - e) / (e - s) - cost / n / (e - s)
    return rec


def c2(panel, arr, t_max, ma: str) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    ev = panel[panel.S1a & (panel.session_date <= t_max)].sort_values(
        ["session_date", "instrument_key"])
    rules, plac, st = [], [], {"events": int(len(ev)), "GAP_CHASE": 0, "NOT_TRIGGERED": 0}
    for i, r in enumerate(ev.itertuples()):
        A = arr[r.instrument_key]
        t = A["pos"][r.session_date]
        f, at = t + 1, float(r.atr)
        o, h, lo, c = A["o"], A["h"], A["lo"], A["c"]
        trig = h[t] * 1.0005
        e = None
        if o[f] > trig + 0.5 * at:
            st["GAP_CHASE"] += 1
        elif o[f] >= trig:
            e = float(o[f])
        elif h[f] >= trig:
            e = trig
        else:
            st["NOT_TRIGGERED"] += 1
        if e is not None:
            s = min(lo[t] - 0.1 * at, trig - at)
            x = orders.exit_trail(o, h, lo, c, A[ma], f, s, 60)
            rules.append(_trade(r.instrument_key, r.session_date, e, s, at, x, r.vix_spike))
        u = np.random.default_rng([SEED, 2, i]).random()
        ep = float(lo[f] + u * (h[f] - lo[f]))
        sp = min(lo[t] - 0.1 * at, ep - at)
        x = orders.exit_trail(o, h, lo, c, A[ma], f, sp, 60)
        plac.append(_trade(r.instrument_key, r.session_date, ep, sp, at, x, r.vix_spike))
    return (pd.DataFrame([x for x in rules if x]), pd.DataFrame([x for x in plac if x]), st)


async def results_events(ps) -> tuple[pd.DataFrame, dict]:
    rows = (await ps.execute(text("""
        select distinct on (instrument_key, period_end) instrument_key, period_end, event_at
        from results_event where kind = 'HISTORICAL_FILING' and instrument_key is not null
          and event_at is not null and period_end is not null
        order by instrument_key, period_end, event_at"""))).all()
    chk = (await ps.execute(text("""select count(*), count(*) filter (where event_at is null),
        count(*) filter (where period_end is null),
        count(*) filter (where (event_at at time zone 'Asia/Kolkata')::time = '00:00'),
        count(*) filter (where extract(hour from event_at at time zone 'Asia/Kolkata') < 8),
        count(*) filter (where event_at >= knowable_at - interval '1 day'),
        count(*) filter (where instrument_key is null)
        from results_event where kind = 'HISTORICAL_FILING'"""))).one()
    df = pd.DataFrame(rows, columns=["instrument_key", "period_end", "event_at"])
    ist = pd.to_datetime(df.event_at, utc=True).dt.tz_convert(IST)
    df["d"], df["after_close"] = ist.dt.date, (ist.dt.hour * 60 + ist.dt.minute) >= 15 * 60 + 30
    keys = ("filings", "event_at_missing", "period_end_missing", "time_midnight",
            "time_before_08_ist", "event_at_within_1d_of_ingest", "instrument_missing")
    q = dict(zip(keys, (int(x) for x in chk)))
    q["first_filing_per_stock_period"] = int(len(df))
    return df, q


def _reaction_session(A, d, after_close) -> int | None:
    import bisect
    i = bisect.bisect_left(A["dates"], d)
    if i < len(A["dates"]) and A["dates"][i] == d and after_close:
        i += 1
    return i if i < len(A["dates"]) else None


def c3(panel, arr, res: pd.DataFrame, t_min, t_max, hold: int):
    el = panel[panel.eligible].set_index(["instrument_key", "session_date"])
    eligible, atr = set(el.index), el.atr
    rules, plac, stats = [], [], {"filings": 0, "reaction_ok": 0}
    by_key = {k: g for k, g in res.groupby("instrument_key")}
    for j, (k, g) in enumerate(sorted(by_key.items())):
        A = arr.get(k)
        if A is None:
            continue
        o, h, lo, c, v = A["o"], A["h"], A["lo"], A["c"], A["v"]
        evpos = []
        for r in g.itertuples():
            R = _reaction_session(A, r.d, r.after_close)
            if R is None or R < 1:
                continue
            evpos.append(R)
            T = A["dates"][R]
            if not (t_min <= T <= t_max) or (k, T) not in eligible:
                continue
            stats["filings"] += 1
            ret, volr = c[R] / c[R - 1] - 1, v[R] / A["vma20"][R]
            if not (ret >= PEAD_MIN_RET and volr >= PEAD_MIN_VOLR) or R + 1 >= len(c):
                continue
            stats["reaction_ok"] += 1
            e, a = float(o[R + 1]), float(atr[(k, T)])
            s = e - PEAD_STOP_ATR * a
            x = orders.exit_path(o, h, lo, c, R + 1, s, math.inf, hold=hold)
            rules.append(_trade(k, T, e, s, a, x))
        # placebo: one seeded random eligible session per event, same stock and year, no
        # filing within +-10 sessions
        ev = np.array(sorted(evpos))
        rng = np.random.default_rng([SEED, 3, j])
        for t in [x for x in rules if x and x["instrument_key"] == k]:
            cand = [i for d, i in A["pos"].items() if d.year == t["T"].year and t_min <= d <= t_max
                    and (k, d) in eligible and i + 1 < len(c)
                    and (len(ev) == 0 or np.min(np.abs(ev - i)) > 10)]
            if not cand:
                continue
            P = int(rng.choice(cand))
            e, a = float(o[P + 1]), float(atr[(k, A["dates"][P])])
            s = e - PEAD_STOP_ATR * a
            x = orders.exit_path(o, h, lo, c, P + 1, s, math.inf, hold=hold)
            plac.append(_trade(k, A["dates"][P], e, s, a, x))
    return (pd.DataFrame([x for x in rules if x]), pd.DataFrame([x for x in plac if x]), stats)


def trade_metrics(t: pd.DataFrame) -> dict:
    if t.empty:
        return {"n": 0}
    t = t.sort_values("T")
    cum = t.net_r_10.cumsum()
    out = {"n": int(len(t)), "hit_rate": float((t.net_r_10 > 0).mean()),
           "gross_r": float(t.gross_r.mean()),
           **{f"net_r_{b}": float(t[f"net_r_{b}"].mean()) for b in BPS},
           "max_dd_r": float((cum - cum.cummax()).min()),
           "losing_streak": _streak(t.net_r_10 <= 0),
           "exits": {k: int(v) for k, v in t.exit_kind.value_counts().items()},
           "by_year": {int(y): {"n": int(len(g)), "net_r_10": float(g.net_r_10.mean())}
                       for y, g in t.groupby("year")}}
    return out


def verdict_events(m: dict, p: dict, years=YEARS) -> dict:
    if not m.get("n"):
        return {"finalist_ok": False, "reason": "no trades"}
    diff = m["net_r_10"] - p.get("net_r_10", float("nan"))
    pos = [y for y in years if m["by_year"].get(y, {}).get("net_r_10", -1) > 0]
    return {"beats_placebo_by": diff, "years_positive": pos,
            "finalist_ok": bool(diff >= 0.05 and len(pos) >= 4)}


def verdict_c1(m: dict, years=YEARS) -> dict:
    yrs = [y for y in years if y in m["by_year"] and m["by_year"][y]["ex_n500"] > 0
           and m["by_year"][y]["ex_ew"] > 0]
    return {"years_beating_both": yrs,
            "months_2020": m["by_year"].get(2020, {}).get("months", 0),
            "finalist_ok": bool(m["excess_n500"] > 0 and m["excess_ew"] > 0 and len(yrs) >= 4)}


async def run(rd: Path, window: str, only: set[str] | None) -> dict:
    man = json.loads((rd / "manifest.json").read_text())
    panel = pd.read_parquet(rd / "panel.parquet")
    for c in ("session_date",):
        panel[c] = pd.to_datetime(panel[c]).dt.date
    inp = await load.cached(_dt.date.fromisoformat(man["data_end"]))
    bars = inp["bars"].sort_values(["instrument_key", "session_date"])
    if window == "dev":
        start, end = _dt.date(2019, 9, 1), DEV_END
    else:
        start, end = HOLDOUT_START, _dt.date.fromisoformat(man["data_end"])
    bars = bars[bars.session_date <= end]
    cal = [d for d in calendar(bars) if d <= end]
    t_max = cal[-61]
    if window == "dev":
        panel = panel[panel.session_date <= end]
    else:
        panel = panel[panel.session_date <= _dt.date.fromisoformat(man["cutoff"])]
    from drishti.features.indicators import regime
    idx = inp["index"][inp["index"].session_date <= end]
    n500 = regime(idx).n500
    want = lambda c: only is None or c in only  # noqa: E731
    out: dict = {"label": LABEL, "window": window, "start": str(start), "end": str(end),
                 "event_t_max": str(t_max), "replay": man["run_id"], "candidates": {}}
    for cid, reg, buf in (("C1a", False, False), ("C1b", True, False), ("C1c", True, True)):
        if not want(cid):
            continue
        res = {str(b): c1_metrics(c1(panel, bars, cal, n500, start, end, reg, buf, b)["periods"])
               for b in BPS}
        out["candidates"][cid] = {"by_bps": res, "verdict": verdict_c1(res["10"])}
    arr = arrays(bars)
    ev_start = start if window == "holdout" else _dt.date(2019, 9, 1)
    for cid, ma in (("C2a", "ma20"), ("C2b", "ma50")):
        if not want(cid):
            continue
        r, p, st = c2(panel[panel.session_date >= ev_start], arr, t_max, ma)
        m, pm = trade_metrics(r), trade_metrics(p)
        out["candidates"][cid] = {"rules": m, "placebo": pm, "fills": st,
                                  "verdict": verdict_events(m, pm)}
    if want("C3a") or want("C3b"):
        from drishti.core.db import prajna_sessionmaker
        async with prajna_sessionmaker()() as ps:
            res_ev, q = await results_events(ps)
        out["c3_event_time_check"] = q
        for cid, hold in (("C3a", 20), ("C3b", 60)):
            if not want(cid):
                continue
            r, p, st = c3(panel, arr, res_ev, ev_start, t_max, hold)
            m, pm = trade_metrics(r), trade_metrics(p)
            out["candidates"][cid] = {"rules": m, "placebo": pm, "stats": st,
                                      "verdict": verdict_events(m, pm)}
    return out


def main(args: list[str]) -> dict:
    opt = lambda k, d=None: args[args.index(k) + 1] if k in args else d  # noqa: E731
    rd, window = Path(opt("--replay")), opt("--window", "dev")
    only = set(opt("--only").split(",")) if opt("--only") else None
    if window == "holdout":
        assert only and len(only) <= 2, "the holdout runs once, for at most 2 finalists"
    out = asyncio.run(run(rd, window, only))
    d = load.research_dir() / "sprint" / f"step_{window}"
    d.mkdir(parents=True, exist_ok=window == "dev")
    (d / f"{window}.json").write_text(json.dumps(out, indent=1, default=str))
    return {"out": str(d), "verdicts": {k: v["verdict"] for k, v in out["candidates"].items()}}


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1:]), indent=1, default=str))
