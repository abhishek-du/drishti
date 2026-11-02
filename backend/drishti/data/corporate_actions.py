"""Drishti's own corporate actions (P1c, Option B) and their split/bonus factors.

Provenance (copied, not imported; Prajna commit 25712fc):
  - parse: Prajna `app/parsers/upstox_corporate_actions.py` (event shape measured 2026-09-24;
    an event is identified by the sha256 of its canonical vendor JSON; ex-date = the "Ex ..."
    detail, else expiry_date).
  - factor_for: Prajna `app/contracts/ca_factor.py` cafactor-v1 (SPLIT: F = old fv / new fv,
    the ratio new:old must agree; BONUS a:b: F = (a+b)/b; RIGHTS and DIVIDEND unsupported).
  - vendor_treatment: Prajna `app/ingest/ca_derive.py` (APPLIED / NOT_APPLIED / UNKNOWN / N/A,
    |ln F| < 0.2 indistinguishable, tolerance 0.15).
knowable_at = greatest(end of the announcement IST day, first time Drishti saw the event)
(Prajna CA-OBSERVED, migration 0018). The vendor serves only ~1 year of history, so this runs
every night from the start.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import text

from drishti.core.clock import IST, now

ENDPOINT = "/v2/fundamentals/{isin}/corporate-actions"
TYPES = {"Dividend": "DIVIDEND", "Bonus": "BONUS", "Split": "SPLIT", "Rights Issue": "RIGHTS"}
METHOD_VERSION = "cafactor-v1"
DISTINGUISHABLE = 0.2
TOLERANCE = 0.15


def content_sha256(event: dict) -> str:
    return hashlib.sha256(json.dumps(event, sort_keys=True, separators=(",", ":"))
                          .encode()).hexdigest()


def _date(s: Any) -> _dt.date | None:
    if not isinstance(s, str) or not s.strip():
        return None
    return _dt.datetime.strptime(s.strip(), "%d %b %Y").date()


def _dec(s: Any) -> Decimal | None:
    if s is None or isinstance(s, bool) or s == "":
        return None
    try:
        d = Decimal(str(s))
    except InvalidOperation:
        raise ValueError(f"not a number: {s!r}") from None
    return d if d.is_finite() else None


def _ratio(s: Any) -> tuple[Decimal | None, Decimal | None]:
    if s in (None, ""):
        return None, None
    a, sep, b = str(s).partition(":")
    if not sep:
        raise ValueError(f"ratio without ':': {s!r}")
    return _dec(a), _dec(b)


def parse(body: dict, isin: str) -> tuple[list[dict], list[str]]:
    """A success envelope -> (events, problems). Pure."""
    if not isinstance(body, dict) or body.get("status") != "success":
        raise ValueError(f"not a success envelope: {str(body)[:200]}")
    events, problems, seen = [], [], set()
    for ev in body.get("data") or []:
        if not isinstance(ev, dict) or not isinstance(ev.get("event_details"), list):
            problems.append("event shape")
            continue
        details = {x.get("name"): x.get("value") for x in ev["event_details"]
                   if isinstance(x, dict)}
        try:
            ex_label = next((k for k in details if k and k.lower().startswith("ex ")), None)
            ex = _date(details.get(ex_label)) if ex_label else None
            ex = ex or _date(ev.get("expiry_date"))
            r_from, r_to = _ratio(ev.get("ratio"))
            row = {"isin": isin, "action_type": TYPES.get(ev.get("name"), "OTHER"),
                   "ex_date": ex, "record_date": _date(details.get("Record date")),
                   "announcement_date": _date(details.get("Announcement date")),
                   "amount": _dec(ev.get("amount")), "ratio_from": r_from, "ratio_to": r_to,
                   "face_value_before": _dec(details.get("Old face value")),
                   "face_value_after": _dec(details.get("New face value")),
                   "content_sha256": content_sha256(ev), "vendor_payload": ev}
        except ValueError as e:
            problems.append(str(e)[:120])
            continue
        if row["content_sha256"] in seen:
            continue
        seen.add(row["content_sha256"])
        events.append(row)
    return events, problems


def knowable_at(announced: _dt.date | None, first_seen: _dt.datetime) -> _dt.datetime:
    if announced is None:
        return first_seen
    eod = _dt.datetime.combine(announced, _dt.time(23, 59, 59, 999000), IST)
    return max(eod, first_seen)


def factor_for(action_type: str, ratio: str | None, fv_before, fv_after) -> dict:
    """cafactor-v1 (Prajna contracts/ca_factor.py). Pure."""
    def r_(s):
        if not s or ":" not in s:
            return None
        a, b = (x.strip() for x in s.split(":", 1))
        try:
            a_, b_ = Decimal(a), Decimal(b)
        except InvalidOperation:
            return None
        return (a_, b_) if a_ > 0 and b_ > 0 else None
    r = r_(ratio)
    if action_type == "SPLIT":
        inputs = {"ratio": ratio, "face_value_before": str(fv_before),
                  "face_value_after": str(fv_after)}
        if not fv_before or not fv_after:
            return {"status": "UNCERTAIN", "method": "SPLIT_FV", "f": None,
                    "reason": "face values missing", "inputs": inputs}
        f = Decimal(fv_before) / Decimal(fv_after)
        if r and (r[0] != Decimal(fv_after) or r[1] != Decimal(fv_before)):
            return {"status": "UNCERTAIN", "method": "SPLIT_FV", "f": None,
                    "reason": "ratio new:old disagrees with the face values", "inputs": inputs}
        return {"status": "EXACT", "method": "SPLIT_FV", "f": f,
                "reason": "old face value / new face value", "inputs": inputs}
    if action_type == "BONUS":
        if r is None:
            return {"status": "UNCERTAIN", "method": "BONUS_RATIO", "f": None,
                    "reason": "unparseable ratio", "inputs": {"ratio": ratio}}
        a, b = r
        return {"status": "EXACT", "method": "BONUS_RATIO", "f": (a + b) / b,
                "reason": f"(a+b)/b for {a}:{b}", "inputs": {"ratio": ratio}}
    if action_type == "RIGHTS":
        return {"status": "UNSUPPORTED", "method": "RIGHTS_TERP", "f": None,
                "reason": "structured Premium is 0.0 on every vendor rights event; the issue "
                          "price exists only in free text and is not provable",
                "inputs": {"ratio": ratio}}
    return {"status": "UNSUPPORTED", "method": "NONE", "f": None,
            "reason": "dividends are not price factors" if action_type == "DIVIDEND"
            else f"{action_type}: no factor rule", "inputs": {}}


def vendor_treatment(f, prev_close, ex_open, prev_basis_date, ex_date,
                     adjustment_observations: int = 0) -> tuple[str, dict]:
    """Prajna ca_derive.vendor_treatment, unchanged. Pure."""
    if f is None:
        return "N/A", {"why": "no factor"}
    ev = {"factor": str(f), "prev_close": str(prev_close), "ex_open": str(ex_open),
          "prev_bar_basis_as_of": str(prev_basis_date),
          "ca_adjustment_observations": adjustment_observations}
    if adjustment_observations:
        return "APPLIED", {**ev, "why": "a later vendor re-serve was explained by this factor"}
    if prev_close is not None and prev_basis_date is not None and prev_basis_date < ex_date:
        return "UNKNOWN", {**ev, "why": "our pre-ex bars predate the ex-date; the vendor's "
                                        "post-ex treatment is not observed yet"}
    if prev_close is None or ex_open is None:
        return "UNKNOWN", {**ev, "why": "no stored bar on one side of the ex-date"}
    ratio = float(prev_close) / float(ex_open)
    ev["ratio"] = round(ratio, 4)
    lf = math.log(float(f))
    if abs(lf) < DISTINGUISHABLE:
        return "UNKNOWN", {**ev, "why": f"factor too close to 1 (|ln F| < {DISTINGUISHABLE})"}
    lr = math.log(ratio)
    if abs(lr - lf) < TOLERANCE:
        return "NOT_APPLIED", {**ev, "why": "the stored series jumps by ~F at the ex-date"}
    if abs(lr) < TOLERANCE:
        return "APPLIED", {**ev, "why": "the stored series is smooth across the ex-date"}
    return "UNKNOWN", {**ev, "why": "the jump matches neither F nor 1"}


# ---- storage -------------------------------------------------------------------------------

async def fetch_store(s, rid, api, isins: list[str], key_of: dict[str, str]) -> dict:
    c = {"isins": len(isins), "ok": 0, "errors": 0, "events_seen": 0, "events_new": 0,
         "problems": 0}
    for i, isin in enumerate(isins):
        r = api.get_response(ENDPOINT.format(isin=isin))
        body = r.content
        events, probs = [], []
        if r.status_code == 200:
            try:
                events, probs = parse(r.json(), isin)
            except ValueError as e:
                probs = [str(e)[:200]]
        pid, ts = (await s.execute(text("""insert into ca_payload(run_id, isin, http_status,
            sha256, n_events, error) values (:r, :i, :st, :h, :n, :e)
            returning id, fetched_at"""),
            {"r": rid, "i": isin, "st": r.status_code, "h": hashlib.sha256(body).hexdigest(),
             "n": len(events), "e": None if r.status_code == 200 and not probs
             else (body[:300].decode(errors="replace") if r.status_code != 200
                   else "; ".join(probs)[:300])})).one()
        for ev in events:
            n = (await s.execute(text("""insert into corporate_action(payload_id, isin,
                instrument_key, action_type, ex_date, record_date, announcement_date, amount,
                ratio_from, ratio_to, face_value_before, face_value_after, content_sha256,
                vendor_payload, first_seen_at, knowable_at) values (:p, :isin, :k, :action_type,
                :ex_date, :record_date, :announcement_date, :amount, :ratio_from, :ratio_to,
                :face_value_before, :face_value_after, :content_sha256, cast(:vp as jsonb),
                :ts, :kn) on conflict (isin, content_sha256) do nothing"""),
                {**ev, "p": pid, "k": key_of.get(isin), "vp": json.dumps(ev["vendor_payload"]),
                 "ts": ts, "kn": knowable_at(ev["announcement_date"], ts)})).rowcount
            c["events_new"] += n
        c["events_seen"] += len(events)
        c["problems"] += len(probs)
        c["ok" if r.status_code == 200 else "errors"] += 1
        if i % 200 == 199:
            await s.commit()
    await s.commit()
    return c


async def _bars(s, key: str, ex: _dt.date):
    """(prev close + its basis date, ex-day open, n re-serves of pre-ex bars explained by
    nothing but a factor) from Drishti's own bar_1d."""
    prev = (await s.execute(text("""select b.close, p.basis_as_of from bar_1d b
        join bar_1d_payload p on p.id = b.payload_id where b.instrument_key = :k
        and b.session_date < :x order by b.session_date desc, b.fetched_at desc limit 1"""),
        {"k": key, "x": ex})).first()
    nxt = (await s.execute(text("""select b.open from bar_1d b where b.instrument_key = :k
        and b.session_date >= :x order by b.session_date, b.fetched_at desc limit 1"""),
        {"k": key, "x": ex})).first()
    return prev, nxt


