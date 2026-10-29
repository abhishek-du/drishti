"""Loaders for the files the user downloads by hand into drishti_data/manual/ (NSE ToU: Drishti
never fetches NSE pages itself). Each file is stored once (by sha256) with its file date, which is
its point-in-time "knowable from" date: the date in the file name, else the file's mtime date."""

from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from sqlalchemy import text

RESULTS = re.compile(r"financial\s+results?|\bresults\b", re.I)
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def kind_of(p: Path) -> str | None:
    n = p.name.lower()
    for pat, k in ((r"^asm", "ASM"), (r"^gsm", "GSM"), (r"delist", "DELISTED"),
                   (r"^equity_l", "EQUITY_L"), (r"symbolchange", "SYMBOL_CHANGE"),
                   (r"namechange", "NAME_CHANGE"), (r"contract_note", "CONTRACT_NOTE"),
                   (r"board_?meeting", "BOARD_MEETINGS")):
        if re.search(pat, n):
            return k
    return None


def file_date(p: Path) -> _dt.date:
    m = re.search(r"(20\d\d-\d\d-\d\d)", p.name)
    return _dt.date.fromisoformat(m.group(1)) if m else \
        _dt.date.fromtimestamp(p.stat().st_mtime)


def _d(s: str) -> _dt.date | None:
    s = (s or "").strip()
    for f in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return _dt.datetime.strptime(s, f).date()
        except ValueError:
            pass
    if re.fullmatch(r"\d{4,5}(\.0+)?", s):               # an Excel serial date
        return _dt.date(1899, 12, 30) + _dt.timedelta(days=int(float(s)))
    return None


def _csv(raw: bytes) -> list[dict]:
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig", errors="replace"))))
    hdr = [h.strip().upper() for h in rows[0]]
    return [dict(zip(hdr, (c.strip() for c in r))) for r in rows[1:] if any(c.strip() for c in r)]


def _xlsx_sheet1(raw: bytes) -> list[dict]:
    z = zipfile.ZipFile(io.BytesIO(raw))
    ss = [] if "xl/sharedStrings.xml" not in z.namelist() else [
        "".join(t.text or "" for t in si.iter("{%s}t" % NS["m"]))
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS)]
    rows = []
    for row in ET.fromstring(z.read("xl/worksheets/sheet1.xml")).iter("{%s}row" % NS["m"]):
        r = []
        for c in row.findall("m:c", NS):
            v = c.find("m:v", NS)
            val = (v.text if v is not None else "") or ""
            r.append(ss[int(val)] if c.get("t") == "s" and val else val)
        rows.append(r)
    hdr = [h.strip().upper() for h in rows[0]]
    return [dict(zip(hdr, (c.strip() for c in r))) for r in rows[1:] if any(r)]


ISIN = re.compile(r"^IN[A-Z0-9]{10}$")


def _clean(r: dict) -> dict:
    """An ISIN that is not a valid Indian ISIN ("Not Available") becomes NULL; the raw text stays."""
    i = (r.get("isin") or "").strip().upper()
    if i and not ISIN.match(i):
        r["extra"] = {**r["extra"], "isin_raw": r["isin"]}
        i = ""
    r["isin"] = i or None
    return r


def parse(kind: str, raw: bytes) -> list[dict]:
    """-> rows {symbol, isin, stage, event_date, extra}."""
    if kind == "CONTRACT_NOTE":
        return []
    recs = _xlsx_sheet1(raw) if raw[:2] == b"PK" else _csv(raw)
    out = []
    for r in recs:
        g = lambda *ks: next((r[k] for k in ks if r.get(k)), "")  # noqa: E731
        if kind in ("ASM", "GSM"):
            if not g("ISIN"):                             # section lines ("Long Term")
                continue
            out.append({"symbol": g("SYMBOL"), "isin": g("ISIN"),
                        "stage": g("ASM STAGE", "GSM STAGE"), "event_date": None, "extra": {}})
        elif kind == "DELISTED":
            out.append({"symbol": g("SYMBOL"), "isin": g("ISIN"), "stage": g("TYPE OF DELISTING"),
                        "event_date": _d(g("DELISTED DATE")),
                        "extra": {"name": g("COMPANY NAME"), "board": g("BOARD")}})
        elif kind == "EQUITY_L":
            out.append({"symbol": g("SYMBOL"), "isin": g("ISIN NUMBER"), "stage": g("SERIES"),
                        "event_date": _d(g("DATE OF LISTING")),
                        "extra": {"name": g("NAME OF COMPANY"), "face_value": g("FACE VALUE")}})
        elif kind == "SYMBOL_CHANGE":
            out.append({"symbol": g("NEW SYMBOL"), "isin": None, "stage": None,
                        "event_date": _d(g("EFFECTIVE DATE")),
                        "extra": {"old_symbol": g("OLD SYMBOL"),
                                  "name": g("NAME OF THE COMPANY")}})
        elif kind == "BOARD_MEETINGS":
            # NSE "Board Meetings" export (user download, D2). Header names VERIFY on the first
            # real file; the date and purpose columns are matched loosely.
            date_col = next((k for k in r if "MEETING DATE" in k or k in ("BM DATE", "DATE")), "")
            purpose = g("PURPOSE", "BOARD MEETING PURPOSE", "AGENDA")
            details = g("DETAILS", "BOARD MEETING DETAILS", "DESCRIPTION")
            if not (g("SYMBOL") and date_col and r.get(date_col)):
                continue
            out.append({"symbol": g("SYMBOL"), "isin": g("ISIN") or None, "stage": purpose,
                        "event_date": _d(r[date_col].split(" ")[0]),
                        "extra": {"name": g("COMPANY NAME", "COMPANY"), "details": details[:500],
                                  "results": bool(RESULTS.search(f"{purpose} {details}"))}})
        elif kind == "NAME_CHANGE":
            out.append({"symbol": g("SYMBOL"), "isin": None, "stage": None,
                        "event_date": _d(g("EFFECTIVE DATE")),
                        "extra": {"old_name": g("OLD NAME OF THE COMPANY"),
                                  "new_name": g("NEW NAME OF THE COMPANY")}})
    return [_clean(r) for r in out]


