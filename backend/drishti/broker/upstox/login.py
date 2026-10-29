"""Drishti's own Upstox TOTP login (Option B, user 2026-10-29: one automated login per day).

Provenance: the six-step flow, endpoints, headers and payload shapes are copied from Prajna
`app/vendor/upstox/auth.py` (Prajna commit 25712fc), which was itself verified live. Drishti
never imports Prajna code.

Guards (every one must hold, or LoginRefused is raised before any network call):
  - DRISHTI_UPSTOX_LOGIN_ENABLED is true (false until the switch day, S+1);
  - DRISHTI_UPSTOX_TOKEN_FILE is Drishti's own file (never Prajna's);
  - every DRISHTI_UPSTOX_* credential is set;
  - no automated login attempt yet today (IST): var/run/upstox_login_<date>.json.
The cached token is probed first and a login happens only when the probe fails. The token is
written to the cache with mode 0600 and is never logged, printed or returned to a caller that
prints. This is the only Drishti module allowed to call login URLs (rest.py refuses them).
"""

from __future__ import annotations

import base64
import json
import random
import string
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from drishti.core.clock import now
from drishti.core.config import BACKEND, LOGIN_FIELDS, OWN_TOKEN_FILE, get_settings

API = "https://api.upstox.com"
SERVICE = "https://service.upstox.com"
LOGIN = "https://login.upstox.com"
UPSTOX_INTERNAL_REDIRECT = "https://api-v2.upstox.com/login/authorization/redirect"
STEP_TIMEOUT = 30
GUARD_DIR = BACKEND / "var" / "run"


class LoginRefused(RuntimeError):
    pass


class LoginStepError(RuntimeError):
    def __init__(self, step: str, detail: str):
        super().__init__(f"{step}: {detail}")
        self.step = step


@dataclass
class Token:
    access_token: str
    minted_at: str
    user_id: str | None = None

    def __repr__(self) -> str:                       # never show the token
        return f"Token(minted_at={self.minted_at!r}, user_id={self.user_id!r})"


def check_guards(today: str | None = None) -> Path:
    """Raise LoginRefused unless a login may happen now. Returns the guard file of today."""
    s = get_settings()
    if not s.DRISHTI_UPSTOX_LOGIN_ENABLED:
        raise LoginRefused("DRISHTI_UPSTOX_LOGIN_ENABLED is false (login starts on the switch day)")
    if Path(s.DRISHTI_UPSTOX_TOKEN_FILE).resolve() != OWN_TOKEN_FILE.resolve():
        raise LoginRefused("DRISHTI_UPSTOX_TOKEN_FILE is not Drishti's own token file")
    missing = [f for f in LOGIN_FIELDS if not getattr(s, f)]
    if missing:
        raise LoginRefused(f"missing in .env: {', '.join(missing)}")
    guard = GUARD_DIR / f"upstox_login_{today or now().date().isoformat()}.json"
    if guard.exists():
        raise LoginRefused(f"an automated login was already attempted today ({guard.name})")
    return guard


def _request_id() -> str:
    return "WPRO-" + "".join(random.choices(string.ascii_letters + string.digits, k=10))


def _browser_headers(rid: str) -> dict[str, str]:
    ua = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
    return {
        "accept": "*/*", "accept-language": "en-GB,en;q=0.9",
        "content-type": "application/json", "origin": LOGIN, "referer": LOGIN,
        "sec-fetch-dest": "empty", "sec-fetch-mode": "cors", "sec-fetch-site": "same-site",
        "user-agent": ua,
        "x-device-details": (
            "platform=WEB|osName=Mac OS/10.15.7|osVersion=Chrome/140.0.0.0|"
            "appVersion=4.0.0|modelName=Chrome|manufacturer=Apple|"
            f"uuid=3Z1IVTlV4rUUGbNp8KP0|userAgent=Upstox 3.0 {ua}"),
        "x-request-id": rid,
    }


def _ok(r, step: str) -> dict:
    try:
        body = r.json()
    except Exception:
        raise LoginStepError(step, f"HTTP {r.status_code}, non-JSON body") from None
    if r.status_code >= 400 or body.get("status") == "error":
        errs = body.get("errors") or body.get("error") or {}
        codes = [e.get("errorCode") or e.get("error_code") for e in errs] \
            if isinstance(errs, list) else []
        raise LoginStepError(step, f"HTTP {r.status_code} {codes}")   # no body echo
    return body


