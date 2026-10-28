"""Settings from backend/.env (0600, never committed, never printed)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND / ".env", extra="ignore")

    DRISHTI_DATABASE_URL: str
    DRISHTI_PRAJNA_RO_URL: str
    DRISHTI_DATA_DIR: Path = Path("/windows/drishti_data")
    DRISHTI_UPSTOX_TOKEN_FILE: Path = Path("/home/cis/windows/prajna/backend/var/upstox_token.json")
    # share of the Upstox per-API budget (50/s, 500/min, 2000/30 min per user) Drishti may use;
    # the account is shared with Prajna (decision A)
    DRISHTI_UPSTOX_RATE_FRACTION: float = 0.3


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
