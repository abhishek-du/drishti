"""Two engines: Drishti's own database (read-write, role drishti_app) and Prajna's database
(read only: role drishti_ro has SELECT only and default_transaction_read_only = on)."""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from drishti.core.config import get_settings


@lru_cache
def engine() -> AsyncEngine:
    return create_async_engine(get_settings().DRISHTI_DATABASE_URL, pool_pre_ping=True)


@lru_cache
def prajna_engine() -> AsyncEngine:
    return create_async_engine(get_settings().DRISHTI_PRAJNA_RO_URL, pool_pre_ping=True)


def sessionmaker() -> async_sessionmaker:
    return async_sessionmaker(engine(), expire_on_commit=False)


def prajna_sessionmaker() -> async_sessionmaker:
    return async_sessionmaker(prajna_engine(), expire_on_commit=False)
