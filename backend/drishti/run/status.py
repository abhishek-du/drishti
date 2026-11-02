"""Nightly live status for docs/HANDOVER.md: /windows/drishti_data/handover/STATUS.md.
Read-only (DB, logs, git, crontab); written outside the repo so the tree stays clean.

    .venv/bin/python -m drishti.run.status
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys

from sqlalchemy import text

from drishti.core.clock import IST, now
from drishti.core.config import BACKEND, get_settings
from drishti.core.db import sessionmaker


def _sh(*cmd: str) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception as e:                      # status must never fail on one probe
        return f"({type(e).__name__})"


async def build() -> str:
    repo = BACKEND.parent
    async with sessionmaker()() as s:
        runs = (await s.execute(text("""
            select distinct on (r.kind) r.kind, r.session_date, r.started_at, e.status,
                   left(coalesce(e.error, ''), 120)
            from run r left join run_end e using (run_id) where r.kind not like 'TEST%'
            order by r.kind, r.started_at desc"""))).all()
        fails = (await s.execute(text("""select r.kind, r.started_at, left(e.error, 120)
            from run r join run_end e using (run_id) where e.status = 'FAILED'
            and r.started_at > now() - interval '3 days' order by r.started_at desc"""))).all()
        fresh = (await s.execute(text("""select
            (select max(session_date) from eod_quote), (select max(session_date) from bar_1d),
            (select max(session_date) from index_bar), (select max(session_date) from watchlist_run),
            (select count(*) from corporate_action), (select max(fetched_at) from ca_payload)"""))).one()
    try:
        live = json.loads((BACKEND / "var" / "run" / "live_health.json").read_text())
    except (OSError, ValueError):
        live = {}
    du = shutil.disk_usage(get_settings().DRISHTI_DATA_DIR)
    root = shutil.disk_usage("/")
    cron = _sh("crontab", "-l").splitlines()
    lines = [f"# Drishti live status ({now():%Y-%m-%d %H:%M} IST, generated)", "",
             f"- git: `{_sh('git', '-C', str(repo), 'log', '-1', '--format=%h %s')[:100]}`; dirty files: "
             f"{len(_sh('git', '-C', str(repo), 'status', '--porcelain').splitlines())}",
             f"- branches ahead of main: track-b "
             f"{_sh('git', '-C', str(repo), 'rev-list', '--count', 'main..track-b')}, track-c "
             f"{_sh('git', '-C', str(repo), 'rev-list', '--count', 'main..track-c')}",
             f"- crontab Drishti lines: {sum('drishti_job' in c and not c.lstrip().startswith('#') for c in cron)}",
             f"- disk free: /windows {du.free / 1e9:.1f} GB, / {root.free / 1e9:.1f} GB",
             f"- services: api {_sh('systemctl', '--user', 'is-active', 'drishti-api')}, web "
             f"{_sh('systemctl', '--user', 'is-active', 'drishti-web')}",
             f"- freshness: eod {fresh[0]}, bar_1d {fresh[1]}, index {fresh[2]}, watchlist "
             f"{fresh[3]}, corporate actions {fresh[4]} (last fetch {fresh[5]})",
             f"- live: state {live.get('state')}, keys {live.get('keys')}, reconnects "
             f"{live.get('reconnects')}, frames {live.get('frames')}, updated {live.get('updated_at')}",
             "", "## Latest run per job", "", "| job | session | started | status | error |",
             "|---|---|---|---|---|"]
    lines += [f"| {k} | {d} | {st.astimezone(IST):%m-%d %H:%M} | {stt or 'running'} | {err} |"
              for k, d, st, stt, err in runs]
    lines += ["", "## Failures, last 3 days", ""]
    lines += [f"- {st.astimezone(IST):%m-%d %H:%M} {k}: {err}" for k, st, err in fails] or ["- none"]
    return "\n".join(lines) + "\n"


async def main() -> int:
    out = get_settings().DRISHTI_DATA_DIR / "handover" / "STATUS.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(await build())
    print(json.dumps({"written": str(out)}))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
