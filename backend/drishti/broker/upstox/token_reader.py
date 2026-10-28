"""Prajna's cached Upstox token, read only (decision A, 2026-10-28).

Drishti never logs in: it opens Prajna's token file for reading, probes /v2/user/profile, and
stops with TokenUnavailable when the token is missing or invalid. The token value is never
logged, printed or written anywhere.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from drishti.core.config import get_settings

PROFILE = "https://api.upstox.com/v2/user/profile"


class TokenUnavailable(RuntimeError):
    pass


def read_token(path: Path | None = None) -> str:
    p = path or get_settings().DRISHTI_UPSTOX_TOKEN_FILE
    try:
        with p.open("rb") as f:            # read-only open; never written
            tok = json.load(f).get("access_token")
    except (OSError, ValueError) as e:
        raise TokenUnavailable(f"token file unreadable: {type(e).__name__}") from None
    if not tok:
        raise TokenUnavailable("token file has no access_token")
    return tok


def probe(token: str, client: httpx.Client) -> bool:
    r = client.get(PROFILE, headers={"Authorization": f"Bearer {token}",
                                     "Accept": "application/json"})
    return r.status_code == 200


def valid_token(client: httpx.Client, path: Path | None = None) -> str:
    tok = read_token(path)
    if not probe(tok, client):
        raise TokenUnavailable("Prajna's token is invalid (its 06:30 login has not run "
                               "or failed); Drishti does not log in")
    return tok
