"""Research sprint step 1 (user 2026-10-29): diagnose v1 on the development window only.
EXPLORATORY, SURVIVORSHIP-BIASED. Variants V01-V05 are logged in
/windows/drishti_data/research/track-b/sprint/VARIANTS.md before this runs.

    .venv/bin/python -m drishti.replay.diagnose --replay <replay dir> [--dev-end 2024-12-31]

No bar after --dev-end is read: the bars are cut there, and every signal has T <= the 60th
session before it, so the longest exit closes inside the window.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from drishti.core import costs
from drishti.replay import load
from drishti.replay.run import calendar
from drishti.strategies import orders

LABEL = "EXPLORATORY, SURVIVORSHIP-BIASED (development window only)"
VARIANTS = {"V01": ("fixed", 5), "V02": ("fixed", 10), "V03": ("fixed", 20),
            "V04": ("fixed", 60), "V05": ("trail20", 60)}
BPS = (0, 5, 10, 20)
MAX_HOLD = 60


def stock_arrays(bars: pd.DataFrame) -> dict:
    out = {}
    for k, g in bars.groupby("instrument_key", sort=False):
        c = g.close.to_numpy(float)
        out[k] = {"pos": {d: i for i, d in enumerate(g.session_date)},
                  "o": g.open.to_numpy(float), "h": g.high.to_numpy(float),
                  "lo": g.low.to_numpy(float), "c": c,
                  "ma20": pd.Series(c).rolling(20, min_periods=20).mean().to_numpy()}
    return out


def trade(a: dict, fill_date, e: float, s: float, tp: float, kind: str, n: int):
    f = a["pos"].get(fill_date)
    if f is None:
        return None
    if kind == "fixed":
        x = orders.exit_path(a["o"], a["h"], a["lo"], a["c"], f, s, tp, hold=n)
    else:
        x = orders.exit_trail(a["o"], a["h"], a["lo"], a["c"], a["ma20"], f, s, max_hold=n)
    return None if x is None else (x[0], x[1], x[2] - f + 1)


def outcomes(ev: pd.DataFrame, arr: dict, v: str) -> pd.DataFrame:
    """Every variant's exit for the entries of variant prefix v (o = frozen, p = placebo)."""
    rows = []
    for r in ev.itertuples():
        a = arr[r.instrument_key]
        e, s, tp = getattr(r, f"{v}_entry"), getattr(r, f"{v}_stop"), getattr(r, f"{v}_tp")
        n = orders.qty(e, s, bool(r.vix_spike))
        for vid, (kind, hold) in VARIANTS.items():
            x = trade(a, getattr(r, f"{v}_fill_date"), e, s, tp, kind, hold)
            if x is None:
                continue
            rec = {"variant": vid, "instrument_key": r.instrument_key, "T": r.session_date,
                   "year": r.session_date.year, "strategy": r.strategy, "exit_kind": x[0],
                   "sessions": x[2], "gross_r": (x[1] - e) / (e - s)}
            for b in BPS:
                cost = costs.round_trip(e, x[1], n, b)
                rec[f"net_r_{b}"] = (x[1] - e) / (e - s) - cost / n / (e - s)
                rec[f"net_inr_{b}"] = (x[1] - e) * n - cost
            rows.append(rec)
    return pd.DataFrame(rows)


def summary(t: pd.DataFrame) -> dict:
    if t.empty:
        return {"n": 0}
    out = {"n": int(len(t)), "hit_rate_net10": float((t.net_r_10 > 0).mean()),
           "gross_r": float(t.gross_r.mean()), "avg_sessions": float(t.sessions.mean()),
           "net_inr_10": float(t.net_inr_10.mean())}
    out.update({f"net_r_{b}": float(t[f"net_r_{b}"].mean()) for b in BPS})
    w, lo = t.net_r_10[t.net_r_10 > 0], t.net_r_10[t.net_r_10 <= 0]
    out["payoff_net10"] = float(w.mean() / -lo.mean()) if len(w) and lo.mean() < 0 else None
    out["exits"] = {k: int(v) for k, v in t.exit_kind.value_counts().items()}
    return out


