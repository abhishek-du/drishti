# Track C task: Scanner + Journal UI (2026-11-03)

Backend is on `main` @ `4e25a54` (Track A). Rebase `track-c` on `main` first.

**Ownership:** Track C writes only `web/`. Do not change `backend/`, migrations, cron or `.env`. If an endpoint lacks something, write the request in your report; Track A adds it.

**PAPER ONLY.** There is no order button anywhere. The journal records what the user did by hand.

## The label (mandatory)

Show it verbatim, from the `label` field of every response (do not hard-code a different text), at the top of the Scanner page, the alert panel and the Journal page, and in the browser tab title suffix:

> **Decision support — detections are NOT validated edges. Your call. Paper only.**

Also show, small, on the Journal page: the `note` field of `/api/journal/stats`.

## Endpoints (API http://127.0.0.1:8700)

| Method | Path | Returns |
|---|---|---|
| GET | `/api/scan/alerts?date=YYYY-MM-DD&tf=1d\|15m&detector=NAME` | `label`, `version`, `session` (the date shown; default the latest), `dates` (last 60 with alerts), `detectors`, `alerts[]` |
| GET | `/api/scan/alert/{id}` | `label`, `alert` (+ `risk_per_share`, `risk_rupees`, `r2_rr`), `grades` {alert, took, placebo}, `journal_history[]` |
| POST | `/api/journal/{alert_id}` | body `{status?: "watching"\|"took"\|"skipped", actual_entry?: number, actual_qty?: int, notes?: string}`; only the fields sent change. 400 if `took` without `actual_entry`, or `actual_entry` ≤ the stop. Returns `journal` |
| GET | `/api/journal/stats?since=&until=&tf=` (default since 2026-11-03) | `overall` and `by_detector[NAME]`, each with 4 groups `took`, `took_base`, `alerts`, `placebo`; each group: `closed`, `hit_rate_r1`, `win_rate`, `expectancy_r_net`, `avg_win_r`, `avg_loss_r`, `payoff_ratio`, `total_r_net`, `by_outcome`. Also `journal_counts`, `alerts_open`, `alerts_no_fill`, `note` |

An alert row has: `id, session_date, tf, bar_time, instrument_key, symbol, detector, entry_ref, stop, r1, r2, rr_r1, qty, capital, risk_pct, cost_r, atr, context{regime, spec_gate, close, rsi, volr20, trend, flags, bar_source}, status, actual_entry, actual_qty, notes, outcome, r_net`. `r1`/`r2`/`rr_r1` can be null (no resistance above: the grader then uses entry + 2R; show "no resistance above → 2R").

Next.js calls the API through the existing proxy/fetch helper used by the chart pages. The POST is the only write; no optimistic UI: refetch the alert after a POST.

## Screens

### 1. Scanner (`/scanner`), nav item "Scanner"
- Date picker from `dates`; tabs **EOD (1d)** / **Intraday (15m)**; detector filter chips.
- Table, one row per alert: symbol (link to the chart page), detector, tf, bar time (IST), entry, stop, R1, R2, **RR to R1** (red < 1.5, amber 1.5–2, green ≥ 2: a colour hint, never a filter), qty, ₹ risk, cost in R, regime (context.regime), journal status pill, outcome pill once graded (R1 green / STOP red / TIMEOUT grey / OPEN outline / NO_FILL muted) with `r_net`.
- Sort by RR, detector, symbol. Empty state: "No alerts for this session".
- Intraday tab: refetch every 60 s during 09:15–15:35 IST; show "new" on rows created in the last 15 min.

### 2. Alert panel (side drawer or `/scanner/[id]`)
- Daily chart (or 15m for tf=15m) of the symbol with horizontal lines: entry (blue), stop (red), R1/R2 (green dashed), and a marker on `bar_time`. Reuse the existing chart component.
- **Point in time:** draw the alert's levels from the alert row only (`entry_ref`, `stop`, `r1`, `r2`, and `context.zones_used` for the R1/R2 bands with their pivot dates). Do **not** draw the chart API's S/R zones on this panel: those are computed from today's data and differ from what the alert saw. If the user turns them on, label them "Today's zones (not the alert's)".
- Levels card: entry, stop, risk/share, qty, ₹ risk (`risk_rupees`), R1 + RR, R2 + `r2_rr`, cost in R, ATR, capital and risk %.
- Context card: regime, spec_gate, trend, RSI, volume ratio, candle flags.
- **Journal form:** buttons Watching / Took / Skipped; inputs actual entry and qty (required for Took); notes textarea; Save → POST. Show the API error text on 400.
- Grades card (after grading): the three bases side by side (alert / took / placebo): outcome, entry used, exit, R net.
- History: `journal_history` as a small timeline.

### 3. Journal (`/journal`), nav item "Journal"
- Date range (default since 2026-11-01) and tf filter.
- Headline table, rows = **Your trades (took)**, **Same alerts, rule entry (took_base)**, **All alerts (base rate)**, **Random entry (placebo)**; columns = closed, hit rate to R1, win rate, expectancy R (net), avg win R, avg loss R, payoff, total R.
- Below it the same table per detector (collapsible).
- Counts: watching / took / skipped / none, open alerts, no-fill alerts.
- A plain-language line under the headline: "Compare Your trades with Random entry, not with zero." plus the API `note`.
- **No equity curve, no ₹ P&L headline.** Show R only.

## Acceptance

- `npm run build` passes; the existing pages are unchanged.
- The label is visible on all three screens (screenshot each, light + dark).
- A POST from the form updates the row (status pill) after refetch; a Took without entry shows the 400 message.
- Works at 390 px width (table scrolls inside its card, not the page).
- Report: commit hash on `track-c`, screenshots, any API gaps found.
