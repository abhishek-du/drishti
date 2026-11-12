"""Two engines: Drishti's own database (read-write, role drishti_app) and Prajna's database
(read only: role drishti_ro has SELECT only and default_transaction_read_only = on)."""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from drishti.core.config import get_settings


@lru_cache
def engine() -> AsyncEngine:
    return create_async_engine(get_settings().DRISHTI_DATABASE_URL, pool_pre_ping=True)


COPY_SCHEMA = "prajna_copy"


@lru_cache
def prajna_engine() -> AsyncEngine:
    """Prajna's history: its live DB (read-only role) or, after the re-point, the verified D1
    copy inside Drishti's own DB (schema prajna_copy, read-only sessions). Every reader keeps
    its queries; only the connection changes."""
    cfg = get_settings()
    if cfg.DRISHTI_HISTORY_SOURCE == "copy":
        return copy_engine(cfg.DRISHTI_DATABASE_URL)
    return create_async_engine(cfg.DRISHTI_PRAJNA_RO_URL, pool_pre_ping=True)


def copy_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True, connect_args={"server_settings": {
        "search_path": f"history,{COPY_SCHEMA},public", "default_transaction_read_only": "on"}})


def sessionmaker() -> async_sessionmaker:
    return async_sessionmaker(engine(), expire_on_commit=False)


def prajna_sessionmaker() -> async_sessionmaker:
    return async_sessionmaker(prajna_engine(), expire_on_commit=False)
