import asyncio
import datetime as _dt
import uuid

import httpx
import pytest

from drishti.broker.upstox import rest
from drishti.broker.upstox.token_reader import TokenUnavailable, read_token
from drishti.core import costs
from drishti.data import eod_quotes, holidays, indices, manual


def test_costs_match_the_contract_note():
    b, s = costs.buy(100_000), costs.sell(100_000)
    assert round(100_000 + b.total, 2) == 100_142.34
    assert round(100_000 - (s.total - s.dp - round(0.18 * s.dp, 2)), 2) == 99_872.66
    assert round(s.dp + 0.18 * s.dp, 2) == 23.60
    assert round(b.total + s.total, 2) == 293.28
    # slippage on top: 10 bps a side on 1 L each way = 200
    assert round(costs.round_trip(1000, 1000, 100), 2) == 493.28


def test_rest_refuses_login_endpoints():
    api = rest.Upstox(fraction=0.1, client=httpx.Client(transport=httpx.MockTransport(
        lambda r: pytest.fail(f"network call {r.url}"))))
    for url in ("https://api.upstox.com/v2/login/authorization/token",
                "https://service.upstox.com/login/open/v4/auth/1fa/otp-totp/verify",
                "/v2/login/authorization/dialog"):
        with pytest.raises(PermissionError):
            api.get(url)


def test_token_reader_errors_never_show_the_token(tmp_path):
    p = tmp_path / "t.json"
    p.write_text('{"access_token": ""}')
    with pytest.raises(TokenUnavailable):
        read_token(p)
    with pytest.raises(TokenUnavailable) as e:
        read_token(tmp_path / "missing.json")
    assert "access_token" not in str(e.value)


def test_rate_limit_fraction():
    api = rest.Upstox(fraction=0.3, client=httpx.Client())
    assert [n for _, n in api.limits] == [15, 150, 600]


def test_holidays_parse():
    rows = holidays.parse([
        {"date": "2026-01-26", "description": "Republic Day", "holiday_type": "TRADING_HOLIDAY",
         "closed_exchanges": ["NSE", "BSE"], "open_exchanges": []},
        {"date": "2026-02-01", "description": "Budget", "holiday_type": "SPECIAL_TIMING",
         "closed_exchanges": ["CDS"], "open_exchanges": [
             {"exchange": "NSE", "start_time": 1769917500000, "end_time": 1769940000000}]}])
    assert rows[0]["nse_closed"] and not rows[1]["nse_closed"]
    assert rows[1]["nse_open_from"].hour == 9 and rows[1]["nse_open_from"].minute == 15


def test_index_candles_sorted():
    r = indices.parse_candles([["2026-10-27T00:00:00+05:30", 2, 3, 1, 2.5, 0, 0],
                               ["2026-10-24T00:00:00+05:30", 1, 2, 0.5, 1.5, 0, 0]])
    assert [x["session_date"] for x in r] == [_dt.date(2026, 10, 24), _dt.date(2026, 10, 27)]


def test_manual_parsers():
    asm = ('﻿"SR. NO","SYMBOL","COMPANY NAME","ISIN","ASM STAGE"\n'
           '"Long Term","","","",""\n"1","ABC","Abc Ltd","INE000A01010","LTASM - I (13)"\n').encode()
    r = manual.parse("ASM", asm)
    assert len(r) == 1 and r[0]["isin"] == "INE000A01010" and r[0]["stage"].startswith("LTASM")
    dl = ("Symbol,ISIN,Company Name,Board,Delisted Date,Type of Delisting\n"
          "KAMALOVER,Not Available,Kamal,Main Board,38194,Compulsory Delisting\n").encode()
    r = manual.parse("DELISTED", dl)
    assert r[0]["isin"] is None and r[0]["extra"]["isin_raw"] == "Not Available"
    assert r[0]["event_date"] == _dt.date(2004, 7, 26)
    eq = ("SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, "
          "ISIN NUMBER, FACE VALUE\n20MICRONS,20 Microns,EQ,06-OCT-2008,5,1,INE144J01027,5\n"
          ).encode()
    r = manual.parse("EQUITY_L", eq)
    assert r[0]["stage"] == "EQ" and r[0]["event_date"] == _dt.date(2008, 10, 6)
    assert manual.kind_of(manual.Path("asm_2026-10-28.csv")) == "ASM"
    assert manual.file_date(manual.Path("gsm_2026-10-28.csv")) == _dt.date(2026, 10, 28)


def test_eod_quote_parse():
    r = eod_quotes.parse({"NSE_EQ:ABC": {"instrument_token": "NSE_EQ|INE000A01010",
                                         "ohlc": {"open": 1, "high": 2, "low": 0.5, "close": 1.5},
                                         "last_price": 1.6, "volume": 10,
                                         "last_trade_time": "1793175659000"}})
    assert r[0]["instrument_key"] == "NSE_EQ|INE000A01010" and r[0]["last_price"] == 1.6
    assert r[0]["quote_ts"].year == 2026


def test_append_only_in_test_db():
    from sqlalchemy import text

    from drishti.core import migrate
    from drishti.core.db import engine

    async def go():
        await migrate.main(False)
        rid = uuid.uuid4()
        async with engine().begin() as c:
            await c.execute(text("insert into run(run_id, kind, code_sha) values (:r,'TEST','x')"),
                            {"r": rid})
        for sql in ("update run set kind = 'X' where run_id = :r",
                    "delete from run where run_id = :r"):
            with pytest.raises(Exception, match="append-only"):
                async with engine().begin() as c:
                    await c.execute(text(sql), {"r": rid})
        await engine().dispose()
    asyncio.run(go())


def test_surveillance_freshness_in_test_db():
    from sqlalchemy import text

    from drishti.core import migrate
    from drishti.core.db import engine, sessionmaker

    async def go():
        await migrate.main(False)
        rid = uuid.uuid4()
        async with sessionmaker()() as s:
            await s.execute(text("insert into run(run_id, kind, code_sha) values (:r,'TEST','x')"),
                            {"r": rid})
            await s.execute(text("""insert into manual_file(run_id, kind, path, file_date, sha256,
                row_count) values (:r, 'ASM', 'x', '2031-01-01', :h, 1)"""),
                {"r": rid, "h": uuid.uuid4().hex * 2})
            await s.commit()
            f = await manual.surveillance_freshness(s, _dt.date(2031, 1, 5))
            assert f["ASM"]["status"] == "OK" and f["ASM"]["age_days"] == 4
            f = await manual.surveillance_freshness(s, _dt.date(2031, 1, 9))
            assert f["ASM"]["status"] == "STALE" and f["warning"]
            f = await manual.surveillance_freshness(s, _dt.date(2030, 12, 31))   # PIT: not yet known
            assert f["ASM"]["status"] in ("MISSING", "STALE")
        await engine().dispose()
    asyncio.run(go())
