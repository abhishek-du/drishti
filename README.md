# Drishti

An agentic chart-reading, prediction and paper-trading system for NSE. It is separate from Prajna.

**Status:** P0 (design freeze). Nothing runs yet.

## Rules

- **PAPER ONLY.** No real order is ever placed, modified or cancelled unless the user authorises live trading in writing. The order adapter is a stub with no network calls.
- **Data sources:**
  - stored market data comes from Upstox only, plus the CSV files the user downloads by hand into `drishti_data/manual/`;
  - Prajna's database is read only, through the `drishti_ro` role;
  - NSE websites are never scraped.
- **Secrets** are never printed, and they live only in `backend/.env` and `backend/var/`.
- **Testing:**
  - no training, replay or look inside a forward window;
  - every forward test is pre-registered and gets exactly one look.

## Docs

- `docs/DESIGN.md`: the approved plan, copied from the planning session on 2026-10-28.
- `docs/SPEC_drishti-swing-v1.md`: the swing rules, costs, label and features. It is a draft until frozen at the end of P0.
- `docs/PREREG_M1.md`: the model #1 forward-test pre-registration (draft).
- `docs/P0_PRAJNA_DECISION.md`: stop Prajna's Upstox jobs vs share the token. The user confirms this at the end of P0.
- `/home/cis/windows/second-module.md`: the research behind all of it (sections 1–9).
