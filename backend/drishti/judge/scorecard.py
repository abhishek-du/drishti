"""Scorecards of the P3 replay: set (a) all-bar and set (b) signal events, separately, against
the seeded random-entry placebo and rules-only (= every (b) event), plus the EXPLORATORY
close-confirm variant side by side. SURVIVORSHIP-BIASED, EXPLORATORY: replay on history only,
never forward data.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from drishti.core.costs import BASE_SLIPPAGE_BPS, SLIPPAGE_BPS
from drishti.replay.load import LABEL
from drishti.strategies.orders import CLOSED
from drishti.strategies.rules import STRATEGIES


def metrics(df: pd.DataFrame, v: str = "o", bps: int = BASE_SLIPPAGE_BPS) -> dict:
    """Hit rate (net R > 0), TP share, expectancy (net R and ₹), payoff, on the closed rows of
    variant `v` (o = frozen buy-stop, p = placebo, c = close-confirm)."""
    st = df[f"{v}_status"]
    c = df[st.isin(CLOSED)]
    r, inr = c[f"{v}_net_r_{bps}"].astype(float), c[f"{v}_net_inr_{bps}"].astype(float)
    win, loss = r[r > 0], r[r <= 0]
    e, s, t = (c[f"{v}_{k}"].astype(float) for k in ("entry", "stop", "trigger"))
    out = {"rows": int(len(df)), "closed": int(len(c)),
           "outcomes": {k: int(n) for k, n in st.value_counts().items()}}
    if len(c):
        out.update({
            "hit_rate": float((r > 0).mean()), "tp_share": float((c[f"{v}_status"] == "TP").mean()),
            "exp_net_r": float(r.mean()), "exp_net_inr": float(inr.mean()),
            "median_net_r": float(r.median()),
            "payoff": float(win.mean() / -loss.mean()) if len(win) and loss.mean() < 0 else None,
            "avg_stop_dist_pct": float(((e - s) / e).mean() * 100),
            "avg_entry_vs_trigger_bps": float(((e - t) / t).mean() * 1e4)})
    return out


def _by(df: pd.DataFrame, key, v: str = "o", min_rows: int = 1) -> dict:
    return {str(k): metrics(g, v) for k, g in df.groupby(key) if len(g) >= min_rows}


def build(panel: pd.DataFrame) -> dict:
    a, b = panel, panel[panel.signal]
    vix = np.where(a.vix_spike, "spike", np.where(a.vix_normal.astype("boolean").fillna(False),
                                                  "normal", "neither"))
    a = a.assign(vix_state=vix, year=pd.to_datetime(a.session_date).dt.year,
                 regime=np.where(a.spec_gate, "spec_gate_ON", "spec_gate_OFF"))
    b = a[a.signal]
    out = {"label": LABEL,
           "set_a_all_bar": {
               "all": metrics(a), "by_regime": _by(a, "regime"), "by_vix": _by(a, "vix_state"),
               "by_year": _by(a, "year"), "by_sector": _by(a, "sector", min_rows=2000),
               "by_slippage": {str(x): metrics(a, "o", x) for x in SLIPPAGE_BPS}},
           "set_b_events": {
               "rules_only": metrics(b), "placebo": metrics(b, "p"),
               "by_strategy": {s: metrics(b[b[s]]) for s in STRATEGIES},
               "placebo_by_strategy": {s: metrics(b[b[s]], "p") for s in STRATEGIES},
               "by_vix": _by(b, "vix_state"), "by_year": _by(b, "year"),
               "by_sector": _by(b, "sector", min_rows=30),
               "by_slippage": {str(x): metrics(b, "o", x) for x in SLIPPAGE_BPS}},
           "close_confirm_exploratory": {
               "note": "EXPLORATORY variant (user definition 2026-10-29); never used for H1-H3",
               "frozen_buy_stop": metrics(b), "close_confirm": metrics(b, "c"),
               "by_strategy": {s: {"frozen": metrics(b[b[s]]), "close_confirm":
                                   metrics(b[b[s]], "c")} for s in STRATEGIES}}}
    return out


def _row(name: str, m: dict) -> str:
    f = lambda k, p=3: "" if m.get(k) is None else f"{m[k]:.{p}f}"  # noqa: E731
    return (f"| {name} | {m['rows']} | {m['closed']} | {f('hit_rate')} | {f('tp_share')} | "
            f"{f('exp_net_r')} | {f('exp_net_inr', 0)} | {f('payoff', 2)} | "
            f"{f('avg_stop_dist_pct', 2)} | {f('avg_entry_vs_trigger_bps', 1)} |")


HEAD = ("| set | rows | closed | hit rate | TP share | exp net R | exp net ₹ | payoff | "
        "stop dist % | entry vs trigger bps |\n|---|---|---|---|---|---|---|---|---|---|")


def markdown(sc: dict, manifest: dict) -> str:
    a, b, cc = sc["set_a_all_bar"], sc["set_b_events"], sc["close_confirm_exploratory"]
    L = [f"# P3 scorecard: {LABEL}", "",
         f"Replay on history (official bars), C = {manifest['cutoff']}, data end "
         f"{manifest['data_end']}, git {manifest['git_sha'][:7]}, draft = {manifest['draft']}. "
         "Base slippage 10 bps/side; ₹ per trade at 1 % risk of ₹10 lakh.", "",
         "## Set (a) all-bar", HEAD, _row("(a) all", a["all"])]
    L += [_row(f"(a) {k}", m) for k, m in a["by_regime"].items()]
    L += [_row(f"(a) slippage {k}", m) for k, m in a["by_slippage"].items()]
    L += ["", "## Set (b) signal events", HEAD, _row("(b) rules-only", b["rules_only"]),
          _row("(b) placebo", b["placebo"])]
    L += [_row(f"(b) {k}", m) for k, m in b["by_strategy"].items()]
    L += [_row(f"placebo {k}", m) for k, m in b["placebo_by_strategy"].items()]
    L += [_row(f"(b) slippage {k}", m) for k, m in b["by_slippage"].items()]
    L += [_row(f"(b) {k}", m) for k, m in b["by_year"].items()]
    L += ["", "## Close-confirm (EXPLORATORY, never H1-H3)", HEAD,
          _row("frozen buy-stop", cc["frozen_buy_stop"]),
          _row("close-confirm", cc["close_confirm"])]
    for k, m in cc["by_strategy"].items():
        L += [_row(f"{k} frozen", m["frozen"]), _row(f"{k} close-confirm", m["close_confirm"])]
    L += ["", "Notes: " + "; ".join(manifest["notes"]), ""]
    return "\n".join(L)
