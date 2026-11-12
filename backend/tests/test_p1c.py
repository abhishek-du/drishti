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


def test_previous_session_skips_weekend_and_short_sessions():
    import asyncio
    import datetime as _dt

    from drishti.core.db import engine, sessionmaker
    from drishti.run import p1

    async def go():
        async with sessionmaker()() as s:
            assert await p1.previous_session(s, _dt.date(2026, 11, 2)) == _dt.date(2026, 10, 30)
            assert await p1.previous_session(s, _dt.date(2025, 10, 22)) == _dt.date(2025, 10, 20)
        await engine().dispose()
    asyncio.run(go())


def test_master_marks_fo_underlyings():
    import gzip
    import json

    from drishti.data import master
    rows = [{"segment": "NSE_EQ", "instrument_key": "NSE_EQ|A", "trading_symbol": "A"},
            {"segment": "NSE_EQ", "instrument_key": "NSE_EQ|B", "trading_symbol": "B"},
            {"segment": "NSE_FO", "instrument_key": "NSE_FO|1", "underlying_key": "NSE_EQ|A",
             "underlying_type": "EQUITY"},
            {"segment": "NSE_FO", "instrument_key": "NSE_FO|2",
             "underlying_key": "NSE_INDEX|Nifty 50", "underlying_type": "INDEX"}]
    total, kept = master.parse(gzip.compress(json.dumps(rows).encode()))
    assert total == 4 and kept["NSE_EQ|A"]["fo"] is True and kept["NSE_EQ|B"]["fo"] is False


def test_history_source_flag_points_the_prajna_engine_at_the_copy(monkeypatch):
    """Re-point (2026-11-12): DRISHTI_HISTORY_SOURCE=copy makes every 'Prajna' reader use the
    D1 copy in Drishti's own DB (schema prajna_copy, read-only sessions); default unchanged."""
    from drishti.core import config, db
    config.get_settings.cache_clear()
    db.prajna_engine.cache_clear()
    try:
        assert config.get_settings().DRISHTI_HISTORY_SOURCE == "prajna_live"
        live = db.prajna_engine()
        assert live.url.database == "prajna"
        monkeypatch.setenv("DRISHTI_HISTORY_SOURCE", "copy")
        config.get_settings.cache_clear()
        db.prajna_engine.cache_clear()
        e = db.prajna_engine()
        assert e.url.database == "drishti_test"          # tests: Drishti's own (test) DB
    finally:
        monkeypatch.delenv("DRISHTI_HISTORY_SOURCE", raising=False)
        config.get_settings.cache_clear()
        db.prajna_engine.cache_clear()


