"""The user's own setup ("Rajat setup", pre-registered 2026-10-29 in
/windows/drishti_data/research/track-b/sprint/VARIANTS.md): hammer at a valid location, a
bullish high-volume confirmation candle, entry, stop below the hammer, target = the real
nearest resistance R1 with a mandatory 1:2, and the X1 (all-in) / X2 (scale-out) behaviour at
R1. Daily (RJ1-RJ4) and 15m (RJ5, RJ6). Long only. EXPLORATORY, SURVIVORSHIP-BIASED.

    .venv/bin/python -m drishti.replay.rajat daily --replay <replay dir> [--window dev]
    .venv/bin/python -m drishti.replay.rajat 15m --replay <replay dir> [--window dev]
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import math
import zlib
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

from drishti.core import costs
from drishti.core.clock import IST
from drishti.data.holidays import SHORT_SESSIONS
from drishti.features import candles, indicators, levels
from drishti.replay import load
from drishti.replay.run import calendar

LABEL = "EXPLORATORY, SURVIVORSHIP-BIASED"
SEED = 20261028
BPS = (5, 10, 20)
RISK_INR = 10_000.0
VARIANTS = {"RJ1": ("daily", True, "E1", "X1"), "RJ2": ("daily", True, "E1", "X2"),
            "RJ3": ("daily", False, "E1", "X1"), "RJ4": ("daily", True, "E2", "X1"),
            "RJ5": ("15m", True, "E1", "X1"), "RJ6": ("15m", True, "E1", "X2"),
            # 15m min-RR test (user 2026-11-02): only the minimum RR (and RJ9's exit) changes
            "RJ7": ("15m", True, "E1", "X1", 1.0), "RJ8": ("15m", True, "E1", "X1", 1.5),
            "RJ9": ("15m", True, "E1", "X0", 1.0),
            # SW exit test, the final test of the hammer entry (user approved 2026-11-02)
            "SW-a": ("15m", True, "E1", "SWa"), "SW-b": ("15m", True, "E1", "SWb"),
            "SW-c": ("15m", True, "E1", "SWc"), "SW-d": ("15m", True, "E1", "SWd")}
WINDOWS = {"daily": {"dev": (_dt.date(2019, 9, 1), _dt.date(2023, 12, 31)),
                     "holdout": (_dt.date(2024, 1, 1), None)},
           "15m": {"dev": (_dt.date(2022, 1, 1), _dt.date(2024, 12, 31)),
                   "holdout": (_dt.date(2025, 1, 1), None)}}
HOLD_DAILY = 20


# ---------- costs ----------

def intraday_leg(turnover: float, side: str) -> float:
    """Intraday charges of one order (ASSUMPTION, user 2026-10-29; not in drishti.core.costs):
    brokerage ₹20, STT 0.025 % on the sell, stamp 0.003 % on the buy, NSE txn 0.00307 %, SEBI
    ₹10/cr, GST 18 % on brokerage + txn + SEBI."""
    r = lambda x: round(x + 1e-9, 2)  # noqa: E731
    brk, txn, sebi = 20.0, r(turnover * 0.0000307), r(turnover * 10 / 1e7)
    stt = r(turnover * 0.00025) if side == "sell" else 0.0
    stamp = r(turnover * 0.00003) if side == "buy" else 0.0
    return brk + txn + sebi + stt + stamp + r(0.18 * (brk + txn + sebi))


def trade_cost(tf: str, e: float, q: int, sells: list[tuple[float, int, object]], bps: float
               ) -> float:
    """₹ cost of a buy of q at e and the sell fills (price, shares, day): charges per order plus
    slippage per side. Daily: delivery (DP once per sell day)."""
    if tf == "daily":
        c = costs.buy(e * q).total
        days = set()
        for px, n, day in sells:
            c += costs.sell(px * n, dp=day not in days).total
            days.add(day)
    else:
        c = intraday_leg(e * q, "buy") + sum(intraday_leg(px * n, "sell") for px, n, _ in sells)
    return c + (e * q + sum(px * n for px, n, _ in sells)) * bps / 1e4


# ---------- one stock ----------

def prep(df: pd.DataFrame) -> dict:
    """Arrays of one stock's bars (oldest first): OHLCV, ATR, 20-bar prior mean volume, pivots,
    the hammer shape and the 3-lower-closes flag."""
    df = df.reset_index(drop=True)
    o, h, lo, c, v = (df[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
    atr = indicators.atr(df).to_numpy()
    ph, pl = levels.pivot_flags(h, lo)
    shape = candles.shape_hammer(df.open, df.high, df.low, df.close, None).to_numpy()
    fall3 = candles.lower_closes(df.close).to_numpy()
    vavg = df.volume.shift(1).rolling(20, min_periods=20).mean().to_numpy()
    from drishti.replay.baserates import swing_flags
    return {"o": o, "h": h, "lo": lo, "c": c, "v": v, "atr": atr, "ph": ph, "pl": pl,
            "sw5": swing_flags(h, 5),
            "shape": shape, "fall3": fall3, "vavg": vavg, "n": len(df)}


def zones(A: dict, t: int) -> list[dict]:
    a = A["atr"][t]
    return levels.zones_at(A["h"], A["lo"], A["ph"], A["pl"], t, float(a)) \
        if np.isfinite(a) and a > 0 else []


def is_signal(A: dict, t: int, location: bool) -> bool:
    """Hammer on t (frozen spec, or shape only for L0) + the confirmation candle on t+1."""
    if t + 1 >= A["n"] or not A["shape"][t] or not np.isfinite(A["atr"][t]):
        return False
    if location and not A["fall3"][t]:
        if levels.at_support(zones(A, t), A["lo"][t], float(A["atr"][t])) is None:
            return False
    k = t + 1
    o, c, v = A["o"][k], A["c"][k], A["v"][k]
    return bool(c > o and c > A["h"][t] and np.isfinite(A["vavg"][k])
                and v >= 1.5 * A["vavg"][k])


def levels_for(A: dict, d: int, e: float, s: float, tf: str, min_rr: float = 2.0) -> dict:
    """R1/R2 from the zones known on the decision bar d, and the skip rules."""
    if e <= s:
        return {"status": "STOP_ABOVE_ENTRY"}
    zs = zones(A, d)
    r1 = levels.resistance_above(zs, e)
    if r1 is None:
        return {"status": "NO_R1"}
    r2 = levels.resistance_above(zs, r1["top"])
    t1, R = r1["bottom"], e - s
    out = {"T1": t1, "R1_top": r1["top"], "T2": r2["bottom"] if r2 else None, "R": R}
    if t1 - e < min_rr * R:
        return out | {"status": "SKIP_RR"}
    q = max(1, math.floor(RISK_INR / R))
    if trade_cost(tf, e, q, [(t1, q, 0)], 10) / q / R > 0.15:
        return out | {"status": "SKIP_COST"}
    return out | {"status": "OK", "q": q}


def path(A: dict, first: int, last: int, e: float, s: float, lv: dict, mode: str,
         fill_bar: bool) -> dict | None:
    """The X1/X2 exit path from bar `first` to bar `last` (inclusive). fill_bar: `first` is the
    entry bar itself (E2: a low <= S there is the stop). None when the bars run out."""
    o, h, lo, c, v, va = A["o"], A["h"], A["lo"], A["c"], A["v"], A["vavg"]
    T1, top, T2 = lv["T1"], lv["R1_top"], lv["T2"]
    frac, stop, target, phase = 1.0, s, None, "pre"
    fills, hit1, hit2 = [], False, False
    for i in range(first, last + 1):
        if i >= A["n"]:
            return None
        gap_ok = not (fill_bar and i == first)
        if T2 is not None and h[i] >= T2:
            hit2 = True
        if gap_ok and o[i] < stop:
            fills.append((float(o[i]), frac, i, "SL" if phase == "pre" else "STOP_MOVED"))
            frac = 0.0
        elif lo[i] <= stop:
            fills.append((stop, frac, i, "SL" if phase == "pre" else "STOP_MOVED"))
            frac = 0.0
        if frac == 0:
            break
        if phase == "pre":
            if h[i] >= T1:
                hit1 = True
                if mode == "X0":                 # full exit at T1, no extension
                    fills.append((max(T1, float(o[i])) if gap_ok else T1, frac, i, "T1"))
                    frac = 0.0
                    break
                if mode == "X2":
                    fills.append((max(T1, float(o[i])) if gap_ok else T1, 0.5, i, "T1_HALF"))
                    frac = 0.5
                    phase = "cand" if c[i] > top else "be"
                    if phase == "be":
                        stop, target = e, T2
                elif c[i] > top:
                    phase = "cand"
                else:
                    fills.append((float(c[i]), frac, i, "R1_REJECT"))
                    frac = 0.0
                    break
        elif phase == "cand":
            conf = c[i] > o[i] and np.isfinite(va[i]) and v[i] >= 1.5 * va[i]
            if conf:
                phase, stop, target = "post", T1, T2
            elif mode == "X2":
                phase, stop, target = "be", e, T2
            else:
                phase = "unconf"
                if c[i] < T1:
                    fills.append((float(c[i]), frac, i, "BACK_BELOW_R1"))
                    frac = 0.0
                    break
        elif phase in ("post", "be"):
            if target is not None:
                if o[i] >= target:
                    fills.append((float(o[i]), frac, i, "T2"))
                    frac = 0.0
                    break
                if h[i] >= target:
                    fills.append((target, frac, i, "T2"))
                    frac = 0.0
                    break
        elif phase == "unconf":
            if c[i] < T1:
                fills.append((float(c[i]), frac, i, "BACK_BELOW_R1"))
                frac = 0.0
                break
        if i == last and frac > 0:
            fills.append((float(c[i]), frac, i, "TIME"))
            frac = 0.0
    if frac > 0:
        return None
    return {"fills": fills, "hit_T1": hit1, "hit_T2": hit2}


def levels_sw(A: dict, d: int, e: float, s: float, tf: str, mode: str) -> dict:
    """SW-a..SW-d levels and skips (VARIANTS.md, 2026-11-02): SW5 = nearest confirmed strict
    5-bar swing high above e known on bar d."""
    from drishti.replay.baserates import nearest_swing
    R = e - s
    if R <= 0:
        return {"status": "STOP_ABOVE_ENTRY"}
    L = nearest_swing(A["h"], A["sw5"], 5, d, e)
    a = float(A["atr"][d])
    T1 = None
    if mode == "SWa":
        if not np.isfinite(L):
            return {"status": "NO_LEVEL"}
        if L - e < 0.5 * R:
            return {"status": "SKIP_NEAR"}
        T1 = L - 0.05 * a
    elif mode == "SWb":
        if np.isfinite(L) and L - e <= R:
            return {"status": "SKIP_OBSTRUCTION"}
        T1 = e + 2 * R
    elif mode == "SWc":
        T1 = e + 1.5 * a
    q = max(1, math.floor(RISK_INR / R))
    est = T1 if T1 is not None else e + R
    if trade_cost(tf, e, q, [(est, q, 0)], 10) / q / R > 0.15:
        return {"status": "SKIP_COST"}
    return {"status": "OK", "q": q, "T1": T1, "T2": None, "R1_top": None, "R": R,
            "SW5": L if np.isfinite(L) else None}


def path_sw(A: dict, first: int, last: int, e: float, s: float, lv: dict, mode: str
            ) -> dict | None:
    """SW-a..SW-d exit paths: stop first (gap through at the open); SW-b/SW-c target; SW-a 50 %
    at P then a breakeven trail on the previous bar's low; SW-d a trail on the previous bar's low
    once high >= E + 1R; the rest exits at the close of `last` (EOD)."""
    o, h, lo, c = A["o"], A["h"], A["lo"], A["c"]
    T1, R = lv["T1"], e - s
    frac, stop, trail, hit, fills = 1.0, s, False, False, []
    for i in range(first, last + 1):
        if i >= A["n"]:
            return None
        kind = "TRAIL" if stop > s else "SL"
        if o[i] < stop:
            fills.append((float(o[i]), frac, i, kind))
            frac = 0.0
            break
        if lo[i] <= stop:
            fills.append((stop, frac, i, kind))
            frac = 0.0
            break
        if mode in ("SWb", "SWc"):
            if o[i] >= T1 or h[i] >= T1:
                fills.append((max(T1, float(o[i])), frac, i, "TP"))
                frac, hit = 0.0, True
                break
        elif mode == "SWa":
            if not hit and h[i] >= T1:
                fills.append((max(T1, float(o[i])), 0.5, i, "PARTIAL"))
                frac, hit = 0.5, True
                stop = max(e, float(lo[i]))
            elif hit:
                stop = max(stop, float(lo[i]))
        elif mode == "SWd":
            if not trail and h[i] >= e + R:
                trail = True
            if trail:
                stop = max(stop, float(lo[i]))
        if i == last:
            fills.append((float(c[i]), frac, i, "TIME"))
            frac = 0.0
    if frac > 0:
        return None
    return {"fills": fills, "hit_T1": hit, "hit_T2": False}


def result(A, dates, tf, e, s, lv, p, e_i) -> dict:
    q = lv["q"]
    first_n = q // 2 if len(p["fills"]) > 1 else q
    shares = [first_n, q - first_n] if len(p["fills"]) > 1 else [q]
    sells = [(px, n, dates[i].date() if tf == "15m" else dates[i])
             for (px, _, i, _), n in zip(p["fills"], shares)]
    gross_inr = sum(n * (px - e) for px, n, _ in sells)
    R = e - s
    rec = {"entry": e, "stop": s, "T1": lv["T1"], "T2": lv["T2"], "R_pct": R / e,
           "exit_kind": p["fills"][-1][3], "kinds": "+".join(f[3] for f in p["fills"]),
           "exit_px": p["fills"][-1][0], "exit_date": sells[-1][2], "entry_i": e_i,
           "exit_i": p["fills"][-1][2], "hit_T1": p["hit_T1"], "hit_T2": p["hit_T2"],
           "gross_r": gross_inr / q / R, "qty": q}
    for b in BPS:
        cost = trade_cost(tf, e, q, sells, b)
        rec[f"net_r_{b}"] = (gross_inr - cost) / q / R
        rec[f"cost_r_{b}"] = cost / q / R
    return rec


def stock_job(args) -> tuple[list, list, dict]:
    """All trades of one stock for every variant of its timeframe, plus the placebos."""
    key, df, tf, elig, t_lo, t_hi, guard = args
    A = prep(df)
    dates = list(df.session_date) if tf == "daily" else list(df.ts)
    day = [d.date() for d in dates] if tf == "15m" else dates
    if tf == "15m":
        hhmm = np.array([d.hour * 100 + d.minute for d in dates])
        end_of_day: dict = {}
        for i, dd in enumerate(day):                    # the 15:00 bar, else the day's last bar
            if hhmm[i] <= 1500:
                end_of_day[dd] = i
        last_of_day = np.array([end_of_day[dd] for dd in day])
    trades, plac, skips = [], [], {}
    rng = np.random.default_rng([SEED, zlib.crc32(key.encode())])
    for vid, (vtf, loc, entry, mode, *rr) in VARIANTS.items():
        min_rr = rr[0] if rr else 2.0
        if vtf != tf:
            continue
        sk = skips.setdefault(vid, {"signals": 0})
        sig_bars = set()
        cand = [t for t in range(A["n"] - 1) if A["shape"][t]]
        for t in cand:
            d0 = day[t]
            if not (t_lo <= d0 <= t_hi) or (key, d0) not in elig:
                continue
            if tf == "15m" and (day[t + 1] != d0 or hhmm[t + 1] > 1445 or (key, d0) in guard):
                continue
            if not is_signal(A, t, loc):
                continue
            sk["signals"] += 1
            sig_bars |= {t, t + 1}
            d = t + 1
            if entry == "E1":
                e, e_i, first, fill_bar = float(A["c"][d]), d, d + 1, False
            else:
                if d + 1 >= A["n"]:
                    continue
                e, e_i, first, fill_bar = float(A["o"][d + 1]), d + 1, d + 1, True
            s = float(A["lo"][t] - 0.1 * A["atr"][t])
            if mode == "SWc":
                s = e - float(A["atr"][d])
            lv = levels_sw(A, d, e, s, tf, mode) if mode.startswith("SW") else \
                levels_for(A, d, e, s, tf, min_rr)
            if lv["status"] != "OK":
                sk[lv["status"]] = sk.get(lv["status"], 0) + 1
                continue
            last = (e_i + HOLD_DAILY) if tf == "daily" else last_of_day[e_i]
            if tf == "15m" and first > last:
                sk["NO_TIME"] = sk.get("NO_TIME", 0) + 1
                continue
            p = path_sw(A, first, last, e, s, lv, mode) if mode.startswith("SW") else \
                path(A, first, last, e, s, lv, mode, fill_bar)
            if p is None:
                sk["OPEN"] = sk.get("OPEN", 0) + 1
                continue
            rec = result(A, dates, tf, e, s, lv, p, e_i)
            rec |= {"variant": vid, "instrument_key": key, "signal_date": d0,
                    "hammer_ts": dates[t], "entry_date": day[e_i]}
            trades.append(rec)
            # placebo: same stock, same month (daily) / same day (15m), same exits
            k_atr = (e - s) / A["atr"][d]
            if tf == "daily":
                pool = [i for i in range(A["n"] - 1) if day[i].year == d0.year
                        and day[i].month == d0.month and (key, day[i]) in elig
                        and i not in sig_bars and t_lo <= day[i] <= t_hi]
            else:
                pool = [i for i in range(A["n"] - 1) if day[i] == d0 and 930 <= hhmm[i] <= 1445
                        and i not in (t, t + 1)]
            for _ in range(20):
                if not pool:
                    break
                P = int(rng.choice(pool))
                if not np.isfinite(A["atr"][P]):
                    continue
                if entry == "E1":
                    ep, pe_i, pf, pfb = float(A["c"][P]), P, P + 1, False
                else:
                    ep, pe_i, pf, pfb = float(A["o"][P + 1]), P + 1, P + 1, True
                sp = ep - k_atr * A["atr"][P]
                plv = levels_sw(A, P, ep, sp, tf, mode) if mode.startswith("SW") else \
                    levels_for(A, P, ep, sp, tf, min_rr)
                if plv["status"] != "OK":
                    continue
                pl = (pe_i + HOLD_DAILY) if tf == "daily" else last_of_day[pe_i]
                if pf > pl:
                    continue
                pp = path_sw(A, pf, pl, ep, sp, plv, mode) if mode.startswith("SW") else \
                    path(A, pf, pl, ep, sp, plv, mode, pfb)
                if pp is None:
                    continue
                pr = result(A, dates, tf, ep, sp, plv, pp, pe_i)
                plac.append(pr | {"variant": vid, "instrument_key": key, "signal_date": day[P],
                                  "entry_date": day[pe_i]})
                break
    return trades, plac, skips


# ---------- metrics ----------

def _streak(x) -> int:
    best = cur = 0
    for v in x:
        cur = cur + 1 if v else 0
        best = max(best, cur)
    return best


def metrics(t: pd.DataFrame) -> dict:
    if t.empty:
        return {"n": 0}
    t = t.sort_values("entry_date")
    r = t.net_r_10
    cum = r.cumsum()
    win, loss = r[r > 0], r[r <= 0]
    hy = t.groupby([pd.to_datetime(t.entry_date).dt.year,
                    (pd.to_datetime(t.entry_date).dt.month > 6).astype(int)]).net_r_10.mean()
    yrs = pd.to_datetime(t.entry_date).dt.year
    return {"n": int(len(t)), "per_year": {int(y): int(n) for y, n in yrs.value_counts().sort_index().items()},
            "pct_T1": float(t.hit_T1.mean()), "pct_T2": float(t.hit_T2.mean()),
            "hit_rate": float((r > 0).mean()),
            "avg_win_r": float(win.mean()) if len(win) else None,
            "avg_loss_r": float(loss.mean()) if len(loss) else None,
            "gross_r": float(t.gross_r.mean()),
            **{f"net_r_{b}": float(t[f"net_r_{b}"].mean()) for b in BPS},
            "max_dd_r": float((cum - cum.cummax()).min()), "losing_streak": _streak(r <= 0),
            "cost_r_10": float(t.cost_r_10.mean()), "avg_R_pct": float(t.R_pct.mean()),
            "half_years": {f"{y}H{h + 1}": float(v) for (y, h), v in hy.items()},
            "half_years_positive": float((hy > 0).mean()),
            "exits": {k: int(v) for k, v in t.kinds.value_counts().items()}}


def verdict(m: dict, p: dict) -> dict:
    if not m.get("n"):
        return {"pass": False, "reason": "no trades"}
    diff = m["net_r_10"] - (p.get("net_r_10") if p.get("n") else float("nan"))
    ok = m["net_r_10"] > 0 and diff >= 0.05 and m["half_years_positive"] >= 0.6
    return {"net_r_10": m["net_r_10"], "beats_placebo_by": diff,
            "half_years_positive": m["half_years_positive"], "pass": bool(ok)}


# ---------- data ----------

async def bars_15m(ps, keys: list[str], d0: _dt.date, d1: _dt.date) -> pd.DataFrame:
    out = []
    for i in range(0, len(keys), 50):
        rows = (await ps.execute(text("""select instrument_key, bar_start_utc, open, high, low,
            close, volume from canon_market_bar where timeframe = '15m' and segment = 'NSE_EQ'
            and session_type = 'NORMAL' and market_date between :a and :b
            and instrument_key = any(:k)"""), {"a": d0, "b": d1, "k": keys[i:i + 50]})).all()
        out.append(pd.DataFrame(rows, columns=["instrument_key", "ts", "open", "high", "low",
                                               "close", "volume"]))
    df = pd.concat(out, ignore_index=True)
    df["ts"] = pd.to_datetime(df.ts, utc=True).dt.tz_convert(IST).dt.tz_localize(None)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    df = df[~df.ts.dt.date.isin(SHORT_SESSIONS)]
    return df.drop_duplicates(["instrument_key", "ts"]).sort_values(
        ["instrument_key", "ts"]).reset_index(drop=True)


async def ca_guard(ps, cal: list, d0, d1) -> set:
    """(stock, session) within the 15 sessions from an EXACT ex-date on (the 15m CA guard)."""
    import bisect
    rows = (await ps.execute(text("""select instrument_key, ex_date from canon_ca_factor
        where status = 'EXACT' and ex_date between :a and :b"""),
        {"a": d0 - _dt.timedelta(days=40), "b": d1})).all()
    out = set()
    for k, ex in rows:
        i = bisect.bisect_left(cal, ex)
        out |= {(k, d) for d in cal[i:i + 16]}
    return out


async def run(tf: str, rd: Path, window: str, only: set | None, jobs: int) -> dict:
    man = json.loads((rd / "manifest.json").read_text())
    panel = pd.read_parquet(rd / "panel.parquet", columns=["instrument_key", "session_date",
                                                           "eligible"])
    panel["session_date"] = pd.to_datetime(panel.session_date).dt.date
    inp = await load.cached(_dt.date.fromisoformat(man["data_end"]))
    start, end = WINDOWS[tf][window]
    end = end or _dt.date.fromisoformat(man["data_end"])
    daily = inp["bars"][inp["bars"].session_date <= end]
    cal = [d for d in calendar(daily) if d <= end]
    t_hi = cal[-1 - HOLD_DAILY - 2] if tf == "daily" else cal[-1]
    t_hi = min(t_hi, _dt.date.fromisoformat(man["cutoff"])) if window == "holdout" else t_hi
    el = panel[panel.eligible]
    if tf == "daily":
        elig = set(zip(el.instrument_key, el.session_date))
        mine = el.groupby("instrument_key").session_date.apply(set).to_dict()
        groups = [(k, g, tf, {(k, d) for d in mine[k]}, start, t_hi, set()) for k, g in
                  daily.sort_values(["instrument_key", "session_date"]).groupby("instrument_key")
                  if k in mine]
    else:
        prev = {d: cal[i - 1] for i, d in enumerate(cal) if i}
        by_day = el.groupby("session_date").instrument_key.apply(set).to_dict()
        elig = {(k, d) for d, p in prev.items() if start <= d <= end for k in by_day.get(p, ())}
        keys = sorted({k for k, _ in elig})
        from drishti.core.db import prajna_sessionmaker
        cache = load.research_dir() / "cache" / f"bars15m_{start}_{end}.parquet"
        async with prajna_sessionmaker()() as ps:
            if cache.exists():
                b15 = pd.read_parquet(cache)
            else:
                b15 = await bars_15m(ps, keys, start - _dt.timedelta(days=30), end)
                cache.parent.mkdir(parents=True, exist_ok=True)
                b15.to_parquet(cache, index=False)
            guard = await ca_guard(ps, cal, start, end)
        mine: dict = {}
        for k, d in elig:
            mine.setdefault(k, set()).add((k, d))
        gk: dict = {}
        for k, d in guard:
            gk.setdefault(k, set()).add((k, d))
        groups = [(k, g, tf, mine.get(k, set()), start, end, gk.get(k, set()))
                  for k, g in b15.groupby("instrument_key")]
    if only:
        global VARIANTS
        VARIANTS = {k: v for k, v in VARIANTS.items() if k in only}
    from concurrent.futures import ProcessPoolExecutor
    with ProcessPoolExecutor(jobs) as ex:
        parts = list(ex.map(stock_job, groups, chunksize=4))
    T = pd.DataFrame([r for p in parts for r in p[0]])
    P = pd.DataFrame([r for p in parts for r in p[1]])
    out = {"label": LABEL, "tf": tf, "window": window, "start": str(start), "end": str(end),
           "signal_t_max": str(t_hi), "replay": man["run_id"], "variants": {}}
    for vid, (vtf, *_ ) in VARIANTS.items():
        if vtf != tf:
            continue
        sk: dict = {}
        for p in parts:
            for k, n in p[2].get(vid, {}).items():
                sk[k] = sk.get(k, 0) + n
        t = T[T.variant == vid] if len(T) else T
        pl = P[P.variant == vid] if len(P) else P
        m, pm = metrics(t), metrics(pl)
        out["variants"][vid] = {"rules": m, "placebo": pm, "skips": sk, "verdict": verdict(m, pm)}
    d = load.research_dir() / "sprint" / "rajat" / (
        f"{tf}_{window}" + (f"_{'_'.join(sorted(only))}" if only else ""))
    d.mkdir(parents=True, exist_ok=window == "dev")
    (d / "result.json").write_text(json.dumps(out, indent=1, default=str))
    T.to_parquet(d / "trades.parquet", index=False)
    P.to_parquet(d / "placebo.parquet", index=False)
    return out


def main(args: list[str]) -> dict:
    opt = lambda k, d=None: args[args.index(k) + 1] if k in args else d  # noqa: E731
    tf, window = args[0], opt("--window", "dev")
    only = set(opt("--only").split(",")) if opt("--only") else None
    if window == "holdout":
        assert only, "the holdout runs once, only for variants that passed development"
    out = asyncio.run(run(tf, Path(opt("--replay")), window, only, int(opt("--jobs", 28))))
    return {k: v["verdict"] | {"skips": v["skips"]} for k, v in out["variants"].items()}


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1:]), indent=1, default=str))