def mint(session, post) -> Token:
    """The six steps. `session` is a curl_cffi Session (or a test double) for steps 1-5 and
    `post` a fresh-session POST for step 6, so the login cookies never reach the token URL."""
    import pyotp
    s = get_settings()
    r = session.get(f"{API}/v2/login/authorization/dialog",
                    params={"response_type": "code", "client_id": s.DRISHTI_UPSTOX_API_KEY,
                            "redirect_uri": s.DRISHTI_UPSTOX_REDIRECT_URL},
                    allow_redirects=True, timeout=STEP_TIMEOUT)
    q = parse_qs(urlparse(r.url).query)
    if not (q.get("user_id") and q.get("client_id")):
        raise LoginStepError("1-dialog", "redirect lacked user_id/client_id")
    user_id, client_id = q["user_id"][0], q["client_id"][0]
    body = _ok(session.post(f"{SERVICE}/login/open/v6/auth/1fa/otp/generate",
                            json={"data": {"mobileNumber": s.DRISHTI_UPSTOX_USERNAME,
                                           "userId": user_id}}, timeout=STEP_TIMEOUT),
               "2-otp-generate")
    otp_token = (body.get("data") or {}).get("validateOTPToken")
    if not otp_token:
        raise LoginStepError("2-otp-generate", "no validateOTPToken")
    _ok(session.post(f"{SERVICE}/login/open/v4/auth/1fa/otp-totp/verify",
                     json={"data": {"otp": pyotp.TOTP(s.DRISHTI_UPSTOX_TOTP_SECRET).now(),
                                    "validateOtpToken": otp_token}}, timeout=STEP_TIMEOUT),
        "3-totp-verify")
    pin_b64 = base64.b64encode(s.DRISHTI_UPSTOX_PIN.encode()).decode()
    _ok(session.post(f"{SERVICE}/login/open/v3/auth/2fa",
                     params={"client_id": client_id, "redirect_uri": UPSTOX_INTERNAL_REDIRECT},
                     json={"data": {"twoFAMethod": "SECRET_PIN", "inputText": pin_b64}},
                     allow_redirects=True, timeout=STEP_TIMEOUT), "4-pin")
    rid = _request_id()
    body = _ok(session.post(f"{SERVICE}/login/v2/oauth/authorize",
                            params={"client_id": client_id,
                                    "redirect_uri": UPSTOX_INTERNAL_REDIRECT,
                                    "requestId": rid, "response_type": "code"},
                            json={"data": {"userOAuthApproval": True}},
                            allow_redirects=True, timeout=STEP_TIMEOUT), "5-oauth")
    code = parse_qs(urlparse((body.get("data") or {}).get("redirectUri", "")).query).get(
        "code", [None])[0]
    if not code:
        raise LoginStepError("5-oauth", "no code in redirectUri")
    body = _ok(post(f"{API}/v2/login/authorization/token",
                    data={"code": code, "client_id": s.DRISHTI_UPSTOX_API_KEY,
                          "client_secret": s.DRISHTI_UPSTOX_API_SECRET,
                          "redirect_uri": s.DRISHTI_UPSTOX_REDIRECT_URL,
                          "grant_type": "authorization_code"},
                    headers={"accept": "application/json",
                             "content-type": "application/x-www-form-urlencoded"},
                    timeout=STEP_TIMEOUT), "6-token")
    if not body.get("access_token"):
        raise LoginStepError("6-token", "no access_token")
    return Token(body["access_token"], now().isoformat(), body.get("user_id"))


def save(tok: Token, path: Path | None = None) -> None:
    path = path or OWN_TOKEN_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.touch(mode=0o600)
    tmp.chmod(0o600)
    tmp.write_text(json.dumps({"access_token": tok.access_token, "minted_at": tok.minted_at,
                               "user_id": tok.user_id}))
    tmp.replace(path)


def ensure(probe, session_factory, post) -> dict:
    """Probe the cached token; log in (once a day, guarded) only when it is invalid.
    Returns a status dict without the token."""
    from drishti.broker.upstox.token_reader import TokenUnavailable, read_token
    try:
        if probe(read_token(OWN_TOKEN_FILE)):
            return {"status": "CACHED_VALID"}
    except TokenUnavailable:
        pass
    guard = check_guards()
    guard.parent.mkdir(parents=True, exist_ok=True)
    guard.write_text(json.dumps({"attempted_at": now().isoformat()}))  # before the network
    sess = session_factory()
    try:
        tok = mint(sess, post)
    finally:
        sess.close()
    if not probe(tok.access_token):
        raise LoginStepError("probe", "the new token failed its own probe")
    save(tok)
    guard.write_text(json.dumps({"attempted_at": json.loads(guard.read_text())["attempted_at"],
                                 "ok_at": now().isoformat(), "user_id": tok.user_id}))
    return {"status": "MINTED", "user_id": tok.user_id}


def main() -> int:
    """CLI: .venv/bin/python -m drishti.broker.upstox.login (runbook drishti_login.sh)."""
    import httpx
    from curl_cffi import requests as curl

    from drishti.broker.upstox.token_reader import probe as _probe
    with httpx.Client(timeout=15) as c:
        out = ensure(lambda t: _probe(t, c),
                     lambda: curl.Session(impersonate="chrome131",
                                          headers=_browser_headers(_request_id())),
                     lambda url, **kw: curl.post(url, impersonate="chrome131", **kw))
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    import sys
    try:
        sys.exit(main())
    except (LoginRefused, LoginStepError) as e:
        print(json.dumps({"status": "REFUSED" if isinstance(e, LoginRefused) else "FAILED",
                          "reason": str(e)}))
        sys.exit(3)
