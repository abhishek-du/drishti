"""Pre-P6 power check (docs/DESIGN.md, user 2026-10-29) from the P3 replay. Replay numbers on
history, not forward results: SURVIVORSHIP-BIASED, EXPLORATORY.

Projection: with a share s of spec_gate ON sessions and c closed (b) events per ON session, a
forward window collects c * s closed events per session. Sessions to the look =
max(40, ceil(100 / (c s)), ceil(30 / (c s q))) + 5 (the last label window), q = the share of
events confirmed (p >= p*; a range until P4 gives the out-of-fold rate). The 120-session cap is
the PREREG_M1 hard cap.
"""

from __future__ import annotations

import math

import pandas as pd

from drishti.replay.load import LABEL
from drishti.strategies.orders import CLOSED
from drishti.strategies.rules import STRATEGIES

CAP, MIN_SESSIONS, N_CLOSED, N_CONFIRMED, LAG = 120, 40, 100, 30, 5
CONFIRM_RATES = (0.2, 0.3, 0.5)
CURRENT_WINDOW = 20


def sessions_to_look(per_session_closed: float, q: float) -> float:
    if per_session_closed <= 0:
        return math.inf
    return max(MIN_SESSIONS, math.ceil(N_CLOSED / per_session_closed),
               math.ceil(N_CONFIRMED / (per_session_closed * q))) + LAG


def build(panel: pd.DataFrame, reg: pd.DataFrame, cal: list, cutoff) -> dict:
    start = panel[panel.rs_score.notna()].session_date.min()
    ses = [d for d in cal if start <= d <= cutoff]
    gate = reg.spec_gate.reindex(ses).fillna(False).astype(bool)
    on = gate[gate].index
    b = panel[panel.signal]
    n_on, n_off = len(on), len(ses) - len(on)
    closed = b[b.o_status.isin(CLOSED)]
    c_on = len(closed) / n_on if n_on else 0.0
    hist_share = n_on / len(ses)
    known = reg[reg.n500_sma200.notna()].index        # sessions with an index bar
    recent = [d for d in cal if d in set(known)][-CURRENT_WINDOW:]
    cur_share = float(reg.spec_gate.reindex(recent).astype(bool).mean())
    shares = {"current": cur_share, "mix_25": cur_share + 0.25 * (hist_share - cur_share),
              "mix_50": cur_share + 0.5 * (hist_share - cur_share),
              "mix_75": cur_share + 0.75 * (hist_share - cur_share), "historical": hist_share}
    proj = {name: {"on_share": s, "closed_per_session": c_on * s,
                   "sessions_to_look": {str(q): sessions_to_look(c_on * s, q)
                                        for q in CONFIRM_RATES}} for name, s in shares.items()}
    cur = proj["current"]["sessions_to_look"]
    flag = any(v > CAP for v in cur.values())
    by_year = {str(y): {"sessions": int(len(g)), "on_share": float(g.mean())}
               for y, g in gate.groupby(pd.to_datetime(gate.index).year)}
    return {
        "label": LABEL,
        "flag": "FLAG_CURRENT_REGIME_OVER_120" if flag else "OK",
        "flag_detail": {q: v for q, v in cur.items()},
        "current_window": [str(recent[0]), str(recent[-1])],
        "latest_spec_gate": {"date": str(recent[-1]),
                             "on": bool(reg.spec_gate.get(recent[-1], False)),
                             "n500": float(reg.n500.get(recent[-1], float("nan"))),
                             "sma200": float(reg.n500_sma200.get(recent[-1], float("nan")))},
        "replay_sessions": len(ses), "first_session": str(ses[0]), "cutoff": str(cutoff),
        "sessions_on": n_on, "sessions_off": n_off, "share_on": hist_share,
        "events_per_session": {"spec_gate_ON": len(b) / n_on if n_on else 0.0,
                               "spec_gate_OFF": 0.0,
                               "spec_gate_OFF_note": "0 by construction (SC4)",
                               "check_events_on_off_sessions": int((~b.spec_gate).sum())},
        "events_per_on_session_by_strategy": {s: float(b[s].sum() / n_on) if n_on else 0.0
                                              for s in STRATEGIES},
        "events_total": int(len(b)), "closed_total": int(len(closed)),
        "closed_share": len(closed) / len(b) if len(b) else 0.0,
        "closed_per_on_session": c_on,
        "outcomes": {k: int(v) for k, v in b.o_status.value_counts().items()},
        "on_share_by_year": by_year,
        "confirm_rates_assumed": list(CONFIRM_RATES),
        "projections": proj, "cap": CAP}


def markdown(pw: dict, manifest: dict) -> str:
    f = lambda x: "never (gate OFF)" if x == math.inf else str(int(x))  # noqa: E731
    L = [f"# Pre-P6 power check: {LABEL}", "",
         f"**{pw['flag']}**: current-regime sessions to the look by confirm rate "
         f"{ {q: f(v) for q, v in pw['flag_detail'].items()} } vs the {pw['cap']} cap.", "",
         f"Latest spec_gate ({pw['latest_spec_gate']['date']}): "
         f"{'ON' if pw['latest_spec_gate']['on'] else 'OFF'} (Nifty 500 "
         f"{pw['latest_spec_gate']['n500']:.1f} vs SMA200 {pw['latest_spec_gate']['sma200']:.1f}).",
         "", f"Replay {pw['first_session']} → {pw['cutoff']} (C), {pw['replay_sessions']} sessions: "
         f"ON {pw['sessions_on']} ({pw['share_on']:.1%}), OFF {pw['sessions_off']}.", "",
         f"(b) events per ON session: {pw['events_per_session']['spec_gate_ON']:.3f}; per OFF "
         "session: 0 (SC4). Closed share "
         f"{pw['closed_share']:.1%}; closed events per ON session {pw['closed_per_on_session']:.3f}.",
         "", "| strategy | events per ON session |", "|---|---|"]
    L += [f"| {s} | {v:.3f} |" for s, v in pw["events_per_on_session_by_strategy"].items()]
    L += ["", "| regime mix | ON share | closed per session | "
          + " | ".join(f"look @ q={q}" for q in pw["confirm_rates_assumed"]) + " |",
          "|---|---|---|" + "---|" * len(pw["confirm_rates_assumed"])]
    for k, p in pw["projections"].items():
        L.append(f"| {k} | {p['on_share']:.1%} | {p['closed_per_session']:.3f} | "
                 + " | ".join(f(v) for v in p["sessions_to_look"].values()) + " |")
    L += ["", "| year | sessions | ON share |", "|---|---|---|"]
    L += [f"| {y} | {v['sessions']} | {v['on_share']:.1%} |" for y, v in
          pw["on_share_by_year"].items()]
    L += ["", f"q = assumed share of events confirmed (p ≥ p*) until P4's out-of-fold rate. "
          f"Look = max(40, sessions to 100 closed, to 30 confirmed) + 5. Cap {pw['cap']}.",
          "Notes: " + "; ".join(manifest["notes"]), ""]
    return "\n".join(L)
