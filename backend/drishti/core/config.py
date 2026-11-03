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
    # Option B (user 2026-10-29): Drishti's own Upstox app and daily TOTP login. The login is
    # refused until DRISHTI_UPSTOX_LOGIN_ENABLED is true (set on the switch day, S+1) and the
    # token file is Drishti's own. Values live only in .env (0600) and are never printed.
    DRISHTI_UPSTOX_API_KEY: str = ""
    DRISHTI_UPSTOX_API_SECRET: str = ""
    DRISHTI_UPSTOX_REDIRECT_URL: str = ""
    DRISHTI_UPSTOX_USERNAME: str = ""
    DRISHTI_UPSTOX_PIN: str = ""
    DRISHTI_UPSTOX_TOTP_SECRET: str = ""
    DRISHTI_UPSTOX_LOGIN_ENABLED: bool = False
    # Decision-support scanner (user 2026-11-03): position size at RISK_PCT of CAPITAL. Paper only.
    DRISHTI_SCAN_CAPITAL: float = 1_000_000.0
    DRISHTI_SCAN_RISK_PCT: float = 1.0
    # Telegram (optional): without both, notifications are only logged. Never printed.
    DRISHTI_TELEGRAM_BOT_TOKEN: str = ""
    DRISHTI_TELEGRAM_CHAT_ID: str = ""


OWN_TOKEN_FILE = BACKEND / "var" / "upstox_token.json"
LOGIN_FIELDS = ("DRISHTI_UPSTOX_API_KEY", "DRISHTI_UPSTOX_API_SECRET",
                "DRISHTI_UPSTOX_REDIRECT_URL", "DRISHTI_UPSTOX_USERNAME", "DRISHTI_UPSTOX_PIN",
                "DRISHTI_UPSTOX_TOTP_SECRET")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
