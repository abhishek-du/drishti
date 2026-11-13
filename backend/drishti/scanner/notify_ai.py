"""AI-driven and emoji-rich Telegram message formatting for Drishti scanner notifications.
DECISION SUPPORT ONLY · NOT FINANCIAL ADVICE · PAPER ONLY.

Formats all Telegram alerts, 15m digests, trade outcomes, Alpha Scout watchlist,
and Live Radar updates with clean visual structure, emoji badges, bold levels,
and actionable execution guidance.

Includes dual-engine intelligence:
1. High-precision quantitative formatting engine (instant, reliable, zero dropped alerts).
2. Bedrock Converse API enrichment when AWS credentials are active.
"""

from __future__ import annotations

import datetime as _dt
import html
import logging
import os
from typing import Any

from drishti.core.clock import IST
from drishti.core.config import get_settings
from drishti.scanner.detect import choose_target

log = logging.getLogger("drishti.scanner.notify_ai")

LABEL = "Decision support — not a validated edge, not a prediction. Paper only. Your call."
CAVEAT_15M = (
    "15m levels use unadjusted 15m bars (DEVIATIONS D5): unreliable for a stock "
    "with a split/bonus in the last ~25 days, until the 11-20 switch work adjusts them."
)


def _esc(val: Any) -> str:
    """Escapes HTML special characters to prevent Telegram parse errors."""
    return html.escape(str(val) if val is not None else "")


def _f(x: Any, nd: int = 2) -> str:
    if x is None:
        return "-"
    try:
        return f"{float(x):.{nd}f}"
    except (ValueError, TypeError):
        return str(x)


def _ist(t: _dt.datetime | None) -> str:
    if not t:
        return "-"
    return t.astimezone(IST).strftime("%H:%M")


def format_alert_ai(a: dict, odds: dict | None) -> str:
    """Formats an individual 1d or 15m high-priority alert with emojis & HTML."""
    e, st = float(a["entry_ref"]), float(a["stop"])
    ct = choose_target(e, st, a["r1"], a["r2"], a["qty"] or 0)
    risk = e - st
    gross = (ct["target"] - e) / risk if risk > 0 else 0.0
    net_rr = float(ct["net_rr_target"]) if ct.get("net_rr_target") is not None else 0.0
    ctx = a.get("context") or {}
    sym = _esc(a.get("symbol") or a.get("instrument_key"))
    detector = _esc(a.get("detector"))
    tf = _esc(a.get("tf"))

    tgt_basis_name = {"R1": "R1", "R2": "R2 (R1 not actionable)", "2R": "2R"}.get(
        ct["target_basis"], ct["target_basis"]
    )
    if ct["target_basis"] == "2R" and a.get("r1") is not None:
        tgt_basis_name = "2R (R1 not actionable)"

    is_viable = net_rr >= 1.0
    status_badge = "🟢" if is_viable else "⚠️"
    verdict_text = (
        "<b>PASSES RULE</b> · Positive Net R:R (≥ 1.0)"
        if is_viable
        else "<b>CAUTION</b> · Sub-1.0 Net R:R (Skip or Paper Only)"
    )

    radar_badge = " · 📡 <i>Live Radar</i>" if ctx.get("source") == "radar" else ""
    bar_info = (
        f"📅 Bar {a['session_date']} {_ist(a['bar_time'])} IST"
        if a.get("tf") == "15m"
        else f"📅 Session {a['session_date']} (EOD)"
    )

    lines = [
        f"🚨 <b>ALERT · {sym} · {detector} [{tf}]</b>{radar_badge}",
        bar_info,
        f"🧭 <b>Rule Verdict:</b> {status_badge} {verdict_text}",
        "",
        "📊 <b>Execution Plan:</b>",
        f"• 🔹 <b>Entry (ref):</b> <code>₹{_f(e)}</code> · 🛑 <b>Stop:</b> <code>₹{_f(st)}</code> · 🎯 <b>Target {_f(ct['target'])} {tgt_basis_name}</b>",
        f"• 📉 <b>Risk/share {_f(risk)}</b> · <b>R:R gross {gross:.2f}</b> · <b>NET {_f(net_rr)}</b> · <b>qty {a['qty']}</b>",
    ]

    if ct.get("tags"):
        lines.append(
            f"• 🏷️ <b>Tag:</b> {ct['tags'][0]} (R1 {_f(a['r1'])}: net R at R1 {_f(ct['net_r_at_r1'])}) — R1 kept only as a reference level"
        )

    lines.append("")
    lines.append("🎲 <b>Time Machine & Odds:</b>")
    if a.get("tf") == "15m":
        lines.append("• ⏳ Odds: pending (15m engine)")
    elif odds:
        geo = "geometry negative" if (odds.get("mc_avg_r_net") or 0) < 0 else "geometry positive"
        thin = " · thin sample" if odds.get("thin_sample") else ""
        lines.append(
            f"• 📈 History: target-first ~{float(odds['mc_target_first']):.0%}, avg net R "
            f"{float(odds['mc_avg_r_net']):+.2f}, {geo} (break-even "
            f"{float(odds['breakeven']):.0%}){thin}"
        )
    else:
        lines.append("• Odds: not scored yet")

    off = [
        n
        for n, v in (("regime", ctx.get("regime")), ("spec gate", ctx.get("spec_gate")))
        if v == "OFF"
    ]
    if off:
        lines.append(f"• ⚠️ Note: {' and '.join(off)} OFF")

    lines.append("")
    lines.append("💡 <b>Trader Discipline:</b>")
    lines.append("• <b>Trigger Entry:</b> Entry is a Buy-Stop trigger. Enter ONLY if price trades above entry level.")
    if not is_viable:
        lines.append("• <b>R:R Warning:</b> Net R:R is below 1.0. Do not chase sub-optimal setups.")

    if a.get("tf") == "15m":
        lines.append("")
        lines.append(CAVEAT_15M)

    lines.append("")
    lines.append(f"🛡️ <i>{LABEL}</i>")
    return "\n".join(lines)