def test_intraday_history_is_split_adjusted_and_the_copy_equals_live(tmp_path, monkeypatch):
    """Re-point step 2 (2026-11-12): bars.intraday adjusts splits/bonuses point in time (D5) and
    reads the same bars from the D1 Parquet copy as from Prajna's live DB."""
    import asyncio
    import datetime as dt
    from pathlib import Path
    import pytest
    from drishti.core import config, db
    from drishti.data import bars
    dry = Path("/windows/drishti_data/prajna_copy_dryrun")
    if not (dry / "15m").is_dir():
        pytest.skip("no dry-run copy on this host")

    async def go():
        async with db.prajna_sessionmaker()() as ps:
            k, ex = "NSE_EQ|INE338H01029", dt.date(2026, 11, 4)          # 1:2 split, ex 11-04
            a = await bars.intraday(ps, [k], "15m", ex - dt.timedelta(days=6))
            r = await bars.intraday(ps, [k], "15m", ex - dt.timedelta(days=6), adjusted=False)
            m = a.merge(r, on="t", suffixes=("_a", "_r"))
            pre, post = m[m.session_date_a < ex], m[m.session_date_a >= ex]
            assert len(pre) > 0 and len(post) > 0
            assert abs((pre.close_r / pre.close_a).median() - 2.0) < 1e-9
            assert abs((post.close_r / post.close_a).median() - 1.0) < 1e-9
            live = await bars.intraday(ps, ["NSE_EQ|INE002A01018", "NSE_EQ|INE009A01021"], "15m",
                                       dt.date(2026, 9, 1), adjusted=False)
        live = live[live.session_date <= dt.date(2026, 9, 30)].reset_index(drop=True)
        await db.prajna_engine().dispose()
        # the same read on the copy (drishti_test's prajna_copy schema + the dry-run Parquet)
        (tmp_path / "prajna_copy").symlink_to(dry)
        monkeypatch.setenv("DRISHTI_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("DRISHTI_HISTORY_SOURCE", "copy")
        config.get_settings.cache_clear()
        db.prajna_engine.cache_clear()
        try:
            async with db.prajna_sessionmaker()() as cp:
                cpy = await bars.intraday(cp, ["NSE_EQ|INE002A01018", "NSE_EQ|INE009A01021"],
                                          "15m", dt.date(2026, 9, 1), adjusted=False)
            await db.prajna_engine().dispose()
        finally:
            config.get_settings.cache_clear()
            db.prajna_engine.cache_clear()
        cpy = cpy[cpy.session_date <= dt.date(2026, 9, 30)].reset_index(drop=True)
        assert len(live) > 100 and len(live) == len(cpy)
        for c in ("open", "high", "low", "close", "volume"):
            assert (live[c].round(4) == cpy[c].round(4)).all(), c
        assert (live.t == cpy.t).all()

    asyncio.run(go())


def test_history_union_adds_own_bars_after_the_cutover_only(monkeypatch):
    """Re-point step 3 (2026-11-12): in copy mode the daily series = the D1 copy up to the cutover
    + Drishti's own newest 1d fetch after it; nothing before the cutover changes."""
    import asyncio
    import datetime as dt
    import random
    import pytest
    from sqlalchemy import text
    from drishti.core import config, db
    from drishti.data import bars
    from drishti.switch import repoint

    try:
        info = repoint.make_history_views("drishti_test")
    except Exception as e:                       # no dry-run copy schema on this host
        pytest.skip(f"no prajna_copy in drishti_test: {e}")
    cut = dt.date.fromisoformat(info["cutover"])
    day = cut + dt.timedelta(days=1)
    while day.weekday() > 4:
        day += dt.timedelta(days=1)
    key = "NSE_EQ|INE002A01018"
    px = round(1000 + random.random() * 100, 2)

    async def go():
        async with db.sessionmaker()() as s:
            rid = (await s.execute(text("""insert into run(run_id, kind, code_sha, params) values
                (gen_random_uuid(), 'TEST_HIST', 't', '{}') returning run_id"""))).scalar()
            pid = (await s.execute(text("""insert into bar_1d_payload(run_id, instrument_key,
                req_from, req_to, http_status, sha256, n_candles, basis_as_of) values (:r, :k, :d,
                :d, 200, md5(random()::text) || md5(random()::text), 1, :d) returning id"""),
                {"r": rid, "k": key, "d": day})).scalar()
            for close in (px - 5, px):                  # two fetches: the newest one wins
                await s.execute(text("""insert into bar_1d(payload_id, instrument_key,
                    session_date, open, high, low, close, volume, fetched_at) values (:p, :k, :d,
                    :c, :c, :c, :c, 100, clock_timestamp())"""),
                    {"p": pid, "k": key, "d": day, "c": close})
            await s.commit()
        await db.engine().dispose()
        monkeypatch.setenv("DRISHTI_HISTORY_SOURCE", "copy")
        config.get_settings.cache_clear()
        db.prajna_engine.cache_clear()
        try:
            async with db.prajna_sessionmaker()() as cp:
                b = await bars.official(cp, day, 40, keys=[key])
            await db.prajna_engine().dispose()
        finally:
            config.get_settings.cache_clear()
            db.prajna_engine.cache_clear()
            db.engine.cache_clear()
        mine = b[b.session_date == day]
        assert len(mine) == 1 and float(mine.close.iloc[0]) == px      # own bar, newest fetch
        assert (b.session_date <= cut).sum() > 0                        # the copy part is there

    asyncio.run(go())


def test_history_union_counts_a_session_and_a_ca_once():
    """P4 findings (2026-11-12): with a cutover before the copy's end, the copy's bars and CA
    factors after the cutover must not be read (they were read next to Drishti's own, so a split
    was applied twice); Drishti's append-only ca_factor gives the newest row per event; an
    event fetched under an issuer's old and new ISIN (same content sha) counts once."""
    import uuid
    import pytest
    from drishti.switch import d1copy as D
    from drishti.switch import repoint

    try:
        cut = repoint.make_history_views("drishti_test", "2026-09-15")["cutover"]
    except Exception as e:                       # no dry-run copy schema on this host
        pytest.skip(f"no prajna_copy in drishti_test: {e}")
    q = lambda sql: D.sql("drishti_test", sql, False)  # noqa: E731
    try:
        assert cut == "2026-09-15"
        n = q("""select count(*) from history.ohlcv_bar where session_date > '2026-09-15'
                 and source <> 'DRISHTI_UPSTOX_V3'""")[0][0]
        assert int(n) == 0
        n = q("""select count(*) from history.canon_ca_factor where ca_id < 1000000000
                 and ex_date > '2026-09-15'""")[0][0]
        assert int(n) == 0
        sha = uuid.uuid4().hex * 2
        ids = q(f"""with r as (insert into run(run_id, kind, code_sha, params) values
              (gen_random_uuid(), 'TEST_HIST_CA', 't', '{{}}') returning run_id),
            p as (insert into ca_payload(run_id, isin, http_status) select run_id, 'INEZZ9T01018',
              200 from r returning id, run_id),
            c as (insert into corporate_action(payload_id, isin, instrument_key, action_type,
              ex_date, content_sha256, vendor_payload, first_seen_at, knowable_at)
              select p.id, v.isin, 'NSE_EQ|' || v.isin, 'SPLIT', date '2033-03-03', '{sha}',
                '{{}}'::jsonb, v.t, v.t from p, (values ('INEZZ9T01018', now() - interval '1 day'),
                ('INEZZ9T01026', now())) v(isin, t) returning id, isin)
            select c.id, c.isin, p.run_id from c, p order by c.isin""")
        for ca, _isin, rid in ids:
            for va in ("UNKNOWN", "APPLIED"):          # a re-derivation adds a row
                q(f"""insert into ca_factor(ca_id, run_id, status, method, factor_price,
                      factor_volume, vendor_applied, reason, method_version) values
                      ({ca}, '{rid}', 'EXACT', 'SPLIT_FV', 5, 5, '{va}', 't', 'cafactor-v1')""")
        own = ",".join(str(int(ca) + 1000000000) for ca, _i, _r in ids)
        rows = q(f"""select substr(instrument_key, 8), vendor_applied
                     from history.canon_ca_factor where ca_id in ({own})""")
        assert [tuple(r) for r in rows] == [("INEZZ9T01026", "APPLIED")]
    finally:
        repoint.make_history_views("drishti_test")
