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

## SC3 (user, 2026-10-29): which bar orders come from, and the morning recheck

The spec says a signal is "recomputed and flagged `BAR_REVISED`" when the official bar differs. The user fixed how this works, and it applies unless the 5-session bar check (10-28 … 11-03) says otherwise:

1. **Evening:** signals and paper orders for T+1 are made at **19:30 from the provisional bar** (as written).
2. **Morning recheck:** at **T+1 08:45**, if Prajna's official 1d bar for T is present, every pending order is re-evaluated on the official bar.
   - If any trigger condition of its signal no longer holds, the order is cancelled with reason **`BAR_REVISED_CANCEL`**.
   - The morning **never creates** a signal or an order, and never enlarges one. It can only cancel.
3. **Recorded on each order:**
   - `bar_source`: `provisional` or `official`;
   - `recheck_status`: `rechecked_ok`, `cancelled` or `official_missing`.
4. **Backtests and replays use official bars.** The forward scorecard reports how many forward orders the recheck cancelled.

**Why this is a clarification:**
- Cancel-only keeps the evening decision as an upper bound.
- No threshold or level changes.
- Order levels (trigger, stop, target) stay as set in the evening. Highs and lows had no revisions on 10-28.

**Not built yet:** the order tables come in P5. The 08:45 recheck job and its fields are part of P5, and are listed in `FREEZE_M1.md` at P6.

**Index bars (same day):**
- The 15:40 snapshot also stores the 21 index quotes as provisional index bars of T (`index_bar`, source `UPSTOX_QUOTE`, same last-trade-today rule).
- The 22:15 historical job still adds the official bars, which arrive one day later.
- The regime gate at 19:30 reads the provisional Nifty 500 close of T.

## SC4 (2026-10-29): what regime OFF does to new long entries

**The frozen spec (section 5).** The "regime OK" condition is one of the common gates that **all must hold on T** for any S1a/S1b/S2/S3 signal. The gate reads: "Nifty 500 close > its SMA200 (index 1d from Upstox; until it is ingested, fallback = ≥ 50 % of the universe above its SMA200)".

**So with regime OFF, new S1a–S3 long entries are blocked entirely, not just filtered or down-ranked.**
- There is no signal, so there is no paper order and no set-(b) event.
- Set (a) all-bar rows and their labels are still computed. They are features and labels, not entries.
- Open positions are not affected. Their exits stay exactly as in section 6, and there is no forced exit on a regime change.

**The VIX rule is separate from the gate:**
- **Normal:** India VIX < 20.
- **Spike:** VIX ≥ 25, or a 5-session change of +20 % or more. In a spike, S3 is off and every other size is halved.
- **VIX 20–25 without a +20 % move:** neither normal nor spike. No VIX rule applies; entries and sizes are unchanged.

**Scout's regime label is not the spec gate.** Screener B's own regime label ("ON" only when Nifty 500 > SMA200 **and** VIX < 20) is a Scout display label. Each Scout run therefore records both:
- `status`: the Screener B view;
- `spec_gate`: the section 5 gate, Nifty 500 > SMA200 only.

Only `spec_gate` will ever gate a signal.

**Consequence to watch.** On 2026-10-28 Nifty 500 was 21,734.9 against an SMA200 of 22,808.7. That is `spec_gate` OFF, so the frozen rules would give **zero** signal events today. P3 reports how this bears on the forward-test timeline; see `DESIGN.md`, "Pre-P6 power check".

## SC5 (2026-10-29): price band in Scout

**Data source.** Upstox's full market quote gives `upper_circuit_limit` and `lower_circuit_limit`. The 15:40 snapshot stores them from 2026-10-29 (migration 0003). The band is computed as the circuit width around the previous close (last_price − net_change), mapped to the nearest NSE band of 2/5/10/20 %. Examples from 2026-10-29: RELINFRA 2 %, SBC 20 %, RELIANCE 10 % (F&O: ±10 % operating range).

**Scout (scout-b-v1)** leaves out names in a 2 % or 5 % band:
- Screener B already lists this exclusion, which was marked "no source" until now. So the Scout thresholds are unchanged.
- It is applied **after** the RS score, so `RS_top` is still scored on the spec's eligible universe.
- A name with an unknown band (no limits in the quote, or rows from before 2026-10-29) is kept.
- The funnel records `band_known` and `band_ok`.

**The frozen spec's universe (section 2) does not list a price-band exclusion.** Signals, labels and the model universe are therefore **not** changed by this; adding the exclusion there would be a rule change, `drishti-swing-v2`.

Delivery % stays out: there is no source.
