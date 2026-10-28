# P0 decision: stop Prajna's Upstox jobs, or share the token?

**Status: DECIDED, 2026-10-28 (user, P0).**

**A (shared token) is chosen.**
- Prajna keeps its 06:30 login and all of its jobs.
- Drishti only reads the token file.

**B (stopping Prajna's Upstox jobs) is rejected.** The reasons:
- results/news ingest (`fundamentals_events.sh`, `news_poll.sh`) also run on Upstox;
- 6–8 extra days of Drishti work before the MVP;
- the risk of corporate-action adjustment errors;
- it would lose P1, P2 and E6.

The cron diff below is **not applied** and is kept only as a record.

All facts below were checked read-only on 2026-10-28.

## Side by side

| | **A. Shared token** (recommended) | **B. Stop Prajna's Upstox jobs** |
|---|---|---|
| Who logs in | Prajna's existing 06:30 `daily.sh --login refresh` (standing permission 2026-09-24). Drishti only reads the cached token, never logs in | Drishti, by its own runbook. Needs a new written OK for 1 automated TOTP login a day |
| Extra Drishti work before the MVP | ~1 day: index 1d (Nifty 500 + sectors) and the holidays API | **~6–8 days:** own login + token cache (0.5 d), nightly 1d ingest (1 d), corporate actions + split/bonus adjustment with checks against Prajna canon (3–4 d), results/events ingest (1 d), index 1d + holidays (1 d), gap/failure handling (0.5–1 d) |
| MVP ready (P0→P6) | ~25–30 working days | ~31–38 working days |
| Data quality risk | Low: Drishti reads Prajna's already-tested adjusted bars, CA factors, calendar, results and news | Medium: a new CA-adjustment path. A wrong split/bonus factor corrupts every indicator of that stock. It must be checked against Prajna canon on the overlap, which then stops being updated |
| Upstox rate limits (50/s, 500/min, 2000/30 min per API per user) | Split by API and time: Drishti uses the quote API after 15:35 (separate budget); historical API only 22:00–05:30 at fraction ≤ 0.5; shared lock with Prajna's backfill supervisor | All of it is Drishti's (fraction ≤ 0.8) |
| WebSocket (2 connections) | Prajna's pre-open recorder takes both from 08:55 to 09:20. Drishti ticks would start 09:20. **No effect on the swing MVP** (ticks are off until the disk is enlarged), and decided again before intraday model #2 | Both are Drishti's, 09:00–15:35 |
| Double-login risk | None (one owner) | None, provided the cron diff removes every Prajna `--login`. Whether a second login invalidates the first token is UNVERIFIED |
| Prajna dashboards (Lab, Buy List, Desk, live quotes) | Keep working | Go stale on the stop date. Live quotes fail closed (token invalid) |

## Tests lost under B

| Test | State | What is lost |
|---|---|---|
| **P1 shadow** (`p1-shadow-v1`: ORB + VWAP, core/strict, 4 variants) | Forward from 2026-10-27, ~2 sessions; looks at 60 / 250 sessions | The only forward test of ORB/VWAP, which are Drishti S4/S5. It would be a free benchmark for intraday model #2. The pre-registration is spent |
| **P2 LLM reviewer** (`p2-reviewer-v1`) | Forward from 2026-10-27, reads P1's trades. Bedrock key invalid since 2026-10-27 23:10 | The answer to "does an LLM veto add value?", which is Drishti's news-veto question too |
| **E5 ensemble** (model `4db89a70…`) | 40-session forward test (~17 Dec). Its out-of-time look E5-H was NOT_ACCEPTED (2026-10-26) | Least: the answer is very likely negative already. Only a negative-control benchmark |
| **E6 PEAD monitor** (`e6-rule-v1`) | Frozen 2026-10-23, one look ~April 2027 | The forward evidence behind Drishti's screener plus point "a strong gap after results that holds" |
| **Prajna pipeline** | Daily 1m/15m/1h for all instruments, CA refresh, Stage 1–3, results/fundamentals events | Prajna's history freezes at the stop date. **`results_event` also stops updating** (`fundamentals_events.sh` uses Upstox), so Drishti must ingest results/events itself under B |

Under B, a protocol-deviation note is added to each of P1/P2/E5/E6: "forward window ended on the stop date by user decision 2026-10-28; Upstox moved to Drishti". No look is taken, and the specs are unchanged.

## Cron diff for B (REJECTED, not applied; kept as a record)

**Lines to comment out.** They call Upstox (checked by reading each runbook):
- `daily.sh --login` (refresh 06:30, morning 07:00, weekly, monthly)
- `preopen_day.sh --login` (08:25)
- `close_then_backfill.sh` (16:05)
- `ca_refresh.sh` (targeted 06:45, selfheal 08:40)
- `backfill_supervisor.sh` (tick, hourly, report)
- `global_refresh.sh` (12:40, 21:10): calls `upstox token-status`
- `fundamentals_events.sh` (13:15): uses the cached Upstox token
- `news_poll.sh` (09:30, 10:00–15:30): calls `upstox token-status`
- `timing_monitor.sh` (09:21, 16:10)
- `stage1_checkpoint.sh` (06:35)
- `stage3_snapshot.sh` (09:00, 09:05)
- `model_predict.sh` (09:06)
- `e51_data.sh` (10:05)

**Lines to comment out because their inputs stop:**
- `e6_daily.sh`, `shadow_daily.sh`, `shadow_review.sh`, `paper_daily.sh`
- `lab_daily.sh` ×2, `lab_brief.sh` ×2
- `market_report.sh` ×2, `signals_snapshot.sh`
- `maintenance.sh canon`

**Lines kept** (no Upstox):
- `news_collect.sh`, `news_reconcile.sh`, `news_ai.sh`
- `maintenance.sh all|status`, `acceptance stage2`
- `yahoo_research.sh`, `dashboard.sh`

**Check after applying:** `crontab -l | grep -vE '^\s*#' | grep -E -- '--login|close_then|preopen|backfill_supervisor|ca_refresh|fundamentals_events|news_poll|global_refresh'` prints nothing. Then, for one session, confirm that no Prajna process calls Upstox.

## Outcome

- **A chosen.** The "Cron diff for B" section is a record only, and nothing in Prajna changes.
- **Drishti P1 ingests only:** the Nifty 500 and sector indices (1d), and the holidays API.
