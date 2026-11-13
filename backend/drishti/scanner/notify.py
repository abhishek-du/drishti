"""Telegram delivery of selections, alerts and outcomes (user 2026-11-04). PAPER ONLY.

Credentials: DRISHTI_TELEGRAM_BOT_TOKEN / DRISHTI_TELEGRAM_CHAT_ID from .env (never printed,
logged or committed; the URL that holds the token is never echoed). Without them every message
is only logged, as before.

Every message is one notify_event row (kind, ref) CLAIMED before it is sent, so a re-run never
repeats it; a failed send is retried by a later run (up to 3 attempts). At most MAX_PER_RUN
messages go out per job run, >= 1.1 s apart (Telegram allows ~1 message/s per chat); the rest
are 'batched' into one summary line. Only events of sessions from NOTIFY_SINCE on are sent (no
flood of older alerts when delivery was switched on).

Kinds: SCOUT_DIGEST (one per EOD watchlist), RADAR_NEW (one per new Live Radar name), ALERT (one
per 1d alert / per exception 15m alert, with the actionable target after the broken-R1 rule and
the odds line for 1d), DIGEST_15M (one per 15m bar: the bar's other 15m alerts), OUTCOME (one
per 1d / exception 15m alert: target reached / stop hit / expired), OUTCOME_15M (one end-of-day
digest of the other 15m outcomes), SUMMARY (overflow). Events folded into a digest are stored
as 'digested' and never sent individually.
"""

from __future__ import annotations

import datetime as _dt
import logging
import time

import httpx
from sqlalchemy import text

from drishti.core.clock import IST
from drishti.core.config import get_settings
from drishti.scanner.detect import choose_target
from drishti.scanner import notify_ai

log = logging.getLogger("drishti.scanner.notify")

LABEL = "Decision support — not a validated edge, not a prediction. Paper only. Your call."
NOTIFY_SINCE = _dt.date(2026, 11, 5)
MAX_PER_RUN = 25
# routing (user 2026-11-04, ~200 messages/day was unusable): 1d alerts + outcomes individual;
# 15m alerts as one digest per 15m bar and their outcomes as one end-of-day digest, except for
# the top WATCHLIST_TOP_INDIVIDUAL watchlist names, today's Live Radar names and anything the
# user marked watching/took (individual). The whole watchlist cannot be the exception: the 15m
# scan only covers watchlist + radar names, so every 15m alert would qualify.
WATCHLIST_TOP_INDIVIDUAL = 10
GAP_S = 1.1
MAX_ATTEMPTS = 3
RETRY_WINDOW = "2 hours"      # a failed message is retried only while it is still current
STUCK_PENDING = "10 minutes"  # a claim whose run died before the send is retried after this
# an event that a later run may (re)send: none yet, or failed / stuck within the retry window
RETRYABLE = f"""(n.status = 'failed' and n.attempts < {MAX_ATTEMPTS}
                 and n.created_at > now() - interval '{RETRY_WINDOW}')
             or (n.status = 'pending' and n.created_at < now() - interval '{STUCK_PENDING}'
                 and n.created_at > now() - interval '{RETRY_WINDOW}')"""
CAVEAT_15M = ("15m levels use unadjusted 15m bars (DEVIATIONS D5): unreliable for a stock "
              "with a split/bonus in the last ~25 days, until the 11-20 switch work adjusts them.")

_state = {"last": 0.0, "sent": 0}


