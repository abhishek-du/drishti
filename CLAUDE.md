# Drishti: instructions for Claude (every worktree / track)

Drishti is a swing-first, agentic chart-reading + ML + **paper-trading** system for NSE. It is separate from Prajna (`/home/cis/windows/prajna`).

**Docs:**
- `docs/DESIGN.md`: the plan, the timeline and P1c.
- `docs/SPEC_drishti-swing-v1.md`: **FROZEN**.
- `docs/SPEC_CLARIFICATIONS.md`: SC1–SC6.
- `docs/PREREG_M1.md`, `docs/FREEZE_P0.md`, `docs/DEVIATIONS.md`.
- `docs/SWITCH_TO_B.md`: the Option-B switch, Fri 2026-11-20.
- `docs/PRAJNA_INVENTORY.md`, `docs/LIVE.md`, `docs/TRACKS.md`.

## Hard rules

- **PAPER ONLY.** Never place, modify or cancel a real order. The order adapter is a no-network stub.
- **Never print secrets:** DB passwords, the Upstox token, API keys. `.env` and token files are never read aloud, copied into chat, logged or committed.
- **Prajna:**
  - read only, through the `drishti_ro` role;
  - never import Prajna code (copy it with a provenance note);
  - never touch Prajna's `frontend/` or `backend/app/api/`, its cron, its WebSocket connections or its token;
  - never touch `/home/cis/windows/auto-trade-pro`.
- **No NSE fetching** from any system (D2). NSE files come only from the user's manual downloads in `/windows/drishti_data/manual/`.
- **Frozen spec:** `drishti-swing-v1` is frozen. Do not change it, its signals, or the forward-test plan. A change is v2 and the user's decision.
- **Test windows:** no training, replay or look inside a forward window, and no interim looks. The research is SURVIVORSHIP-BIASED and EXPLORATORY.
- **Blind rule:** no outcome, P&L or hit rate of a forward signal/model is shown before the look.
- **The instance:** any change on the database instance needs a backup + sha256 first (`/windows/prajna_archive`). Until the switch-night final dump there is **no change to the `prajna` DB at all** (DEVIATIONS D3).
- **Processes:** kill by exact PID only; never edit a running runbook; never edit code while pytest runs.
- **Track ownership:** follow `docs/TRACKS.md`. Only Track A (main) owns migrations, cron/ops, the spec and SC docs, `.env`/token, Upstox calls, and merges to `main`.
- **Language:** the user writes in Hinglish; reply in Hinglish.