async def reserve_ratios(s, key: str, ex: _dt.date) -> list[float]:
    """For pre-ex sessions fetched both before and after the ex-date: earliest close /
    latest close (a vendor that applied F re-serves them divided by F)."""
    rows = (await s.execute(text("""
        with b as (select b.session_date, b.close, p.basis_as_of, b.fetched_at from bar_1d b
                   join bar_1d_payload p on p.id = b.payload_id
                   where b.instrument_key = :k and b.session_date < :x)
        select (select close from b b1 where b1.session_date = d.session_date
                  and b1.basis_as_of < :x order by fetched_at limit 1),
               (select close from b b2 where b2.session_date = d.session_date
                  and b2.basis_as_of >= :x order by fetched_at desc limit 1)
        from (select distinct session_date from b) d"""), {"k": key, "x": ex})).all()
    return [float(a) / float(z) for a, z in rows if a and z]


async def derive(s, rid) -> dict:
    """A new ca_factor row for every corporate action whose derivation changed."""
    cas = (await s.execute(text("""select c.id, c.instrument_key, c.action_type, c.ex_date,
        c.face_value_before, c.face_value_after, c.vendor_payload from corporate_action c
        order by c.id"""))).mappings().all()
    num = lambda x: None if x is None else round(float(x), 8)  # noqa: E731
    last = {r[0]: (r[1], r[2], num(r[3]))
            for r in (await s.execute(text("""select distinct on (ca_id) ca_id, status,
                vendor_applied, factor_price from ca_factor order by ca_id, derived_at desc,
                id desc"""))).all()}
    counts, new = {}, 0
    for c in cas:
        f = factor_for(c["action_type"], (c["vendor_payload"] or {}).get("ratio"),
                       c["face_value_before"], c["face_value_after"])
        applied, evidence = "N/A", {"why": "no factor"}
        if f["status"] == "EXACT" and c["instrument_key"] and c["ex_date"]:
            prev, nxt = await _bars(s, c["instrument_key"], c["ex_date"])
            rs = await reserve_ratios(s, c["instrument_key"], c["ex_date"])
            lf = math.log(float(f["f"]))
            n_obs = sum(1 for x in rs if abs(math.log(x) - lf) < 0.01) \
                if abs(lf) >= DISTINGUISHABLE else 0
            applied, evidence = vendor_treatment(
                f["f"], prev[0] if prev else None, nxt[0] if nxt else None,
                prev[1] if prev else None, c["ex_date"], adjustment_observations=n_obs)
        key = f"{c['action_type']}/{f['status']}/{applied}"
        counts[key] = counts.get(key, 0) + 1
        if last.get(c["id"]) == (f["status"], applied, num(f["f"])):
            continue
        await s.execute(text("""insert into ca_factor(ca_id, run_id, status, method,
            factor_price, factor_volume, vendor_applied, vendor_evidence, reason, inputs,
            method_version) values (:ca, :r, :st, :m, :f, :f, :va, cast(:ev as jsonb), :re,
            cast(:inp as jsonb), :mv)"""),
            {"ca": c["id"], "r": rid, "st": f["status"], "m": f["method"], "f": f["f"],
             "va": applied, "ev": json.dumps(evidence, default=str), "re": f["reason"][:500],
             "inp": json.dumps(f["inputs"], default=str), "mv": METHOD_VERSION})
        new += 1
    await s.commit()
    return {"events": len(cas), "new_rows": new, "counts": dict(sorted(counts.items()))}