def build(panel: pd.DataFrame, bars: pd.DataFrame, dev_end: _dt.date) -> dict:
    bars = bars[bars.session_date <= dev_end]
    cal = [d for d in calendar(bars) if d <= dev_end]
    t_max = cal[-1 - MAX_HOLD]
    panel = panel[panel.session_date <= t_max]
    arr = stock_arrays(bars.sort_values(["instrument_key", "session_date"]))
    b = panel[panel.signal]
    rules = outcomes(b[b.o_status.isin(orders.CLOSED)], arr, "o")
    plac = outcomes(b[b.p_status.isin(orders.CLOSED)], arr, "p")
    a = panel[panel.o_status.isin(orders.CLOSED)]
    a_reg = {}
    for name, g in a.groupby(np.where(a.spec_gate, "spec_gate_ON", "spec_gate_OFF")):
        r = g.o_net_r_10.astype(float)
        a_reg[name] = {"n": int(len(g)), "hit_rate_net10": float((r > 0).mean()),
                       "net_r_10": float(r.mean())}
    out = {"label": LABEL, "dev_end": str(dev_end), "t_max": str(t_max),
           "variants": {v: {"rules": summary(rules[rules.variant == v]),
                            "placebo": summary(plac[plac.variant == v])} for v in VARIANTS},
           "by_year_net_r_10": {v: {
               "rules": {int(y): float(g.net_r_10.mean())
                         for y, g in rules[rules.variant == v].groupby("year")},
               "placebo": {int(y): float(g.net_r_10.mean())
                           for y, g in plac[plac.variant == v].groupby("year")}}
               for v in VARIANTS},
           "by_strategy_net_r_10": {v: {s: float(g.net_r_10.mean()) for s, g in
                                        rules[rules.variant == v].groupby("strategy")}
                                    for v in VARIANTS},
           "set_a_V01_by_regime": a_reg}
    return out, rules, plac


def markdown(d: dict) -> str:
    f = lambda x, k=3: "" if x is None else f"{x:.{k}f}"  # noqa: E731
    L = [f"# Sprint step 1: v1 diagnosis ({d['label']})", "",
         f"Signals T ≤ {d['t_max']} (60 sessions before {d['dev_end']}); bars ≤ {d['dev_end']}.",
         "", "| variant | set | n | hit (net 10) | gross R | net R 0 | net R 5 | net R 10 | "
         "net R 20 | net ₹ @10 | payoff | avg sessions |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for v, s in d["variants"].items():
        for k in ("rules", "placebo"):
            m = s[k]
            L.append(f"| {v} | {k} | {m['n']} | {f(m['hit_rate_net10'])} | {f(m['gross_r'])} | "
                     + " | ".join(f(m[f'net_r_{b}']) for b in BPS)
                     + f" | {f(m['net_inr_10'], 0)} | {f(m['payoff_net10'], 2)} | "
                     f"{f(m['avg_sessions'], 1)} |")
    years = sorted({y for v in d["by_year_net_r_10"].values() for y in v["rules"]})
    L += ["", "Net R @10 bps by signal year (rules / placebo):", "",
          "| variant | " + " | ".join(map(str, years)) + " |", "|---|" + "---|" * len(years)]
    for v, s in d["by_year_net_r_10"].items():
        L.append(f"| {v} | " + " | ".join(f"{f(s['rules'].get(y), 2)} / "
                                          f"{f(s['placebo'].get(y), 2)}" for y in years) + " |")
    L += ["", "Net R @10 by strategy (rules):", "", "| variant | S1a | S1b | S2 | S3 |",
          "|---|---|---|---|---|"]
    for v, s in d["by_strategy_net_r_10"].items():
        L.append(f"| {v} | " + " | ".join(f(s.get(k)) for k in ("S1a", "S1b", "S2", "S3"))
                 + " |")
    L += ["", "Set (a) all-bar, V01, by spec_gate:", "", "| regime | n | hit | net R 10 |",
          "|---|---|---|---|"]
    L += [f"| {k} | {m['n']} | {f(m['hit_rate_net10'])} | {f(m['net_r_10'])} |"
          for k, m in d["set_a_V01_by_regime"].items()]
    return "\n".join(L + [""])


def main(args: list[str]) -> dict:
    opt = lambda k, d=None: args[args.index(k) + 1] if k in args else d  # noqa: E731
    rd = Path(opt("--replay"))
    man = json.loads((rd / "manifest.json").read_text())
    panel = pd.read_parquet(rd / "panel.parquet")
    for c in ("session_date", "o_fill_date", "p_fill_date"):
        panel[c] = pd.to_datetime(panel[c]).dt.date
    dev_end = _dt.date.fromisoformat(opt("--dev-end", "2024-12-31"))
    inp = asyncio.run(load.cached(_dt.date.fromisoformat(man["data_end"])))
    d, rules, plac = build(panel, inp["bars"], dev_end)
    d["replay"] = man["run_id"]
    out = load.research_dir() / "sprint" / "step1"
    out.mkdir(parents=True, exist_ok=True)
    (out / "step1.json").write_text(json.dumps(d, indent=1, default=str))
    (out / "step1.md").write_text(markdown(d))
    rules.to_parquet(out / "trades_rules.parquet", index=False)
    plac.to_parquet(out / "trades_placebo.parquet", index=False)
    return {"out": str(out)}


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1:]), indent=1))
