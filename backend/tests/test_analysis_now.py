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


def test_rule_verdict_tags():
    base = {"context": {"regime": "ON", "spec_gate": "ON"},
            "plan_if_bought_now": {"net_rr": 1.4, "qty_1pct_of_10L": 100, "entry": 100,
                                   "stop": 95, "target": 110, "target_basis": "2R",
                                   "horizon_sessions": 5},
            "odds": {"paper_gate_pass": True, "thin_sample": False, "replay_n": 300,
                     "breakeven": 0.4, "verdict": "Net R:R ... clear",
                     "mc": {"avg_r_net": 0.12, "median_r_net": 0.1, "target_first": 0.4,
                            "stop_first": 0.3, "neither": 0.3, "p05_r_net": -1.2,
                            "p95_r_net": 1.9}},
            "events": {"results_in_hold": False, "code": None, "why": "none"},
            "scanner": {"alerts_today": []}}
    v = A.verdicts(base)
    assert v["1d"]["tag"] == "RULES: BUY ✓" and v["15m"]["tag"] == "NO 15m RULE YET"
    assert v["5m"]["tag"] == "NO RULE" and len(v["1d"]["time_machine"]) == 5
    bad = {**base, "odds": {**base["odds"], "paper_gate_pass": False,
                            "mc": {**base["odds"]["mc"], "avg_r_net": -0.06}}}
    v = A.verdicts(bad)
    assert v["1d"]["tag"] == "RULES: DON'T BUY ✗" and "odds gate" in v["1d"]["reasons"][0]
    unk = {**base, "events": {"results_in_hold": True, "code": "SKIP_RESULTS_UNKNOWN", "why": "x"}}
    assert any("UNKNOWN" in r for r in A.verdicts(unk)["1d"]["reasons"])
