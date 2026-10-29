"""Rule-based bias box of the chart (Track C): plain rules on the last bar, in words.
NOT a prediction: no model, no probabilities, no buy/sell wording; not used by
drishti-swing-v1."""

from __future__ import annotations

import math

BIAS_LABEL = "NOT a prediction - rule summary only; not used by drishti-swing-v1"
TREND_WORDS = {"UP": "uptrend (close > SMA50 > SMA200, rising pivot highs and lows)",
               "DOWN": "downtrend (close < SMA50 < SMA200, falling pivot highs and lows)",
               "SIDE": "no clear trend by the spec rule (SIDE)"}


def _ok(x) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x))


def rsi_zone(r: float) -> str:
    if r < 30:
        return "below 30 (oversold zone)"
    if r < 40:
        return "30-40 (weak zone)"
    if r <= 60:
        return "40-60 (neutral zone)"
    if r <= 70:
        return "60-70 (strong zone)"
    return "above 70 (overbought zone)"


def bias(close: float, trend: str, last: dict, support: dict | None, resistance: dict | None,
         atr: float | None) -> dict:
    """`last` holds the last bar's ema20, sma50, vwap, rsi (any may be None/NaN)."""
    rules: list[dict] = [{"rule": "trend", "state": trend, "text": f"Trend: {TREND_WORDS[trend]}."}]
    for name, label in (("ema20", "EMA20"), ("sma50", "SMA50"), ("vwap", "VWAP")):
        v = last.get(name)
        if _ok(v) and v > 0:
            side = "above" if close > v else "below" if close < v else "at"
            rules.append({"rule": name, "state": side,
                          "text": f"Close is {side} {label} ({100 * (close / v - 1):+.2f}%)."})
    r = last.get("rsi")
    if _ok(r):
        rules.append({"rule": "rsi", "state": rsi_zone(r).split(" (")[0],
                      "text": f"RSI(14) {r:.0f}: {rsi_zone(r)}."})
    for z, side in ((support, "support"), (resistance, "resistance")):
        if z is None:
            rules.append({"rule": side, "state": None, "text": f"No {side} zone on this timeframe."})
            continue
        d = abs(close - z["price"])
        dist = f"{d / atr:.1f} ATR" if _ok(atr) and atr > 0 else f"{100 * d / close:.1f}%"
        rules.append({"rule": side, "state": round(z["price"], 2),
                      "text": f"Nearest {side} zone {z['price']:.2f} "
                              f"(band {z['bottom']:.2f}-{z['top']:.2f}, {z['touches']} touches), "
                              f"{dist} away."})
    return {"label": BIAS_LABEL, "rules": rules, "text": " ".join(x["text"] for x in rules)}
