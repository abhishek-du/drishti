"""Nightly grader of every scan alert: the triple-barrier outcome on real bars after the alert.

Barriers (the frozen exit rules of drishti.strategies.orders.exit_path: stop first on a bar that
touches both, gap-through at the open):
  target = R1 (or entry + 2R when no resistance was above), stop = the alert's stop,
  time-out = HOLD bars (1d: 5 sessions; 15m: 25 bars, about one session).
Bases:
  alert    fill only if the next bar trades through entry_ref (at the open on a gap above it)
  took     the user's actual entry, from the next bar (journal status 'took')
  placebo  seeded random entry inside the next bar's range, same stop / target / time-out
A trade whose bars have not run out yet is OPEN and is graded again the next night (append-
only: the latest row per (alert, basis) wins).
"""

from __future__ import annotations

import numpy as np

from drishti.core import costs
from drishti.strategies.orders import exit_path

HOLD = {"1d": 5, "15m": 25}
SEED = 20261103


def _qty(entry: float, stop: float, qty: int | None, capital: float, risk_pct: float) -> int:
    if qty:
        return qty
    r = entry - stop
    return max(1, int(capital * risk_pct / 100 // r)) if r > 0 else 0


def grade(a: dict, o, h, lo, c, basis: str, actual_entry: float | None = None,
          capital: float = 1e6, risk_pct: float = 1.0) -> dict:
    """a: the alert (entry_ref, stop, r1, qty); o/h/lo/c: the bars AFTER the detection bar."""
    stop = float(a["stop"])
    ref = float(a["entry_ref"])
    if len(c) == 0:
        return {"outcome": "OPEN", "bars_used": 0}
    if basis == "alert":
        if h[0] < ref:
            return {"outcome": "NO_FILL", "bars_used": 1}
        entry = float(o[0]) if o[0] >= ref else ref
        seed = None
    elif basis == "took":
        entry, seed = float(actual_entry), None
    else:
        seed = SEED + int(a["id"])
        u = np.random.default_rng(seed).random()
        entry = float(lo[0] + u * (h[0] - lo[0]))
    risk = entry - stop
    if risk <= 0:
        return {"outcome": "NO_FILL", "bars_used": 1, "entry_used": entry, "seed": seed}
    target = float(a["r1"]) if a.get("r1") and float(a["r1"]) > entry else entry + 2 * risk
    res = exit_path(o, h, lo, c, 0, stop, target, HOLD[a["tf"]])
    if res is None:
        return {"outcome": "OPEN", "bars_used": len(c), "entry_used": entry,
                "target_used": target, "seed": seed}
    kind, px, i = res
    n = _qty(entry, stop, a.get("qty") if basis != "placebo" else None, capital, risk_pct)
    gross = (px - entry) / risk
    net = gross - costs.round_trip(entry, px, n) / n / risk
    return {"outcome": {"TP": "R1", "SL": "STOP", "TIME": "TIMEOUT"}[kind], "entry_used": entry,
            "exit_price": px, "exit_index": i, "target_used": target, "r_gross": gross,
            "r_net": net, "bars_used": i + 1, "seed": seed}
