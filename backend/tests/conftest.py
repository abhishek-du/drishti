import os

import pytest

# tests never touch the production database: point Drishti at drishti_test
_env = dict(line.strip().split("=", 1) for line in open(
    os.path.join(os.path.dirname(__file__), "..", ".env")) if "=" in line)
# a track worktree (docs/TRACKS.md) has its own test URL (role drishti_tracks on drishti_test)
os.environ["DRISHTI_DATABASE_URL"] = _env.get("DRISHTI_TEST_DATABASE_URL") or \
    _env["DRISHTI_DATABASE_URL"].rsplit("/", 1)[0] + "/drishti_test"


@pytest.fixture(scope="session", autouse=True)
def _never_prod():
    from drishti.core.config import get_settings
    assert get_settings().DRISHTI_DATABASE_URL.endswith("/drishti_test")
