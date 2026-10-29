"""Live 1m / 5m candles and VWAP from the V3 full feed (LIVE VIEW: not used by drishti-swing-v1).

Per tick: ltp, ltt (exchange last-trade time, ms), vtt (cumulative day volume), atp (the
exchange's average traded price = the day's VWAP), cp (previous close).
  - A tick goes to the minute of its ltt (IST). A tick with an older ltt than the key's last
    one does not move price or time, but its vtt increase is booked into the bar of the last
    trade (volume is never lost or moved forward).
  - Bar volume = the vtt increase since the previous tick. The first tick of a key sets the
    baseline (its bar gets no volume), so connecting at 09:21 never books 09:15-09:20 volume
    into one minute.
  - 5m candles are folded from the 1m candles.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

from drishti.core.clock import IST


@dataclass
class Bar:
    t: int                      # minute start, epoch seconds (UTC)
    o: float
    h: float
    l: float                    # noqa: E741
    c: float
    v: int = 0
    vwap: float | None = None   # the exchange atp at the bar's last tick

    def as_dict(self) -> dict:
        return {"time": self.t, "open": self.o, "high": self.h, "low": self.l, "close": self.c,
                "volume": self.v, "vwap": self.vwap}


@dataclass
class KeyState:
    bars: dict[int, Bar] = field(default_factory=dict)
    last_ltt: int = 0
    last_vtt: int | None = None
    ltp: float | None = None
    cp: float | None = None
    atp: float | None = None
    vtt: int | None = None
    ticks: int = 0
    flushed_upto: int = 0       # minutes < this are already in Parquet


class Builder:
    def __init__(self):
        self.keys: dict[str, KeyState] = {}

    def tick(self, key: str, ltp: float, ltt_ms: int, vtt: int | None, atp: float | None,
             cp: float | None) -> None:
        st = self.keys.setdefault(key, KeyState())
        st.ticks += 1
        st.cp = cp or st.cp
        if atp:
            st.atp = atp
        dv = 0
        if vtt is not None:
            dv = max(0, vtt - st.last_vtt) if st.last_vtt is not None else 0
            st.last_vtt = vtt if st.last_vtt is None else max(st.last_vtt, vtt)
            st.vtt = st.last_vtt
        if not ltp or not ltt_ms or ltt_ms < st.last_ltt:
            last = st.bars.get(st.last_ltt // 60000 * 60)
            if last is not None:
                last.v += dv
            return
        st.last_ltt, st.ltp = ltt_ms, ltp
        m = ltt_ms // 60000 * 60
        b = st.bars.get(m)
        if b is None:
            st.bars[m] = Bar(m, ltp, ltp, ltp, ltp, dv, st.atp)
        else:
            b.h, b.l, b.c = max(b.h, ltp), min(b.l, ltp), ltp
            b.v += dv
            b.vwap = st.atp

    def candles(self, key: str, minutes: int = 1) -> list[dict]:
        st = self.keys.get(key)
        if not st:
            return []
        one = [st.bars[m] for m in sorted(st.bars)]
        if minutes == 1:
            return [b.as_dict() for b in one]
        out: dict[int, Bar] = {}
        for b in one:
            t = b.t // (minutes * 60) * minutes * 60
            x = out.get(t)
            if x is None:
                out[t] = Bar(t, b.o, b.h, b.l, b.c, b.v, b.vwap)
            else:
                x.h, x.l, x.c, x.v, x.vwap = max(x.h, b.h), min(x.l, b.l), b.c, x.v + b.v, b.vwap
        return [out[t].as_dict() for t in sorted(out)]

    def summary(self) -> dict[str, dict]:
        out = {}
        for k, st in self.keys.items():
            out[k] = {"ltp": st.ltp, "cp": st.cp, "vwap": st.atp, "vtt": st.vtt,
                      "chg_pct": round(100 * (st.ltp / st.cp - 1), 3) if st.ltp and st.cp else None,
                      "last_trade": _dt.datetime.fromtimestamp(st.last_ltt / 1000, IST).isoformat()
                      if st.last_ltt else None, "ticks": st.ticks}
        return out

    def closed_bars(self, now_epoch: float) -> list[tuple[str, Bar]]:
        """1m bars whose minute has ended and that are not flushed yet (marks them flushed)."""
        cut = int(now_epoch) // 60 * 60 - 60        # the minute before the current one
        out = []
        for k, st in self.keys.items():
            for m in sorted(x for x in st.bars if st.flushed_upto <= x <= cut):
                out.append((k, st.bars[m]))
            st.flushed_upto = max(st.flushed_upto, cut + 60)
        return out
