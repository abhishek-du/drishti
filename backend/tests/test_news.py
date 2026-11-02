"""Upstox news (P1c, D2). Fixture copied from Prajna tests/fixtures/upstox_news/news_3keys.json
(a real vendor response, measured 2026-09-24: 12 listings, 11 distinct articles)."""

import asyncio
import datetime as _dt
import json
from pathlib import Path

import httpx

from drishti.data import news

FIX = json.loads((Path(__file__).parent / "fixtures" / "news_3keys.json").read_text())
KEYS = sorted(FIX["data"])


def test_parse_real_fixture_dedups_articles_and_keeps_links():
    p = news.parse(FIX, KEYS, _dt.datetime.now(_dt.timezone.utc))
    assert p["problems"] == []
    assert len(p["articles"]) == FIX["metadata"]["page"]["total_records"] == 11
    assert len(p["links"]) == sum(len(v) for v in FIX["data"].values()) == 12
    a = next(iter(p["articles"].values()))
    assert a["knowable_at"] == a["published_at"] and a["headline"]


def test_path_limits():
    assert news.path(KEYS, 1).startswith("/v2/news?category=instrument_keys&instrument_keys=")
    for bad in ([], ["k"] * 31):
        try:
            news.path(bad, 1)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass


def test_fetch_store_idempotent_in_test_db():
    from sqlalchemy import text

    from drishti.broker.upstox.rest import Upstox
    from drishti.core import migrate, runlog
    from drishti.core.db import engine, sessionmaker

    api = Upstox(fraction=1.0, client=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json=FIX))))
    api._token = "test"

    async def body():
        await migrate.main(False)
        async with sessionmaker()() as s:
            rid = await runlog.start(s, "TEST_NEWS")
            await news.fetch_store(s, rid, api, KEYS)
            c2 = await news.fetch_store(s, rid, api, KEYS)
            assert c2["requests"] == 1 and c2["articles_seen"] == 11
            assert c2["articles_new"] == 0 and c2["links_new"] == 0       # append-only, no dups
            n = (await s.execute(text("""select count(*) from news_link l join news_article a
                on a.id = l.article_id where l.instrument_key = any(:k)"""), {"k": KEYS})).scalar()
            assert n == 12

    async def go():
        try:
            await body()
        finally:
            await engine().dispose()
    asyncio.run(go())