async def load_dir(s, rid, folder: Path) -> list[dict]:
    done = []
    for p in sorted(folder.iterdir()):
        k = kind_of(p) if p.is_file() else None
        if not k:
            continue
        raw = p.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        if (await s.execute(text("select 1 from manual_file where sha256 = :s"),
                            {"s": sha})).first():
            done.append({"file": p.name, "kind": k, "status": "ALREADY_LOADED"})
            continue
        rows = parse(k, raw)
        fid = (await s.execute(text("""insert into manual_file(run_id, kind, path, file_date,
            sha256, row_count) values (:r, :k, :p, :d, :s, :n) returning file_id"""),
            {"r": rid, "k": k, "p": str(p), "d": file_date(p), "s": sha, "n": len(rows)})).scalar()
        for r in rows:
            await s.execute(text("""insert into manual_row(file_id, symbol, isin, stage,
                event_date, extra) values (:f, :symbol, :isin, :stage, :event_date,
                cast(:extra as jsonb))"""), {"f": fid, **r, "extra": json.dumps(r["extra"])})
        await s.commit()
        done.append({"file": p.name, "kind": k, "rows": len(rows), "status": "LOADED"})
    return done


STALE_DAYS = 7          # user 2026-10-28: warn when the newest ASM/GSM file is older than 7 days
BOARD_STALE_DAYS = 4    # user 2026-10-29 (D2): board-meetings CSV downloaded Mon and Thu


async def _fresh(s, on: _dt.date, kinds: tuple[str, ...], stale: int) -> dict:
    out: dict = {}
    for k in kinds:
        d = (await s.execute(text("""select max(file_date) from manual_file
            where kind = :k and file_date <= :on"""), {"k": k, "on": on})).scalar()
        age = (on - d).days if d else None
        out[k] = {"file_date": d, "age_days": age,
                  "status": "MISSING" if d is None else "STALE" if age > stale else "OK"}
    out["warning"] = any(out[k]["status"] != "OK" for k in kinds)
    return out


async def board_meetings_freshness(s, on: _dt.date) -> dict:
    """The newest board-meetings file as of `on`; STALE when older than 4 calendar days. Every
    consumer of the results calendar (Scout's results flag, the results gate) records this dict;
    a stale calendar is a warning and the results flag is shown as UNKNOWN, never as 'none'."""
    import logging
    out = await _fresh(s, on, ("BOARD_MEETINGS",), BOARD_STALE_DAYS)
    if out["warning"]:
        logging.getLogger("drishti.results").warning("board-meetings CSV not fresh: %s",
                                                     out["BOARD_MEETINGS"])
    return out


async def surveillance_freshness(s, on: _dt.date) -> dict:
    """The newest ASM and GSM file dates as of `on` (point in time: file_date <= on), their age
    in calendar days, and STALE when older than STALE_DAYS or missing. Scout records this dict in
    every watchlist run and logs a warning (Telegram from P5) when anything is stale. A warning
    only: it does not change the trading rules."""
    import logging
    out: dict = {}
    for k in ("ASM", "GSM"):
        d = (await s.execute(text("""select max(file_date) from manual_file
            where kind = :k and file_date <= :on"""), {"k": k, "on": on})).scalar()
        age = (on - d).days if d else None
        out[k] = {"file_date": d, "age_days": age,
                  "status": "MISSING" if d is None else "STALE" if age > STALE_DAYS else "OK"}
    out["warning"] = any(v["status"] != "OK" for v in (out["ASM"], out["GSM"]))
    if out["warning"]:
        logging.getLogger("drishti.surveillance").warning(
            "ASM/GSM list not fresh: %s", {k: out[k] for k in ("ASM", "GSM")})
    return out
