"""Tests for AI Trading Intelligence breakdown (user 2026-11-13)."""

import asyncio

from drishti.analysis import ai_agent as AI
from drishti.api import chart_data
from drishti.core import db


def test_ai_agent_compilation_and_markdown():
    async def go():
        try:
            async with db.sessionmaker()() as s, db.prajna_sessionmaker()() as ps:
                res = await AI.get_ai_analysis(s, ps, "NSE_EQ|INE385F01024", chart_data, force=True)
                assert res["symbol"] == "SGMART"
                assert "engine" in res
                assert "markdown" in res
                md = res["markdown"]
                assert "### 1. SGMART Kyun Strong / Weak Hai?" in md
                assert "### 2. Iska Catch Kya Hai?" in md
                assert "### 3. Final Verdict: Kya Ise Abhi Buy Karna Chahiye?" in md
                assert "Option A:" in md
                assert "Option B:" in md
                assert "facts" in res
                f = res["facts"]
                assert "trends" in f
                assert "technical" in f
                assert "plan" in f
                assert "time_machine" in f
        finally:
            await db.engine().dispose()
            db.engine.cache_clear()
            await db.prajna_engine().dispose()
            db.prajna_engine.cache_clear()

    asyncio.run(go())
