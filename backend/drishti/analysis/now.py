"""'Analysis now' for one stock (user 2026-11-12). DECISION SUPPORT, NOT A PREDICTION.

Every number comes from the existing, tested pieces; nothing here is a new signal:
  price      the recorder's live LTP (else the latest quote) and today's OHLC (today_ohlc)
  context    regime / spec gate (Scout's latest run), trend (spec section 3 rule), SMA50/200,
             RSI, ATR on the adjusted daily bars + today's live bar
  levels     support/resistance zones (features.levels) around the live price
  plan       "if bought now": entry = LTP, stop = the scanner's structure stop (detect._stop:
             wider of support - 0.1 ATR and entry - 1 ATR), target = detect.choose_target (R1 if
             its net R:R >= 1, else R2, else 2R), qty at 1 % of Rs 10 lakh, costs in R
  odds       Track B's Time Machine (judge.odds) on that plan, using ONLY daily bars before today
             (point in time), horizon 5 sessions; + whether the frozen paper-portfolio odds gate
             (avg net R >= +0.05, replay N >= 30, not thin) would pass
  events     results / board meetings in the next 5 sessions (paper.gates.results_veto)
  scanner    today's scanner alerts and Live Radar entry for the stock, watchlist membership
"""

from __future__ import annotations

import datetime as _dt
import time

import numpy as np
import pandas as pd
from sqlalchemy import text

from drishti.core.clock import IST, now
from drishti.data import bars, master
from drishti.features import indicators, levels
from drishti.judge import odds as O
from drishti.scanner import detect, grade

LABEL = ("Decision support — not a prediction and not a validated edge. Paper only. Your call. "
         "Odds are the stock's own history for this stop/target geometry, not a forecast.")
_cache: dict[str, tuple[float, dict]] = {}
TTL_S = 20.0


def _f(x, nd=2):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else round(float(x), nd)


