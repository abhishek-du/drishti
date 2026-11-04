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
per scanner alert, with the actionable target after the broken-R1 rule and the odds verdict for
1d alerts), OUTCOME (one per alert: target reached / stop hit / expired), SUMMARY (overflow).
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

log = logging.getLogger("drishti.scanner.notify")

LABEL = "Decision support — not a validated edge, not a prediction. Paper only. Your call."
NOTIFY_SINCE = _dt.date(2026, 11, 5)
MAX_PER_RUN = 25
GAP_S = 1.1
CAVEAT_15M = ("15m levels use unadjusted 15m bars (DEVIATIONS D5): unreliable for a stock "
              "with a split/bonus in the last ~25 days, until the 11-20 switch work adjusts them.")

_state = {"last": 0.0, "sent": 0}


def send(msg: str) -> str:
    """'sent' | 'logged' (no credentials) | 'failed ...'."""
    cfg = get_settings()
    if not (cfg.DRISHTI_TELEGRAM_BOT_TOKEN and cfg.DRISHTI_TELEGRAM_CHAT_ID):
        log.info("telegram not configured; message logged only:\n%s", msg)
        return "logged"
    wait = _state["last"] + GAP_S - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    try:
        r = httpx.post(f"https://api.telegram.org/bot{cfg.DRISHTI_TELEGRAM_BOT_TOKEN}/sendMessage",
                       json={"chat_id": cfg.DRISHTI_TELEGRAM_CHAT_ID, "text": msg[:4000],
                             "disable_web_page_preview": True}, timeout=20)
        _state["last"] = time.monotonic()
        return "sent" if r.status_code == 200 else f"failed HTTP {r.status_code}"
    except httpx.HTTPError as e:                  # never echo the URL (it holds the token)
        _state["last"] = time.monotonic()
        return f"failed {type(e).__name__}"


async def deliver(s, kind: str, ref: str, body: str) -> str:
    """Claim (kind, ref), then send once. Returns sent / logged / failed / dup / batched."""
    row = (await s.execute(text("""insert into notify_event(kind, ref, text) values (:k, :r, :t)
        on conflict (kind, ref) do nothing returning id"""), {"k": kind, "r": ref, "t": body})
    ).first()
    if row is None:
        old = (await s.execute(text("""select id, status, attempts from notify_event
            where kind = :k and ref = :r"""), {"k": kind, "r": ref})).first()
        if old is None or old.status != "failed" or old.attempts >= 3:
            return "dup"
        row = old
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
    e, st = float(a["entry_ref"]), float(a["stop"])
    ct = choose_target(e, st, a["r1"], a["r2"], a["qty"] or 0)
    risk = e - st
    gross = (ct["target"] - e) / risk
    ctx = a.get("context") or {}
    tgt = {"R1": "R1", "R2": "R2 (R1 not actionable)", "2R": "2R"}[ct["target_basis"]]
    if ct["target_basis"] == "2R" and a["r1"] is not None:
        tgt = "2R (R1 not actionable)"
    lines = [f"ALERT · {a['symbol'] or a['instrument_key']} · {a['detector']} [{a['tf']}]"
             + (" · Live Radar name" if ctx.get("source") == "radar" else ""),
             f"Bar {a['session_date']} {_ist(a['bar_time'])} IST" if a["tf"] == "15m"
             else f"Session {a['session_date']} (EOD)",
             f"Entry (ref) {_f(e)} · Stop {_f(st)} · Target {_f(ct['target'])} {tgt}",
             f"Risk/share {_f(risk)} · R:R gross {gross:.2f} · NET {_f(ct['net_rr_target'])}"
             f" · qty {a['qty']}"]
    if ct["tags"]:
        lines.append(f"Tag: {ct['tags'][0]} (R1 {_f(a['r1'])}: net R at R1 "
                     f"{_f(ct['net_r_at_r1'])}) — R1 kept only as a reference level")
    if a["tf"] == "15m":
        lines.append("Odds: pending (15m engine)")
    elif odds:
        geo = "geometry negative" if (odds.get("mc_avg_r_net") or 0) < 0 else "geometry positive"
        thin = " · thin sample" if odds.get("thin_sample") else ""
        lines.append(f"History: target-first ~{float(odds['mc_target_first']):.0%}, avg net R "
                     f"{float(odds['mc_avg_r_net']):+.2f}, {geo} (break-even "
                     f"{float(odds['breakeven']):.0%}){thin}")
    else:
        lines.append("Odds: not scored yet")
    off = [n for n, v in (("regime", ctx.get("regime")), ("spec gate", ctx.get("spec_gate")))
           if v == "OFF"]
    if off:
        lines.append(f"Note: {' and '.join(off)} OFF")
    if a["tf"] == "15m":
        lines.append(CAVEAT_15M)
    lines += ["", LABEL]
    return "\n".join(lines)