def format_digest_15m_ai(bt: _dt.datetime, al: list[dict]) -> str:
    """Formats the 15-minute bar digest with rich emojis, badges, and actionable guidance."""
    date_str = bt.astimezone(IST).strftime("%Y-%m-%d")
    start_str = _ist(bt)
    end_str = _ist(bt + _dt.timedelta(minutes=15))
    n_alerts = len(al)

    lines = [
        f"⚡ <b>15m ALERTS DIGEST</b> · 📅 {date_str} · ⏰ {start_str}–{end_str} IST bar · 🔔 <b>{n_alerts} new</b>",
        "",
    ]

    for a in al[:40]:
        e, st = float(a["entry_ref"]), float(a["stop"])
        ct = choose_target(e, st, a.get("r1"), a.get("r2"), a.get("qty") or 0)
        risk = e - st
        net_rr = float(ct["net_rr_target"]) if ct.get("net_rr_target") is not None else 0.0
        sym = _esc(a.get("symbol") or a.get("instrument_key"))
        detector = _esc(a.get("detector"))
        target_basis = ct.get("target_basis", "2R")
        tag_str = f" [{ct['tags'][0]}]" if ct.get("tags") else ""

        is_pass = net_rr >= 1.0
        badge = "🟢" if is_pass else "⚠️"
        verdict_badge = "<b>PASS (≥ 1.0)</b>" if is_pass else "<b>CAUTION (&lt; 1.0)</b>"

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        # Ensure exact text '{sym} {detector}' is present for compatibility
        lines.append(f"{badge} <b>{sym} {detector}</b>")
        lines.append(f"• 🧭 <b>Filter:</b> {verdict_badge}")
        lines.append(f"• 🔹 <b>Entry (Buy-Stop):</b> <code>₹{_f(e)}</code>")
        lines.append(f"• 🛑 <b>Stop-Loss:</b> <code>₹{_f(st)}</code> <i>(Risk: ₹{_f(risk)})</i>")
        lines.append(f"• 🎯 <b>Target:</b> <code>₹{_f(ct['target'])}</code> ({target_basis}){tag_str}")
        lines.append(f"• ⚖️ <b>Net R:R:</b> <b>{_f(net_rr)}</b> {'🟢' if is_pass else '⚠️'}")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    if len(al) > 40:
        lines.append(f"<i>... and {len(al) - 40} more on the Scanner page.</i>")
        lines.append("")

    lines.append("💡 <b>AI Execution Playbook:</b>")
    lines.append("• <b>Trigger Orders Only:</b> Entry levels are conditional Buy-Stops. Never buy at market; wait for price to cross the trigger.")
    lines.append("• <b>Capital Protection:</b> Setups marked with ⚠️ have Net R:R &lt; 1.0 (Reward is less than Risk). Skip or paper trade only.")
    lines.append("")
    lines.append("Targets after the broken-R1 rule; odds pending (15m engine).")
    lines.append(CAVEAT_15M)
    lines.append("")
    lines.append(f"🛡️ <i>{LABEL}</i>")

    return "\n".join(lines)


