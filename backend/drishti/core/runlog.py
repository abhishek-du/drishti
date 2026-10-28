"""run / run_end rows: every job writes one run row first and one run_end row last."""

from __future__ import annotations

import datetime as _dt
import json
import subprocess
import uuid

from sqlalchemy import text

from drishti.core.config import BACKEND


def code_sha() -> str:
    try:
        sha = subprocess.run(["git", "-C", str(BACKEND), "rev-parse", "HEAD"], capture_output=True,
                             text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(BACKEND), "status", "--porcelain"],
                               capture_output=True, text=True).stdout.strip()
        return sha[:39] + ("+" if dirty else "")
    except Exception:
        return "unknown"


async def start(s, kind: str, session_date: _dt.date | None = None, params: dict | None = None
                ) -> uuid.UUID:
    rid = uuid.uuid4()
    await s.execute(text("""insert into run(run_id, kind, session_date, code_sha, params)
        values (:r, :k, :d, :c, cast(:p as jsonb))"""),
        {"r": rid, "k": kind, "d": session_date, "c": code_sha(), "p": json.dumps(params or {})})
    await s.commit()
    return rid


async def end(s, rid: uuid.UUID, status: str, counts: dict | None = None,
              error: str | None = None) -> None:
    await s.execute(text("""insert into run_end(run_id, status, counts, error)
        values (:r, :s, cast(:c as jsonb), :e)"""),
        {"r": rid, "s": status, "c": json.dumps(counts or {}, default=str), "e": error})
    await s.commit()
