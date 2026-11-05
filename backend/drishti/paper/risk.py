"""Sizing and the risk gate, pf-v1 (frozen in docs/PREREG_PAPER_PORTFOLIO.md section 3). Pure
functions: the same state gives the same answer. PAPER ONLY.

qty = floor(1 % of equity / (entry - stop)), whole shares, then reduced to fit:
  max position 20 % of equity      qty <= floor(0.20 * equity / entry)
  max open risk 3 % of equity      qty <= floor((0.03 * equity - open risk) / (entry - stop))
  cash buffer 10 % of equity       qty <= floor((cash - 0.10 * equity) / (entry * (1 + cost)))
A trade whose qty falls below 1 is rejected and the binding cap is the reason; it is never placed.
Pauses (no new entries): max 5 open positions (pending entries count), daily loss -2 % of the
day's opening equity, kill-switch -10 % from the starting capital (or a recorded pause flag).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

RISK_PER_TRADE = 0.01
MAX_POSITION = 0.20
MAX_OPEN_POSITIONS = 5
CASH_BUFFER = 0.10
MAX_OPEN_RISK = 0.03
DAILY_LOSS = -0.02
KILL_SWITCH = -0.10
COST_PAD = 0.0025          # buy-side costs + slippage head-room when checking the cash cap


@dataclass
class State:
    equity: float
    cash: float
    open_positions: int        # open + pending (decided, not yet filled)
    open_risk: float           # sum over open + pending of (entry - stop) * qty, rupees
    day_open_equity: float
    starting_capital: float
    paused: bool = False


def size(entry: float, stop: float, st: State) -> dict:
    """{'ok', 'qty', 'reason_code', 'binding', 'caps': {...}} for a long entry at `entry` with
    `stop`. reason_code is RISK_OK or the SKIP_ code of the first failing rule."""
    out = {"ok": False, "qty": 0, "binding": None, "caps": {}}
    if st.paused or st.equity <= (1 + KILL_SWITCH) * st.starting_capital:
        return {**out, "reason_code": "SKIP_KILL_SWITCH",
                "why": f"drawdown {st.equity / st.starting_capital - 1:.2%} <= {KILL_SWITCH:.0%}"
                       " (or paused): no new entries"}
    if st.day_open_equity > 0 and st.equity <= (1 + DAILY_LOSS) * st.day_open_equity:
        return {**out, "reason_code": "SKIP_DAILY_LOSS",
                "why": f"day P&L {st.equity / st.day_open_equity - 1:.2%} <= {DAILY_LOSS:.0%}"}
    if st.open_positions >= MAX_OPEN_POSITIONS:
        return {**out, "reason_code": "SKIP_MAX_POSITIONS",
                "why": f"{st.open_positions} open/pending >= {MAX_OPEN_POSITIONS}"}
    rps = entry - stop
    if not (rps > 0 and entry > 0):
        return {**out, "reason_code": "SKIP_BAD_LEVELS", "why": "entry must be above the stop"}
    e = st.equity
    caps = {
        "risk_1pct": math.floor(RISK_PER_TRADE * e / rps),
        "position_20pct": math.floor(MAX_POSITION * e / entry),
        "open_risk_3pct": math.floor(max(0.0, MAX_OPEN_RISK * e - st.open_risk) / rps),
        "cash_buffer_10pct": math.floor(max(0.0, st.cash - CASH_BUFFER * e)
                                        / (entry * (1 + COST_PAD))),
    }
    binding = min(caps, key=caps.get)
    qty = caps[binding]
    if qty < 1:
        return {**out, "caps": caps, "binding": binding,
                "reason_code": f"SKIP_CAP_{binding.upper()}",
                "why": f"{binding} leaves {qty} shares"}
    return {"ok": True, "qty": qty, "caps": caps, "binding": binding, "reason_code": "RISK_OK",
            "risk_rupees": round(qty * rps, 2), "position_value": round(qty * entry, 2),
            "why": f"{qty} sh, risk ₹{qty * rps:,.0f} ({qty * rps / e:.2%}), value "
                   f"{qty * entry / e:.1%} of equity; binding {binding}"}
