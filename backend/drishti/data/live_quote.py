"""Live quote provider for chart and radar (read only)."""

from __future__ import annotations

import time

from drishti.broker.upstox.rest import Upstox

_quote_cache: dict[str, tuple[float, dict]] = {}


def fetch_quote(key: str) -> dict | None:
    now_t = time.monotonic()
    hit = _quote_cache.get(key)
    if hit and now_t - hit[0] < 10.0:
        return hit[1]

    try:
        u = Upstox()
        res = u.get("/v2/market-quote/quotes", {"instrument_key": key})
        for _, item in res.get("data", {}).items():
            if item.get("instrument_token") == key or item.get("symbol") in key:
                ohlc = item.get("ohlc") or {}
                ltp = item.get("last_price")
                if ltp is not None:
                    uc = item.get("upper_circuit_limit")
                    lc = item.get("lower_circuit_limit")
                    nc = item.get("net_change") or 0.0
                    prev_c = ohlc.get("close") or (ltp - nc)
                    pct = round((nc / prev_c * 100), 2) if prev_c else 0.0
                    info = {
                        "ltp": float(ltp),
                        "net_change": float(nc),
                        "pct_change": float(pct),
                        "open": float(ohlc.get("open") or ltp),
                        "high": float(ohlc.get("high") or ltp),
                        "low": float(ohlc.get("low") or ltp),
                        "volume": float(item.get("volume") or 0),
                        "vwap": float(item.get("average_price") or ltp),
                        "upper_circuit": float(uc) if uc else None,
                        "lower_circuit": float(lc) if lc else None,
                        "is_upper_circuit": bool(uc is not None and float(ltp) >= float(uc) - 0.05),
                        "is_lower_circuit": bool(lc is not None and float(ltp) <= float(lc) + 0.05),
                    }
                    _quote_cache[key] = (now_t, info)
                    return info
    except Exception:
        pass
    return None
