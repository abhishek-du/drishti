# Switch to Option B: Prajna stops, Drishti owns Upstox

**Status: PLAN for the user's approval (2026-10-29). Nothing has been stopped, copied or changed.**

- **Decision (user, 2026-10-29):** Option B. Prajna stops fully, and Drishti gets the whole Upstox budget and both WebSocket connections.
- **Written OK in the same message:** one automated Upstox TOTP login per day, run by Drishti. The token cache is 0600 and gitignored.
- **Inventory and job list:** `docs/PRAJNA_INVENTORY.md` (Parts 1–3).
- **This file:** Part 4 (preserving the history), Part 5 (Drishti's own ingest) and Part 6 (switch-over).

**Decisions for the user** (they are listed again at the end):
- **D1:** how the history is preserved;
- **D2:** news and the results calendar;
- **D3:** intraday ingest from the switch;
- **D4:** the pre-open/tick gap;
- **D5:** how the credentials move;
- **D6:** the switch date.

---

## Part 4: Keep Prajna's history for Drishti (one time)

**Datasets that cannot be fetched again** decide this choice:
- `corporate_action` / `ca_factor`: the vendor serves about one year of CAs;
- `ohlcv_payload_basis` / `ohlcv_observation`: the record of what the vendor had adjusted, and when;
- the pre-open recordings;
- the news;
- `results_event` intimations.

Everything else (bars, fundamentals) could in principle be fetched again, but the user's rule is: never backfill what already exists.

### Option 1: keep reading Prajna's DB through `drishti_ro` (frozen at the stop date)

- **Copy:** 0 GB, no copy time.
- **Disk:** `prajna` stays at 207 GB on the **root** disk (86 % used, 51 GB free).
- **Risks:**
  - Drishti depends on Prajna's schema and views for good (`canon_*` views, `ohlcv_payload_basis` …).
  - Prajna can only be "frozen" by keeping its writers off. Any Prajna run would write again.
  - The 207 GB can never be freed while Drishti depends on it.
- **Verification:** nothing to copy. Once a day, check the write counters (`pg_stat_user_tables.n_tup_ins/upd/del`) to confirm Prajna stays unchanged.

### Option 2: one-time copy of what Drishti needs into Drishti

**What is copied** (raw, lossless, with every `fetched_at` / `knowable_at` / `payload_sha256`, so Drishti can still compute PIT adjustments itself):

| Data | Rows | Into | Estimated size* |
|---|---|---|---|
| 1d bars, all segments (NSE_EQ, NSE_INDEX, GLOBAL_*) | 3.88 M | Drishti Postgres `prajna_bar_1d` | ~0.6 GB (root) |
| 1h bars | 20.0 M | Parquet/zstd, one file per month | ~0.4–0.55 GB (/windows) |
| 15m bars | 70.4 M | Parquet/zstd | ~0.8–1.1 GB |
| 1m bars | 84.0 M | Parquet/zstd | ~0.5–0.6 GB |
| 5m bars | 55.2 M | Parquet/zstd | ~0.4–0.6 GB |
| `ohlcv_payload_basis`, `ohlcv_observation` | 604 k + 34 k | Postgres | ~0.25 GB |
| `corporate_action`, `ca_factor` | 2,377 + 2,377 | Postgres | < 0.02 GB |
| `instrument`, `instrument_attribute_version`, `instrument_lifecycle_period`, `instrument_security_class`, `canon_instrument` (sector) | ~15 k | Postgres | < 0.05 GB |
| `trading_session` | 2,677 | Postgres | ~0 |
| `results_event`, `fundamental_snapshot`, `macro_observation` | 132 k + 50 k + 6 k | Postgres | ~0.25 GB |
| News: `news_item` family + `news_article` / `news_instrument` + `news_ai_enrichment` | ~0.5 M rows over 13 tables | Postgres | ~0.2 GB |
| `canon_coverage` | 262 k | Postgres | ~0.07 GB |
| Experiment outputs as benchmarks (P1/P2/E5 tables, Lab `strategy_day`/`trade_forensic`, paper, desk) | ~10 k | Postgres | ~0.01 GB |
| *Optional:* `preopen_tick` + `preopen_book` | 51 M | Parquet/zstd | ~1.5–2 GB (not measured) |

\* The bar sizes come from an in-memory Parquet/zstd test on 2026-10-27 (1m 6.6, 15m 16.2, 1h 27.1 B/row in one-day files). Month files compress better, so these are upper bounds.

**What is not copied.** It stays in the frozen Prajna DB and files:
- `training_*` (85 GB), `feature_value` (3.6 GB);
- `raw_payload` (3.9 GB) + `var/archive` (6.1 GB of raw payload files);
- `ingest_run` (1.7 GB), the logs.

These are Prajna's own derived data and provenance.

**Disk impact:**
- /windows: **+3–5 GB** of 39 GB free (+2 GB more with pre-open).
- root: **+~1.5 GB** of 51 GB free.

**Time:**
- ~230 M bar rows are read by index range, timeframe by month, with a read-only session: ~30–60 min, at night.
- Small tables: minutes.
- A **delta copy** on the switch night picks up rows with `fetched_at` after the first copy.

**Verification, per table and per (timeframe, month) for bars:**
- `count(*)`;
- `sum(close)`, `sum(volume)`;
- an order-independent hash `sum(('x'||substr(md5(instrument_key||bar_start_utc||open||high||low||close||volume),1,15))::bit(60)::bigint)`.

Each is computed in Prajna (read only) and again on the copy (Postgres, or pandas over the Parquet). All must match exactly. They are stored in `docs/COPY_VERIFY_<date>.json` and committed. Plus a smoke test: `bars.official()` on the copy equals `bars.official()` on Prajna for 50 random names and for every name with a CA.

### Recommendation: Option 2 (copy), with Prajna's DB kept frozen as the archive of record

- Drishti stops depending on Prajna's schema.
- The irreplaceable CA and basis history gets a second copy.
- The cost is only ~5 GB.
- Prajna's DB stays untouched (read only, writers off) at least until P6. Archiving or dropping its 207 GB (to free root) is a **separate, later** decision for the user. Dropping it would need a full backup first, and a full backup of `prajna` does not fit on today's free disk.

---

## Part 5: Drishti's own ingest from the stop date

**Budget:**
- Drishti uses at most **0.8** of each Upstox API budget (50/s, 500/min, 2000/30 min per API per user). That is 40/s, 400/min and 1,600/30 min, which leaves headroom.
- The 30-minute cap binds, so a full sweep of ~3,600 keys takes about 70 minutes.
- The WebSocket's 2 connections are Drishti's.

| Dataset | Upstox endpoint | Schedule (IST) | Requests | Continuity check on the overlap window |
|---|---|---|---|---|
| **Login** (token) | TOTP login flow (copied from Prajna `app/vendor/upstox/auth.py`, with provenance) | 06:10 daily: probe first, log in only if invalid, at most 1 login a day (guard file) | 1 | Token valid (`/v2/user/profile`); never printed |
| Instrument master | public `assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz` | 06:20 daily | 0 (public file) | Same key set and series as Prajna `instrument` on the overlap; new and removed names listed |
| **Official 1d** (NSE_EQ + 21 indices) | `/v3/historical-candle/{key}/days/1/{to}/{from}` (T−5..T per key) | 06:30 Tue–Sat for T = the previous session; retry 07:45 for anything missing | ~3,620 (~70 min) | Raw OHLCV equal to Prajna's stored bar for the same (key, date) on ≥ 5 sessions (same vendor; any difference must be a CA re-serve); adjusted: median \|diff\| < 0.01 %, max < 0.1 % against Prajna `bars_adjusted` |
| Provisional bar, circuit limits, index quotes | `/v2/market-quote/quotes` | 15:40 (exists) | ~8 | barcheck (exists) |
| **Corporate actions** | `/v2/fundamentals/{ISIN}/corporate-actions` | 22:30 daily, all ISINs (fundamentals API, own budget) | ~3,600 | Identical (isin, type, ex_date, ratio) to Prajna `corporate_action` on the overlap |
| CA factors | derived (copy of Prajna's SPLIT_FV / BONUS_RATIO derivation, with provenance) | after the CA sweep | 0 | Identical to Prajna `ca_factor` for **every** row 2025-09-24 → stop (exact) |
| Vendor-applied detection (basis) | `/v3/historical-candle` full 1d history of each name with a split/bonus ex-date in [T−5, T] (Prajna `ca_refresh selfheal`) | 07:50 | ~0–20 | `vendor_applied` matches Prajna on the overlap's CA names |
| Holidays | `/v2/market/holidays` | Mon 07:30 (exists) | 1 | Matches `trading_session` for 2026 (with SC1) |
| Results calendar | see **D2** | n/a | n/a | n/a |
| News (veto, P5) | see **D2** | n/a | n/a | n/a |
| Intraday 1m/15m/1h (P7) | `/v3/historical-candle/{key}/minutes/1`, `/minutes/15`, `/hours/1` | see **D3**: 22:00 nightly | top 500: ~1,500 (~30 min); all: ~10,800 (~3.5 h) | Equal to Prajna's bars on the overlap |
| Fundamentals, FII/DII, global indices | `/v2/fundamentals/...`, institutional, V3 global | **not ingested** (not in the plan) | 0 | n/a. Fundamentals history is re-servable later; FII/DII depth is unknown, so a small permanent gap is accepted |
| Ticks / pre-open | WS V3 | OFF until the disk is enlarged (**D4**) | n/a | n/a |

**Drishti code that reads Prajna live today** and must be re-pointed to the copies plus its own ingest:
- `drishti/data/bars.py` (`official()`: `canon_market_bar`, `ohlcv_payload_basis`, `canon_ca_factor`, `ohlcv_observation`);
- `bar_check.official_history`;
- `eod_quotes.universe_keys` (Prajna `instrument`);
- `run/p2.py` (`results_event`, `canon_instrument`);
- `api/app.py` (search, symbols, sectors).

Also: `DRISHTI_UPSTOX_TOKEN_FILE` moves from Prajna's path to `drishti/backend/var/upstox_token.json`. `rest.py` keeps refusing login URLs; only the new `login.py` may call them.

**Estimate under B** (new phase **P1c**, before P3):

| Item | Days |
|---|---|
| Login | 0.5 |
| Master | 0.5 |
| 1d nightly + official/provisional plumbing | 1 |
| CA ingest + factor derivation + vendor-applied detection + checks | 3–4 |
| Results/news per D2 | 0.5–1 |
| Copy + verification | 1–1.5 |
| Re-pointing reads | 0.5 |
| Switch ops | 0.5 |
| **Total** | **≈ 8–10 working days** |

---

## Part 6: Switch-over plan

### 6.1 Prajna crontab diff (the user applies it; lines are commented, never deleted)

**Generate it for review** (nothing is installed until the last line):
```bash
crontab -l > ~/crontab_before_B.txt
awk '
  /drishti_job\.sh/ { print; next }                                   # Drishti lines untouched
  /^[[:space:]]*#/ || /^[[:space:]]*$/ || /^[A-Z_]+=/ { print; next } # comments, blanks, vars
  /daily\.sh --login|preopen_day\.sh|maintenance\.sh|global_refresh\.sh|news_poll\.sh|timing_monitor\.sh|close_then_backfill\.sh|backfill_supervisor\.sh|ca_refresh\.sh|paper_daily\.sh|stage1_checkpoint\.sh|stage3_snapshot\.sh|model_predict\.sh|fundamentals_events\.sh|e51_data\.sh|dashboard\.sh|market_report\.sh|signals_snapshot\.sh|yahoo_research\.sh|e6_daily\.sh|shadow_daily\.sh|shadow_review\.sh|lab_daily\.sh|lab_brief\.sh|lab_eod\.sh|desk_run\.sh/ {
     print "# B-STOP 2026-10-29: " $0; next }
  /news_collect\.sh|news_reconcile\.sh|news_ai\.sh|acceptance stage2/ {
     if (ENVIRON["KEEP_NEWS"] == "1") print; else print "# B-STOP 2026-10-29: " $0; next }
  { print }' ~/crontab_before_B.txt > ~/crontab_B.txt
diff ~/crontab_before_B.txt ~/crontab_B.txt     # review
# crontab ~/crontab_B.txt                         # install (user)
```

- **If D2 = keep news:** run it with `KEEP_NEWS=1`, and add the events-only line. It is the same CLI that `fundamentals_events.sh` calls first, without its Upstox `refresh` step, so no Prajna code changes:
  ```
  15 13 * * *  cd $B && .venv/bin/python -m app.cli.main --plain fundamentals events --commit >> var/logs/cron.log 2>&1
  ```
- **Check after installing:**
  ```bash
  crontab -l | grep -vE '^\s*#' | grep -E -- '--login|preopen_day|close_then|backfill_supervisor|ca_refresh|global_refresh|news_poll|timing_monitor|fundamentals_events|dashboard\.sh|desk_run'
  ```
  It must print nothing.

**Processes to stop** (by exact PID, after their cron lines are off):
- **Dashboard:** `cd /home/cis/windows/prajna/backend && ops/runbooks/dashboard.sh stop` (pid 1044991 today; it polls Upstox quotes).
- **News collector:** if D2 = stop, its current PID (1648217 today; re-check with `ps` on the day), after the 23:30 window or at once.
- **Transient jobs** (`fundamentals refresh`, `ingest …`): let them finish. They hold flocks, and none starts again.

### 6.2 Order of steps (no trading day without data)

1. **Build P1c** (≈ 8–10 days) while decision A rules still apply: Drishti does not log in, and uses the historical API only 22:00–05:30.
2. **Shadow overlap, ≥ 5 consecutive sessions before the switch.** On the shared token, every night, Drishti runs its 1d, CA, factor and master ingest into its own tables, and all the continuity checks of Part 5 must pass. Prajna keeps running unchanged.
3. **First copy (Part 4)**, at night, after a session. Verified with the checks; the result goes to `docs/COPY_VERIFY_<date>.json`.
4. **Switch day S** (a trading day; proposed **Friday 2026-11-13**, D6):
   1. 16:05–~19:15: Prajna's close job runs as usual (intraday of S).
   2. **~19:30:** the user installs the cron diff (6.1); the dashboard is stopped; the news collector is stopped if D2 = stop.
   3. 20:30: the **delta copy** (rows with `fetched_at` after the first copy) and its verification. **Prajna is frozen from here.**
   4. Prajna's token of S (06:30 login) expires at ~03:30 Saturday, so no two Upstox owners overlap.
5. **S+1 (Saturday):**
   1. 06:10 Drishti's first own login;
   2. 06:30 official 1d of S, which Prajna never fetched, so there is no gap. It re-fetches S−5..S−1 and compares them with the copy;
   3. 22:30 CA sweep.
6. **Monday S+3:** the first full Drishti-only session (06:10 login → 06:30 1d → 15:40 EOD → 19:30 Scout → 22:30 CA).

### 6.3 Protocol-deviation notes (text for Prajna's docs, committed on switch day after approval)

To be added to the P1 (`STAGE4_P1_SHADOW_PREREGISTRATION.md`), P2 (`STAGE4_P2_REVIEWER_PREREGISTRATION.md`), E5 (`STAGE4_E5_PREREGISTRATION.md`) and E6 (`STAGE4_E6_PREREGISTRATION.md`) documents:

> **Protocol deviation (2026-11-13): forward window ended by user decision 2026-10-29. Upstox moved to Drishti (Option B); Prajna stopped.**
> - The last forward session is <S>.
> - **No look was taken.** The specification, the frozen policy and the thresholds are unchanged.
> - The data collected so far stays in Prajna's frozen database (and in Drishti's copy).
> - It must not be evaluated as this pre-registration's result, because the planned length was not reached.

Only docs are changed; no Prajna code is changed. Prajna's memory notes are updated the same day.

### 6.4 Verification after the switch (first full session, Monday S+3)

1. **Cron:** the check command in 6.1 prints nothing. `crontab -l | grep -c drishti_job` equals the Drishti count.
2. **Processes:** `ps -eo pid,cmd | grep -E 'app\.(cli|dashboard)'` shows no Prajna process, except the news collector if D2 = keep.
3. **No Prajna Upstox use for one session:**
   - Prajna's `backend/var/upstox_token.json` mtime has not changed since S, so there was no login;
   - `ingest_run` has no new row (`max(started_at)` ≤ S 19:30);
   - `ohlcv_bar` `max(fetched_at)` ≤ S.
4. **Prajna DB frozen:** `pg_stat_user_tables` `n_tup_ins/upd/del` are unchanged across the session for every table except `news_*` / `results_event` (D2 = keep).
5. **Drishti jobs all OK:** login, master, 1d, EOD, Scout, CA, barcheck. Check `run` / `run_end` and `cron.log`.
6. **Both WebSocket connections free:** a 60-second read-only connect probe at 10:00 opens 2 connections and closes them. It records nothing, because the tick recorder stays OFF until the disk is enlarged.
7. **Docs:**
   - `P0_PRAJNA_DECISION.md` records B as final (done 2026-10-29).
   - `DESIGN.md` gets phase P1c.
   - `FREEZE_M1.md` at P6 notes the data source change.

---

## Decisions for the user

| # | Decision | My recommendation |
|---|---|---|
| **D1** | History: keep reading the frozen Prajna DB (1) or copy what Drishti needs (2) | **2**: copy ~5 GB, and keep Prajna's DB frozen as the archive; archiving or dropping it later is a separate decision |
| **D2** | News + results calendar after the stop. **(a)** Keep Prajna's **non-Upstox** news collector (7 sources, including **NSE corporate announcements**, which is a fetch from an NSE site that Prajna had already approved), with `acceptance stage2`, `news_reconcile`, `news_ai` and the events-only line; Drishti reads it. **(b)** Stop all of it. The results calendar then comes from an NSE board-meetings CSV that the user downloads weekly into `drishti_data/manual/` (like ASM/GSM), and news for the veto from Drishti's own Upstox `/v2/news` in P5 | Your call, because of the NSE terms question. **(a)** keeps the results calendar automatic and forward-continuous. **(b)** keeps Drishti fully inside its no-NSE-fetch rule. Risk for (a): the collector's `stage2_pass` lock may fail once bars stop ("unexpected_gaps", UNVERIFIED). Check it on S+1..S+3; if it fails, (a) falls back to (b) |
| **D3** | Intraday from the switch: nightly 1m/15m/1h for the top-500 now (~30 min/night, ~5 MB/day Parquet), or nothing until P7 (backfill later; the depth of Upstox's 1m history is to be verified) | **Top-500 nightly from S+1**: cheap, and it keeps P7's history unbroken |
| **D4** | Pre-open and tick recordings stop at S (not backfillable) until the disk is enlarged | Accept, unless the disk can be enlarged before S |
| **D5** | Credentials: Drishti's `.env` needs `UPSTOX_API_KEY`, `UPSTOX_API_SECRET`, `UPSTOX_CLIENT_ID`, `UPSTOX_PIN`, `UPSTOX_REDIRECT_URL`, `UPSTOX_TOTP_SECRET`, `UPSTOX_USERNAME` | Either the user copies them, or the user authorises a one-line copy from Prajna's `.env` to Drishti's `.env` (0600) that never prints a value |
| **D6** | Switch date | **Friday 2026-11-13**, after the close job (P1c built by ~11-12; shadow sessions 11-05, 06, 09, 11, 12; 11-10 is a holiday) |

---

## User decisions (2026-10-29)

| # | Decision |
|---|---|
| D1 | **Copy** (Option 2). Also a separate **archival dump** of every irreplaceable dataset, with sha256, kept on /windows and on a second location the user provides: CA history, vendor basis/observations, pre-open recordings, news + news AI + results events. Interim archive 2026-10-29 in `/windows/prajna_archive/irreplaceable_20261029/`; the final archive is made on the switch night after the delta copy |
| D2 | **(b): no NSE fetching from any system.** Prajna's news collector stops with the rest. The results calendar comes from an NSE board-meetings CSV that the user downloads by hand every **Mon and Thu** into `drishti_data/manual/`, with a freshness guard like ASM/GSM: **STALE > 4 days**. News comes from Upstox **`/v2/news`** (Drishti ingest). So the cron diff runs **without** `KEEP_NEWS`, and the events-only line is not added |
| D3 | **Yes:** nightly 1m/15m/1h for the top 500. Also fill Prajna's pending 1m gap (2026-04-29 → 07-17) once Drishti owns the budget. GB estimate below |
| D4 | Accepted: the pre-open/tick gap lasts until the disk is enlarged |
| D5 | The user creates a **separate Upstox developer app** for Drishti (new API key and secret, same TOTP) and puts its keys in `drishti/backend/.env`. After the switch the user **revokes Prajna's app keys**, so Prajna cannot call Upstox at all. The env names are below |
| D6 | The shadow overlap starts only once P1c is complete, so the switch moves to **Friday 2026-11-20** (schedule below) |

### Env names Drishti expects (names only; in `drishti/backend/.env`, mode 0600)

| Name | Meaning |
|---|---|
| `DRISHTI_UPSTOX_API_KEY` | API key (client_id) of the new Drishti app |
| `DRISHTI_UPSTOX_API_SECRET` | Its API secret |
| `DRISHTI_UPSTOX_REDIRECT_URL` | The redirect URL registered on the new app (it must match exactly) |
| `DRISHTI_UPSTOX_USERNAME` | The login mobile number (same account) |
| `DRISHTI_UPSTOX_PIN` | The account PIN |
| `DRISHTI_UPSTOX_TOTP_SECRET` | The TOTP seed (same as today) |
| `DRISHTI_UPSTOX_LOGIN_ENABLED` | `false` until the switch. Login is refused unless this is `true` (set on S+1) |
| `DRISHTI_UPSTOX_TOKEN_FILE` | Already present. On S+1 it changes from Prajna's token file to `/home/cis/windows/drishti/backend/var/upstox_token.json` |

**Why Drishti never logs in before S:** whether a second login on the same account (even through a different app) invalidates Prajna's token is UNVERIFIED, so decision A's rule stands until the switch.

### D3 estimate (shown before anything runs)

- **Nightly top 500:**
  - 1m: 500 × 375 bars ≈ 188 k rows/day, ≈ 1.3 MB;
  - 15m: ≈ 0.2 MB;
  - 1h: ≈ 0.1 MB;
  - **≈ 1.6 MB/day, ≈ 0.4 GB/year** on /windows. Requests: 1,500 a night, ≈ 30 min at fraction 0.8.
- **1m gap 2026-04-29 → 07-17:**
  - 55 sessions × 3,606 instruments (Prajna's PENDING_BACKFILL set), ~85 % trading on a given day, gives ≈ 62 M rows, **≈ 0.4–0.5 GB** Parquet/zstd.
  - About 11 k requests (minute data comes in ≤ 1 month per request), ≈ 3.5 h at fraction 0.8, run over one or two nights after S.
  - The Upstox depth for April 2026 is verified on 5 names before the full run.

### Schedule (D6): switch Friday 2026-11-20

NSE holidays in the window: **Tue 2026-11-10** (Diwali Balipratipada) and **Tue 2026-11-24** (Guru Nanak Jayanti). Muhurat is Sun 2026-11-08, which is not a session (SC1).

| Date | Work | Session? |
|---|---|---|
| Thu 10-29 | Decisions recorded; Drishti env and config; guarded login module (mocked tests only); interim archive dump | yes |
| Fri 10-30 | Instrument master ingest + Drishti instrument tables; board-meetings CSV loader + STALE > 4 d guard (needs one sample CSV from the user) | yes |
| Mon 11-02 | Official 1d ingest (V3 historical, Drishti tables; nightly 22:00–05:30 under A rules) | yes |
| Tue 11-03 | CA ingest (`/v2/fundamentals/{ISIN}/corporate-actions`) | yes |
| Wed 11-04 | CA factor derivation (port of Prajna's), exact check against `ca_factor` | yes |
| Thu 11-05 | Vendor-applied detection + basis; Drishti-adjusted vs Prajna-adjusted check tool | yes |
| Fri 11-06 | Upstox `/v2/news` ingest; intraday top-500 job (Parquet); 1m-gap job (runs only after S) | yes |
| Mon 11-09 | Copy tooling + verification hashes; dry run on one month | yes |
| Tue 11-10 | **NSE holiday.** Re-point Drishti reads to its own tables (behind a flag); switch scripts | **no** |
| Wed 11-11 | Full copy #1 (night) + verification | yes |
| Thu 11-12 | **P1c complete.** Shadow session 1 (night ingest) | yes |
| Fri 11-13 | Shadow 2 (P2's remaining work continues during the shadow) | yes |
| Mon 11-16 | Shadow 3 | yes |
| Tue 11-17 | Shadow 4 | yes |
| Wed 11-18 | Shadow 5 | yes |
| Thu 11-19 | Shadow 6 (buffer); switch rehearsal checklist | yes |
| **Fri 11-20** | **Switch** after Prajna's close job (~19:30): cron diff, dashboard stop, delta copy + verification, **final archive dump** | yes |
| Sat 11-21 | 06:10 first Drishti login; 06:30 1d of 11-20; CA sweep | no |
| Mon 11-23 | First Drishti-only session; verification 6.4; the user revokes Prajna's app keys after it passes; 1m-gap backfill night 1 | yes |
| Tue 11-24 | **NSE holiday**; 1m-gap night 2 if needed | **no** |

If any shadow session fails a continuity check, the switch moves one week, to Fri 2026-11-27.

### Interim archive (D1), made 2026-10-29, read only

All files are in `/windows/prajna_archive/irreplaceable_20261029/` (mode 0600). Each has a `.sha256` file, `MANIFEST.txt` lists them, and every sha256 was checked.

| File | Size | Tables (rows) |
|---|---|---|
| `prajna_ca_20261029.dump` | 0.4 MB | corporate_action 2,377; ca_factor 2,377 |
| `prajna_basis_20261029.dump` | 25.0 MB | ohlcv_payload_basis 604,127; ohlcv_observation 34,224 |
| `prajna_news_20261029.dump` | 31.9 MB | 16 tables: news_item 46,126 … news_article 1,049, news_instrument 2,826, news_ai_enrichment 12,272, results_event 131,841 |
| `prajna_preopen_20261029.dump` | 825.2 MB | preopen_tick 8,512,199; preopen_book 42,559,455; preopen_session_status 396; instrument_universe_membership 78,199 |
| `prajna_raw_payloads_20261029.tar.zst` | 4,015.1 MB | Raw vendor payload files `prajna/backend/var/archive`: 792,166 files, 4.13 GB before compression. The payloads are already gzip, so zstd gains little. `zstd -t` OK (user Q5, 2026-10-29) |
| **Total** | **≈ 4.9 GB** | n/a |

**Caveat on the row counts:** they were taken right after each dump, not inside its snapshot, so news rows added in between may differ by a few. **The final archive on the switch night is made with Prajna frozen**, and it is test-restored into a scratch database, whose counts are compared with the source exactly. Both archives are then copied to the second location the user provides, and the sha256 is checked there.

The raw vendor payload files are in the archive too (row above). **User decision (Q5):** they are included in every archive, and the final archive on the switch night re-makes this file.
