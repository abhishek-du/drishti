import asyncio
import datetime as _dt
import uuid

import numpy as np
import pandas as pd
import pytest

from drishti.features import candles, indicators
from drishti.scout import screener_b as sb


def _stock(seed: int, n: int = 320, drift: float = 0.001) -> pd.DataFrame:
    r = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(drift + 0.015 * r.standard_normal(n)))
    o = c * (1 + 0.005 * r.standard_normal(n))
    h, lo = np.maximum(o, c) * (1 + 0.01 * r.random(n)), np.minimum(o, c) * (1 - 0.01 * r.random(n))
    d0 = _dt.date(2030, 1, 1)
    return pd.DataFrame({"session_date": [d0 + _dt.timedelta(days=i) for i in range(n)],
                         "open": o, "high": h, "low": lo, "close": c,
                         "volume": 1e6 * (1 + r.random(n))})


def test_grouped_indicators_and_flags_equal_one_stock_at_a_time():
    a, b = _stock(1).assign(instrument_key="A"), _stock(2, 280).assign(instrument_key="B")
    both = pd.concat([a, b], ignore_index=True)
    g = indicators.basic(both, by="instrument_key")
    for k, one in (("A", a), ("B", b)):
        s = indicators.basic(one.reset_index(drop=True))
        pd.testing.assert_frame_equal(g[both.instrument_key == k].reset_index(drop=True), s,
                                      check_exact=False, rtol=1e-12)
    gf = candles.flags(pd.concat([both, g[["atr"]]], axis=1), by="instrument_key")
    sf = candles.flags(pd.concat([b.reset_index(drop=True),
                                  indicators.basic(b.reset_index(drop=True))[["atr"]]], axis=1))
    assert (gf[both.instrument_key == "B"].reset_index(drop=True) == sf).all().all()


def test_indicators_are_point_in_time():
    a = _stock(3)
    full = indicators.basic(a)
    cut = indicators.basic(a.iloc[:300])
    pd.testing.assert_frame_equal(full.iloc[:300], cut)        # the future changes nothing


def test_candle_shapes():
    rows = [(100, 101, 99, 100)] * 20 + [
        (100.2, 100.5, 99.6, 99.9), (99.9, 100, 99, 99.5), (99.5, 99.6, 97, 97.5),   # 3 lower closes
        (97.4, 97.45, 93.4, 97.3),                                                # hammer
        (97.0, 97.2, 95.5, 95.8), (95.6, 99.2, 95.5, 99.0)]                       # engulfing
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    df["atr"] = 2.0
    f = candles.flags(df)
    assert f.hammer.iloc[23] and not f.hammer.iloc[22]
    assert f.engulfing_bull.iloc[25]
    assert len(candles.FLAGS) == 26 and list(f.columns) == list(candles.FLAGS)


def test_screen_funnel_and_rules():
    day = _dt.date(2030, 11, 16)
    rows = []
    for i, k in enumerate(["NSE_EQ|INE00000000" + str(i) for i in range(6)]):
        st = _stock(10 + i, drift=0.003 if i < 4 else -0.002).assign(instrument_key=k)
        st["volume"] *= 50                                   # traded value >= 20 crore
        rows.append(st)
    b = pd.concat(rows, ignore_index=True)
    b = b[b.session_date <= day]
    st = sb.per_stock(b)
    isins = {k.split("|")[1] for k in st.index}
    blocked = {"INE000000000"}
    reg = {"nifty500_ret63": -1.0}
    listed, f = sb.screen(st, isins, blocked, reg, day)
    assert f["eligible"] == 5                                 # one ASM/GSM name removed
    assert "NSE_EQ|INE000000000" not in listed.index
    assert list(listed["rank"]) == list(range(1, len(listed) + 1))
    assert (listed.close > listed.sma50).all() and (listed.sma50 > listed.sma200).all()
    assert ((listed.atr_pct >= 0.02) & (listed.atr_pct <= 0.06)).all()
    assert f["listed"] == len(listed) <= f["atr_pct_ok"] <= f["near_52w_high"]


def test_watchlist_tables_are_append_only_in_test_db():
    from sqlalchemy import text

    from drishti.core import migrate
    from drishti.core.db import engine

    async def go():
        await migrate.main(False)
        rid = uuid.uuid4()
        async with engine().begin() as c:
            await c.execute(text("insert into run(run_id, kind, code_sha) values (:r,'TEST','x')"),
                            {"r": rid})
            await c.execute(text("""insert into watchlist_run(run_id, session_date, version,
                params, bar_source, regime, surveillance, counts) values (:r, '2031-01-01',
                'x', '{}', 'official', '{}', '{}', '{}')"""), {"r": rid})
        for sql in ("update watchlist_run set version = 'y' where run_id = :r",
                    "delete from watchlist_run where run_id = :r"):
            with pytest.raises(Exception, match="append-only"):
                async with engine().begin() as c:
                    await c.execute(text(sql), {"r": rid})
        await engine().dispose()
    asyncio.run(go())
