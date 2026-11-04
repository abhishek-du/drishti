# Drishti handover (read this first)

**Last updated:** 2026-11-03 13:00 IST, by Claude Code (Track A, the main session). Updated every evening.

**Live status** (runs, freshness, git, cron) is written automatically every night at 21:45 to `/windows/drishti_data/handover/STATUS.md` (`p1 status`). Read that too: it is newer than this file.

**Rules:** before doing anything, read `CLAUDE.md` (hard rules) and `docs/TRACKS.md` (who owns what). The user writes in Hinglish; reply in Hinglish.

---

## 1. What Drishti is

A swing-first NSE system: chart reading, a daily watchlist, ML and **paper trading only**.
- **Repo:** `/home/cis/windows/drishti`. Web http://127.0.0.1:3100, API :8700 (systemd user units `drishti-api`, `drishti-web`).
- **Databases:** `drishti` (prod) and `drishti_test` (tests) on the `prajna-postgres` Docker instance.
- **Data directory:** `/windows/drishti_data` (manual/, ticks/, bars_1m/, research/).
- **Prajna** (`/home/cis/windows/prajna`) is the older system. It is being stopped on **Fri 2026-11-20** (Option B, `docs/SWITCH_TO_B.md`). Until then Drishti only *reads* Prajna's DB (role `drishti_ro`) and its Upstox token (decision A).

## 2. Status by phase (2026-11-02)

| Phase | Status | Notes |
|---|---|---|
| P0 spec freeze | DONE | `SPEC_drishti-swing-v1.md` frozen (tag `p0-freeze`); clarifications SC1–SC6 in `SPEC_CLARIFICATIONS.md` |
| P1 data | DONE | Holidays, 21 indices, manual NSE files, the 15:40 EOD snapshot (+ circuit limits, + 21 index quotes), barcheck |
| P2 part 1 | DONE | Scout `scout-b-v1`, watchlist log, API, web |
| Live view + ticks | RUNNING | One Upstox WebSocket 09:21–15:35; ticks in zstd (30-day retention, 10 GB guard); 1m bars in Parquet. LIVE VIEW only, never used by v1 |
| Chart UI (Track C) | MERGED | KLineChart 10.1.0, timeframes, indicators, patterns, hindsight card (display only) |
| P3/P4 (Track B) | DRAFT DONE | **No edge** (section 3). Code merged; outputs stay as files in `/windows/drishti_data/research/track-b/` |
| Research sprint | DONE | No v2 frozen; C3b failed its holdout |
| P6 / forward test | **ON HOLD** | v1 is not taken forward; waits for the user's v2 decision |
| **P1c (Option B prep)** | IN PROGRESS | Done: guarded login (off until the switch), master + F&O flag, board-meetings CSV loader, official 1d ingest (nightly), compare1d, corporate actions + factors. Pending: section 4 |
| **Scanner + journal** | BACKEND DONE 2026-11-03 | Decision support only (NOT validated edges, PAPER ONLY): `scan-v1` detectors, alerts + triple-barrier grades vs placebo, journal API. UI = Track C task. DESIGN "Decision-support scanner", DEVIATIONS D4 |
| **Live Radar** | BUILT 2026-11-04 | Intraday re-run of Scout's criteria on the liquid/F&O universe every 15 min (partial daily bar to the last closed 15m bar); new names go to `radar_event` and get setup scans. First full day 11-05. DESIGN "Live Radar"; UI = Track C task |
| Switch to B | PLANNED | Fri 2026-11-20 evening (`SWITCH_TO_B.md` 6.2) |

## 3. Key research results (all SURVIVORSHIP-BIASED, EXPLORATORY)

- **v1 rules vs placebo** (set (b), 2020-09 → 2026-10-21): rules **−0.100 R** vs random-entry placebo **−0.090 R**. Negative at every slippage level and in every year except 2020.
- **Model #1** (LightGBM + isotonic, purged walk-forward): AUC ~0.50, BSS ~0.
- **Power check:** spec_gate is OFF (Nifty 500 < SMA200), so v1 makes **zero** entries in the current regime.
- **Research sprint** (`DESIGN.md`, "Research sprint result"): no candidate's entry beat random entry with the same exits. The only finalist, C3b (PEAD, 60 sessions), failed its holdout (−0.202 R vs placebo). **The holdout window 2025-01-01 → 2026-10-21 has been used once; do not reuse it.**
- **Momentum book C1a CLOSED (2026-11-03):** after tax it ties/loses to a Nifty200 Momentum 30 index fund; excess-return DSR at N≈30 = 0.23–0.27. No momentum forward test.
- **Rajat setup** (Track B, after the sprint): every variant failed on the development window; no holdout run.
- **Data facts:**
  - The provisional (15:40) and official bars differ only in **close** (LTP vs NSE close); high and low were never revised in 3 sessions.
  - Drishti's 1d equals Prajna's 1d exactly (10-28: 3,416/3,416; 10-29: 3,429/3,429).
  - **Corporate actions, full universe** (11-02: 3,594 ISINs, 0 errors, 2,324 events): factors equal Prajna's on all **2,297** common events. 88 Prajna events are no longer served by the vendor; 80 of them were announced 2025-05 … 2025-10, beyond the ~1-year window, and they include **16 splits and 15 bonuses**. **They exist only in Prajna and the irreplaceable archive, so the D1 copy must bring them in.**
  - F&O stocks have no price band; they are marked `fo` in the master (SC5/SC6 correction).

