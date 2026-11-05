"""Nightly grader of every scan alert: the triple-barrier outcome on real bars after the alert.

Barriers (the frozen exit rules of drishti.strategies.orders.exit_path: stop first on a bar that
touches both, gap-through at the open):
  target = detect.choose_target: R1 if its net R:R >= 1, else R2, else entry + 2R (user
  2026-11-04: an R1 just above the entry loses money even when hit), stop = the alert's stop,
  time-out = HOLD bars (1d: 5 sessions); 15m: flat by 15:20 (the 15:00-15:15 bar's close).
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

HOLD = {"1d": 5, "15m": 25}     # 15m: the default only; scan_grade / the live tracker pass the
                                 # bars left in the alert's session (flat by the end of the day)
SEED = 20261103


def _qty(entry: float, stop: float, qty: int | None, capital: float, risk_pct: float) -> int:
    if qty:
        return qty
    r = entry - stop
    return max(1, int(capital * risk_pct / 100 // r)) if r > 0 else 0


FLAT_BY = "15:20"     # MIS flat by 15:20 everywhere (user 2026-11-05, PREREG_PAPER_PORTFOLIO 4)
LAST_BAR_START = (15, 0)   # so the last 15m bar held is 15:00-15:15, exited at its close


def slots_left(bar_start) -> int:
    """15m bars after the alert's bar up to the 15:00-15:15 bar: a 15m alert is flat by 15:20,
    exited at the close of the last bar that ends before it (one rule for the grader, the live
    tracker and Telegram)."""
    from drishti.core.clock import IST
    t = bar_start.astimezone(IST)
    last = t.replace(hour=LAST_BAR_START[0], minute=LAST_BAR_START[1], second=0, microsecond=0)
    return max(0, int((last - t).total_seconds() // 900))


def grade(a: dict, o, h, lo, c, basis: str, actual_entry: float | None = None,
          capital: float = 1e6, risk_pct: float = 1.0, hold: int | None = None) -> dict:
    """a: the alert (entry_ref, stop, r1, qty); o/h/lo/c: the bars AFTER the detection bar."""
    stop = float(a["stop"])
    ref = float(a["entry_ref"])
    hold = HOLD[a["tf"]] if hold is None else hold
    if hold == 0:                               # no bar left in the session: no trade
        return {"outcome": "NO_FILL", "bars_used": 0}
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
    from drishti.scanner.detect import choose_target
    t = choose_target(ref, stop, a.get("r1"), a.get("r2"), int(a.get("qty") or 0) or
                      _qty(ref, stop, None, capital, risk_pct))["target"]
    # the alert's target (R1, or R2 / 2R when R1's net R:R < 1); entry + 2R if the fill is
    # already above it
    target = t if t > entry else entry + 2 * risk
    res = exit_path(o, h, lo, c, 0, stop, target, hold)
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