def outcome_text(a: dict, o: dict, took: dict | None, marked: str | None) -> str:
    name = f"{a['symbol'] or a['instrument_key']} {a['detector']} [{a['tf']}]"
    when = (f"{_ist(o['exit_bar'])}–{_ist(o['exit_bar'] + _dt.timedelta(minutes=15))} IST bar"
            if a["tf"] == "15m" and o.get("exit_bar") else
            f"on {o['exit_bar'].astimezone(IST).date()}" if o.get("exit_bar") else "")
    tgt, st = _f(o.get("target_used")), _f(a["stop"])
    k = o["outcome"]
    if k == "R1":
        what = f"target {tgt} reached {when} (net R {_f(o['r_net'])})"
    elif k == "STOP":
        what = f"stop {st} hit {when}, exit {_f(o['exit_price'])} (net R {_f(o['r_net'])})"
    elif k == "TIMEOUT":
        what = (f"expired, neither hit, closed {_f(o['exit_price'])} {when} "
                f"(net R {_f(o['r_net'])})")
    else:
        what = (f"expired, entry {_f(a['entry_ref'])} not triggered "
                f"{'in the next 15m bar' if a['tf'] == '15m' else 'on the next session'}"
                " (no trade)")
    lines = [f"OUTCOME · {name} · alert #{a['id']} ({a['session_date']})", what]
    if marked:
        lines.append(f"Your marked trade ({marked})"
                     + (f": your entry {_f(took['entry_used'])}, {took['outcome']}, net R "
                        f"{_f(took['r_net'])}" if took and took.get("r_net") is not None else ""))
    if a["tf"] == "15m":
        lines.append(CAVEAT_15M)
    lines += ["", LABEL]
    return "\n".join(lines)


# ---- delivery passes ----------------------------------------------------------------------

async def deliver_alerts(s, tf: str | None = None, odds_wait_min: int = 40) -> dict:
    """Every alert from NOTIFY_SINCE without an ALERT message. A 1d alert waits up to
    `odds_wait_min` for its odds (scan_eod scores them right after the scan)."""
    rows = [dict(r) for r in (await s.execute(text("""
        select a.*, ol.mc_target_first, ol.mc_avg_r_net, ol.breakeven, ol.thin_sample,
               ol.verdict
        from scan_alert a left join odds_latest ol on ol.alert_id = a.id and ol.basis = 'alert'
        where a.session_date >= :since and (cast(:tf as text) is null or a.tf = :tf)
          and not exists (select 1 from notify_event n where n.kind = 'ALERT'
                          and n.ref = 'alert:' || a.id)
        order by a.id"""), {"since": NOTIFY_SINCE, "tf": tf})).mappings()]
    out: dict = {}
    now_ = _dt.datetime.now(_dt.timezone.utc)
    for a in rows:
        has_odds = a.get("mc_target_first") is not None
        if a["tf"] == "1d" and not has_odds and now_ - a["created_at"] < \
                _dt.timedelta(minutes=odds_wait_min):
            out["waiting_odds"] = out.get("waiting_odds", 0) + 1
            continue
        st = await deliver(s, "ALERT", f"alert:{a['id']}", alert_text(a, a if has_odds else None))
        out[st] = out.get(st, 0) + 1
    return out


