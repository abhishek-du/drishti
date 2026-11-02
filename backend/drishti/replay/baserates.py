"""NSE base rates for chart levels (user 2026-11-02): DESCRIPTIVE only, not a strategy and not a
variant. EXPLORATORY, SURVIVORSHIP-BIASED. Design pre-written in
/windows/drishti_data/research/track-b/sprint/VARIANTS.md.

For generic long entries (stop 1 ATR below, so R = ATR) it measures, per level type above the
entry (confirmed swing highs, previous-session high, opening-range high, day high so far):
P(touch before stop / horizon), P(close beyond | touch), and how far price runs after a break,
next to an arbitrary level at the same R distance.

    .venv/bin/python -m drishti.replay.baserates --replay <replay dir> [--tf 15m|daily|both]
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import sys
import zlib
from pathlib import Path

import numpy as np
import pandas as pd

from drishti.features import indicators
from drishti.replay import load
from drishti.replay.run import calendar

LABEL = "DESCRIPTIVE base rates; EXPLORATORY, SURVIVORSHIP-BIASED"
SEED = 20261028
DAILY_END, M15_START, M15_END = _dt.date(2023, 12, 31), _dt.date(2022, 1, 1), _dt.date(2024, 12, 31)
HOLD_DAILY, LOOKBACK = 20, 250
DIST_BUCKETS = (0, 0.5, 1, 2, np.inf)


def swing_flags(h: np.ndarray, k: int) -> np.ndarray:
    """Strict swing highs: h[i] > every high of the k bars on each side (usable from i + k)."""
    n, out = len(h), np.zeros(len(h), bool)
    if n < 2 * k + 1:
        return out
    from numpy.lib.stride_tricks import sliding_window_view as win
    left = win(h[:n - k - 1], k).max(axis=1)              # h[i-k..i-1] for i = k..n-k-1
    right = win(h[k + 1:], k).max(axis=1)                 # h[i+1..i+k]
    mid = h[k:n - k]
    out[k:n - k] = (mid > left[:n - 2 * k]) & (mid > right[:n - 2 * k])
    return out


def nearest_swing(h, flags, k, t, e) -> float:
    """The lowest confirmed swing high above e known at bar t (pivot i with i + k <= t)."""
    a, b = max(0, t - LOOKBACK), t - k + 1
    if b <= a:
        return np.nan
    idx = a + np.flatnonzero(flags[a:b])
    p = h[idx]
    p = p[p > e]
    return float(p.min()) if len(p) else np.nan


def outcome(h, lo, c, first, last, e, s, L) -> tuple:
    """(kind, bar, broke, mfe_r, end_r): kind TOUCH / STOP / TIME from `first` to `last`."""
    R = e - s
    for i in range(first, last + 1):
        if lo[i] <= s:
            return "STOP", i, False, np.nan, np.nan
        if h[i] >= L:
            broke = c[i] > L
            if not broke:
                return "TOUCH", i, False, np.nan, np.nan
            mfe = (h[i:last + 1].max() - L) / R
            return "TOUCH", i, True, float(mfe), float((c[last] - L) / R)
    return "TIME", last, False, np.nan, np.nan


def stock_15m(args) -> list[dict]:
    key, df, ctx = args
    df = df.reset_index(drop=True)
    h, lo, c = (df[k].to_numpy(float) for k in ("high", "low", "close"))
    atr = indicators.atr(df).to_numpy()
    adx = indicators.adx(df).to_numpy()
    ts = df.ts
    day = ts.dt.date.to_numpy()
    hhmm = (ts.dt.hour * 100 + ts.dt.minute).to_numpy()
    sw = {k: swing_flags(h, k) for k in (2, 5)}
    rng = np.random.default_rng([SEED, zlib.crc32(key.encode())])
    out, prev_high = [], None
    starts = np.r_[0, np.flatnonzero(day[1:] != day[:-1]) + 1, len(df)]
    for a, b in zip(starts[:-1], starts[1:]):
        d = day[a]
        dh = h[a:b].max()
        if (key, d) in ctx and prev_high is not None:
            gate, adx_ok, liq = ctx[(key, d)]
            pool = [i for i in range(a, b) if 930 <= hhmm[i] <= 1430]
            eod = max([i for i in range(a, b) if hhmm[i] <= 1500], default=None)
            if pool and eod is not None:
                t = int(rng.choice(pool))
                if np.isfinite(atr[t]) and atr[t] > 0 and t < eod:
                    e = c[t]
                    s = e - atr[t]
                    lv = {"PDH": prev_high,
                          "ORH": h[a] if hhmm[a] == 915 and t > a else np.nan,
                          "HOD": h[a:t + 1].max(),
                          "SW2": nearest_swing(h, sw[2], 2, t, e),
                          "SW5": nearest_swing(h, sw[5], 5, t, e)}
                    base = {"key": key, "date": d, "gate": gate, "liq": liq,
                            "adx_trend": bool(np.isfinite(adx[t]) and adx[t] >= 25),
                            "R_pct": atr[t] / e, "e": e, "s": s}
                    for name, L in lv.items():
                        if not np.isfinite(L) or L <= e:
                            continue
                        kind, i, broke, mfe, end = outcome(h, lo, c, t + 1, eod, e, s, L)
                        out.append(base | {"level": name, "dist_r": (L - e) / (e - s),
                                           "kind": kind, "broke": broke, "mfe_r": mfe,
                                           "end_r": end, "first": t + 1, "last": eod})
        prev_high = dh
    # arbitrary-level baseline: same level type, the R distance of another entry (seeded)
    return _with_arbitrary(out, h, lo, c, rng)


def _with_arbitrary(rows, h, lo, c, rng):
    for r in rows:
        r["arb_dist_r"] = np.nan
    by = {}
    for r in rows:
        by.setdefault(r["level"], []).append(r)
    for name, rs in by.items():
        d = np.array([r["dist_r"] for r in rs])
        for r in rs:
            dd = float(rng.choice(d))
            L = r["e"] + dd * (r["e"] - r["s"])
            kind, _, broke, mfe, _ = outcome(h, lo, c, r["first"], r["last"], r["e"], r["s"], L)
            r.update(arb_dist_r=dd, arb_kind=kind, arb_broke=broke, arb_mfe_r=mfe)
    return rows


def stock_daily(args) -> list[dict]:
    key, df, ctx = args
    df = df.reset_index(drop=True)
    h, lo, c = (df[k].to_numpy(float) for k in ("high", "low", "close"))
    atr = indicators.atr(df).to_numpy()
    adx = indicators.adx(df).to_numpy()
    dates = df.session_date.to_numpy()
    sw = {k: swing_flags(h, k) for k in (3, 5)}
    rng = np.random.default_rng([SEED, 7, zlib.crc32(key.encode())])
    out = []
    for t in range(1, len(df) - HOLD_DAILY):
        x = ctx.get((key, dates[t]))
        if x is None or not np.isfinite(atr[t]) or atr[t] <= 0:
            continue
        gate, _, liq = x
        e = c[t]
        s = e - atr[t]
        lv = {"PDH": h[t - 1], "SW3": nearest_swing(h, sw[3], 3, t, e),
              "SW5": nearest_swing(h, sw[5], 5, t, e)}
        base = {"key": key, "date": dates[t], "gate": gate, "liq": liq,
                "adx_trend": bool(np.isfinite(adx[t]) and adx[t] >= 25), "R_pct": atr[t] / e,
                "e": e, "s": s}
        for name, L in lv.items():
            if not np.isfinite(L) or L <= e:
                continue
            kind, i, broke, mfe, end = outcome(h, lo, c, t + 1, t + HOLD_DAILY, e, s, L)
            out.append(base | {"level": name, "dist_r": (L - e) / (e - s), "kind": kind,
                               "broke": broke, "mfe_r": mfe, "end_r": end, "first": t + 1,
                               "last": t + HOLD_DAILY})
    return _with_arbitrary(out, h, lo, c, rng)


def summary(t: pd.DataFrame) -> dict:
    n = len(t)
    if not n:
        return {"n": 0}
    touch = t.kind == "TOUCH"
    br = t[touch & t.broke]
    at = t.arb_kind == "TOUCH"
    return {"n": int(n), "median_dist_r": float(t.dist_r.median()),
            "touch": float(touch.mean()), "stop_first": float((t.kind == "STOP").mean()),
            "timeout": float((t.kind == "TIME").mean()),
            "break_given_touch": float(t.broke[touch].mean()) if touch.any() else None,
            "mfe_after_break_median": float(br.mfe_r.median()) if len(br) else None,
            "mfe_after_break_p90": float(br.mfe_r.quantile(0.9)) if len(br) else None,
            "end_after_break_median": float(br.end_r.median()) if len(br) else None,
            "arb_touch": float(at.mean()),
            "arb_break_given_touch": float(t.arb_broke[at].mean()) if at.any() else None}


def report(rows: pd.DataFrame) -> dict:
    out = {}
    rows = rows.assign(dist_b=pd.cut(rows.dist_r, DIST_BUCKETS, right=True).astype(str),
                       liq_b=pd.cut(rows.liq, (0, 100, 250, 500)).astype(str))
    for name, g in rows.groupby("level"):
        out[name] = {"all": summary(g),
                     "by_dist_r": {k: summary(x) for k, x in g.groupby("dist_b")},
                     "by_spec_gate": {str(k): summary(x) for k, x in g.groupby("gate")},
                     "by_adx_ge_25": {str(k): summary(x) for k, x in g.groupby("adx_trend")},
                     "by_liquidity_rank": {k: summary(x) for k, x in g.groupby("liq_b")}}
    return out


async def run(rd: Path, tf: str, jobs: int) -> dict:
    man = json.loads((rd / "manifest.json").read_text())
    panel = pd.read_parquet(rd / "panel.parquet", columns=["instrument_key", "session_date",
                                                           "eligible", "tv20", "spec_gate"])
    panel["session_date"] = pd.to_datetime(panel.session_date).dt.date
    el = panel[panel.eligible].copy()
    el["liq"] = el.groupby("session_date").tv20.rank(ascending=False)
    inp = await load.cached(_dt.date.fromisoformat(man["data_end"]))
    from concurrent.futures import ProcessPoolExecutor
    res = {"label": LABEL, "replay": man["run_id"]}
    if tf in ("daily", "both"):
        bars = inp["bars"][inp["bars"].session_date <= DAILY_END]
        e = el[el.session_date <= DAILY_END]
        ctx: dict = {}
        for k, d, gte, lq in zip(e.instrument_key, e.session_date, e.spec_gate, e.liq):
            ctx.setdefault(k, {})[(k, d)] = (bool(gte), None, float(lq))
        jobs_ = [(k, g, ctx[k]) for k, g in bars.sort_values(["instrument_key", "session_date"])
                 .groupby("instrument_key") if k in ctx]
        with ProcessPoolExecutor(jobs) as ex:
            rows = pd.DataFrame([r for p in ex.map(stock_daily, jobs_, chunksize=4) for r in p])
        res["daily"] = {"window": f"<= {DAILY_END}, horizon {HOLD_DAILY} sessions",
                        "entries": int(rows.groupby(["key", "date"]).ngroups),
                        "levels": report(rows)}
    if tf in ("15m", "both"):
        cal = [d for d in calendar(inp["bars"]) if d <= M15_END]
        prev = {d: cal[i - 1] for i, d in enumerate(cal) if i}
        idx = {(k, d): (bool(gt), float(lq)) for k, d, gt, lq in
               zip(el.instrument_key, el.session_date, el.spec_gate, el.liq)}
        per: dict = {}
        days = sorted({d for d in prev if d >= M15_START})
        prev_of = {d: prev[d] for d in days}
        for k, g in b15.groupby("instrument_key"):
            m = {}
            for d in pd.unique(g.ts.dt.date):
                pv = prev_of.get(d)
                v = idx.get((k, pv)) if pv else None
                if v:
                    m[(k, d)] = (v[0], None, v[1])
            if m:
                per[k] = (g, m)
        jobs_ = [(k, g, m) for k, (g, m) in per.items()]
        with ProcessPoolExecutor(jobs) as ex:
            rows = pd.DataFrame([r for p in ex.map(stock_15m, jobs_, chunksize=4) for r in p])
        res["15m"] = {"window": f"{M15_START} -> {M15_END}, EOD = close of the 15:00 bar",
                      "entries": int(rows.groupby(["key", "date"]).ngroups),
                      "levels": report(rows)}
    d = load.research_dir() / "sprint" / "baserates"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"baserates_{tf}.json").write_text(json.dumps(res, indent=1, default=str))
    return res


def main(args: list[str]) -> dict:
    opt = lambda k, d=None: args[args.index(k) + 1] if k in args else d  # noqa: E731
    r = asyncio.run(run(Path(opt("--replay")), opt("--tf", "both"), int(opt("--jobs", 28))))
    return {tf: {lv: x["all"] for lv, x in r[tf]["levels"].items()}
            for tf in ("daily", "15m") if tf in r}


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1:]), indent=1, default=str))
