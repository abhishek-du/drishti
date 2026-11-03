"""Telegram notification of new scan alerts. Optional: without DRISHTI_TELEGRAM_BOT_TOKEN and
DRISHTI_TELEGRAM_CHAT_ID (in .env, never printed) the message is only logged. Read-only on
everything else; PAPER ONLY."""

from __future__ import annotations

import logging

import httpx
from sqlalchemy import text

from drishti.core.config import get_settings
from drishti.scanner.detect import LABEL

log = logging.getLogger("drishti.scanner.notify")


def message(title: str, rows: list[dict]) -> str:
    lines = [f"Drishti {title}: {len(rows)} new alert(s)", LABEL, ""]
    for r in rows[:30]:
        r1 = f"{float(r['r1']):.2f}" if r["r1"] is not None else "-"
        rr = f"{float(r['rr_r1']):.1f}" if r["rr_r1"] is not None else "-"
        lines.append(f"{r['symbol'] or r['instrument_key']} {r['detector']} [{r['tf']}] "
                     f"entry {float(r['entry_ref']):.2f} stop {float(r['stop']):.2f} "
                     f"R1 {r1} RR {rr} qty {r['qty']}")
    if len(rows) > 30:
        lines.append(f"... and {len(rows) - 30} more (see the Drishti scanner page)")
    return "\n".join(lines)


async def alerts(s, ids: list[int], title: str) -> str:
    if not ids:
        return "nothing to send"
    rows = [dict(r) for r in (await s.execute(text("""select symbol, instrument_key, detector,
        tf, entry_ref, stop, r1, rr_r1, qty from scan_alert where id = any(:i)
        order by detector, symbol"""), {"i": ids})).mappings()]
    msg = message(title, rows)
    cfg = get_settings()
    if not (cfg.DRISHTI_TELEGRAM_BOT_TOKEN and cfg.DRISHTI_TELEGRAM_CHAT_ID):
        log.info("telegram not configured; message logged only:\n%s", msg)
        return "not configured (logged)"
    try:
        r = httpx.post(f"https://api.telegram.org/bot{cfg.DRISHTI_TELEGRAM_BOT_TOKEN}/sendMessage",
                       json={"chat_id": cfg.DRISHTI_TELEGRAM_CHAT_ID, "text": msg}, timeout=20)
        return "sent" if r.status_code == 200 else f"failed HTTP {r.status_code}"
    except httpx.HTTPError as e:                  # never echo the URL (it holds the token)
        return f"failed {type(e).__name__}"
