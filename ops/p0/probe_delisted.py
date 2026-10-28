"""P0 survivorship probe (read-only, run by hand): does Upstox historical V3 still serve daily
candles for securities that left the instrument master (delisted / suspended / removed)?

Input: a CSV whose first column is an Upstox key (NSE_EQ|<ISIN>) or an ISIN; other columns are
kept as labels. Output: one JSON line per key (candles returned, first/last date, HTTP status)
and a summary. Read-only: no login, the token is never printed, at most 1 request per second.

    /home/cis/windows/prajna/backend/.venv/bin/python ops/p0/probe_delisted.py INPUT.csv [--limit N]

Run it outside Prajna's 16:05 close job (historical-candle API budget is shared).
"""

from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path

import httpx

TOKEN = Path("/home/cis/windows/prajna/backend/var/upstox_token.json")
API = "https://api.upstox.com/v3/historical-candle"
FROM, TO = "2019-01-01", "2026-10-28"


def keys(path: Path, limit: int) -> list[tuple[str, list[str]]]:
    out = []
    with path.open(newline="") as f:
        for row in csv.reader(f):
            if not row or not row[0].strip():
                continue
            k = row[0].strip()
            if len(k) == 12 and k[:2].isalpha():          # a bare ISIN
                k = f"NSE_EQ|{k}"
            if k.startswith("NSE_EQ|"):
                out.append((k, row[1:]))
    return out[:limit] if limit else out


def main() -> int:
    path = Path(sys.argv[1])
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 0
    with TOKEN.open("rb") as f:
        tok = json.load(f)["access_token"]
    h = {"Authorization": f"Bearer {tok}", "Accept": "application/json"}
    got = none = err = 0
    with httpx.Client(timeout=30) as c:
        for k, label in keys(path, limit):
            r = c.get(f"{API}/{k}/days/1/{TO}/{FROM}", headers=h)
            rec = {"key": k, "label": label, "status": r.status_code}
            if r.status_code == 200:
                cs = (r.json().get("data") or {}).get("candles") or []
                rec.update(candles=len(cs), first=cs[-1][0][:10] if cs else None,
                           last=cs[0][0][:10] if cs else None)
                got += bool(cs)
                none += not cs
            else:
                rec["error"] = r.text[:200]
                err += 1
            print(json.dumps(rec), flush=True)
            if r.status_code == 429:
                break
            time.sleep(1.0)
    print(json.dumps({"summary": {"with_history": got, "empty": none, "errors": err}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
