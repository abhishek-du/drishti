# Drishti handover (read this first)

**Last updated:** 2026-11-02 evening, by Claude Code (Track A, the main session). Updated every evening.

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
| Switch to B | PLANNED | Fri 2026-11-20 evening (`SWITCH_TO_B.md` 6.2) |

## 3. Key research results (all SURVIVORSHIP-BIASED, EXPLORATORY)

- **v1 rules vs placebo** (set (b), 2020-09 → 2026-10-21): rules **−0.100 R** vs random-entry placebo **−0.090 R**. Negative at every slippage level and in every year except 2020.
- **Model #1** (LightGBM + isotonic, purged walk-forward): AUC ~0.50, BSS ~0.
- **Power check:** spec_gate is OFF (Nifty 500 < SMA200), so v1 makes **zero** entries in the current regime.
- **Research sprint** (`DESIGN.md`, "Research sprint result"): no candidate's entry beat random entry with the same exits. The only finalist, C3b (PEAD, 60 sessions), failed its holdout (−0.202 R vs placebo). **The holdout window 2025-01-01 → 2026-10-21 has been used once; do not reuse it.**
- **Rajat setup** (Track B, after the sprint): every variant failed on the development window; no holdout run.
- **Data facts:**
  - The provisional (15:40) and official bars differ only in **close** (LTP vs NSE close); high and low were never revised in 3 sessions.
  - Drishti's 1d equals Prajna's 1d exactly (10-28: 3,416/3,416; 10-29: 3,429/3,429).
  - Corporate-action factors equal Prajna's (212/212 on the split/bonus ISINs).
  - F&O stocks have no price band; they are marked `fo` in the master (SC5/SC6 correction).

## 4. Pending work, in order

1. **P1c remaining** (target: complete 11-12, then shadow sessions 11-12 … 11-19):
   1. ~~Upstox `/v2/news` ingest~~ DONE 2026-11-02 (migration 0007, `p1 news`). The first live run had 120 requests, 0 errors, 173 articles. Versus Prajna over 6 days: 133 common, 60 only in Prajna, because the vendor drops older articles of busy stocks before 7 days and Prajna polls every 30 min. Fix: the 30-min cron (`ops/cron.txt`); the pre-switch gap is filled by the D1 copy.
   2. Nightly intraday for the top 500 (1m/15m/1h → Parquet), plus the 1m gap 2026-04-29 → 07-17 after the switch (D3; ~0.5 GB).
   3. The D1 copy tool (Prajna history → Drishti: 1d into Postgres, intraday into Parquet; per-month counts and hashes) and the first copy.
   4. Re-point Drishti's reads (`bars.py`, `bar_check`, `eod_quotes.universe_keys`, `run/p2.py`, the API) from Prajna's live DB to Drishti's tables, behind a flag.
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

## 5. Open questions for the user

- **The v2 research direction.** v1 is on hold; any new candidate needs a new untouched window, i.e. forward data.
- **The second archive location** (external disk ≥ 128 GB or an rclone crypt remote). Needed before 11-20.
- **Tick retention** (30 days now). The measured cost is ~0.03 GB/day for ~50 keys and ~0.15 GB/day for 300 keys, so 60+ days is affordable.
- **Cron lines not yet installed** (`ops/cron.txt`): `news` every 30 min. The crontab has 13 Drishti lines now; with `news` it becomes 14.

## 6. Hard rules (summary; the full text is in `CLAUDE.md`)

- **PAPER ONLY.**
- **Never print secrets:** `.env`, token files, passwords.
- **Prajna:** read only. Never touch its `frontend/` or `backend/app/api/`, its cron, its WebSocket connections or its token. Never touch `/home/cis/windows/auto-trade-pro`. **No change to the `prajna` DB before the switch-night final dump.**
- **No NSE fetching** from any system. NSE files are only the user's manual downloads.
- **`drishti-swing-v1` is frozen**; no change to it or the forward plan without the user.
- **Testing:** no replay or training inside a forward window, no interim looks, the blind rule.
- **Any DB-instance change:** backup + sha256 first, into `/windows/prajna_archive`.
- **Ownership** (`TRACKS.md`): only Track A merges to `main` and owns migrations, cron, `.env`/token and Upstox calls. *(Note 2026-11-02: two commits on `main` came from outside Track A: the Track C merge `250eb87` on 10-30 17:45 and `GEMINI.md` on 11-02. Both were checked and are fine.)*
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
