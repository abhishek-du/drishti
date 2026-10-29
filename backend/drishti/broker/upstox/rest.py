"""Rate-limited Upstox REST client for Drishti (read-only market data).

Upstox limits are per API per user: 50/s, 500/min, 2000/30 min. The account is shared with
Prajna, so Drishti takes only a fraction of each window (DRISHTI_UPSTOX_RATE_FRACTION).
A 429 is never retried: RateLimited is raised and the caller stops. Login endpoints are
refused outright: Drishti never logs in.
"""

from __future__ import annotations

import time
from collections import deque

import httpx

from drishti.broker.upstox.token_reader import valid_token
from drishti.core.config import get_settings

API = "https://api.upstox.com"
WINDOWS = ((1.0, 50), (60.0, 500), (1800.0, 2000))
FORBIDDEN = ("/login", "/oauth", "/authorization", "service.upstox.com")


class RateLimited(RuntimeError):
    pass


class Upstox:
    def __init__(self, fraction: float | None = None, client: httpx.Client | None = None):
        f = fraction if fraction is not None else get_settings().DRISHTI_UPSTOX_RATE_FRACTION
        self.limits = [(w, max(1, int(n * f))) for w, n in WINDOWS]
        self.calls: deque[float] = deque()
        self.client = client or httpx.Client(timeout=30)
        self._token: str | None = None

    def _wait(self) -> None:
        while True:
            t = time.monotonic()
            while self.calls and t - self.calls[0] > WINDOWS[-1][0]:
                self.calls.popleft()
            waits = []
            for w, n in self.limits:
                inside = [c for c in self.calls if t - c <= w]
                if len(inside) >= n:
                    waits.append(w - (t - inside[0]) + 0.01)
            if not waits:
                self.calls.append(t)
                return
            time.sleep(max(waits))

    def get(self, path: str, params: dict | None = None) -> dict:
        r = self.get_response(path, params)
        r.raise_for_status()
        return r.json()

    def get_response(self, path: str, params: dict | None = None) -> httpx.Response:
        """The raw response (the caller checks the status and keeps the body's sha256)."""
        url = path if path.startswith("http") else f"{API}{path}"
        if any(x in url for x in FORBIDDEN):
            raise PermissionError("Drishti never calls Upstox login endpoints")
        if self._token is None:
            self._token = valid_token(self.client)
        self._wait()
        r = self.client.get(url, params=params, headers={
            "Authorization": f"Bearer {self._token}", "Accept": "application/json"})
        if r.status_code == 429:
            raise RateLimited(url)
        return r
