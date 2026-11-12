"""'Analysis now' API (read only; decision support, not a prediction)."""

from __future__ import annotations

from fastapi import APIRouter

from drishti.analysis import now as A
from drishti.api import chart_data
from drishti.core.db import prajna_sessionmaker, sessionmaker

router = APIRouter()


@router.get("/api/analysis/now")
async def analysis_now(key: str) -> dict:
    async with sessionmaker()() as s, prajna_sessionmaker()() as ps:
        return await A.analyse(s, ps, key, chart_data)
