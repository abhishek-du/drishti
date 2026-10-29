"""Chart endpoints of the stock page (Track C; read only, GET only). Mounted from api/app.py.

/api/chart2/bars?key=&tf=1m|5m|15m|1h|1d   bars, indicator lines and panes, candle flags,
                                            S/R zones, the trend class and the bias box
/api/chart2/trends?key=                     the trend class per timeframe

Indicators and patterns come only from drishti.features (bollinger/macd/vwap/trend/nearest from
chart_stubs until Track B lands them). 1m/5m are LIVE VIEW: not used by drishti-swing-v1.
Blind rule: no outcome or P&L of any signal or watchlist entry is served here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException
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
async def chart_bars(key: str, tf: str = "1d") -> dict:
    tf = _tf(tf)
    df, src = await load(key, tf)
    if df.empty:
        raise HTTPException(404, f"no {tf} bars for {key}")
    return {"key": key, "tf": tf, "label": LIVE_LABEL if tf in ("1m", "5m") else None,
            "adjusted": tf == "1d", "sources": src, **await _meta(key, tf), **compute(df, tf)}


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