async def deliver_outcomes(s) -> dict:
    """One OUTCOME message per alert (from NOTIFY_SINCE) once its latest 'alert' grade is final
    (R1 = target, STOP, TIMEOUT = expired, NO_FILL = entry not triggered)."""
    rows = [dict(r) for r in (await s.execute(text("""
        with l as (select distinct on (alert_id, basis) * from journal_outcome
                   order by alert_id, basis, graded_at desc, id desc)
        select a.*, j.status as jstatus, j.actual_entry,
               o.outcome, o.entry_used, o.exit_price, o.exit_bar, o.target_used, o.r_net,
               t.outcome as t_outcome, t.entry_used as t_entry, t.r_net as t_r_net
        from scan_alert a join l o on o.alert_id = a.id and o.basis = 'alert'
        left join l t on t.alert_id = a.id and t.basis = 'took'
        left join journal_entry j on j.alert_id = a.id
        where a.session_date >= :since and o.outcome in ('R1', 'STOP', 'TIMEOUT', 'NO_FILL')
          and not exists (select 1 from notify_event n where n.kind = 'OUTCOME'
                          and n.ref = 'outcome:' || a.id)
        order by o.exit_bar nulls first, a.id"""), {"since": NOTIFY_SINCE})).mappings()]
    out: dict = {}
    for r in rows:
        marked = None
        if r["jstatus"] in ("watching", "took"):
            marked = r["jstatus"] + (f" at {_f(r['actual_entry'])}" if r["actual_entry"] else "")
        took = {"entry_used": r["t_entry"], "outcome": r["t_outcome"], "r_net": r["t_r_net"]} \
            if r["t_outcome"] else None
        st = await deliver(s, "OUTCOME", f"outcome:{r['id']}", outcome_text(r, r, took, marked))
        out[st] = out.get(st, 0) + 1
    return out


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
    lines = [f"SCOUT WATCHLIST · {day}: {len(rows)} stocks (added {c.get('added', '-')}, "
             f"dropped {c.get('dropped', '-')})",
             "Every listed stock passed all of: trend (close > SMA50 > SMA200, SMA200 rising), "
             "RS top 25 %, beats Nifty 500 over 3 months, within 15 % of the 52-week high, "
             "ATR 2–6 %, no 2/5 % price band.", f"Top {min(top_n, len(rows))} by RS:"]
    for r in rows[:top_n]:
        m = r["metrics"] or {}
        res = " · results ahead" if (r["flags"] or {}).get("results_meeting") else ""
        lines.append(f"{r['rank']}. {r['symbol'] or r['instrument_key']} — RS "
                     f"{_f(r['rs_score'])}, {_f(m.get('dist_52w_high_pct'), 1)} % below the 52w "
                     f"high, ATR {_f((m.get('atr_pct') or 0) * 100, 1)} %{res}")
    off = [n for n, v in (("regime", reg.get("status")), ("spec gate", reg.get("spec_gate")))
           if v == "OFF"]
    if off:
        lines.append(f"Note: {' and '.join(off)} OFF")
    lines += ["", "A watchlist is context, not a signal.", LABEL]
    return await deliver(s, "SCOUT_DIGEST", f"scout:{day}", "\n".join(lines))


async def deliver_radar(s, day: _dt.date) -> dict:
    from drishti.scanner.radar import CAVEAT, LABEL as RL
    if day < NOTIFY_SINCE:
        return {}
    rows = (await s.execute(text("""select r.* from radar_event r where r.session_date = :d
        and not exists (select 1 from notify_event n where n.kind = 'RADAR_NEW'
                        and n.ref = 'radar:' || r.id) order by r.bar_time, r.id"""),
        {"d": day})).mappings().all()
    out: dict = {}
    for r in rows:
        m = r["metrics"] or {}
        body = "\n".join([
            f"LIVE RADAR · {r['symbol'] or r['instrument_key']} newly meets Scout's criteria",
            f"Time: the {_ist(r['bar_time'])}–{_ist(r['bar_time'] + _dt.timedelta(minutes=15))} "
            "IST bar",
            "Flipped since T-1: " + ", ".join(r["reason"]),
            f"Close {_f(m.get('close'))} · RS {_f(m.get('rs_score'))} (cut {_f(m.get('rs_cut'))})"
            f" · {_f(m.get('dist_52w_high_pct'), 1)} % below the 52w high",
            "", RL, CAVEAT, LABEL])
        st = await deliver(s, "RADAR_NEW", f"radar:{r['id']}", body)
        out[st] = out.get(st, 0) + 1
    return out