## 4. Pending work, in order

1. **P1c remaining** (target: complete 11-12, then shadow sessions 11-12 … 11-19):
   1. ~~Upstox `/v2/news` ingest~~ DONE 2026-11-02 (migration 0007, `p1 news`). The first live run had 120 requests, 0 errors, 173 articles. Versus Prajna over 6 days: 133 common, 60 only in Prajna, because the vendor drops older articles of busy stocks before 7 days and Prajna polls every 30 min. Fix: the 30-min cron (`ops/cron.txt`); the pre-switch gap is filled by the D1 copy.
   2. ~~Nightly intraday for the top 500~~ BUILT 2026-11-03 (`p1 intraday`, `drishti/data/intraday.py`; files in `/windows/drishti_data/intraday/`, overlap check vs Prajna in the run counts). Cron `0 1 * * 2-6` pending user install. First real run = the first night after install. The 1m gap 2026-04-29 → 07-17 stays for after the switch (D3; ~0.5 GB).
   3. ~~The D1 copy tool~~ BUILT 2026-11-03 (`drishti/switch/d1copy.py`; `plan` / `copy [--delta]`). Dry run into `drishti_test` for 2026-09: 63/63 tables and 1d/1h/15m/1m bars all equal (count, OHLCV sums, md5 hash); delta path OK. 23 min for 28 M rows (1m 243 MB/month). Full copy #1 into `drishti` (after a backup) is planned for 11-11 night: ~230 M rows, ~3 h, ~3 GB on /windows (`/windows/drishti_data/prajna_copy/`). The dry-run output in `/windows/drishti_data/prajna_copy_dryrun/` can be deleted after that.
   4. Re-point Drishti's reads (**include CA adjustment of the intraday/15m read path: DEVIATIONS D5**) (`bars.py`, `bar_check`, `eod_quotes.universe_keys`, `run/p2.py`, the API) from Prajna's live DB to Drishti's tables, behind a flag.
   5. Vendor-applied detection for older CAs, once the copy brings Prajna's bars and observations.
   6. The switch scripts and checklist.
2. **The switch on 2026-11-20** (`SWITCH_TO_B.md` 6.2):
   - the user installs the cron diff;
   - **the final full `pg_dump` of the frozen Prajna DB is mandatory** (DEVIATIONS D3) before anything else;
   - delta copy;
   - **final archive** of the irreplaceable data, test-restored;
   - copy both archives to the user's second location.
