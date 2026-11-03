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
from drishti.data.holidays import SHORT_SESSIONS
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


STCG, LTCG, LTCG_EXEMPT, CESS = 0.20, 0.125, 1_25_000.0, 0.04   # s.111A / s.112A, from 2024-07-23
FUND_TER = 0.003


def fy(d: _dt.date) -> int:
    """Indian financial year (April-March), named by its starting year."""
    return d.year if d.month >= 4 else d.year - 1


def fy_tax(st: float, lt: float, cf: dict) -> float:
    """Tax of one FY on net short- and long-term gains (EXPLORATORY estimate): a short-term loss
    offsets LTCG; a long-term loss offsets only LTCG; unused losses carry forward in `cf`
    (keys st, lt); 20 % STCG, 12.5 % LTCG above ₹1.25 lakh, + 4 % cess, no surcharge."""
    if st < 0:
        x = min(-st, max(lt, 0.0))
        lt -= x
        st += x
        cf["st"] += -st
        st = 0.0
    if lt < 0:
        cf["lt"] += -lt
        lt = 0.0
    u = min(cf["st"], st); st -= u; cf["st"] -= u          # noqa: E702
    u = min(cf["st"], lt); lt -= u; cf["st"] -= u          # noqa: E702
    u = min(cf["lt"], lt); lt -= u; cf["lt"] -= u          # noqa: E702
    return (1 + CESS) * (STCG * st + LTCG * max(lt - LTCG_EXEMPT, 0.0))


def c1_after_tax(panel, bars, cal, start, end, bps: float, pay_tax: bool = True) -> dict:
    """C1a (top 30, monthly, no regime, no buffer) with FIFO lots and Indian capital-gains tax
    paid at the first rebalance of each new FY, then a full sale at the last exit with sell
    charges and the final FY's tax. Pre-tax it follows `c1` (same targets, charges and prices).
    EXPLORATORY, information only."""
    el = panel[panel.eligible & panel.rs_score.notna()]
    rank = {d: g.sort_values("rs_score", ascending=False).instrument_key.tolist()
            for d, g in el.groupby("session_date")}
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

    lots: dict[str, list] = {}               # k -> [[units, cost per unit, buy date], ...]
    gains: dict[int, list] = {}              # fy -> [st, lt]
    cf, paid, taxes, cur_fy = {"st": 0.0, "lt": 0.0}, set(), {}, None
    last_px: dict[str, float] = {}

    def value(d):
        return sum(sum(u for u, _, _ in L) * (px(k, d) or last_px.get(k, 0.0))
                   for k, L in lots.items())

    def sell(k, units, p, d):
        g = gains.setdefault(fy(d), [0.0, 0.0])
        L = lots[k]
        while units > 1e-12 and L:
            u, c0, d0 = L[0]
            x = min(u, units)
            g[0 if (d - d0).days < 365 else 1] += x * (p - c0)
            L[0][0] -= x
            units -= x
            if L[0][0] <= 1e-12:
                L.pop(0)
        if not L:
            del lots[k]

    V = CAPITAL
    for r, r2, e0, e1 in periods:
        V = value(e0) if lots else V
        tax = 0.0
        for y in sorted(gains):
            if pay_tax and y < fy(e0) and y not in paid:
                st_, lt_ = gains[y]
                taxes[y] = fy_tax(st_, lt_, cf)
                tax += taxes[y]
                paid.add(y)
        Va = V - tax
        target = [k for k in rank[r][:TOP] if px(k, e0) is not None]
        names = set(target) | set(lots)
        cost = 0.0
        cur = {k: sum(u for u, _, _ in lots.get(k, [])) * (px(k, e0) or 0.0) for k in names}
        for k in names:
            d = (Va / len(target) if k in target else 0.0) - cur[k]
            if abs(d) < 1:
                continue
            leg = costs.buy(d) if d > 0 else costs.sell(-d, dp=True)
            cost += leg.total + abs(d) * bps / 1e4
        post = (Va - cost) / len(target)
        for k in names:
            p = px(k, e0)
            have = sum(u for u, _, _ in lots.get(k, []))
            want = post / p if k in target else 0.0
            if want < have:
                sell(k, have - want, p, e0)
            elif want > have:
                lots.setdefault(k, []).append([want - have, p, e0])
            last_px[k] = p
        for k in lots:
            last_px[k] = px(k, e1) or last_px[k]
    # final sale at the last exit open
    end_d = periods[-1][3]
    Vend = value(end_d)
    sc = sum(costs.sell(sum(u for u, _, _ in L) * last_px[k], dp=True).total
             + sum(u for u, _, _ in L) * last_px[k] * bps / 1e4 for k, L in lots.items())
    for k in list(lots):
        sell(k, sum(u for u, _, _ in lots[k]), last_px[k], end_d)
    tax_end = 0.0
    for y in sorted(gains):
        if y not in paid:
            taxes[y] = fy_tax(*gains[y], cf)
            tax_end += taxes[y]
    return {"book_final": Vend, "after_tax_final": Vend - sc - tax_end,
            "tax_by_fy": {f"FY{y}-{(y + 1) % 100:02d}": t for y, t in taxes.items()},
            "gains_by_fy": {f"FY{y}-{(y + 1) % 100:02d}": {"st": g[0], "lt": g[1]}
                            for y, g in gains.items()},
            "final_sell_charges": sc, "months": len(periods),
            "start": str(periods[0][2]), "end": str(end_d)}


