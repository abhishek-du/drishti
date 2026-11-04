"""Time Machine odds panel, swing (daily-bar) version (user 2026-11-04). RESEARCH / SIMULATION,
NOT a signal.

For one plan (stock, as-of date, entry E, stop S, target T, horizon H sessions, long only) two
engines run on the same barrier rule, using only the stock's adjusted daily bars BEFORE the
as-of date:

1. Block-bootstrap Monte Carlo: each pool day is (gap = O/C_prev, H/O, L/O, C/O). Paths are
   drawn by the stationary bootstrap (Politis-Romano, mean block length `block`, circular), so
   runs of days keep their autocorrelation. Every path starts at C_0 = E.
2. Historical replay: the same %-geometry (stop E(1 - s%), target E(1 + t%)) entered at the
   close of every past session, scored over the next H sessions.

Point in time (the research-stack discipline):
- bars: session_date < as_of, with the corporate-action adjustment knowable at as_of 09:00 IST
  (`drishti.data.bars.official(as_of=...)`); short sessions dropped (SC1);
- purge: a replay occurrence is used only when its whole window [t+1, t+H] is before as_of;
- embargo: the last `embargo` sessions before as_of are not used as replay windows either.

Barrier rule (that of `drishti.strategies.orders.exit_path`, with the entry at the previous
close, so a day-1 gap is a real gap): per session, an open below S = stop at the open; a low
<= S = stop; an open above T = target at the open; a high >= T = target; else the close of
session H (NEITHER). Stop first on a bar that touches both.
Loss side: a stop exit is booked at min(the actual R, -slip_r), default -1.2 R, so a slip or
gap through the stop is never booked as a clean -1 R.
Costs: every R is NET of `drishti.core.costs.round_trip` (STT, brokerage, exchange, SEBI, stamp,
DP, GST, slippage `bps` per side), at qty = the plan's, else 1 % risk of ₹10 lakh.

Verdict: from the NET break-even win rate p_be = 1 / (1 + R_net), R_net = net R at the target /
|net R at the stop|, against the Monte Carlo target-first share among the paths that resolved
(target or stop), and the average net R over all paths (NEITHER included). Never gross.

Storage: Track B may not create tables (docs/TRACKS.md). Results are JSONL files under
research/track-b/odds/ (one row per plan, keyed by alert id); the proposed table for Track A
is `PROPOSED_DDL` below, with the same columns.

Intraday (15m/5m) version: the same engine on intraday bars, once the recorder holds ~60
sessions. Not here yet.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from drishti.core import costs
from drishti.strategies import orders

LABEL = ("Odds from the stock's own history — a reality check on your stop/target geometry, "
         "NOT a prediction and NOT evidence of entry edge.")
CAVEAT = ("Stationarity: the recent past may not resemble the future (results, news, circuits, "
          "a regime change). Replay occurrences overlap, so they are not independent. Taxes "
          "are not included.")
VERSION = "odds-1d-v2"
SEED = 20261104
MIN_POOL = 250
THIN_N = 30                      # replay windows below this: THIN SAMPLE, not reliable
GRADER_HOLD_1D = 5               # drishti.scanner.grade.HOLD['1d']: what journal_outcome measures

PROPOSED_DDL = """-- PROPOSED for Track A (Track B does not own migrations): odds per plan, append-only
CREATE TABLE odds_result (
  id bigserial PRIMARY KEY,
  alert_id bigint REFERENCES scan_alert(id),     -- NULL for a manual plan
  basis varchar(8) NOT NULL CHECK (basis IN ('alert', 'took', 'manual')),
  instrument_key varchar(64) NOT NULL, symbol varchar(64),
  as_of date NOT NULL, tf varchar(4) NOT NULL DEFAULT '1d',
  entry numeric(14,4) NOT NULL, stop numeric(14,4) NOT NULL, target numeric(14,4) NOT NULL,
  horizon integer NOT NULL, qty integer NOT NULL,
  replay_n integer NOT NULL, thin_sample boolean NOT NULL,   -- replay windows; thin = < 30
  version varchar(16) NOT NULL, seed bigint NOT NULL, code_sha varchar(40),
  params jsonb NOT NULL,                         -- n_paths, block, lookback, embargo, slip_r, bps
  pool jsonb NOT NULL,                           -- first/last pool date, n days
  mc jsonb NOT NULL, replay jsonb NOT NULL,      -- target/stop/neither/green %, R quantiles
  p_target_mc numeric(6,4),                      -- for the calibration check
  breakeven numeric(6,4), verdict text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX ON odds_result (alert_id, basis, created_at DESC);"""


@dataclass
class Plan:
    instrument_key: str
    as_of: _dt.date              # the entry session: only bars < as_of are read
    entry: float
    stop: float
    target: float
    horizon: int                 # sessions; explicit per plan, no default (user 2026-11-04)
    qty: int | None = None
    symbol: str | None = None
    alert_id: int | None = None
    basis: str = "manual"        # alert / took / manual


@dataclass
class Params:
    n_paths: int = 10_000
    seed: int = SEED
    block: float = 5.0           # mean block length, sessions
    lookback: int = 750          # pool sessions (both engines), ~3 years
    embargo: int = 5
    slip_r: float = 1.2
    bps: float = costs.BASE_SLIPPAGE_BPS
    extra: dict = field(default_factory=dict)


# ---------- the barrier rule (one implementation, both engines) ----------

def barrier(o, h, lo, c, stop: float, target: float) -> tuple[np.ndarray, np.ndarray]:
    """o/h/lo/c: (N, H) price paths after the entry. Returns (kind, exit price): kind 1 =
    target first, -1 = stop first, 0 = neither (close of session H)."""
    sl_hit = (o < stop) | (lo <= stop)
    tp_hit = (o > target) | (h >= target)
    n, hz = c.shape
    big = hz + 1
    ds = np.where(sl_hit.any(1), sl_hit.argmax(1), big)
    dt = np.where(tp_hit.any(1), tp_hit.argmax(1), big)
    rows = np.arange(n)
    kind = np.where((ds <= dt) & (ds < big), -1, np.where(dt < big, 1, 0))
    px = c[:, -1].copy()
    s_i, t_i = np.minimum(ds, hz - 1), np.minimum(dt, hz - 1)
    px = np.where(kind == -1, np.where(o[rows, s_i] < stop, o[rows, s_i], stop), px)
    px = np.where(kind == 1, np.where(o[rows, t_i] > target, o[rows, t_i], target), px)
    return kind, px


def net_r(kind, px, entry: float, stop: float, qty: int, slip_r: float, bps: float
          ) -> tuple[np.ndarray, np.ndarray]:
    """(net R, net ₹) per path; a stop exit is at least -slip_r R before costs."""
    r = entry - stop
    gross = (px - entry) / r
    gross = np.where(kind == -1, np.minimum(gross, -slip_r), gross)
    exit_px = entry + gross * r
    cache: dict[float, float] = {}
    cost = np.empty(len(px))
    for i, x in enumerate(np.round(exit_px, 4)):
        if x not in cache:
            cache[x] = costs.round_trip(entry, float(x), qty, bps)
        cost[i] = cache[x]
    net = gross - cost / qty / r
    return net, net * r * qty


# ---------- engines ----------

def day_table(bars: pd.DataFrame) -> pd.DataFrame:
    """Per session: log gap (O / C_prev) and the intraday shape (H/O, L/O, C/O)."""
    b = bars.sort_values("session_date").reset_index(drop=True)
    out = pd.DataFrame({"session_date": b.session_date,
                        "g": np.log(b.open / b.close.shift(1)), "h": np.log(b.high / b.open),
                        "l": np.log(b.low / b.open), "c": np.log(b.close / b.open)})
    return out.dropna().reset_index(drop=True)


def bootstrap_paths(pool: pd.DataFrame, entry: float, horizon: int, n: int, block: float,
                    rng: np.random.Generator):
    """(N, H) o/h/lo/c paths from C_0 = entry by the circular stationary bootstrap."""
    m = len(pool)
    g, hh, ll, cc = (pool[k].to_numpy() for k in ("g", "h", "l", "c"))
    idx = np.empty((n, horizon), int)
    idx[:, 0] = rng.integers(0, m, n)
    p_new = 1.0 / block
    for j in range(1, horizon):
        jump = rng.random(n) < p_new
        idx[:, j] = np.where(jump, rng.integers(0, m, n), (idx[:, j - 1] + 1) % m)
    o = np.empty((n, horizon))
    h, lo, c = np.empty_like(o), np.empty_like(o), np.empty_like(o)
    prev = np.full(n, float(entry))
    for j in range(horizon):
        k = idx[:, j]
        o[:, j] = prev * np.exp(g[k])
        h[:, j], lo[:, j], c[:, j] = o[:, j] * np.exp(hh[k]), o[:, j] * np.exp(ll[k]), \
            o[:, j] * np.exp(cc[k])
        prev = c[:, j]
    return o, h, lo, c


def replay_paths(bars: pd.DataFrame, horizon: int, embargo: int):
    """Every past occurrence: entry at the close of session t, the window t+1..t+H, purged so
    the window ends >= `embargo` sessions before the last bar (all bars are < as_of). Returns
    (entry close per occurrence, (K, H) o/h/lo/c, entry dates)."""
    b = bars.sort_values("session_date").reset_index(drop=True)
    o, h, lo, c = (b[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    last_t = len(b) - 1 - horizon - embargo
    if last_t < 0:
        return np.array([]), *(np.empty((0, horizon)),) * 4, []
    ts = np.arange(0, last_t + 1)
    win = ts[:, None] + 1 + np.arange(horizon)[None, :]
    return c[ts], o[win], h[win], lo[win], c[win], list(b.session_date.iloc[ts])


def summarize(kind, net, inr) -> dict:
    n = len(kind)
    if n == 0:
        return {"n": 0}
    q = np.quantile(net, [0.05, 0.5, 0.95])
    return {"n": int(n), "target_first": float((kind == 1).mean()),
            "stop_first": float((kind == -1).mean()), "neither": float((kind == 0).mean()),
            "green": float((net > 0).mean()), "avg_r_net": float(net.mean()),
            "median_r_net": float(q[1]), "p05_r_net": float(q[0]), "p95_r_net": float(q[2]),
            "avg_inr_net": float(inr.mean()), "median_inr_net": float(np.median(inr))}


def verdict(mc: dict, r_net: float, p_be: float) -> str:
    if mc.get("n", 0) == 0:
        return "NO VERDICT: not enough history before the as-of date."
    avg = f"avg net {mc['avg_r_net']:+.2f} R over all paths"
    if r_net <= 0:
        return (f"BROKEN GEOMETRY: the target is so close to the entry that a target hit is "
                f"{'a loss' if r_net < 0 else 'zero'} after costs (net R:R {r_net:.2f}); no win "
                f"rate can break even ({avg}).")
    resolved = mc["target_first"] + mc["stop_first"]
    if mc["neither"] >= 0.5:
        return (f"MOSTLY NEITHER: {mc['neither']:.0%} of paths reach neither stop nor target in "
                f"the horizon, so the outcome is mostly the horizon's drift ({avg}); net "
                f"break-even on resolved paths would be {p_be:.0%}.")
    p_win = mc["target_first"] / resolved
    gap = p_win - p_be
    head = (f"Net R:R {r_net:.2f} needs {p_be:.0%} target-first (net break-even, 1/(1+R_net)); "
            f"the stock's own history gives {p_win:.0%} of resolved paths ({avg}).")
    if gap < -0.02:
        tail = " Base rates LOSE on this geometry: any profit has to come from an entry edge " \
               "the history cannot show."
    elif gap < 0.05:
        tail = " Base rates are about break-even: no cushion for errors in the plan."
    else:
        tail = (f" Base rates clear break-even by {gap * 100:.0f} pp; that is drift/volatility "
                "geometry, not entry edge.")
    return head + tail


def odds(plan: Plan, bars: pd.DataFrame, prm: Params = Params()) -> dict:
    """The full result for one plan. `bars`: this stock's adjusted daily bars; anything on or
    after plan.as_of is dropped here as well (defence in depth)."""
    if not (plan.stop < plan.entry < plan.target):
        raise ValueError("long plans only: stop < entry < target")
    b = bars[pd.to_datetime(bars.session_date).dt.date < plan.as_of]
    b = b.sort_values("session_date").tail(prm.lookback + 1).reset_index(drop=True)
    qty = plan.qty or orders.qty(plan.entry, plan.stop)
    s_pct, t_pct = 1 - plan.stop / plan.entry, plan.target / plan.entry - 1
    r = plan.entry - plan.stop
    tp_net, _ = net_r(np.array([1]), np.array([plan.target]), plan.entry, plan.stop, qty,
                      prm.slip_r, prm.bps)
    sl_net, _ = net_r(np.array([-1]), np.array([plan.stop]), plan.entry, plan.stop, qty,
                      prm.slip_r, prm.bps)
    r_net = float(tp_net[0] / -sl_net[0])
    p_be = 1 / (1 + r_net)
    pool = day_table(b)
    out = {"label": LABEL, "caveat": CAVEAT, "version": VERSION, "seed": prm.seed,
           "plan": {**asdict(plan), "as_of": str(plan.as_of), "qty": qty,
                    "stop_pct": s_pct, "target_pct": t_pct, "rr_gross": (plan.target -
                                                                         plan.entry) / r},
           "params": {k: v for k, v in asdict(prm).items() if k != "extra"} | prm.extra,
           "net": {"r_at_target": float(tp_net[0]), "r_at_stop": float(sl_net[0]),
                   "rr_net": r_net, "breakeven_win_rate": p_be},
           "pool": {"n_days": int(len(pool)),
                    "first": str(pool.session_date.iloc[0]) if len(pool) else None,
                    "last": str(pool.session_date.iloc[-1]) if len(pool) else None}}
    if len(pool) < MIN_POOL:
        out.update(mc={"n": 0}, replay={"n": 0}, verdict=verdict({"n": 0}, r_net, p_be))
        _thin(out)
        return out
    a = b.assign(tr=np.maximum(b.high, b.close.shift()) - np.minimum(b.low, b.close.shift()))
    atr_pct = (a.tr / a.close.shift()).rolling(14).mean()
    out["pool"].update(atr14_pct_last=float(atr_pct.iloc[-1]),
                       atr14_pct_median=float(atr_pct.median()),
                       stop_in_atr=float(s_pct / atr_pct.iloc[-1]))
    rng = np.random.default_rng(prm.seed)
    paths = bootstrap_paths(pool, plan.entry, plan.horizon, prm.n_paths, prm.block, rng)
    k, px = barrier(*paths, plan.stop, plan.target)
    out["mc"] = summarize(k, *net_r(k, px, plan.entry, plan.stop, qty, prm.slip_r, prm.bps))
    e0, *rp, dates = replay_paths(b, plan.horizon, prm.embargo)
    if len(e0):
        # the plan's geometry scaled to each occurrence's entry: R = 1 at every occurrence
        scale = (plan.entry / e0)[:, None]
        k2, px2 = barrier(*(x * scale for x in rp), plan.stop, plan.target)
        net2, inr2 = net_r(k2, px2, plan.entry, plan.stop, qty, prm.slip_r, prm.bps)
        out["replay"] = summarize(k2, net2, inr2) | {
            "first_entry": str(dates[0]), "last_entry": str(dates[-1]),
            "counts": {"target": int((k2 == 1).sum()), "stop": int((k2 == -1).sum()),
                       "neither": int((k2 == 0).sum())}}
    else:
        out["replay"] = {"n": 0}
    out["verdict"] = verdict(out["mc"], r_net, p_be)
    _thin(out)
    return out


def _thin(out: dict) -> None:
    n = int(out["replay"].get("n", 0))
    out["replay_n"] = n
    out["replay_n_independent"] = n // out["plan"]["horizon"]
    out["thin_sample"] = n < THIN_N
    if out["thin_sample"]:
        out["verdict"] = (f"THIN SAMPLE (N = {n} replay windows < {THIN_N}): NOT reliable, do "
                          "not act on it. " + out["verdict"])


def row(res: dict) -> dict:
    """The flat record of `PROPOSED_DDL` (also the JSONL line)."""
    p = res["plan"]
    return {"alert_id": p["alert_id"], "basis": p["basis"], "instrument_key": p["instrument_key"],
            "symbol": p["symbol"], "as_of": p["as_of"], "tf": "1d", "entry": p["entry"],
            "stop": p["stop"], "target": p["target"], "horizon": p["horizon"], "qty": p["qty"],
            "replay_n": res["replay_n"], "replay_n_independent": res["replay_n_independent"],
            "thin_sample": res["thin_sample"],
            "version": res["version"], "seed": res["seed"], "code_sha": res.get("code_sha"),
            "params": res["params"], "pool": res["pool"], "mc": res["mc"],
            "replay": res["replay"], "p_target_mc": res["mc"].get("target_first"),
            "breakeven": res["net"]["breakeven_win_rate"], "verdict": res["verdict"],
            "label": res["label"], "caveat": res["caveat"],
            "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat()}


# ---------- calibration (predicted target-first vs realized) ----------

def calibration(pred: pd.DataFrame, bins: int = 5) -> dict:
    """pred: one row per graded plan with p_target_mc and realized ('R1' / 'STOP' / 'TIMEOUT';
    OPEN / NO_FILL are left out). Brier score, base rate and reliability bins."""
    d = pred[pred.realized.isin(["R1", "STOP", "TIMEOUT"])].copy()
    if d.empty:
        return {"n": 0, "note": "no graded plans yet"}
    y = (d.realized == "R1").astype(float)
    p = d.p_target_mc.astype(float)
    edges = np.linspace(0, 1, bins + 1)
    d["bin"] = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    rel = [{"bin": f"{edges[i]:.1f}-{edges[i + 1]:.1f}", "n": int((d.bin == i).sum()),
            "predicted": float(p[d.bin == i].mean()), "realized": float(y[d.bin == i].mean())}
           for i in range(bins) if (d.bin == i).any()]
    base = float(y.mean())
    brier = float(((p - y) ** 2).mean())
    return {"n": int(len(d)), "realized_target_rate": base,
            "mean_predicted": float(p.mean()), "brier": brier,
            "brier_climatology": float(((base - y) ** 2).mean()), "reliability": rel,
            "note": "a handful of plans is noise; read this only at n >= ~100"}


# ---------- I/O (read only) ----------

def code_sha() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return None


async def stock_bars(ps, key: str, as_of: _dt.date, lookback: int) -> pd.DataFrame:
    from drishti.core.clock import IST
    from drishti.data import bars
    cut = _dt.datetime.combine(as_of, _dt.time(9, 0), tzinfo=IST)
    days = int(lookback * 1.6) + 60
    b = await bars.official(ps, as_of - _dt.timedelta(days=1), days=days, as_of=cut, keys=[key])
    return b


async def resolve(ps, symbol: str) -> str:
    from sqlalchemy import text
    k = (await ps.execute(text("""select instrument_key from canon_instrument
        where trading_symbol = :s and instrument_key like 'NSE_EQ|%' limit 1"""),
        {"s": symbol})).scalar()
    if not k:
        raise SystemExit(f"unknown symbol {symbol}")
    return k


async def alert_plans(s, session: _dt.date | None, horizon: int) -> list[Plan]:
    """Plans from the 1d scan alerts (+ the 'took' journal entries with an actual entry),
    mapped as the nightly grader does: target R1, else entry + 2R; `horizon` explicit (the
    grader times out at GRADER_HOLD_1D = 5);
    as_of = the session after the detection bar (bars <= the detection day are read)."""
    from sqlalchemy import text
    q = """select a.id, a.session_date, a.instrument_key, a.symbol, a.entry_ref, a.stop, a.r1,
               a.qty, j.status, j.actual_entry, j.actual_qty
        from scan_alert a left join journal_entry j on j.alert_id = a.id
        where a.tf = '1d'""" + (" and a.session_date = :d" if session else "") + " order by a.id"
    rows = (await s.execute(text(q), {"d": session} if session else {})).mappings().all()
    out = []
    for r in rows:
        e, st = float(r["entry_ref"]), float(r["stop"])
        if not st < e:
            continue
        as_of = r["session_date"] + _dt.timedelta(days=1)
        t = float(r["r1"]) if r["r1"] and float(r["r1"]) > e else e + 2 * (e - st)
        out.append(Plan(r["instrument_key"], as_of, e, st, t, horizon, r["qty"], r["symbol"],
                        r["id"], "alert"))
        if r["status"] == "took" and r["actual_entry"] and st < float(r["actual_entry"]) < t:
            ae = float(r["actual_entry"])
            out.append(Plan(r["instrument_key"], as_of, ae, st, t, horizon,
                            r["actual_qty"] or r["qty"], r["symbol"], r["id"], "took"))
    return out


async def realized(s) -> pd.DataFrame:
    from sqlalchemy import text
    rows = (await s.execute(text("""select distinct on (alert_id, basis) alert_id, basis, outcome
        from journal_outcome where basis in ('alert', 'took')
        order by alert_id, basis, graded_at desc"""))).all()
    return pd.DataFrame(rows, columns=["alert_id", "basis", "realized"])


def text_report(res: dict) -> str:
    p, mc, rp = res["plan"], res["mc"], res["replay"]
    f = lambda x: "-" if x is None else f"{x:.1%}"  # noqa: E731
    L = [res["label"], "",
         f"{p['symbol'] or p['instrument_key']}  as-of {p['as_of']}  E {p['entry']:.2f}  "
         f"S {p['stop']:.2f} (-{p['stop_pct']:.1%})  T {p['target']:.2f} "
         f"(+{p['target_pct']:.1%})  H {p['horizon']}  qty {p['qty']}  replay N "
         f"{res['replay_n']}{' THIN' if res['thin_sample'] else ''}",
         f"seed {res['seed']}  version {res['version']}  pool {res['pool']['first']} .. "
         f"{res['pool']['last']} ({res['pool']['n_days']} days)  slip-through "
         f"-{res['params']['slip_r']} R  slippage {res['params']['bps']} bps/side", "",
         "              target  stop  neither  green  avg R  median R  5%..95% R"]
    for name, m in (("Monte Carlo", mc), ("Replay", rp)):
        if m.get("n"):
            L.append(f"{name:<12} {f(m['target_first']):>7} {f(m['stop_first']):>5} "
                     f"{f(m['neither']):>8} {f(m['green']):>6} {m['avg_r_net']:+.2f} "
                     f"{m['median_r_net']:+9.2f}  {m['p05_r_net']:+.2f}..{m['p95_r_net']:+.2f}"
                     f"  (n {m['n']})")
    L += ["", f"NET: +{res['net']['r_at_target']:.2f} R at target, {res['net']['r_at_stop']:.2f}"
          f" R at stop, break-even {res['net']['breakeven_win_rate']:.1%}",
          "VERDICT: " + res["verdict"], "", res["caveat"]]
    return "\n".join(L)


async def _main(args: list[str]) -> None:
    from drishti.core.db import prajna_sessionmaker, sessionmaker
    from drishti.replay import load
    opt = lambda k, d=None: args[args.index(k) + 1] if k in args else d  # noqa: E731
    prm = Params(n_paths=int(opt("--n", 10_000)), seed=int(opt("--seed", SEED)),
                 block=float(opt("--block", 5)), lookback=int(opt("--lookback", 750)),
                 embargo=int(opt("--embargo", 5)), slip_r=float(opt("--slip-r", 1.2)),
                 bps=float(opt("--bps", costs.BASE_SLIPPAGE_BPS)))
    sha = code_sha()
    outdir = load.research_dir() / "odds"
    async with prajna_sessionmaker()() as ps, sessionmaker()() as s:
        if "--calibrate" in args:
            done = [json.loads(x) for x in (outdir / "odds_results.jsonl").read_text()
                    .splitlines()] if (outdir / "odds_results.jsonl").exists() else []
            # the grader measures a 5-session time-out: only plans at that horizon compare
            pred = pd.DataFrame([{"alert_id": d["alert_id"], "basis": d["basis"],
                                  "p_target_mc": d["p_target_mc"]} for d in done
                                 if d["alert_id"] is not None
                                 and d["horizon"] == GRADER_HOLD_1D])
            if pred.empty:
                print(json.dumps({"n": 0, "note": "no stored odds yet"}))
                return
            pred = pred.drop_duplicates(["alert_id", "basis"], keep="last")
            m = pred.merge(await realized(s), on=["alert_id", "basis"])
            print(json.dumps(calibration(m), indent=1))
            return
        if opt("--horizon") is None:
            raise SystemExit("--horizon (sessions) is required: no silent default")
        hz = int(opt("--horizon"))
        if "--alerts" in args:
            d = opt("--session")
            plans = await alert_plans(s, _dt.date.fromisoformat(d) if d else None, hz)
        else:
            key = opt("--key") or await resolve(ps, opt("--symbol"))
            plans = [Plan(key, _dt.date.fromisoformat(opt("--as-of")), float(opt("--entry")),
                          float(opt("--stop")), float(opt("--target")),
                          hz, int(opt("--qty")) if opt("--qty") else None,
                          opt("--symbol"))]
        res_all = []
        for i, pl in enumerate(plans):
            seed = prm.seed + (pl.alert_id or 0)       # one reproducible seed per alert
            b = await stock_bars(ps, pl.instrument_key, pl.as_of, prm.lookback)
            res = odds(pl, b, Params(**{**asdict(prm), "seed": seed}))
            res["code_sha"] = sha
            res_all.append(res)
            print(text_report(res), "\n", "-" * 100, sep="")
    if "--store" in args:
        outdir.mkdir(parents=True, exist_ok=True)
        with open(outdir / "odds_results.jsonl", "a") as f:
            for r in res_all:
                f.write(json.dumps(row(r), default=str) + "\n")
        (outdir / "PROPOSED_DDL.sql").write_text(PROPOSED_DDL + "\n")
        print(f"stored {len(res_all)} -> {outdir / 'odds_results.jsonl'}")


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1:]))