3. **After the switch:**
   - S+1 06:10 Drishti's first own login (set `DRISHTI_UPSTOX_LOGIN_ENABLED=true`, token file → Drishti's own);
   - verification (6.4);
   - the user revokes Prajna's Upstox app keys;
   - DISK_CLEANUP 3.1 (drop Prajna's training tables) only after their dump is verified at the second location;
   - the 1m-gap backfill.
4. **Live recorder:** diagnose the repeated `keepalive ping timeout` reconnects. Since 11-03 the health file has a `reconnect_log` and `max_loop_gap_s`.
5. **Bar source decision** after 5 barcheck sessions (11-03): keep the 19:30 provisional run with the SC3 08:45 recheck, or move it.

**Postgres shm (11-02):**
- 12 "could not resize shared memory" errors came from Drishti's unfiltered CA payload query in `bars.official`. It is fixed in code (`4e481fb`); the parallel load test afterwards had 0 errors.
- The container's `/dev/shm` is 64 MB; it is raised to 1 GB on the switch night (`SWITCH_TO_B` 6.2).
- The `ALTER ROLE ... max_parallel_workers_per_gather = 0` fallback was NOT applied: it is not needed so far.

## 5. Open questions for the user

- **The v2 research direction.** v1 is on hold; any new candidate needs a new untouched window, i.e. forward data.
- **The second archive location** (external disk ≥ 128 GB or an rclone crypt remote). Needed before 11-20.
- **Tick retention** (30 days now). The measured cost is ~0.03 GB/day for ~50 keys and ~0.15 GB/day for 300 keys, so 60+ days is affordable.
- **Cron lines not yet installed** (`ops/cron.txt`): `news` every 30 min, and the 3 scanner lines (`scan_eod` 19:45, `scan_intraday` every 15 min, `scan_grade` 09:45). The crontab has 13 Drishti lines now; with all four it becomes 17, and 18 with `intraday` (01:00).
- **Telegram for the scanner:** optional; set `DRISHTI_TELEGRAM_BOT_TOKEN` and `DRISHTI_TELEGRAM_CHAT_ID` in `backend/.env` (otherwise the alert list is only logged).

**Telegram (2026-11-04, DEVIATIONS D6):**
- Drishti sends to the bot and chat Prajna used, from the 11-05 session on. A test message was sent and delivered on 11-04.
- Routing: swing alerts and their outcomes are individual; 15m alerts go in one digest per bar and their outcomes in one end-of-day digest. Exceptions: the watchlist top 10, Live Radar names, and anything marked in the journal.
- **Only source on that chat:** V1 (auto-trade-pro) holds the same token but has no running process, cron, unit or container (its last logs are from 2026-09-21); Prajna is disabled.
- **Prajna's own Telegram is off:** its `.env` TELEGRAM lines are commented out. To undo, remove the prefix.

**Known caveat (DEVIATIONS D5, 2026-11-04):**
- Intraday (15m) scanner alerts use **unadjusted** 15m bars: Prajna's raw 15m plus the recorder's 1m.
- For a stock with a split or bonus in the last ~25 days, its zone levels, R1/R2, stop and indicators can be wrong.
- **Do not rely on intraday alerts for split- or bonus-affected stocks** until the 15m path applies CA factors.
- The 11-20 switch alone does not fix it: the fix is part of the re-point (P1c item 4).
- EOD alerts are fine (adjusted 1d).

**Cron line to install (2026-11-04):** `15 20 * * 1-5 … drishti_job.sh odds`, the nightly Time Machine odds for the day's 1d alerts. Also still pending: `30 4 * * 2-6 … intraday --missing`.

**Asked 2026-11-03, waiting for the user:**
- A git remote for Drishti (none is configured; "push" is not possible yet). A private repo is recommended.
- `drishti/data/live_quote.py` (committed `7b9b11a` from another session on user order) calls the Upstox quote API from the chart during market hours with Prajna's shared token: keep, restrict to after 15:35, or remove.
- Next for Track A: the re-point (Drishti reads → copy + own ingest, behind a flag), then full copy #1 on 11-11 night.

## 6. Hard rules (summary; the full text is in `CLAUDE.md`)

- **PAPER ONLY.**
- **Never print secrets:** `.env`, token files, passwords.
- **Prajna:** read only. Never touch its `frontend/` or `backend/app/api/`, its cron, its WebSocket connections or its token. Never touch `/home/cis/windows/auto-trade-pro`. **No change to the `prajna` DB before the switch-night final dump.**
- **No NSE fetching** from any system. NSE files are only the user's manual downloads.
- **`drishti-swing-v1` is frozen**; no change to it or the forward plan without the user.
- **Testing:** no replay or training inside a forward window, no interim looks, the blind rule.
- **Any DB-instance change:** backup + sha256 first, into `/windows/prajna_archive`.
- **Ownership** (`TRACKS.md`): only Track A merges to `main` and owns migrations, cron, `.env`/token and Upstox calls. *(Note 2026-11-02: the Track C merge `250eb87` on 10-30 17:45 came from outside Track A; it was checked and is fine. `GEMINI.md` + `.geminiignore` (`d644304`) are the user's backup instructions for Gemini CLI: keep both on `main`.)*
- **Processes:** kill by exact PID only; never edit a running runbook; never edit code while pytest runs.

## 7. Where things are

| What | Where |
|---|---|
| Cron log | `backend/var/logs/cron.log` (lines `DRISHTI_<JOB>_OK/FAIL`) |
| Run log | tables `run` / `run_end` in `drishti` (Ops page http://127.0.0.1:3100/ops) |
| Bar-check reports | `backend/var/reports/bar_check_<day>.json` |
| Live health | `backend/var/run/live_health.json` |
| Backups and archives | `/windows/prajna_archive/` (the irreplaceable Prajna archive is in `irreplaceable_20261029/`) |
| Worktrees | Track B `/home/cis/windows/drishti-trackB` (branch `track-b`), Track C `/home/cis/windows/drishti-trackC` (branch `track-c`) |
| Services | `systemctl --user restart drishti-api drishti-web` (never the serve script while the units run) |
