import json
import stat

import pytest

from drishti.broker.upstox import login
from drishti.core import config


class _R:
    def __init__(self, body=None, url="", status=200):
        self._b, self.url, self.status_code = body or {}, url, status

    def json(self):
        return self._b


class _Session:
    """Test double of the six login steps; no network."""
    def __init__(self):
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(url)
        return _R(url="https://x/cb?user_id=U1&client_id=C1")

    def post(self, url, **kw):
        self.calls.append(url)
        if "otp/generate" in url:
            return _R({"data": {"validateOTPToken": "otp-tok"}})
        if "oauth/authorize" in url:
            return _R({"data": {"redirectUri": "https://x/cb?code=CODE1"}})
        return _R({"status": "success"})

    def close(self):
        pass


@pytest.fixture
def env(monkeypatch, tmp_path):
    tok = tmp_path / "upstox_token.json"
    monkeypatch.setattr(login, "OWN_TOKEN_FILE", tok)
    monkeypatch.setattr(login, "GUARD_DIR", tmp_path / "run")
    for k, v in {"DRISHTI_UPSTOX_API_KEY": "k", "DRISHTI_UPSTOX_API_SECRET": "s",
                 "DRISHTI_UPSTOX_REDIRECT_URL": "https://example.invalid/cb",
                 "DRISHTI_UPSTOX_USERNAME": "9999999999", "DRISHTI_UPSTOX_PIN": "123456",
                 "DRISHTI_UPSTOX_TOTP_SECRET": "JBSWY3DPEHPK3PXP",
                 "DRISHTI_UPSTOX_LOGIN_ENABLED": "true",
                 "DRISHTI_UPSTOX_TOKEN_FILE": str(tok)}.items():
        monkeypatch.setenv(k, v)
    config.get_settings.cache_clear()
    yield tok
    config.get_settings.cache_clear()


def test_login_refused_until_enabled_and_never_on_prajnas_token(env, monkeypatch):
    monkeypatch.setenv("DRISHTI_UPSTOX_LOGIN_ENABLED", "false")
    config.get_settings.cache_clear()
    with pytest.raises(login.LoginRefused, match="LOGIN_ENABLED"):
        login.check_guards()
    monkeypatch.setenv("DRISHTI_UPSTOX_LOGIN_ENABLED", "true")
    monkeypatch.setenv("DRISHTI_UPSTOX_TOKEN_FILE",
                       "/home/cis/windows/prajna/backend/var/upstox_token.json")
    config.get_settings.cache_clear()
    with pytest.raises(login.LoginRefused, match="not Drishti's own"):
        login.check_guards()
    monkeypatch.setenv("DRISHTI_UPSTOX_TOKEN_FILE", str(env))
    monkeypatch.setenv("DRISHTI_UPSTOX_PIN", "")
    config.get_settings.cache_clear()
    with pytest.raises(login.LoginRefused, match="DRISHTI_UPSTOX_PIN"):
        login.check_guards()


def test_login_flow_once_a_day_token_0600_and_never_shown(env):
    posted = {}

    def post(url, **kw):
        posted["url"] = url
        return _R({"access_token": "SECRET-TOKEN-VALUE", "user_id": "U1"})

    sess = _Session()
    out = login.ensure(lambda t: t == "SECRET-TOKEN-VALUE", lambda: sess, post)
    assert out == {"status": "MINTED", "user_id": "U1"}
    assert "SECRET" not in json.dumps(out)
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert json.loads(env.read_text())["access_token"] == "SECRET-TOKEN-VALUE"
    assert posted["url"].endswith("/v2/login/authorization/token")
    assert len(sess.calls) == 5
    # cached token valid -> no second login
    assert login.ensure(lambda t: True, lambda: pytest.fail("no login"), post) == \
        {"status": "CACHED_VALID"}
    # cached token invalid, but today's attempt is spent -> refused before any network call
    with pytest.raises(login.LoginRefused, match="already attempted today"):
        login.ensure(lambda t: False, lambda: pytest.fail("no login"), post)
    assert "SECRET" not in repr(login.Token("SECRET-TOKEN-VALUE", "t"))
    assert not (config.OWN_TOKEN_FILE.exists() and "SECRET" in config.OWN_TOKEN_FILE.read_text())


def test_login_step_error_does_not_echo_the_body(env):
    class Bad(_Session):
        def post(self, url, **kw):
            return _R({"status": "error", "errors": [{"errorCode": "UDAPI1", "message": "pin 123456"}]},
                      status=401)
    with pytest.raises(login.LoginStepError) as e:
        login.ensure(lambda t: False, lambda: Bad(), lambda *a, **k: None)
    assert "123456" not in str(e.value) and "UDAPI1" in str(e.value)