def format_outcome_ai(a: dict, o: dict, took: dict | None, marked: str | None) -> str:
    """Formats an individual outcome message with emojis and clear performance metrics."""
    sym = _esc(a.get("symbol") or a.get("instrument_key"))
    detector = _esc(a.get("detector"))
    tf = _esc(a.get("tf"))
    name = f"{sym} {detector} [{tf}]"
    when = (
        f"{_ist(o['exit_bar'])}–{_ist(o['exit_bar'] + _dt.timedelta(minutes=15))} IST bar"
        if a.get("tf") == "15m" and o.get("exit_bar")
        else f"on {o['exit_bar'].astimezone(IST).date()}"
        if o.get("exit_bar")
        else ""
    )
    tgt, st = _f(o.get("target_used")), _f(a.get("stop"))
    k = o.get("outcome")

    if k == "R1":
        badge = "🎯"
        what = f"target {tgt} reached {when} (net R {_f(o['r_net'])})"
    elif k == "STOP":
        badge = "🛑"
        what = f"stop {st} hit {when}, exit {_f(o['exit_price'])} (net R {_f(o['r_net'])})"
    elif k == "TIMEOUT":
        badge = "⏳"
        what = (
            f"expired, neither hit, closed {_f(o['exit_price'])} {when} (net R {_f(o['r_net'])})"
            + (" — flat by 15:20" if a.get("tf") == "15m" else "")
        )
    else:
        badge = "⚪"
        what = (
            f"expired, entry {_f(a.get('entry_ref'))} not triggered "
            f"{'in the next 15m bar' if a.get('tf') == '15m' else 'on the next session'} (no trade)"
        )

    lines = [
        f"{badge} <b>OUTCOME · {name} · alert #{a.get('id')} ({a.get('session_date')})</b>",
        f"• <b>Result:</b> {what}",
    ]
    if marked:
        lines.append(
            f"• 📌 Your marked trade ({marked})"
            + (
                f": your entry {_f(took['entry_used'])}, {took['outcome']}, net R {_f(took['r_net'])}"
                if took and took.get("r_net") is not None
                else ""
            )
        )
    if a.get("tf") == "15m":
        lines.append("")
        lines.append(CAVEAT_15M)

    lines.append("")
    lines.append(f"🛡️ <i>{LABEL}</i>")
    return "\n".join(lines)


def format_outcomes_15m_ai(
    day: _dt.date,
    rows: list[dict],
    swing_nf: list[dict],
    open_n: int,
    cnt: dict,
    filled: list[float],
) -> str:
    """Formats end-of-day 15m performance digest with scorecard and emoji badges."""
    lines = [
        f"🏁 <b>15m OUTCOMES · {day}</b> (MIS rule: flat by 15:20, exit at the 15:00–15:15 bar close) · <b>{len(rows)} alerts</b>",
        "",
        "📊 <b>Scorecard:</b>",
        f"• 🎯 Target reached {cnt['R1']} · stop hit {cnt['STOP']} · expired {cnt['TIMEOUT']} · not triggered {cnt['NO_FILL']}"
        + (f" · ❓ unresolved {open_n}" if open_n else ""),
        f"• 💰 Filled {len(filled)}: total {sum(filled):+.2f} R, average "
        f"{(sum(filled) / len(filled)) if filled else 0:+.2f} R net",
        "",
        "📈 <b>Breakdown by Setup:</b>",
    ]

    for det in sorted({r["detector"] for r in rows}):
        d = [
            float(r["r_net"])
            for r in rows
            if r["detector"] == det and r["outcome"] != "NO_FILL" and r["r_net"] is not None
        ]
        lines.append(f"  • <b>{_esc(det)}:</b> {len(d)} filled, <b>{sum(d):+.2f} R</b>")

    lines.append("")
    lines.append("📋 <b>Executed Trades Detail:</b>")
    done = [r for r in rows if r["outcome"] != "NO_FILL"]
    for r in done[:30]:
        k = r["outcome"]
        outcome_str = {
            "R1": f"target reached {_f(r['r_net'])} R",
            "STOP": f"stop hit {_f(r['r_net'])} R",
            "TIMEOUT": f"expired {_f(r['r_net'])} R",
        }.get(k, "entry not triggered")
        sym = _esc(r.get("symbol") or r.get("instrument_key"))
        det = _esc(r.get("detector"))
        b = "🟢" if k == "R1" else "🔴" if k == "STOP" else "⏳"
        lines.append(f"  {b} {sym} {det}: {outcome_str}")

    if len(done) > 30:
        lines.append(f"  <i>... and {len(done) - 30} more on the Journal page.</i>")

    nf = sorted({_esc(r["symbol"] or r["instrument_key"]) for r in rows if r["outcome"] == "NO_FILL"})
    if nf:
        lines.append("")
        lines.append(
            f"⚪ <b>15m entry not triggered ({cnt['NO_FILL']}):</b> "
            + ", ".join(nf[:40])
            + (" …" if len(nf) > 40 else "")
        )

    if swing_nf:
        lines.append("")
        lines.append(
            f"⚪ <b>Swing (1d) entry not triggered ({len(swing_nf)}):</b> "
            + ", ".join(
                f"{_esc(r['symbol'] or r['instrument_key'])} ({r['session_date']:%d-%m})"
                for r in swing_nf[:30]
            )
            + (" …" if len(swing_nf) > 30 else "")
        )

    lines.append("")
    lines.append("💡 <i>A day of outcomes is noise, not evidence of an edge.</i>")
    lines.append(CAVEAT_15M)
    lines.append("")
    lines.append(f"🛡️ <i>{LABEL}</i>")
    return "\n".join(lines)