def send(msg: str, parse_mode: str = "HTML") -> str:
    """'sent' | 'logged' (no credentials) | 'failed ...'."""
    cfg = get_settings()
    if not (cfg.DRISHTI_TELEGRAM_BOT_TOKEN and cfg.DRISHTI_TELEGRAM_CHAT_ID):
        log.info("telegram not configured; message logged only:\n%s", msg)
        return "logged"
    wait = _state["last"] + GAP_S - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    url = f"https://api.telegram.org/bot{cfg.DRISHTI_TELEGRAM_BOT_TOKEN}/sendMessage"
    body = {
        "chat_id": cfg.DRISHTI_TELEGRAM_CHAT_ID,
        "text": msg[:4000],
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    try:
        for attempt in (1, 2):
            r = httpx.post(url, json=body, timeout=20)
            _state["last"] = time.monotonic()
            if r.status_code == 200:
                return "sent"
            if r.status_code == 400 and parse_mode:
                # If Telegram HTML parsing fails, fallback immediately to plain text
                log.warning("telegram HTML parse error; retrying plain text: %s", r.text)
                body.pop("parse_mode", None)
                r_plain = httpx.post(url, json=body, timeout=20)
                _state["last"] = time.monotonic()
                if r_plain.status_code == 200:
                    return "sent"
                return f"failed HTTP {r_plain.status_code}"
            if r.status_code == 429 and attempt == 1:  # flood control: wait as told, once
                try:
                    wait_s = float((r.json().get("parameters") or {}).get("retry_after") or 5)
                except ValueError:
                    wait_s = 5.0
                time.sleep(min(wait_s, 30.0) + 0.5)
                continue
            return f"failed HTTP {r.status_code}"
        return "failed HTTP 429"
    except httpx.HTTPError as e:                  # never echo the URL (it holds the token)
        _state["last"] = time.monotonic()
        return f"failed {type(e).__name__}"


async def deliver(s, kind: str, ref: str, body: str) -> str:
    """Claim (kind, ref), then send once. Returns sent / logged / failed / dup / batched."""
    row = (await s.execute(text("""insert into notify_event(kind, ref, text) values (:k, :r, :t)
        on conflict (kind, ref) do nothing returning id"""), {"k": kind, "r": ref, "t": body})
    ).first()
    if row is None:
        old = (await s.execute(text(f"""select n.id from notify_event n
            where n.kind = :k and n.ref = :r and ({RETRYABLE})"""), {"k": kind, "r": ref})).first()
        if old is None:
            return "dup"                              # sent / logged / digested, or given up
        row = old
        await s.execute(text("""update notify_event set text = :t, status = 'pending'
            where id = :i"""), {"t": body, "i": row.id})
    await s.commit()
    if _state["sent"] >= MAX_PER_RUN:
        await s.execute(text("update notify_event set status = 'batched' where id = :i"),
                        {"i": row.id})
        await s.commit()
        return "batched"
    st = send(body)
    _state["sent"] += st == "sent"
    await s.execute(text("""update notify_event set status = cast(:s as varchar),
        attempts = attempts + 1,
        sent_at = case when cast(:s as text) in ('sent', 'logged') then clock_timestamp() end,
        error = :e
        where id = :i"""), {"s": "sent" if st == "sent" else "logged" if st == "logged"
                            else "failed", "e": None if st in ("sent", "logged") else st,
                            "i": row.id})
    await s.commit()
    return st.split()[0]


async def flush_batched(s, rid) -> str:
    """One summary line for messages over the per-run cap (they are marked 'batched')."""
    n = (await s.execute(text("""select count(*) from notify_event where status = 'batched'
        and created_at > now() - interval '1 hour' and kind <> 'SUMMARY'"""))).scalar()
    if not n:
        return "none"
    _state["sent"] = 0
    return await deliver(s, "SUMMARY", f"summary:{rid}",
                         f"Drishti: {n} more message(s) not sent individually (rate cap); "
                         f"see the Scanner / Live Radar pages.\n\n{LABEL}")


def _f(x, nd=2):
    return "-" if x is None else f"{float(x):.{nd}f}"


def _ist(t) -> str:
    return t.astimezone(IST).strftime("%H:%M")


# ---- message texts ------------------------------------------------------------------------

def alert_text(a: dict, odds: dict | None) -> str:
    return notify_ai.format_alert_ai(a, odds)


def outcome_text(a: dict, o: dict, took: dict | None, marked: str | None) -> str:
    return notify_ai.format_outcome_ai(a, o, took, marked)


# ---- delivery passes ----------------------------------------------------------------------

async def individual_15m(s, day: _dt.date) -> tuple[set[str], set[int]]:
    """(keys, alert ids) whose 15m alerts/outcomes of `day` go out individually: the top
    WATCHLIST_TOP_INDIVIDUAL of the watchlist the intraday scan used that day (the latest run
    before `day`), the day's Live Radar names, and alerts the user marked watching/took."""
    top = set((await s.execute(text("""
        with r as (select run_id from watchlist_run where session_date < :d
                   order by session_date desc, created_at desc limit 1)
        select instrument_key from watchlist_event where run_id = (select run_id from r)
          and event <> 'DROPPED' order by rank nulls last limit :n"""),
        {"d": day, "n": WATCHLIST_TOP_INDIVIDUAL})).scalars())
    radar = set((await s.execute(text("""select instrument_key from radar_event
        where session_date = :d"""), {"d": day})).scalars())
    marked = set((await s.execute(text("""select a.id from scan_alert a join journal_entry j
        on j.alert_id = a.id where a.session_date = :d and a.tf = '15m'
        and j.status in ('watching', 'took')"""), {"d": day})).scalars())
    return top | radar, marked


async def _sent_15m_keys(s, day: _dt.date) -> set[str]:
    """Stocks whose 15m alert of `day` already went out individually (one per stock per day)."""
    return set((await s.execute(text("""select distinct a.instrument_key from notify_event n
        join scan_alert a on n.ref = 'alert:' || a.id where n.kind = 'ALERT'
        and n.status in ('sent', 'logged', 'pending', 'failed') and a.tf = '15m'
        and a.session_date = :d"""), {"d": day})).scalars())


def digest_line(a: dict) -> str:
    ct = choose_target(float(a["entry_ref"]), float(a["stop"]), a["r1"], a["r2"], a["qty"] or 0)
    tag = f" [{ct['tags'][0]}]" if ct["tags"] else ""
    return (f"{a['symbol'] or a['instrument_key']} {a['detector']}: E {_f(a['entry_ref'])} "
            f"S {_f(a['stop'])} T {_f(ct['target'])} ({ct['target_basis']}) "
            f"net R:R {_f(ct['net_rr_target'])}{tag}")


async def _mark(s, kind: str, ids: list[int], ref: str) -> None:
    for i in ids:
        await s.execute(text("""insert into notify_event(kind, ref, status, text)
            values (:k, :r, 'digested', :t) on conflict (kind, ref) do nothing"""),
            {"k": kind, "r": f"{'alert' if kind == 'ALERT' else 'outcome'}:{i}",
             "t": f"in {ref}"})
    await s.commit()


async def deliver_alerts(s, tf: str | None = None, odds_wait_min: int = 40) -> dict:
    """Every alert from NOTIFY_SINCE without an ALERT event. 1d: one message each (a 1d alert
    waits up to `odds_wait_min` for its odds; scan_eod scores them right after the scan). 15m:
    exceptions one each, the rest one DIGEST_15M per bar."""
    rows = [dict(r) for r in (await s.execute(text(f"""
        select a.*, ol.mc_target_first, ol.mc_avg_r_net, ol.breakeven, ol.thin_sample,
               ol.verdict
        from scan_alert a left join odds_latest ol on ol.alert_id = a.id and ol.basis = 'alert'
        where a.session_date >= :since and (cast(:tf as text) is null or a.tf = :tf)
          and not exists (select 1 from notify_event n where n.kind = 'ALERT'
                          and n.ref = 'alert:' || a.id and not ({RETRYABLE}))
        order by a.bar_time, a.id"""), {"since": NOTIFY_SINCE, "tf": tf})).mappings()]
    out: dict = {}
    now_ = _dt.datetime.now(_dt.timezone.utc)
    groups: dict = {}
    exc: dict = {}
    for a in rows:
        if a["tf"] == "15m":
            if a["session_date"] not in exc:
                exc[a["session_date"]] = (*await individual_15m(s, a["session_date"]),
                                          await _sent_15m_keys(s, a["session_date"]))
            keys, marked, done = exc[a["session_date"]]
            # individual: a journal-marked alert, or the FIRST 15m alert of the day of a watchlist
            # top-N / Live Radar stock; everything else goes into the bar digest
            if a["id"] not in marked and (a["instrument_key"] not in keys
                                          or a["instrument_key"] in done):
                groups.setdefault(a["bar_time"], []).append(a)
                continue
            done.add(a["instrument_key"])
        has_odds = a.get("mc_target_first") is not None
        if a["tf"] == "1d" and not has_odds and now_ - a["created_at"] < \
                _dt.timedelta(minutes=odds_wait_min):
            out["waiting_odds"] = out.get("waiting_odds", 0) + 1
            continue
        st = await deliver(s, "ALERT", f"alert:{a['id']}", alert_text(a, a if has_odds else None))
        out[st] = out.get(st, 0) + 1
    for bt, al in groups.items():
        ref = f"digest15:{bt.astimezone(IST):%Y-%m-%dT%H:%M}"
        body = notify_ai.format_digest_15m_ai(bt, al)
        st = await deliver(s, "DIGEST_15M", ref, body)
        if st in ("sent", "logged", "dup"):
            await _mark(s, "ALERT", [a["id"] for a in al], ref)
        out[f"digest_{st}"] = out.get(f"digest_{st}", 0) + 1
    return out


def _outcome_word(r: dict) -> str:
    k = r["outcome"]
    return {"R1": f"target reached {_f(r['r_net'])} R", "STOP": f"stop hit {_f(r['r_net'])} R",
            "TIMEOUT": f"expired {_f(r['r_net'])} R"}.get(k, "entry not triggered")


_OUTCOME_SQL = f"""
    with l as (select distinct on (alert_id, basis) * from journal_outcome
               order by alert_id, basis, graded_at desc, id desc)
    select a.*, j.status as jstatus, j.actual_entry,
           (select n2.status from notify_event n2 where n2.kind = 'ALERT'
             and n2.ref = 'alert:' || a.id) as alert_status,
           o.outcome, o.entry_used, o.exit_price, o.exit_bar, o.target_used, o.r_net,
           t.outcome as t_outcome, t.entry_used as t_entry, t.r_net as t_r_net
    from scan_alert a join l o on o.alert_id = a.id and o.basis = 'alert'
    left join l t on t.alert_id = a.id and t.basis = 'took'
    left join journal_entry j on j.alert_id = a.id
    where a.session_date >= :since and o.outcome in ('R1', 'STOP', 'TIMEOUT', 'NO_FILL')
      and not exists (select 1 from notify_event n where n.kind = 'OUTCOME'
                          and n.ref = 'outcome:' || a.id and not ({RETRYABLE}))"""


async def deliver_outcomes(s) -> dict:
    """One OUTCOME message per final alert (target reached / stop hit / expired) for: every 1d
    alert, and a 15m alert whose own alert message went out individually. "Entry not triggered"
    (NO_FILL) is never sent alone; it goes into the end-of-day digest. A journal-marked
    (watching / took) alert always gets its own outcome."""
    rows = [dict(r) for r in (await s.execute(text(_OUTCOME_SQL + """
        order by o.exit_bar nulls first, a.id"""), {"since": NOTIFY_SINCE})).mappings()]
    out: dict = {}
    for r in rows:
        marked_j = r["jstatus"] in ("watching", "took")
        if not marked_j:
            if r["outcome"] == "NO_FILL":
                continue                     # "entry not triggered": end-of-day digest only
            if r["tf"] == "15m" and r["alert_status"] not in ("sent", "logged"):
                continue                     # its alert went in a digest: so does its outcome
        mk = None
        if r["jstatus"] in ("watching", "took"):
            mk = r["jstatus"] + (f" at {_f(r['actual_entry'])}" if r["actual_entry"] else "")
        took = {"entry_used": r["t_entry"], "outcome": r["t_outcome"], "r_net": r["t_r_net"]} \
            if r["t_outcome"] else None
        st = await deliver(s, "OUTCOME", f"outcome:{r['id']}", outcome_text(r, r, took, mk))
        out[st] = out.get(st, 0) + 1
    return out


async def deliver_outcomes_15m(s, day: _dt.date) -> str:
    """The end-of-day digest of `day`'s 15m outcomes that were not sent individually: counts,
    net R, by setup, and one compact line per alert. Call after the session's last bar."""
    if day < NOTIFY_SINCE:
        return "before NOTIFY_SINCE"
    rows = [dict(r) for r in (await s.execute(text(_OUTCOME_SQL + """
        and a.tf = '15m' and a.session_date = :d order by a.detector, a.id"""),
        {"since": NOTIFY_SINCE, "d": day})).mappings()]
    rows = [r for r in rows if r["jstatus"] not in ("watching", "took")]
    # swing alerts whose buy-stop never triggered (graded today or earlier, not yet reported)
    swing_nf = [dict(r) for r in (await s.execute(text(_OUTCOME_SQL + """
        and a.tf = '1d' and o.outcome = 'NO_FILL' order by a.session_date, a.id"""),
        {"since": NOTIFY_SINCE})).mappings()]
    swing_nf = [r for r in swing_nf if r["jstatus"] not in ("watching", "took")]
    open_n = (await s.execute(text("""
        with l as (select distinct on (alert_id) alert_id, outcome from journal_outcome
                   where basis = 'alert' order by alert_id, graded_at desc, id desc)
        select count(*) from scan_alert a left join l on l.alert_id = a.id
        where a.tf = '15m' and a.session_date = :d
          and (l.outcome is null or l.outcome = 'OPEN')"""), {"d": day})).scalar()
    if not rows and not swing_nf:
        return "nothing to send"
    ref = f"outcome15:{day}"
    cnt = {k: sum(1 for r in rows if r["outcome"] == k) for k in ("R1", "STOP", "TIMEOUT",
                                                                    "NO_FILL")}
    filled = [float(r["r_net"]) for r in rows if r["outcome"] != "NO_FILL"
              and r["r_net"] is not None]
    body = notify_ai.format_outcomes_15m_ai(day, rows, swing_nf, open_n, cnt, filled)
    st = await deliver(s, "OUTCOME_15M", ref, body)
    if st in ("sent", "logged", "dup"):
        await _mark(s, "OUTCOME", [r["id"] for r in rows + swing_nf], ref)
    return st


async def deliver_scout(s, day: _dt.date, top_n: int = 10) -> str:
    if day < NOTIFY_SINCE:
        return "before NOTIFY_SINCE"
    run = (await s.execute(text("""select run_id, regime, counts from watchlist_run
        where session_date = :d order by created_at desc limit 1"""), {"d": day})).mappings().first()
    if run is None:
        return "no watchlist"
    rows = (await s.execute(text("""select symbol, instrument_key, rank, rs_score, metrics, flags
        from watchlist_event where run_id = :r and event <> 'DROPPED' order by rank"""),
        {"r": run["run_id"]})).mappings().all()
    c, reg = run["counts"] or {}, run["regime"] or {}
    body = notify_ai.format_scout_ai(day, rows, c, reg, top_n)
    return await deliver(s, "SCOUT_DIGEST", f"scout:{day}", body)


async def deliver_radar(s, day: _dt.date) -> dict:
    if day < NOTIFY_SINCE:
        return {}
    rows = (await s.execute(text(f"""select r.* from radar_event r where r.session_date = :d
        and not exists (select 1 from notify_event n where n.kind = 'RADAR_NEW'
                          and n.ref = 'radar:' || r.id and not ({RETRYABLE})) order by r.bar_time, r.id"""),
        {"d": day})).mappings().all()
    out: dict = {}
    for r in rows:
        body = notify_ai.format_radar_ai(r)
        st = await deliver(s, "RADAR_NEW", f"radar:{r['id']}", body)
        out[st] = out.get(st, 0) + 1
    return out
