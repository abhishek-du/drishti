"""Switch flags in backend/.env (SWITCH_CHECKLIST step F). Values are never printed.

    .venv/bin/python -m drishti.switch.flip status   [--env PATH]
    .venv/bin/python -m drishti.switch.flip apply    [--env PATH]     # backup, then flip
    .venv/bin/python -m drishti.switch.flip rollback --backup PATH [--env PATH]

apply sets DRISHTI_HISTORY_SOURCE=copy, DRISHTI_UPSTOX_LOGIN_ENABLED=true and
DRISHTI_UPSTOX_TOKEN_FILE=<Drishti's own token file>, after copying the .env to
.env.pre_switch_<stamp> (0600). It refuses unless every login credential is set.
"""

from __future__ import annotations

import datetime as _dt
import json
import shutil
import sys
from pathlib import Path

from drishti.core.config import BACKEND, LOGIN_FIELDS, OWN_TOKEN_FILE

FLIP = {"DRISHTI_HISTORY_SOURCE": "copy", "DRISHTI_UPSTOX_LOGIN_ENABLED": "true",
        "DRISHTI_UPSTOX_TOKEN_FILE": str(OWN_TOKEN_FILE)}
SHOW = ("DRISHTI_HISTORY_SOURCE", "DRISHTI_UPSTOX_LOGIN_ENABLED")   # non-secret, shown as-is


def _read(env: Path) -> list[str]:
    return env.read_text().splitlines()


def _vals(lines: list[str]) -> dict[str, str]:
    out = {}
    for ln in lines:
        if ln.strip() and not ln.lstrip().startswith("#") and "=" in ln:
            k, v = ln.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def status(env: Path) -> dict:
    v = _vals(_read(env))
    return {"env": str(env), "mode": oct(env.stat().st_mode & 0o777),
            "login_fields": {k: ("set" if v.get(k) else "MISSING") for k in LOGIN_FIELDS},
            **{k: v.get(k, "(default)") for k in SHOW},
            "token_file": ("own" if v.get("DRISHTI_UPSTOX_TOKEN_FILE") == str(OWN_TOKEN_FILE)
                           else "prajna/other")}


def apply(env: Path) -> dict:
    lines = _read(env)
    miss = [k for k in LOGIN_FIELDS if not _vals(lines).get(k)]
    if miss:
        raise SystemExit(f"refused: login credentials missing: {miss}")
    if _vals(lines).get("DRISHTI_HISTORY_SOURCE") == "copy":
        raise SystemExit("refused: already flipped (the pre-switch backup must stay the newest)")
    bak = env.with_name(f".env.pre_switch_{_dt.datetime.now():%Y%m%dT%H%M%S}")
    shutil.copy2(env, bak)
    bak.chmod(0o600)
    out, done = [], set()
    for ln in lines:
        k = ln.split("=", 1)[0].strip() if "=" in ln and not ln.lstrip().startswith("#") else None
        if k in FLIP:
            out.append(f"{k}={FLIP[k]}")
            done.add(k)
        else:
            out.append(ln)
    out += [f"{k}={v}" for k, v in FLIP.items() if k not in done]
    tmp = env.with_suffix(".tmp")
    tmp.touch(mode=0o600)
    tmp.chmod(0o600)
    tmp.write_text("\n".join(out) + "\n")
    tmp.replace(env)
    return {"backup": str(bak), **status(env)}


def rollback(env: Path, backup: Path) -> dict:
    shutil.copy2(backup, env)
    env.chmod(0o600)
    return {"restored_from": str(backup), **status(env)}


def main(argv: list[str]) -> dict:
    a = dict(zip(argv[1::2], argv[2::2]))
    env = Path(a.get("--env", BACKEND / ".env"))
    cmd = argv[0] if argv else ""
    if cmd == "status":
        return status(env)
    if cmd == "apply":
        return apply(env)
    if cmd == "rollback":
        return rollback(env, Path(a["--backup"]))
    raise SystemExit(__doc__)


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1:]), indent=1))
