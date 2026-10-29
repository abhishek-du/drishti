"""Chart endpoints of the stock page (Track C; read only, GET only). Mounted from api/app.py.

/api/chart2/bars?key=&tf=1m|5m|15m|1h|1d   bars, indicator lines and panes, candle flags,
                                            S/R zones, the trend class and the bias box
/api/chart2/trends?key=                     the trend class per timeframe
/api/chart2/indicator?key=&tf=&kind=&...    one indicator with the user's parameters (the chart's
                                            indicators menu); kinds in INDICATORS

Indicators and patterns come only from drishti.features (bollinger/macd/vwap/trend/nearest from
chart_stubs until Track B lands them). 1m/5m are LIVE VIEW: not used by drishti-swing-v1.
Blind rule: no outcome or P&L of any signal or watchlist entry is served here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from drishti.api import chart_stubs as stubs
from drishti.api.chart_bias import bias
from drishti.api.chart_data import TFS, load
from drishti.core.db import prajna_sessionmaker, sessionmaker
from drishti.features import candles, indicators, levels

router = APIRouter(prefix="/api/chart2", tags=["chart"])
LIVE_LABEL = "LIVE VIEW - not used by drishti-swing-v1"
LINES = ("ema20", "sma50", "sma200", "vwap", "bb_mid", "bb_up", "bb_lo")
PANES = ("rsi", "macd", "macd_signal", "macd_hist")


def _f(x):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), 4)


def heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """Heikin Ashi candles of one stock (a chart type, display only: indicators, flags, zones
    and the trend stay on the real bars)."""
    o, h, lo, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    hc = (o + h + lo + c) / 4
    ho = np.empty(len(df))
    if len(df):
        ho[0] = (o[0] + c[0]) / 2
    for i in range(1, len(df)):
        ho[i] = (ho[i - 1] + hc[i - 1]) / 2
    return df.assign(open=ho, close=hc, high=np.maximum.reduce([h, ho, hc]),
                     low=np.minimum.reduce([lo, ho, hc]))


def _tf(tf: str) -> str:
    if tf not in TFS:
        raise HTTPException(400, f"tf must be one of {', '.join(TFS)}")
    return tf


def compute(df: pd.DataFrame, tf: str) -> dict:
    """Everything the chart draws, from bars (chart_data COLS, oldest first)."""
    df = df.reset_index(drop=True)
    ind = indicators.basic(df)
    ind = pd.concat([ind, stubs.bollinger(df), stubs.macd(df)], axis=1)
    if tf == "1d":
        ind["vwap"] = np.nan
    else:   # the exchange VWAP where the feed has it, else the session VWAP of the bars
        ind["vwap"] = df.vwap.where(df.vwap.notna(), stubs.vwap(df))
    fl = candles.flags(pd.concat([df, ind[["atr"]]], axis=1))
    atr = ind.atr.iloc[-1]
    zs = levels.zones(df.high.to_numpy(float), df.low.to_numpy(float), float(atr)) \
        if atr > 0 else []
    t = [x if isinstance(x, str) else int(x) for x in df.time]
    tr = stubs.trend(df, ind)
    close = float(df.close.iloc[-1])
    sup, res = stubs.nearest(zs, close)
    last = {n: _f(ind[n].iloc[-1]) for n in ("ema20", "sma50", "vwap", "rsi")}
    names, on = np.array(candles.FLAGS), fl.to_numpy()
    chg = df.close.pct_change() * 100
    return {
        "bars": [{"time": t[i], "open": _f(r.open), "high": _f(r.high), "low": _f(r.low),
                  "close": _f(r.close), "volume": _f(r.volume), "chg_pct": _f(chg.iloc[i])}
                 for i, r in enumerate(df.itertuples())],
        "lines": {n: [{"time": t[i], "value": _f(v)} for i, v in enumerate(ind[n]) if pd.notna(v)]
                  for n in LINES},
        "panes": {n: [{"time": t[i], "value": _f(v)} for i, v in enumerate(ind[n]) if pd.notna(v)]
                  for n in PANES},
        "candles": [{"time": t[i], "flags": names[on[i]].tolist()}
                    for i in range(len(df)) if on[i].any()],
        "bullish_flags": list(candles.BULLISH),
        "zones": [{k: (_f(v) if isinstance(v, float) else v) for k, v in z.items()}
                  | {"first": t[z["first"]], "last": t[z["last"]]} for z in zs],
        "trend": tr,
        "last": {**last, "atr": _f(atr), "close": _f(close)},
        "bias": bias(close, tr, last, sup, res, _f(atr)),
    }


async def _meta(key: str, tf: str) -> dict:
    async with prajna_sessionmaker()() as ps:
        sym = (await ps.execute(text("""select trading_symbol, name, sector from canon_instrument
            where instrument_key = :k"""), {"k": key})).mappings().first()
    wl: list[str] = []
    if tf == "1d":
        async with sessionmaker()() as s:
            wl = [str(d) for d in (await s.execute(text("""select distinct session_date
                from watchlist_event where instrument_key = :k and event <> 'DROPPED'
                order by 1"""), {"k": key})).scalars()]
    return {"symbol": sym and sym["trading_symbol"], "name": sym and sym["name"],
            "sector": sym and sym["sector"], "watchlist_dates": wl}


@router.get("/bars")
async def chart_bars(key: str, tf: str = "1d", ha: bool = False) -> dict:
    """`ha`: the bars are Heikin Ashi (chg_pct too); everything else is on the real bars."""
    tf = _tf(tf)
    df, src = await load(key, tf)
    if df.empty:
        raise HTTPException(404, f"no {tf} bars for {key}")
    out = compute(df, tf)
    if ha:
        hb = heikin_ashi(df.reset_index(drop=True))
        chg = hb.close.pct_change() * 100
        out["bars"] = [dict(b, open=_f(r.open), high=_f(r.high), low=_f(r.low), close=_f(r.close),
                            chg_pct=_f(chg.iloc[i]))
                       for i, (b, r) in enumerate(zip(out["bars"], hb.itertuples()))]
    return {"key": key, "tf": tf, "label": LIVE_LABEL if tf in ("1m", "5m") else None,
            "adjusted": tf == "1d", "heikin_ashi": ha, "sources": src,
            **await _meta(key, tf), **out}


INDICATORS = ("ema", "sma", "vwap", "bb", "rsi", "macd", "atr", "supertrend")


def indicator(df: pd.DataFrame, tf: str, kind: str, n: int, k: float, fast: int, slow: int,
              signal: int, mult: float) -> dict[str, pd.Series]:
    """One indicator with the given parameters, from drishti.features (stubs until Track B)."""
    df = df.reset_index(drop=True)
    g = pd.Series(0, index=df.index)
    if kind == "ema":
        return {"ema": indicators.ema(df.close, g, n)}
    if kind == "sma":
        return {"sma": indicators.roll(df.close, g, n, "mean")}
    if kind == "vwap":
        if tf == "1d":
            raise HTTPException(400, "VWAP is intraday only")
        return {"vwap": df.vwap.where(df.vwap.notna(), stubs.vwap(df))}
    if kind == "bb":
        return dict(stubs.bollinger(df, n=n, k=k).items())
    if kind == "rsi":
        return {"rsi": indicators.rsi(df, n=n)}
    if kind == "macd":
        if fast >= slow:
            raise HTTPException(400, "fast must be < slow")
        return dict(stubs.macd(df, fast=fast, slow=slow, signal=signal).items())
    if kind == "atr":
        return {"atr": indicators.atr(df, n=n)}
    return dict(stubs.supertrend(df, n=n, mult=mult).items())


@router.get("/indicator")
async def chart_indicator(key: str, tf: str = "1d", kind: str = "ema",
                          n: int = Query(20, ge=1, le=500), k: float = Query(2.0, gt=0, le=10),
                          fast: int = Query(12, ge=1, le=200), slow: int = Query(26, ge=2, le=400),
                          signal: int = Query(9, ge=1, le=200),
                          mult: float = Query(3.0, gt=0, le=20)) -> dict:
    tf = _tf(tf)
    if kind not in INDICATORS:
        raise HTTPException(400, f"kind must be one of {', '.join(INDICATORS)}")
    df, _ = await load(key, tf)
    if df.empty:
        raise HTTPException(404, f"no {tf} bars for {key}")
    out = indicator(df, tf, kind, n, k, fast, slow, signal, mult)
    t = [x if isinstance(x, str) else int(x) for x in df.time]
    return {"key": key, "tf": tf, "kind": kind,
            "label": LIVE_LABEL if tf in ("1m", "5m") else None,
            "params": {"n": n, "k": k, "fast": fast, "slow": slow, "signal": signal, "mult": mult},
            "series": {name: [{"time": t[i], "value": _f(v)} for i, v in enumerate(s)
                              if pd.notna(v)] for name, s in out.items()}}


@router.get("/trends")
async def chart_trends(key: str) -> dict:
    out: dict[str, str | None] = {}
    for tf in TFS:
        try:
            df, _ = await load(key, tf)
            out[tf] = stubs.trend(df.reset_index(drop=True),
                                  indicators.basic(df.reset_index(drop=True))) \
                if not df.empty else None
        except Exception:   # one missing source must not hide the other badges
            out[tf] = None
    return {"key": key, "trends": out, "live_label": LIVE_LABEL, "live_tfs": ["1m", "5m"]}
