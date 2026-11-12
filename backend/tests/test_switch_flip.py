"""flip.py: the switch flags in .env; values never printed; refuses without credentials."""
import json

import pytest

from drishti.core.config import LOGIN_FIELDS, OWN_TOKEN_FILE
from drishti.switch import flip


def _env(tmp_path, creds=True):
    p = tmp_path / ".env"
    lines = ["DRISHTI_DATABASE_URL=postgresql+asyncpg://u:ZQXVPW@h/db",
             "DRISHTI_UPSTOX_TOKEN_FILE=/home/cis/windows/prajna/backend/var/upstox_token.json",
             "# a comment"]
    if creds:
        lines += [f"{k}=VAL_{i}_ZQXV" for i, k in enumerate(LOGIN_FIELDS)]
    p.write_text("\n".join(lines) + "\n")
    p.chmod(0o600)
    return p


def test_apply_flips_and_backs_up_without_printing_values(tmp_path):
    env = _env(tmp_path)
    out = flip.apply(env)
    assert "ZQXV" not in json.dumps(out)
    v = flip._vals(flip._read(env))
    assert v["DRISHTI_HISTORY_SOURCE"] == "copy" and v["DRISHTI_UPSTOX_LOGIN_ENABLED"] == "true"
    assert v["DRISHTI_UPSTOX_TOKEN_FILE"] == str(OWN_TOKEN_FILE)
    assert v["DRISHTI_DATABASE_URL"].endswith("ZQXVPW@h/db") and "# a comment" in env.read_text()
    assert oct(env.stat().st_mode & 0o777) == "0o600"
    back = flip.rollback(env, tmp_path / out["backup"].rsplit("/", 1)[1])
    assert back["DRISHTI_HISTORY_SOURCE"] == "(default)" and back["token_file"] == "prajna/other"


def test_apply_refuses_without_credentials(tmp_path):
    env = _env(tmp_path, creds=False)
    before = env.read_text()
    with pytest.raises(SystemExit, match="missing"):
        flip.apply(env)
    assert env.read_text() == before and not list(tmp_path.glob(".env.pre_switch_*"))


def test_apply_twice_is_refused(tmp_path):
    env = _env(tmp_path)
    flip.apply(env)
    with pytest.raises(SystemExit, match="already flipped"):
        flip.apply(env)
    assert len(list(tmp_path.glob(".env.pre_switch_*"))) == 1
