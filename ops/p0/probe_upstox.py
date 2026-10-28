"""P0 probe (read-only, run once by hand): is Prajna's Upstox token usable by Drishti, what does the
holidays API return, which NSE index keys exist (Nifty 500 + sectors), and do their quotes work.

It never logs in, never prints the token, and makes 4 small requests (profile, holidays, quotes)
plus one public download (the instrument master). Output: JSON on stdout and the index-key list in
/windows/drishti_data/cache/.

    /home/cis/windows/prajna/backend/.venv/bin/python ops/p0/probe_upstox.py
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import httpx

TOKEN = Path("/home/cis/windows/prajna/backend/var/upstox_token.json")   # Prajna's; read only
CACHE = Path("/windows/drishti_data/cache")
MASTER = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
API = "https://api.upstox.com"


def main() -> int:
    out: dict = {}
    with TOKEN.open("rb") as f:                      # read-only open; the file is never written
        tok = json.load(f)["access_token"]
    h = {"Authorization": f"Bearer {tok}", "Accept": "application/json"}
    with httpx.Client(timeout=30) as c:
        r = c.get(f"{API}/v2/user/profile", headers=h)
        out["token_valid"] = r.status_code == 200
        if not out["token_valid"]:
            out["profile_status"] = r.status_code
            print(json.dumps(out, indent=1))
            return 2

        r = c.get(f"{API}/v2/market/holidays", headers=h)
        hol = r.json().get("data") or []
        out["holidays"] = {"status": r.status_code, "count": len(hol),
                           "sample": hol[:3], "fields": sorted(hol[0]) if hol else []}

        CACHE.mkdir(parents=True, exist_ok=True)
        raw = gzip.decompress(c.get(MASTER).content)
        rows = json.loads(raw)
        idx = [{"key": x.get("instrument_key"), "name": x.get("name") or x.get("trading_symbol")}
               for x in rows if x.get("segment") == "NSE_INDEX"]
        (CACHE / "nse_index_keys.json").write_text(json.dumps(idx, indent=1))
        out["nse_index_count"] = len(idx)
        want = ("Nifty 500", "Nifty 50", "Nifty Bank", "India VIX", "Nifty IT", "Nifty Auto",
                "Nifty FMCG", "Nifty Pharma", "Nifty Metal", "Nifty Realty", "Nifty Energy",
                "Nifty Media", "Nifty PSU Bank", "Nifty Pvt Bank", "Nifty Fin Service",
                "Nifty Infra", "Nifty Healthcare")
        have = {x["name"]: x["key"] for x in idx}
        out["index_keys"] = {w: have.get(w) for w in want}

        keys = [k for k in out["index_keys"].values() if k][:20]
        r = c.get(f"{API}/v2/market-quote/ohlc", headers=h,
                  params={"instrument_key": ",".join(keys), "interval": "1d"})
        q = r.json().get("data") or {}
        out["index_quote"] = {"status": r.status_code, "returned": len(q),
                              "sample": {k: v.get("ohlc") for k, v in list(q.items())[:3]}}
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
