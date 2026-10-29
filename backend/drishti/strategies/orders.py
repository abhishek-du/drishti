"""Orders, levels and the exit path of spec drishti-swing-v1 section 6 (= the section 8 label),
on one stock's adjusted daily bars (numpy arrays, oldest first). PAPER ONLY: these are
simulations, nothing here talks to a broker.

The signal is on bar t; the order is for bar t + 1. Every function reads bars > t only for the
fill and the exit, never for a level.
"""

from __future__ import annotations

import math

import numpy as np

from drishti.core import costs
from drishti.features import levels

CAPITAL = 10_00_000.0
RISK = 0.01
HOLD = 5                     # sessions, fill day = session 1
CLOSED = ("TP", "SL", "TIME")


def levels_at(high_t: float, low_t: float, atr_t: float, zs: list[dict], entry: float | None = None
              ) -> dict:
    """Trigger, stop, capped target and RRR on bar t. `entry` (default: the trigger) is E_est."""
    trig = high_t * 1.0005
    e = trig if entry is None else entry
    stop = min(low_t - 0.1 * atr_t, e - 1.0 * atr_t)
    r = e - stop
    res = levels.resistance_above(zs, e)
    tp = min(e + 2 * r, res["bottom"]) if res else e + 2 * r
    return {"trigger": trig, "stop": stop, "tp": tp, "rrr": (tp - e) / r if r > 0 else -np.inf}


def exit_path(o, h, lo, c, start: int, stop: float, tp: float) -> tuple[str, float, int] | None:
    """Section 6 exit from the fill bar `start` (session 1) to session 5: stop first (at the stop,
    or the open on a gap through), then the target (or the open above it), else the session-5
    close. On the fill bar a low <= stop is the stop. None when the bars run out first."""
    for j in range(HOLD):
        i = start + j
        if i >= len(c):
            return None
        if j > 0 and o[i] < stop:
            return "SL", float(o[i]), i
        if lo[i] <= stop:
            return "SL", stop, i
        if j > 0 and o[i] > tp:
            return "TP", float(o[i]), i
        if h[i] >= tp:
            return "TP", tp, i
    return "TIME", float(c[start + HOLD - 1]), start + HOLD - 1


def _result(status: str, **kw) -> dict:
    return {"status": status, **kw}


def spec_order(o, h, lo, c, t: int, atr_t: float, zs: list[dict]) -> dict:
    """The frozen buy-stop of section 6 for a signal on bar t."""
    lv = levels_at(h[t], lo[t], atr_t, zs)
    base = {"trigger": lv["trigger"], "stop": lv["stop"], "tp": lv["tp"], "rrr_est": lv["rrr"]}
    if lv["rrr"] < 1.5:
        return _result("SKIP_RRR", **base)
    f = t + 1
    if f >= len(c):
        return _result("OPEN", **base)
    trig = lv["trigger"]
    if o[f] > trig + 0.5 * atr_t:
        return _result("GAP_CHASE", **base)
    if o[f] >= trig:
        e = float(o[f])
    elif h[f] >= trig:
        e = trig
    else:
        return _result("NOT_TRIGGERED", **base)
    if (lv["tp"] - e) / (e - lv["stop"]) < 1.5:
        return _result("CANCEL_RRR_AT_FILL", entry=e, **base)
    return _close(o, h, lo, c, f, e, base)


def _close(o, h, lo, c, f: int, e: float, base: dict) -> dict:
    x = exit_path(o, h, lo, c, f, base["stop"], base["tp"])
    if x is None:
        return _result("OPEN", entry=e, fill_i=f, **base)
    return _result(x[0], entry=e, exit=x[1], fill_i=f, exit_i=x[2], **base)


def placebo_order(o, h, lo, c, t: int, atr_t: float, zs: list[dict], u: float) -> dict:
    """Random-entry placebo (PREREG_M1: same name and day, random trigger): entry at
    low + u * (high - low) of bar t + 1 (u ~ U[0, 1), seeded by the caller), then the same stop,
    target, RRR and exit rules with that entry as E."""
    f = t + 1
    if f >= len(c):
        return _result("OPEN")
    e = float(lo[f] + u * (h[f] - lo[f]))
    lv = levels_at(h[t], lo[t], atr_t, zs, entry=e)
    base = {"trigger": e, "stop": lv["stop"], "tp": lv["tp"], "rrr_est": lv["rrr"]}
    if lv["rrr"] < 1.5:
        return _result("SKIP_RRR", **base)
    return _close(o, h, lo, c, f, e, base)


def close_confirm_order(o, h, lo, c, t: int, atr_t: float, zs: list[dict]) -> dict:
    """EXPLORATORY close-confirm entry (user definition, 2026-10-29; never used for H1-H3):
    - a close of bar t+1 or t+2 above the frozen trigger confirms; neither = NO_CONFIRM;
    - a low <= the stop before (or on) the confirming bar cancels (STOP_BEFORE_CONFIRM);
    - entry at the open of the bar after the confirming close, cancelled as GAP_CHASE when it
      is > trigger + 0.5 ATR(t);
    - stop as the spec from the signal candle; target min(E + 2R, resistance above E) and
      RRR >= 1.5 from the actual entry E;
    - the section 6 exit path from the entry bar."""
    lv = levels_at(h[t], lo[t], atr_t, zs)
    trig, stop = lv["trigger"], lv["stop"]
    for k in (1, 2):
        i = t + k
        if i >= len(c):
            return _result("OPEN", trigger=trig, stop=stop)
        if lo[i] <= stop:
            return _result("STOP_BEFORE_CONFIRM", trigger=trig, stop=stop)
        if c[i] > trig:
            f = i + 1
            if f >= len(c):
                return _result("OPEN", trigger=trig, stop=stop)
            e = float(o[f])
            if e > trig + 0.5 * atr_t:
                return _result("GAP_CHASE", trigger=trig, stop=stop)
            if e <= stop:
                return _result("STOP_BEFORE_CONFIRM", trigger=trig, stop=stop)
            r = e - stop
            res = levels.resistance_above(zs, e)
            tp = min(e + 2 * r, res["bottom"]) if res else e + 2 * r
            base = {"trigger": trig, "stop": stop, "tp": tp, "rrr_est": (tp - e) / r}
            if (tp - e) / r < 1.5:
                return _result("SKIP_RRR", entry=e, **base)
            return _close(o, h, lo, c, f, e, base)
    return _result("NO_CONFIRM", trigger=trig, stop=stop)


def qty(entry: float, stop: float, spike: bool = False) -> int:
    """Section 6 size: floor(risk / (E - S)), risk 1 % of ₹10 lakh (x 0.5 in a VIX spike); at
    least 1 share so that per-share costs stay defined."""
    risk = CAPITAL * RISK * (0.5 if spike else 1.0)
    return max(1, math.floor(risk / (entry - stop)))


def net(entry: float, exit_: float, stop: float, n: int) -> dict:
    """Net R (spec section 7: (exit - E) / R - costs per share / R) and net ₹ for the three
    slippage scenarios."""
    r = entry - stop
    out = {"gross_r": (exit_ - entry) / r, "qty": n}
    for bps in costs.SLIPPAGE_BPS:
        cost = costs.round_trip(entry, exit_, n, bps)
        out[f"net_r_{bps}"] = (exit_ - entry) / r - cost / n / r
        out[f"net_inr_{bps}"] = (exit_ - entry) * n - cost
    return out
