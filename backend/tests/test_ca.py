"""Corporate actions (P1c). Fixture copied from Prajna tests/fixtures/upstox_corporate_actions/
split_bonus_INE838B01021.json (a real vendor response, measured 2026-09-24)."""

import asyncio
import datetime as _dt
import json
from decimal import Decimal
from pathlib import Path

import httpx

from drishti.core.clock import IST
from drishti.data import corporate_actions as ca

FIX = json.loads((Path(__file__).parent / "fixtures" / "ca_split_bonus_INE838B01021.json")
                 .read_text())


def test_parse_and_factors_on_the_real_fixture():
    evs, probs = ca.parse(FIX, "INE838B01021")
    assert probs == []
    by = {e["action_type"]: e for e in evs}
    s, b = by["SPLIT"], by["BONUS"]
    assert s["ex_date"] == _dt.date(2025, 12, 12) and s["announcement_date"] == _dt.date(2025, 10, 24)
    assert ca.content_sha256(s["vendor_payload"]) == s["content_sha256"]
    fs = ca.factor_for("SPLIT", s["vendor_payload"]["ratio"], s["face_value_before"],
                       s["face_value_after"])
    fb = ca.factor_for("BONUS", b["vendor_payload"]["ratio"], None, None)
    assert (fs["status"], fs["f"]) == ("EXACT", Decimal(2))
    assert (fb["status"], fb["f"]) == ("EXACT", Decimal(2))
    assert ca.factor_for("SPLIT", "2:10", Decimal(10), Decimal(5))["status"] == "UNCERTAIN"
    assert ca.factor_for("DIVIDEND", None, None, None)["status"] == "UNSUPPORTED"
    seen = _dt.datetime(2026, 11, 2, 10, 0, tzinfo=IST)
    assert ca.knowable_at(_dt.date(2025, 10, 24), seen) == seen          # seen later: seen
    early = _dt.datetime(2025, 10, 24, 9, 0, tzinfo=IST)
    assert ca.knowable_at(_dt.date(2025, 10, 24), early).hour == 23       # end of that day


def test_vendor_treatment_cases():
    x = _dt.date(2026, 1, 10)
    assert ca.vendor_treatment(Decimal(2), 200, 101, _dt.date(2026, 1, 20), x)[0] == "NOT_APPLIED"
    assert ca.vendor_treatment(Decimal(2), 100, 101, _dt.date(2026, 1, 20), x)[0] == "APPLIED"
    assert ca.vendor_treatment(Decimal(2), 200, 101, _dt.date(2026, 1, 5), x)[0] == "UNKNOWN"
    assert ca.vendor_treatment(Decimal(2), 200, 101, _dt.date(2026, 1, 5), x, 3)[0] == "APPLIED"
    assert ca.vendor_treatment(Decimal("1.1"), 110, 100, _dt.date(2026, 1, 20), x)[0] == "UNKNOWN"
    assert ca.vendor_treatment(None, 1, 1, None, x)[0] == "N/A"


def test_fetch_store_derive_idempotent_in_test_db():
    from sqlalchemy import text

    from drishti.broker.upstox.rest import Upstox
    from drishti.core import migrate, runlog
    from drishti.core.db import engine, sessionmaker

    api = Upstox(fraction=1.0, client=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json=FIX))))
    api._token = "test"
    import uuid
    isin = "INT" + uuid.uuid4().hex[:9].upper()       # fresh per run: drishti_test is append-only

    async def go():
        try:
            await body()
        finally:
            await engine().dispose()

    async def body():
        await migrate.main(False)
        async with sessionmaker()() as s:
            rid = await runlog.start(s, "TEST_CA")
            c1 = await ca.fetch_store(s, rid, api, [isin], {isin: "NSE_EQ|" + isin})
            c2 = await ca.fetch_store(s, rid, api, [isin], {isin: "NSE_EQ|" + isin})
            assert c1["events_new"] == len(FIX["data"]) and c2["events_new"] == 0
            d1 = await ca.derive(s, rid)
            d2 = await ca.derive(s, rid)
            assert d2["new_rows"] == 0                                  # unchanged: no new row
            st = (await s.execute(text("""select f.status, f.factor_price from ca_factor f
                join corporate_action c on c.id = f.ca_id where c.isin = :i
                and c.action_type = 'SPLIT'"""), {"i": isin})).all()
            assert [(a, float(b)) for a, b in st] == [("EXACT", 2.0)]
            assert d1["new_rows"] >= 2
    asyncio.run(go())
