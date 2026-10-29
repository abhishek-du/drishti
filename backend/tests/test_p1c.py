import json
import stat

import pytest

from drishti.broker.upstox import login
from drishti.core import config


class _R:
    def __init__(self, body=None, url="", status=200):
        self._b, self.url, self.status_code = body or {}, url, status

    def json(self):
        return self._b


class _Session:
    """Test double of the six login steps; no network."""
    def __init__(self):
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(url)
        return _R(url="https://x/cb?user_id=U1&client_id=C1")

    def post(self, url, **kw):
        self.calls.append(url)
        if "otp/generate" in url:
            return _R({"data": {"validateOTPToken": "otp-tok"}})
        if "oauth/authorize" in url:
            return _R({"data": {"redirectUri": "https://x/cb?code=CODE1"}})
        return _R({"status": "success"})

    def close(self):
        pass


@pytest.fixture
def env(monkeypatch, tmp_path):
    tok = tmp_path / "upstox_token.json"
    monkeypatch.setattr(login, "OWN_TOKEN_FILE", tok)
    monkeypatch.setattr(login, "GUARD_DIR", tmp_path / "run")
    for k, v in {"DRISHTI_UPSTOX_API_KEY": "k", "DRISHTI_UPSTOX_API_SECRET": "s",
                 "DRISHTI_UPSTOX_REDIRECT_URL": "https://example.invalid/cb",
                 "DRISHTI_UPSTOX_USERNAME": "9999999999", "DRISHTI_UPSTOX_PIN": "123456",
                 "DRISHTI_UPSTOX_TOTP_SECRET": "JBSWY3DPEHPK3PXP",
                 "DRISHTI_UPSTOX_LOGIN_ENABLED": "true",
                 "DRISHTI_UPSTOX_TOKEN_FILE": str(tok)}.items():
        monkeypatch.setenv(k, v)
    config.get_settings.cache_clear()
    yield tok
    config.get_settings.cache_clear()


def test_login_refused_until_enabled_and_never_on_prajnas_token(env, monkeypatch):
    monkeypatch.setenv("DRISHTI_UPSTOX_LOGIN_ENABLED", "false")
    config.get_settings.cache_clear()
    with pytest.raises(login.LoginRefused, match="LOGIN_ENABLED"):
        login.check_guards()
    monkeypatch.setenv("DRISHTI_UPSTOX_LOGIN_ENABLED", "true")
    monkeypatch.setenv("DRISHTI_UPSTOX_TOKEN_FILE",
                       "/home/cis/windows/prajna/backend/var/upstox_token.json")
    config.get_settings.cache_clear()
    with pytest.raises(login.LoginRefused, match="not Drishti's own"):
        login.check_guards()
    monkeypatch.setenv("DRISHTI_UPSTOX_TOKEN_FILE", str(env))
    monkeypatch.setenv("DRISHTI_UPSTOX_PIN", "")
    config.get_settings.cache_clear()
    with pytest.raises(login.LoginRefused, match="DRISHTI_UPSTOX_PIN"):
        login.check_guards()


def test_login_flow_once_a_day_token_0600_and_never_shown(env):
    posted = {}

    def post(url, **kw):
        posted["url"] = url
        return _R({"access_token": "SECRET-TOKEN-VALUE", "user_id": "U1"})

    sess = _Session()
    out = login.ensure(lambda t: t == "SECRET-TOKEN-VALUE", lambda: sess, post)
    assert out == {"status": "MINTED", "user_id": "U1"}
    assert "SECRET" not in json.dumps(out)
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert json.loads(env.read_text())["access_token"] == "SECRET-TOKEN-VALUE"
    assert posted["url"].endswith("/v2/login/authorization/token")
    assert len(sess.calls) == 5
    # cached token valid -> no second login
    assert login.ensure(lambda t: True, lambda: pytest.fail("no login"), post) == \
        {"status": "CACHED_VALID"}
    # cached token invalid, but today's attempt is spent -> refused before any network call
    with pytest.raises(login.LoginRefused, match="already attempted today"):
        login.ensure(lambda t: False, lambda: pytest.fail("no login"), post)
    assert "SECRET" not in repr(login.Token("SECRET-TOKEN-VALUE", "t"))
    assert not (config.OWN_TOKEN_FILE.exists() and "SECRET" in config.OWN_TOKEN_FILE.read_text())


def test_login_step_error_does_not_echo_the_body(env):
    class Bad(_Session):
        def post(self, url, **kw):
            return _R({"status": "error", "errors": [{"errorCode": "UDAPI1", "message": "pin 123456"}]},
                      status=401)
    with pytest.raises(login.LoginStepError) as e:
        login.ensure(lambda t: False, lambda: Bad(), lambda *a, **k: None)
    assert "123456" not in str(e.value) and "UDAPI1" in str(e.value)


def test_master_diff_events():
    from drishti.data import master
    old = {"A": {"trading_symbol": "AAA", "instrument_type": "EQ"},
           "B": {"trading_symbol": "BBB", "instrument_type": "EQ"}}
    new = {"A": {"trading_symbol": "AAA2", "instrument_type": "BE"},
           "C": {"trading_symbol": "CCC", "instrument_type": "SM"}}
    ev = {k: (e, ch) for k, e, _, ch in master.diff(old, new)}
    assert ev["A"] == ("CHANGED", {"trading_symbol": ["AAA", "AAA2"], "instrument_type": ["EQ", "BE"]})
    assert ev["B"][0] == "REMOVED" and ev["C"][0] == "ADDED"
    assert master.diff(new, new) == []