async def analyse(s, ps, key: str, live, ltp: float | None = None, n_paths: int = 4000) -> dict:
    """`live`: the API layer's live helpers (chart_data: get_live_quote, today_ohlc, LIVE_DIR),
    passed in so this module never imports the API package."""
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < TTL_S and ltp is None:
        return hit[1]
    get_live_quote, today_ohlc = live.get_live_quote, live.today_ohlc
    t = now()
    day = t.date()
    hist = await bars.official(ps, day, 450, keys=[key])
    hist = hist[hist.session_date < day].sort_values("session_date")
    if hist.empty or len(hist) < 60:
        return {"label": LABEL, "key": key, "error": "not enough daily history"}
    q = get_live_quote(key) if ltp is None else None
    ltp = ltp if ltp is not None else (q or {}).get("ltp")
    if ltp is None:
        ltp = float(hist.close.iloc[-1])
        price_src = "last official close (no live price)"
    else:
        price_src = "live"
    ltp = float(ltp)
    tod = today_ohlc(key, day, ltp)
    today_bar = pd.DataFrame([{"session_date": day, "open": tod["open"], "high": tod["high"],
                          "low": tod["low"], "close": ltp, "volume": tod["volume"]}])
    b = pd.concat([hist[["session_date", "open", "high", "low", "close", "volume"]], today_bar],
                  ignore_index=True)
    ind = indicators.basic(b)
    r = ind.iloc[-1]
    atr = float(r.atr)
    zs = levels.zones(b.high.to_numpy(float), b.low.to_numpy(float), atr)
    sup = levels.support_below(zs, ltp)
    res = levels.resistance_above(zs, ltp)
    # the previous close: the live feed's own (cp), else the quote's change, else the last
    # stored official close; flag a daily history that lags the previous session
    prev_close, prev_src = float(hist.close.iloc[-1]), "last stored official close"
    try:
        import json as _json
        cp = (_json.loads((live.LIVE_DIR / "summary.json").read_text())["keys"].get(key)
              or {}).get("cp")
        if cp:
            prev_close, prev_src = float(cp), "live feed previous close"
    except (OSError, ValueError, KeyError):
        if q and q.get("pct_change") not in (None, 0):
            prev_close = ltp / (1 + float(q["pct_change"]) / 100)
            prev_src = "quote change"
    from drishti.run.p1 import previous_session
    want_prev = await previous_session(s, day)
    hist_last = hist.session_date.max()

    # the plan "if bought now"
    low_ref = sup["bottom"] if sup and ltp - sup["bottom"] <= 3 * atr else float(tod["low"])
    stop = detect._stop(low_ref, ltp, atr)
    z1 = levels.resistance_above(zs, ltp)
    z2 = levels.resistance_above(zs, z1["top"]) if z1 else None
    sized = detect.size({"detector": "NOW", "entry_ref": ltp, "stop": stop,
                         "r1": z1["bottom"] if z1 else None, "r2": z2["bottom"] if z2 else None,
                         "rr_r1": None, "atr": atr}, 1_000_000, 1.0)
    plan = {"entry": _f(ltp), "stop": _f(stop), "risk_per_share": _f(ltp - stop),
            "stop_basis": "support - 0.1 ATR" if low_ref != float(tod["low"]) else "today's low",
            "r1": _f(sized["r1"]), "r2": _f(sized["r2"]), "target": _f(sized["target"]),
            "target_basis": sized["target_basis"], "tags": sized["tags"],
            "rr_gross": _f((sized["target"] - ltp) / (ltp - stop)),
            "net_rr": _f(sized["net_rr_target"]), "qty_1pct_of_10L": sized["qty"],
            "cost_r": _f(sized["cost_r"], 3), "horizon_sessions": grade.HOLD["1d"]}

    # Time Machine odds on that plan, bars before today only (point in time)
    od = None
    try:
        res_o = O.odds(O.Plan(key, day, ltp, stop, sized["target"], grade.HOLD["1d"],
                              sized["qty"], None, None, "manual"),
                       hist.assign(session_date=pd.to_datetime(hist.session_date).dt.date),
                       O.Params(n_paths=n_paths, seed=O.SEED))
        mc, rp = res_o["mc"], res_o["replay"]
        od = {"verdict": res_o["verdict"], "breakeven": _f(res_o["net"]["breakeven_win_rate"], 3),
              "mc": {k: _f(mc.get(k), 3) for k in ("target_first", "stop_first", "neither",
                                                   "green", "avg_r_net", "median_r_net",
                                                   "p05_r_net", "p95_r_net")},
              "replay_n": rp.get("n", 0), "replay_target_first": _f(rp.get("target_first"), 3),
              "thin_sample": rp.get("n", 0) < 30,
              "pool": res_o["pool"], "label": res_o["label"], "caveat": res_o["caveat"]}
        od["paper_gate_pass"] = bool((mc.get("avg_r_net") or -9) >= 0.05
                                     and rp.get("n", 0) >= 30)
    except ValueError as e:
        od = {"error": str(e)}

    # context: regime, events, scanner, watchlist
    reg = (await s.execute(text("""select session_date, regime from watchlist_run
        order by session_date desc, created_at desc limit 1"""))).first()
    from drishti.paper import gates
    from drishti.run.p2 import next_sessions
    window = await next_sessions(s, day, grade.HOLD["1d"])
    sym = (await master.state(s)).get(key, {}).get("trading_symbol")
    ev = await gates.results_veto(s, ps, key, sym, window, t, day)
    alerts = [dict(x) for x in (await s.execute(text("""select id, tf, detector, bar_time,
        entry_ref, stop, created_at from scan_alert where instrument_key = :k
        and session_date = :d order by bar_time desc limit 5"""), {"k": key, "d": day})).mappings()]
    radar = (await s.execute(text("""select bar_time, reason from radar_event
        where instrument_key = :k and session_date = :d"""), {"k": key, "d": day})).first()
    wl = (await s.execute(text("""
        with r as (select run_id from watchlist_run order by session_date desc, created_at desc
                   limit 1)
        select rank from watchlist_event where run_id = (select run_id from r)
          and instrument_key = :k and event <> 'DROPPED'"""), {"k": key})).scalar()
    out = {
        "label": LABEL, "key": key, "symbol": sym, "as_of": t.isoformat(),
        "history": {"daily_bars_until": str(hist_last), "previous_session": str(want_prev),
                    "lagging": hist_last < want_prev,
                    "note": f"daily history ends {hist_last}; the {want_prev} bar is not stored yet"
                    if hist_last < want_prev else None},
        "price": {"ltp": _f(ltp), "source": price_src, "prev_close": _f(prev_close),
                  "prev_close_source": prev_src,
                  "change_pct": _f(100 * (ltp / prev_close - 1)),
                  "today": {k: _f(tod[k]) for k in ("open", "high", "low")},
                  "today_from_recorder": tod["from_recorder"]},
        "context": {"regime": (reg[1] or {}).get("status") if reg else None,
                    "spec_gate": (reg[1] or {}).get("spec_gate") if reg else None,
                    "regime_session": str(reg[0]) if reg else None,
                    "trend": levels.trend(b, ind), "sma50": _f(r.sma50), "sma200": _f(r.sma200),
                    "above_sma50": bool(ltp > r.sma50), "above_sma200": bool(ltp > r.sma200),
                    "rsi14": _f(r.rsi, 1), "atr": _f(atr), "atr_pct": _f(100 * atr / ltp),
                    "hi52": _f(r.hi52), "pct_below_52w_high": _f(100 * (1 - ltp / r.hi52))},
        "levels": {"support": {"bottom": _f(sup["bottom"]), "top": _f(sup["top"]),
                               "touches": sup["touches"]} if sup else None,
                   "resistance": {"bottom": _f(res["bottom"]), "top": _f(res["top"]),
                                  "touches": res["touches"]} if res else None,
                   "pct_to_support": _f(100 * (sup["top"] / ltp - 1)) if sup else None,
                   "pct_to_resistance": _f(100 * (res["bottom"] / ltp - 1)) if res else None},
        "plan_if_bought_now": plan, "odds": od,
        "events": {"results_in_hold": not ev["pass"], "code": ev["code"], "why": ev["why"],
                   "window": [str(window[0]), str(window[-1])]},
        "scanner": {"alerts_today": [{**a, "bar_time": a["bar_time"].isoformat(),
                                      "created_at": a["created_at"].isoformat(),
                                      "entry_ref": _f(a["entry_ref"]), "stop": _f(a["stop"])}
                                     for a in alerts],
                    "radar_today": {"bar_time": radar[0].isoformat(), "reason": radar[1]}
                    if radar else None, "watchlist_rank": wl},
    }
    _cache[key] = (time.monotonic(), out)
    return out
