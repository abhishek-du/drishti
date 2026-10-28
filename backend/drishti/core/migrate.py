"""Apply backend/migrations/NNNN_*.sql in order, once each (schema_migration keeps the sha).

    .venv/bin/python -m drishti.core.migrate [--dry-run]
"""

from __future__ import annotations

import asyncio
import hashlib
import sys

from sqlalchemy import text

from drishti.core.config import BACKEND
from drishti.core.db import engine

DIR = BACKEND / "migrations"


async def main(dry: bool) -> None:
    async with engine().begin() as c:
        await c.execute(text("""CREATE TABLE IF NOT EXISTS schema_migration (
            version varchar(64) PRIMARY KEY, sha256 char(64) NOT NULL,
            applied_at timestamptz NOT NULL DEFAULT clock_timestamp())"""))
        done = dict((await c.execute(text("select version, sha256 from schema_migration"))).all())
    for f in sorted(DIR.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        sql = f.read_text()
        sha = hashlib.sha256(sql.encode()).hexdigest()
        if f.stem in done:
            if done[f.stem] != sha:
                raise SystemExit(f"REFUSED: {f.name} changed after it was applied")
            continue
        print(("DRY " if dry else "APPLY ") + f.name)
        if dry:
            continue
        async with engine().begin() as c:
            raw = await c.get_raw_connection()
            await raw.driver_connection.execute(sql)       # simple protocol: many statements
            await c.execute(text("insert into schema_migration(version, sha256) values (:v, :s)"),
                            {"v": f.stem, "s": sha})


if __name__ == "__main__":
    asyncio.run(main("--dry-run" in sys.argv))
