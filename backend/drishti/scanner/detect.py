"""Decision-support detectors (scan-v1). PAPER ONLY; nothing here places an order.

LABEL: "Decision support - detections are NOT validated edges. Your call. Paper only."
No new entry research: every detector reuses drishti.features / drishti.strategies rules
(spec section 4 candle flags, section 3 zones, the S2/S3 conditions, ATR stops). The research
families behind them were tested and CLOSED (DESIGN.md); these are prompts for the user's own
judgement, graded honestly against a placebo in the journal.

Works on any bar timeframe (1d or 15m): one stock's bars, oldest first, the last row is the
closed detection bar t. A hit gives the entry reference (a buy-stop just above bar t's high,
for the next bar), a structure stop, R1/R2 = the next resistance zones above the entry (band
bottoms), RR to R1, size at risk % of capital, and the round-trip cost in R.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from drishti.core import costs
from drishti.features import candles, indicators, levels

VERSION = "scan-v1"
LABEL = "Decision support - detections are NOT validated edges. Your call. Paper only."
DETECTORS = ("HAMMER_CONFIRM", "BREAKOUT_52W", "SUPPORT_REVERSAL", "EMA20_PULLBACK")
HI52 = 250                      # bars of the 52-week high (1d); on 15m: the last 250 bars


def prepare(bars: pd.DataFrame) -> pd.DataFrame:
    """bars (open/high/low/close/volume) -> + indicators.basic + the 26 candle flags."""
    b = bars.reset_index(drop=True)
    ind = indicators.basic(b)
    out = pd.concat([b, ind], axis=1)
    return pd.concat([out, candles.flags(out)], axis=1)


def _stop(low_ref: float, entry: float, atr: float) -> float:
    """Structure stop: the wider of (low_ref - 0.1 ATR) and (entry - 1 ATR) (spec section 6)."""
    return min(low_ref - 0.1 * atr, entry - atr)


def hits(df: pd.DataFrame) -> list[dict]:
    """Detector hits on the last (closed) bar of a prepared frame. Each detector names itself."""
    if len(df) < 60:
        return []
    t = len(df) - 1
    r = df.iloc[t]
    p = df.iloc[t - 1]
    atr = float(r.atr)
    if not atr > 0 or not np.isfinite(atr):
        return []
    out = []
    # 1) hammer on t-1, confirmed by t closing above the hammer's high
    if bool(p.hammer) and r.close > p.high:
        out.append(("HAMMER_CONFIRM", float(min(p.low, r.low))))
    # 2) close above the highest high of the previous 250 bars (52 weeks on 1d)
    prior = df.high.iloc[max(0, t - HI52):t]
    if len(prior) >= min(HI52, 120) and r.close > prior.max() and p.close <= prior.max():
        out.append(("BREAKOUT_52W", float(r.low)))
    zs = levels.zones(df.high.to_numpy(float), df.low.to_numpy(float), atr)
    bull = bool(r.hammer or r.engulfing_bull or r.morning_star)
    # 3) S3-style: above SMA200, low at a support zone, a reversal candle, above-average volume
    sup = levels.at_support(zs, float(r.low), atr)
    if sup and bull and pd.notna(r.sma200) and r.close > r.sma200 and r.volr20 > 1.0:
        out.append(("SUPPORT_REVERSAL", float(min(r.low, sup["bottom"]))))
    # 4) S2-style: close > SMA50 > SMA200, low tags EMA20, hammer or bullish engulfing
    if pd.notna(r.sma200) and r.close > r.sma50 > r.sma200 and r.low <= r.ema20 * 1.005 \
            and r.close >= r.ema20 * 0.99 and bool(r.hammer or r.engulfing_bull):
        out.append(("EMA20_PULLBACK", float(r.low)))
    return [levels_for(name, float(r.high), low_ref, atr, zs) for name, low_ref in out]


def levels_for(detector: str, high_t: float, low_ref: float, atr: float, zs: list[dict]) -> dict:
    entry = high_t * 1.0005                  # buy-stop just above the detection bar's high
    stop = _stop(low_ref, entry, atr)
    z1 = levels.resistance_above(zs, entry)
    z2 = levels.resistance_above(zs, z1["top"]) if z1 else None
    r1 = z1["bottom"] if z1 else None
    r2 = z2["bottom"] if z2 else None
    risk = entry - stop
    return {"detector": detector, "entry_ref": entry, "stop": stop, "r1": r1, "r2": r2,
            "rr_r1": (r1 - entry) / risk if r1 and risk > 0 else None, "atr": atr,
            "zones_used": {n: {k: z.get(k) for k in ("bottom", "top", "touches", "first", "last")}
                           for n, z in (("r1", z1), ("r2", z2)) if z}}


MIN_NET_RR = 1.0
STOP_SLIP_R = 1.2               # = the odds engine's slip_r: a stop exit is booked at -1.2 R


def net_r_at(entry: float, stop: float, exit_px: float, qty: int) -> float:
    """Net R of one exit under the backtester cost model (costs.round_trip, base slippage)."""
    risk = entry - stop
    return (exit_px - entry) / risk - costs.round_trip(entry, exit_px, qty) / qty / risk


def net_rr(entry: float, stop: float, target: float, qty: int) -> float | None:
    """Net reward:risk as the odds engine computes it: net R at the target / |net R at a stop
    booked at -STOP_SLIP_R R| (round-trip costs on both, base slippage)."""
    risk = entry - stop
    if not (risk > 0 and qty and target > entry):
        return None
    loss = net_r_at(entry, stop, entry - STOP_SLIP_R * risk, qty)
    return net_r_at(entry, stop, target, qty) / -loss


def choose_target(entry: float, stop: float, r1, r2, qty: int) -> dict:
    """The actionable target (user 2026-11-04), used by the grader, the odds and the API:
    R1 when its net R:R >= MIN_NET_RR; else R2 when that clears it; else entry + 2R. R1 and R2
    stay as reference levels. Tags: "broken R1" when even a hit at R1 is <= 0 net R (it does not
    clear costs), "low R:R" when R1 is net positive but below MIN_NET_RR. Uses stored fields
    only, so it applies to old alerts as well."""
    risk = entry - stop
    r1 = float(r1) if r1 is not None else None
    r2 = float(r2) if r2 is not None else None
    qty = int(qty or 0)
    n1 = net_rr(entry, stop, r1, qty) if r1 is not None and r1 > entry else None
    nr1 = net_r_at(entry, stop, r1, qty) if r1 is not None and qty and risk > 0 else None
    for basis, t in (("R1", r1), ("R2", r2)):
        if t is not None and t > entry and (net_rr(entry, stop, t, qty) or 0) >= MIN_NET_RR:
            break
    else:
        basis, t = "2R", entry + 2 * risk
    broken = r1 is not None and (nr1 is None or nr1 <= 0)
    low = r1 is not None and not broken and (n1 or 0) < MIN_NET_RR
    tags = ["broken R1"] if broken else ["low R:R"] if low else []
    return {"target": t, "target_basis": basis, "net_rr_r1": n1, "net_r_at_r1": nr1,
            "net_rr_target": net_rr(entry, stop, t, qty), "low_rr": broken or low,
            "broken_r1": broken, "tags": tags, "cost_model": costs.MODEL,
            "stop_slip_r": STOP_SLIP_R}


def size(h: dict, capital: float, risk_pct: float) -> dict:
    """Qty at risk_pct of capital, the target (choose_target), and the round-trip cost (base
    slippage) in R to that target."""
    risk = h["entry_ref"] - h["stop"]
    qty = max(1, math.floor(capital * risk_pct / 100 / risk)) if risk > 0 else 0
    ct = choose_target(h["entry_ref"], h["stop"], h["r1"], h["r2"], qty)
    target = ct["target"]
    cost_r = costs.round_trip(h["entry_ref"], target, qty) / qty / risk if qty and risk > 0 \
        else None
    return {**h, **ct, "qty": qty, "cost_r": cost_r, "target_ref": target}
