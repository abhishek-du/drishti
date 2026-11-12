"""'Analysis now' (decision support): label, plan geometry, point-in-time odds."""

import asyncio
import datetime as dt

from drishti.analysis import now as A


def test_analysis_now_plan_and_point_in_time_odds():
    from drishti.core import db

    async def go():
        try:
            async with db.sessionmaker()() as s, db.prajna_sessionmaker()() as ps:
                from drishti.api import chart_data
                r = await A.analyse(s, ps, "NSE_EQ|INE002A01018", chart_data, ltp=1300.0,
                                    n_paths=500)
                assert "not a prediction" in r["label"]
                p = r["plan_if_bought_now"]
                assert p["entry"] == 1300.0 and p["stop"] < p["entry"] < p["target"]
                assert p["qty_1pct_of_10L"] * p["risk_per_share"] <= 10_000 + 1e-6
                assert p["target_basis"] in ("R1", "R2", "2R")
                o = r["odds"]
                assert "mc" in o and o["pool"]["last"] < str(dt.date.today())    # bars < today
                assert isinstance(o["paper_gate_pass"], bool)
                assert r["context"]["trend"] in ("UP", "DOWN", "SIDE")
                assert r["events"]["code"] in (None, "SKIP_RESULTS_UNKNOWN", "SKIP_RESULTS_VETO")
        finally:
            await db.engine().dispose()
            db.engine.cache_clear()
            await db.prajna_engine().dispose()
            db.prajna_engine.cache_clear()

    asyncio.run(go())
