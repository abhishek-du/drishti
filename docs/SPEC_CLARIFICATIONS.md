# Clarifications of `drishti-swing-v1`

These clarify cases the frozen spec (`SPEC_drishti-swing-v1.md`, sha in `FREEZE_P0.md`) does not spell out. **They are not rule changes:**
- no threshold, signal, level or exit changes;
- the spec file itself is not edited, so its frozen sha still holds.

A clarification that would change a decision on a regular session would be a rule change, so it would need `drishti-swing-v2` instead.

## SC1 (2026-10-29): Muhurat trading and other short special sessions

**The 2026 session.** Muhurat trading 2026 is on **Sunday 2026-11-08 (Diwali Laxmi Pujan), 14:20–15:30 IST**. Two sources give it:
- the Upstox holidays API: `SPECIAL_TIMING`, NSE open 14:20–15:30, fetched 2026-10-28;
- secondary reports of NSE's circular dated 2026-10-19.

Trades in that session settle normally.

**What the stored history shows.** Earlier short sessions carry about 1 hour of 15m bars in Prajna's `ohlcv_bar`, and a **1d bar** on the date. These are:
- Muhurat: 2022-10-24 (Mon), 2023-11-12 (Sun), 2024-11-01 (Fri), 2025-10-21 (Tue);
- the Saturday DR test sessions of 2024-03-02 and 2024-05-18.

Drishti's `index_bar` has the same pattern: 19 of the 21 indices have a bar on each Muhurat date from 2019-10-27 to 2025-10-21. So, unless these bars are removed, the daily series counts a 1-hour session as a full session.

**Clarification.** A **session** of drishti-swing-v1 is a regular NSE session. A special session shorter than 3 hours is not a session, whatever the weekday. Its date is in `drishti/data/holidays.py: SHORT_SESSIONS` (history), or the holidays API gives it as `SPECIAL_TIMING` with an NSE window under 3 hours. A full-length special session, such as the Sunday Budget session of 2026-02-01 (09:15–15:30), is a session. For a non-session date:
1. No provisional bar is taken: the 15:40 EOD job records a NOOP.
2. No signal is computed and no order is made for it.
3. Its 1d bar is dropped from every daily series (stocks and indices). That covers indicators, zones, the 52-week high, the universe turnover rank, the listing-age count, labels, features, the 5-session time-out and the look's session count.
4. Open paper positions: exits are not evaluated on that bar. The next regular session is the next bar, and its open is compared with the last regular close, which is the gap rule as written.

**Why this is not a rule change:**
- The spec defines everything in "sessions".
- A 1-hour ceremonial session with thin, unusual flow is not the daily bar that the rules, the PDF strategies and the costs assume.
- Leaving it in would add one short bar a year to every window. That shifts SMA200 and the time-out by a session around Diwali.

**Code:** `drishti/data/holidays.py: is_session` (used by the EOD job), plus `bar_check.official_history`. The P3 bar loader must use the same rule; a P3 test checks that.

## SC2 (2026-10-29): unannounced holiday or dead feed at 15:40

The spec builds the provisional bar of T from the 15:40 quote. Clarification: a quote becomes a provisional bar of T **only if its last trade time is on T (IST)**. If fewer than 50 % of quotes traded on T, the day is treated as having no session:
- nothing is stored;
- the run is recorded as NOOP `SKIPPED_NO_SESSION`.

**Why:**
- On an unannounced closure the quote API still answers, with the previous session's values.
- A stock that did not trade on T has no official 1d bar either. On 2026-10-28, 160 of 3,576 quotes had an older last trade, and all 3,416 others matched an official bar.

**Code:** `drishti/run/p1.py: eod`, `drishti/data/eod_quotes.py: split_fresh`; test `test_eod_skips_a_day_without_trades_in_test_db`.