async def compare_with_prajna(s, ps) -> dict:
    """Continuity: every Prajna event (by content sha256) is in Drishti and gets the same
    status / method / factor; vendor_applied is compared where Drishti has bars (from
    2026-10-21) on both sides of the ex-date."""
    ours = {r[0]: r[1:] for r in (await s.execute(text("""
        select c.content_sha256, f.status, f.method, f.factor_price, f.vendor_applied, c.ex_date
        from corporate_action c join lateral (select * from ca_factor x where x.ca_id = c.id
        order by derived_at desc, id desc limit 1) f on true"""))).all()}
    theirs = {r[0]: r[1:] for r in (await ps.execute(text("""
        select c.content_sha256, f.status, f.method, f.factor_price, f.vendor_applied, c.ex_date
        from corporate_action c join ca_factor f on f.ca_id = c.id"""))).all()}
    both = ours.keys() & theirs.keys()
    num = lambda x: None if x is None else round(float(x), 8)  # noqa: E731
    fdiff = [h for h in both if (ours[h][0], ours[h][1], num(ours[h][2]))
             != (theirs[h][0], theirs[h][1], num(theirs[h][2]))]
    recent = [h for h in both if ours[h][3] not in ("N/A", "UNKNOWN")]
    vdiff = [h for h in recent if ours[h][3] != theirs[h][3]]
    return {"drishti": len(ours), "prajna": len(theirs), "both": len(both),
            "only_prajna": len(theirs.keys() - ours.keys()),
            "only_drishti": len(ours.keys() - theirs.keys()),
            "factor_equal": len(both) - len(fdiff), "factor_differ": len(fdiff),
            "vendor_applied_compared": len(recent), "vendor_applied_differ": len(vdiff),
            "factor_differ_sample": fdiff[:5]}
