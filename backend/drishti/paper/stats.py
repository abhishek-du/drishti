"""Per-trade and equity statistics of a paper book, pf-v1 Phase 4 (PAPER ONLY; display, not a
look: the pre-registered test is the one look in PREREG section 7)."""

from __future__ import annotations


def _r(x, n=4):
    return None if x is None else round(float(x), n)


def trade_stats(trades: list[dict]) -> dict:
    """Net of costs: count, hit rate, average win / loss, payoff ratio, expectancy (Rs and R),
    total costs, average holding sessions, and counts by exit reason code."""
    n = len(trades)
    if not n:
        return {"trades": 0}
    net = [float(t["net_pnl"]) for t in trades]
    wins, losses = [x for x in net if x > 0], [x for x in net if x <= 0]
    rr = [float(t["r_multiple"]) for t in trades if t.get("r_multiple") is not None]
    hold = [int(t["holding_sessions"]) for t in trades if t.get("holding_sessions") is not None]
    aw = sum(wins) / len(wins) if wins else None
    al = sum(losses) / len(losses) if losses else None
    by: dict[str, int] = {}
    for t in trades:
        code = (t.get("reason_out") or "").split(" ", 1)[0] or "?"
        k = {"target": "target", "stop": "stop", "time": "time", "regime": "regime"}.get(
            code.lower(), "other")
        by[k] = by.get(k, 0) + 1
    return {"trades": n, "net_pnl": round(sum(net), 2), "hit_rate": _r(len(wins) / n),
            "avg_win": _r(aw, 2), "avg_loss": _r(al, 2),
            "payoff_ratio": _r(aw / -al) if aw and al else None,
            "expectancy_rs": round(sum(net) / n, 2),
            "expectancy_r": _r(sum(rr) / len(rr)) if rr else None,
            "costs": round(sum(float(t["costs"]) for t in trades), 2),
            "avg_hold_sessions": _r(sum(hold) / len(hold), 2) if hold else None,
            "exits": by}


def equity_stats(rows: list[dict], start_capital: float) -> dict:
    """From pf_equity rows (date order): total return, max drawdown, and the same for the
    benchmark series carried on the main book's rows."""
    if not rows:
        return {"days": 0}
    eq = [float(r["equity"]) for r in rows]
    peak, mdd = start_capital, 0.0
    for e in eq:
        peak = max(peak, e)
        mdd = min(mdd, e / peak - 1)
    be = [float(r["benchmark_equity"]) for r in rows if r.get("benchmark_equity") is not None]
    return {"days": len(rows), "total_return": _r(eq[-1] / start_capital - 1, 5),
            "max_drawdown": _r(mdd, 5),
            "benchmark_return": _r(be[-1] / start_capital - 1, 5) if be else None,
            "excess_return": _r(eq[-1] / start_capital - be[-1] / start_capital, 5) if be else None}