def test_board_meetings_parse_and_freshness_in_test_db():
    import asyncio
    import datetime as _dt
    import uuid

    from sqlalchemy import text

    from drishti.core import migrate
    from drishti.core.db import engine, sessionmaker
    from drishti.data import manual
    raw = ('"SYMBOL","COMPANY NAME","PURPOSE","BOARD MEETING DATE","DETAILS"\n'
           '"ABC","Abc Ltd","Financial Results","05-Nov-2026","To consider quarterly results"\n'
           '"XYZ","Xyz Ltd","Fund Raising","06-Nov-2026","Issue of NCDs"\n').encode()
    assert manual.kind_of(manual.Path("board_meetings_2026-11-02.csv")) == "BOARD_MEETINGS"
    r = manual.parse("BOARD_MEETINGS", raw)
    assert [(x["symbol"], x["event_date"], x["extra"]["results"]) for x in r] == [
        ("ABC", _dt.date(2026, 11, 5), True), ("XYZ", _dt.date(2026, 11, 6), False)]

    async def go():
        await migrate.main(False)
        rid = uuid.uuid4()
        async with sessionmaker()() as s:
            await s.execute(text("insert into run(run_id, kind, code_sha) values (:r,'TEST','x')"),
                            {"r": rid})
            await s.execute(text("""insert into manual_file(run_id, kind, path, file_date, sha256,
                row_count) values (:r, 'BOARD_MEETINGS', 'x', '2032-03-01', :h, 2)"""),
                {"r": rid, "h": uuid.uuid4().hex * 2})
            await s.commit()
            f = await manual.board_meetings_freshness(s, _dt.date(2032, 3, 5))
            assert f["BOARD_MEETINGS"]["status"] == "OK" and not f["warning"]
            f = await manual.board_meetings_freshness(s, _dt.date(2032, 3, 6))
            assert f["BOARD_MEETINGS"]["status"] == "STALE" and f["warning"]
        await engine().dispose()
    asyncio.run(go())


def test_a_rule_window(monkeypatch):
    import datetime as _dt

    from drishti.core.clock import IST
    from drishti.data import daily1d
    monkeypatch.setenv("DRISHTI_UPSTOX_LOGIN_ENABLED", "false")
    config.get_settings.cache_clear()
    t = lambda h, m: _dt.datetime(2026, 11, 2, h, m, tzinfo=IST)  # noqa: E731
    assert daily1d.a_rule_ok(t(22, 0)) and daily1d.a_rule_ok(t(3, 0)) and daily1d.a_rule_ok(t(5, 30))
    assert not daily1d.a_rule_ok(t(5, 31)) and not daily1d.a_rule_ok(t(14, 0))
    monkeypatch.setenv("DRISHTI_UPSTOX_LOGIN_ENABLED", "true")
    config.get_settings.cache_clear()
    assert daily1d.a_rule_ok(t(14, 0))
    config.get_settings.cache_clear()


def test_daily1d_store_keeps_every_fetch_in_test_db():
    import asyncio
    import datetime as _dt
    import uuid

    import httpx
    from sqlalchemy import text

    from drishti.broker.upstox.rest import Upstox
    from drishti.core import migrate, runlog
    from drishti.core.db import engine, sessionmaker
    from drishti.data import daily1d

    def handler(req):
        if "BAD" in str(req.url):
            return httpx.Response(400, json={"status": "error", "errors": [{"errorCode": "UDAPI100011"}]})
        return httpx.Response(200, json={"data": {"candles": [
            ["2031-05-06T00:00:00+05:30", 10, 12, 9, 11, 1000, 0],
            ["2031-05-05T00:00:00+05:30", 9, 10, 8, 9.5, 900, 0]]}})

    api = Upstox(fraction=1.0, client=httpx.Client(transport=httpx.MockTransport(handler)))
    api._token = "test"

    async def go():
        await migrate.main(False)
        async with sessionmaker()() as s:
            rid = await runlog.start(s, "TEST_1D")
            c = await daily1d.fetch_store(s, rid, api, ["NSE_EQ|INETEST00001", "NSE_EQ|BAD"],
                                          _dt.date(2031, 5, 6))
            assert c == {"keys": 2, "ok": 1, "empty": 0, "errors": 1, "candles": 2}
            rid2 = await runlog.start(s, "TEST_1D")
            await daily1d.fetch_store(s, rid2, api, ["NSE_EQ|INETEST00001"], _dt.date(2031, 5, 6))
            n = (await s.execute(text("""select count(*) from bar_1d
                where instrument_key = 'NSE_EQ|INETEST00001' and session_date = '2031-05-06'"""))).scalar()
            assert n >= 2                                     # both fetches kept
            e = (await s.execute(text("""select http_status, error from bar_1d_payload
                where run_id = :r and instrument_key = 'NSE_EQ|BAD'"""), {"r": rid})).one()
            assert e[0] == 400 and "UDAPI100011" in e[1]
        await engine().dispose()
    asyncio.run(go())


def test_guard_refusal_is_recorded_as_noop_in_test_db(monkeypatch):
    import asyncio

    from sqlalchemy import text

    from drishti.core import migrate
    from drishti.core.db import engine, sessionmaker
    from drishti.data import daily1d
    from drishti.run import p1
    monkeypatch.setattr(daily1d, "a_rule_ok", lambda t: False)

    async def go():
        await migrate.main(False)
        out = await p1.main("daily1d", ["--limit", "1"])
        assert out["refused"].startswith("REFUSED: decision A")
        async with sessionmaker()() as s:
            st, c = (await s.execute(text("""select e.status, e.counts->>'refused' from run r
                join run_end e using (run_id) where r.kind = 'P1_DAILY1D'
                order by r.started_at desc limit 1"""))).one()
            assert st == "NOOP" and c.startswith("REFUSED")
        await engine().dispose()
    asyncio.run(go())