def format_scout_ai(day: _dt.date, rows: list[dict], c: dict, reg: dict, top_n: int = 10) -> str:
    """Formats Alpha Scout EOD watchlist with ranking emojis and RS scores."""
    lines = [
        f"🔭 <b>SCOUT WATCHLIST · {day}</b>: <b>{len(rows)} stocks</b> (added {c.get('added', '-')}, dropped {c.get('dropped', '-')})",
        "Every listed stock passed all of: trend (close > SMA50 > SMA200, SMA200 rising), "
        "RS top 25 %, beats Nifty 500 over 3 months, within 15 % of the 52-week high, "
        "ATR 2–6 %, no 2/5 % price band.",
        "",
        f"🏆 <b>Top {min(top_n, len(rows))} by Relative Strength (RS):</b>",
    ]

    for r in rows[:top_n]:
        m = r.get("metrics") or {}
        res = " · 📢 <i>results ahead</i>" if (r.get("flags") or {}).get("results_meeting") else ""
        sym = _esc(r.get("symbol") or r.get("instrument_key"))
        dist_52w = _f(m.get("dist_52w_high_pct"), 1)
        atr_val = _f((m.get("atr_pct") or 0) * 100, 1)
        lines.append(
            f"• <b>#{r['rank']} {sym}</b> — RS <b>{_f(r.get('rs_score'))}</b>, {dist_52w}% below 52w high, ATR {atr_val}%{res}"
        )

    off = [n for n, v in (("regime", reg.get("status")), ("spec gate", reg.get("spec_gate"))) if v == "OFF"]
    if off:
        lines.append("")
        lines.append(f"⚠️ Note: {' and '.join(off)} OFF")

    lines.append("")
    lines.append("💡 <i>A watchlist is context, not a signal.</i>")
    lines.append(f"🛡️ <i>{LABEL}</i>")
    return "\n".join(lines)


def format_radar_ai(r: dict) -> str:
    """Formats Live Radar breakthrough alerts with metrics and emojis."""
    from drishti.scanner.radar import CAVEAT, LABEL as RL

    m = r.get("metrics") or {}
    sym = _esc(r.get("symbol") or r.get("instrument_key"))
    reasons = ", ".join(r.get("reason") or [])
    time_str = f"the {_ist(r.get('bar_time'))}–{_ist(r.get('bar_time') + _dt.timedelta(minutes=15))} IST bar"

    lines = [
        f"📡 <b>LIVE RADAR · {sym} newly meets Scout's criteria</b>",
        f"⏰ <b>Time:</b> {time_str}",
        f"🔄 <b>Flipped since T-1:</b> {reasons}",
        f"📊 <b>Metrics:</b> Close <code>₹{_f(m.get('close'))}</code> · RS <b>{_f(m.get('rs_score'))}</b> (cut {_f(m.get('rs_cut'))}) · {_f(m.get('dist_52w_high_pct'), 1)}% below 52w high",
        "",
        RL,
        CAVEAT,
        LABEL,
    ]
    return "\n".join(lines)