def fund_after_tax(mom30: pd.Series, start_d: _dt.date, end_d: _dt.date) -> dict:
    """An index fund on the Nifty200 Momentum 30 price index: monthly returns minus the TER,
    tax only at the end (LTCG if held >= 12 months, else STCG)."""
    v = CAPITAL * float((1 + mom30 - FUND_TER / 12).prod())
    g = v - CAPITAL
    lt = (end_d - start_d).days >= 365
    t = fy_tax(0.0 if lt else g, g if lt else 0.0, {"st": 0.0, "lt": 0.0})
    return {"pre_tax_final": v, "after_tax_final": v - t, "tax": t, "ltcg": lt}


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


async def c1a_holdout(rd: Path) -> dict:
    """The ONE C1a holdout look (user override 2026-11-02, rule in VARIANTS.md): pass = C1a after
    costs at 10 bps beats the Nifty200 Momentum 30 price index over 2025-01-01 -> C on the same
    monthly periods. Tax and index-fund numbers are information only."""
    out_dir = load.research_dir() / "sprint" / "c1a_holdout"
    out_dir.mkdir(parents=True, exist_ok=False)               # one look only
    man = json.loads((rd / "manifest.json").read_text())
    cutoff, data_end = (_dt.date.fromisoformat(man[k]) for k in ("cutoff", "data_end"))
    panel = pd.read_parquet(rd / "panel.parquet")
    panel["session_date"] = pd.to_datetime(panel.session_date).dt.date
    panel = panel[panel.session_date <= cutoff]
    inp = await load.cached(data_end)
    bars = inp["bars"].sort_values(["instrument_key", "session_date"])
    cal = calendar(bars)
    from drishti.core.db import sessionmaker
    from drishti.features.indicators import regime
    async with sessionmaker()() as s:
        rows = (await s.execute(text("""select session_date, close from index_bar
            where index_key = 'NSE_INDEX|Nifty200Momentm30' and source = 'UPSTOX_HIST'
              and session_date <= :d order by fetched_at"""), {"d": data_end})).all()
    m30 = pd.DataFrame(rows, columns=["d", "c"]).drop_duplicates("d", keep="last")
    m30 = m30[~m30.d.isin(SHORT_SESSIONS)].set_index("d").c.astype(float).sort_index()
    n500 = regime(inp["index"]).n500
    at = lambda s_, d: float(s_[s_.index <= d].iloc[-1])  # noqa: E731
    res = {str(b): c1(panel, bars, cal, n500, HOLDOUT_START, data_end, False, False, b)
           for b in BPS}
    p = res["10"]["periods"].copy()
    pos = {d: i for i, d in enumerate(cal)}
    p["r2"] = [cal[pos[e] - 1] for e in p.exit]
    p["mom30"] = [at(m30, b) / at(m30, a) - 1 for a, b in zip(p.rebalance, p.r2)]
    p["fund"] = p.mom30 - FUND_TER / 12

    def stats(r):
        return {"total": float((1 + r).prod() - 1), "cagr": _cagr(r),
                "vol": float(r.std() * math.sqrt(12)), "max_dd": _mdd(r),
                "worst_month": float(r.min()),
                "by_year": {int(y): {"months": int(len(g)), "ret": float((1 + g).prod() - 1)}
                            for y, g in r.groupby(p.year)}}

    c1a, idx = stats(p.net), stats(p.mom30)
    tax = c1_after_tax(panel, bars, cal, HOLDOUT_START, data_end, 10)
    fund = fund_after_tax(p.mom30, p.rebalance.iloc[0], p.r2.iloc[-1])
    yrs = len(p) / 12
    out = {"label": LABEL + "; HOLDOUT, the one approved look (user 2026-11-02)",
           "replay": man["run_id"], "cutoff": str(cutoff), "data_end": str(data_end),
           "months": int(len(p)), "first_entry": str(p.entry.iloc[0]),
           "last_exit": str(p.exit.iloc[-1]),
           "PASS": bool(c1a["total"] > idx["total"]),
           "c1a_net_10": c1a, "mom30_index": idx,
           "c1a_by_bps_total": {b: float((1 + r["periods"].net).prod() - 1)
                                for b, r in res.items()},
           "c1a_turnover_month": float(p.turnover.mean()),
           "c1a_cost_drag_year": float(p.cost_frac.sum() / yrs),
           "monthly_corr": float(p.net.corr(p.mom30)),
           "tracking_error": float((p.net - p.mom30).std() * math.sqrt(12)),
           "info_after_tax": {
               "c1a": tax | {"after_tax_cagr": (tax["after_tax_final"] / CAPITAL) ** (1 / yrs) - 1},
               "fund": fund | {"after_tax_cagr": (fund["after_tax_final"] / CAPITAL) ** (1 / yrs) - 1},
               "c1a_no_tax_check_vs_c1": float(c1_after_tax(
                   panel, bars, cal, HOLDOUT_START, data_end, 10, pay_tax=False)["book_final"]
                   / res["10"]["final"] - 1),
               "rates": "STCG 20 % (<12 m), LTCG 12.5 % above ₹1.25 lakh per FY, + 4 % cess, "
                        "no surcharge; fund TER 0.3 %/yr, price index (no dividends)"}}
    (out_dir / "c1a_holdout.json").write_text(json.dumps(out, indent=1, default=str))
    p.to_csv(out_dir / "c1a_holdout_monthly.csv", index=False)
    return out


def main(args: list[str]) -> dict:
    opt = lambda k, d=None: args[args.index(k) + 1] if k in args else d  # noqa: E731
    rd, window = Path(opt("--replay")), opt("--window", "dev")
    if "--c1a-holdout" in args:
        return asyncio.run(c1a_holdout(rd))
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
