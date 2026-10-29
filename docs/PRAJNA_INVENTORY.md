# Prajna database inventory for Drishti (Option B prep)

**Status:** report for the user's approval, 2026-10-29. All of it was gathered **read only**:
- `drishti_ro`, or psql with `default_transaction_read_only=on`;
- the code, docs and crontab were read, not changed.

Nothing in Prajna was stopped, changed or copied.

**Instance:**
- **Database:** `prajna-postgres`, Postgres 18, database `prajna`, **207 GB**.
- **Location:** the Docker volume `/var/lib/docker/volumes/prajna_prajna_pgdata`, on the **root disk**. Root has 377 GB, 86 % used, 51 GB free.
- **Also on the instance:** `drishti` (20 MB), `drishti_test`, `prajna_test` (58 MB).
- **Files outside the DB:** `prajna/backend/var/archive` holds 6.1 GB of raw vendor payloads.

Contents:
1. Part 1: inventory, by group
2. Part 2: Drishti needs → Prajna coverage, and look-ahead risks
3. Part 3: what stops when Prajna stops (every job, keep/stop)

---

## Part 1: Inventory

**Notes on the columns:**
- **Rows:** exact counts for the small tables; for the big ones, an exact count by timeframe and year (index-only scans).
- **Size:** the table plus its indexes.
- **Freshness:** the last write seen on 2026-10-29 around 13:00 IST.

### 1.1 Price bars (`ohlcv_bar`: 220.8 M rows, 118.7 GB)

**What it is.** One row per (instrument, timeframe, bar start, source). Prajna stores the vendor value **as fetched**. Each row carries:
- `fetched_at`, `knowable_at` and `payload_sha256`;
- the payload's price basis, in `ohlcv_payload_basis`.

It is filled by `ingest candles` (Upstox REST V3 `/v3/historical-candle/...` and `.../intraday/...`).

| Timeframe | Segment | Rows | Instruments | Range | Sessions | Filled by | Last write |
|---|---|---|---|---|---|---|---|
| **1d** | NSE_EQ | 3,854,153 | 3,599 | 2019-09-03 → 2026-10-28 | 1,776 | 07:00 `daily.sh morning` (all NSE, T−1), 06:30 new listings, `ca_refresh` re-fetches | 2026-10-29 08:32 IST |
| 1d | NSE_INDEX | 5,325 | 3 (Nifty 50, Nifty Bank, India VIX) | 2019-09-03 → 2026-10-28 | 1,775 | same | same |
| 1d | GLOBAL_INDEX / GLOBAL_INDICATOR | 16,812 / 5,502 | 10 / 3 | 2020-03/04 → 2026-10-28 | ~2,000 (non-NSE calendars) | `global_refresh.sh` 12:40, 21:10 | 2026-10-29 12:40 |
| **1h** | NSE_EQ | 20,042,365 (2022: 3.2 M … 2026: 4.4 M) | 3,602 | 2022-01-03 → 2026-10-28 | 1,196 | 16:05 `close_then_backfill.sh` (+ the 2026-10 backfill) | 2026-10-28 17:38 |
| **15m** | NSE_EQ | 70,415,855 (2022: 11.3 M … 2026: 15.5 M) | 3,601 | 2022-01-03 → 2026-10-28 | 1,196 | same | 2026-10-28 16:39 |
| **1m** | NSE_EQ | 84,028,203 | 3,598 | **2026-07-20** → 2026-10-28 (70 sessions) | 70 | 16:05 job; the backfill from 2026-04-29 is still PENDING | 2026-10-28 18:47 |
| **5m** | NSE_EQ | 55,200,543 | n/c | 2025-10-21 → 2026-10-21 | ~247 | an earlier research backfill; out of canon scope (D2-5m) | 2026-10-21 |

(n/c = not counted. The per-instrument count over 220 M rows was cancelled to spare the production instance.)

**Gaps**, from Prajna's own `canon_coverage` and a check against `trading_session`:
- **No NSE trading day is missing** in 1d for 2019-09 → 2026-10.
  - One extra 1d date: **2025-04-26**, a Saturday special session.
  - `trading_session` itself counts the Muhurat and DR-test sessions as trading days. Drishti's SC1 does not.
- **Inner holes:** sessions between an instrument's first and last bar where the vendor served nothing (no trade, suspension).

  | Timeframe | Instruments with inner holes | Hole sessions |
  |---|---|---|
  | 1d | 1,016 | 146,128 |
  | 15m | 910 | 102,068 |
  | 1h | 911 | 102,939 |
  | 1m | 521 | 7,282 |

  These are mostly illiquid names (state `EMPTY`).
- **PENDING_BACKFILL:**
  - 1m: 3,606 instruments, 200,554 sessions, 2026-04-29 → 2026-07-17;
  - 15m/1h: 34–36 instruments;
  - 1d: 23 single sessions.
- **QUARANTINED:** a handful (1d: 2, 15m: 11, 1h: 4), mostly on Muhurat 2023-11-12.
- **VENDOR_ERROR:** 1–2 per timeframe.

**Adjustment.** Prajna never edits a stored price. The adjusted view is computed on read (`app/canon/pit.bars_adjusted`), from:

| Table | What it holds |
|---|---|
| `ohlcv_payload_basis` (604 k payloads, 0.2 GB) | Per payload: `VENDOR_ADJUSTED` (367,905; Upstox history comes back already split/bonus-adjusted as of `basis_as_of`) or `RAW_OBSERVED` (236,222; same-day bars) |
| `ohlcv_observation` (34 k) | Re-served bars, classified `CA_ADJUSTMENT` (30,504), `REOBSERVED` (3,608) or `GLOBAL_REVISION` (112) |
| `corporate_action` → `ca_factor` / `canon_ca_factor` | The split and bonus factors (1.3) |

Only splits and bonuses are adjusted: dividends are `UNSUPPORTED` and rights are `RIGHTS_TERP UNSUPPORTED`.

**Quality checks Prajna already runs:**
- **Stage 1 gates:** `acceptance stage1`, an incremental gate checkpoint (`gate_scan_checkpoint`, 06:35), and `ingest_anomaly` (1,942 rows).
- **Stage 2:** the canon views (`canon_market_bar`, `canon_excluded_bar`, `canon_coverage`), plus `acceptance stage2` with tests at 05:30 daily (gates for gaps, duplicates, identifiers, timeframes …).
- **PIT read API** (`app/canon/pit.py`): every read takes `as_of` and returns only rows with `knowable_at < as_of`.
- Drishti's own `bars.official()` reproduces `bars_adjusted` exactly (3 names with a 2026 split or bonus, max relative diff 2e-9).

### 1.2 Indices and VIX

| Data | Where | Range | Source |
|---|---|---|---|
| Nifty 50, Nifty Bank, India VIX (1d) | `ohlcv_bar` (NSE_INDEX) | 2019-09-03 → 2026-10-28 | Upstox V3 |
| Global indices and indicators (10 + 3) | `ohlcv_bar` (GLOBAL_*), `canon_global_bar`, `global_bar_finality`, `global_instrument_contract` (13) | 2020-03 → 2026-10-28 | Upstox V3, 12:40/21:10 |
| Nifty 500, 15 sectors, Nifty200 Momentum 30 … (21 keys) | **Drishti's own** `index_bar` (not Prajna) | 2019 → now | Drishti P1 |

### 1.3 Corporate actions

| Table | Rows | Range | Source / job | Notes |
|---|---|---|---|---|
| `corporate_action` | 2,377 (dividend 2,162, split 80, bonus 71, rights 64) | ex-date **2025-09-24** → 2026-10-28 | Upstox `/v2/fundamentals/{ISIN}/corporate-actions`: Sat 10:00 `daily.sh weekly`, 06:45 `ca_refresh targeted`, 06:30 new listings | **The vendor serves only about 1 year of history** (`app/ingest/corporate_actions.py`). Events older than ~2025-09 cannot be fetched again, so this table is the only record |
| `ca_factor` / view `canon_ca_factor` | 2,377 (EXACT: 73 split + 57 bonus APPLIED, 11 + 6 UNKNOWN, 3 + 1 NOT_APPLIED) | same | `maintenance.sh all` (06:40) derives them | `vendor_applied` = whether Upstox already baked the factor into served history. It is detected by `ca_refresh selfheal` re-fetches (08:40) |
| views `canon_corporate_action`, `canon_corporate_action_kn_ca` | n/a | n/a | n/a | The knowable-at rule is CA-OBSERVED (migration 0018): greatest(announcement end of day, fetched_at) |

### 1.4 Instruments, master and symbol changes

| Table | Rows | Range | Source | Notes |
|---|---|---|---|---|
| `instrument` | 3,619 (NSE_EQ: EQ 2,724, SM 483, BE 219, ST 98, BZ 33, IV 21; GLOBAL 10; REMOVED_FROM_MASTER 23) | valid_from 2026-09-23 → | Upstox public master `assets.upstox.com/.../NSE.json.gz`, 06:30 | `instrument_type` = the series. **History only since 2026-09-23** |
| `instrument_attribute_version` | 4,043 | 2026-09-23 → | same | Attribute changes (symbol, name, lot …) since 2026-09-23 |
| `instrument_lifecycle_period` | 3,764 | 2026-09-23 → | same | ACTIVE / REMOVED_FROM_MASTER periods |
| `instrument_security_class` | 3,619 | 2026-10-29 | `classify securities` | STOCK 3,215, FUND_UNIT 358, INVIT 21, RE 5 … |
| `canon_instrument` | 3,619 | latest | derived | **`sector`: 3,236 names, 154 sectors.** It is the latest profile snapshot (not PIT); the historical sector is `pit.sector(key, as_of)` |
| `instrument_universe_membership` | 78,199 | 2026-09-24 → | pre-open universe (`universe = preopen`) | Daily pre-open universe only |

### 1.5 Results, fundamentals and events

| Table | Rows | Range | Source / job | Notes |
|---|---|---|---|---|
| `results_event` HISTORICAL_FILING | 129,844, 2,340 names | period 2014-03 → 2026-09, filed 2015-01-09 → 2026-10-23 | NSE "financial results" CSVs downloaded **by the user** (F4, 2026-10-23) | `event_at` = filing time; `knowable_at` = the load date 2026-10-23 |
| `results_event` INTIMATION | 1,436, 1,407 names | meeting 2026-08-07 → 2026-11-14 | `fundamentals events` (13:15), parsed from stored **NSE_ANNOUNCEMENTS** news (no vendor call) | **The results calendar: the results gate and the Scout "results ahead" flag** |
| `results_event` OUTCOME | 537 | 2025-09 → 2026-09 periods | same | It triggers `fundamentals refresh` (Upstox) |
| `results_event` ESTIMATE_NEWS | 24 | n/a | RSS headlines | Beat/miss |
| `fundamental_snapshot` | 50,445 (profile, competitors, key ratios, income Q/FY 2017→, balance sheet, cash flow, share holdings 2021→) | period 2003 → 2026-10 | Upstox `/v2/fundamentals/{id}/{endpoint}`: 06:30 new listings, monthly, 13:15 refresh after an OUTCOME | `knowable_at` = fetch time (since 2026-09); history is **as restated** at fetch |
| `fundamental_refresh` | 378 | 2026-10-21 → | `fundamentals refresh` | n/a |
| `macro_observation` | 5,720 | 2026-04-01 → 2026-10-28 | Upstox V2 FII/DII (`ingest institutional`, 07:00) | n/a |
| view `canon_fundamental`, `canon_macro_observation` | n/a | n/a | PIT views | n/a |

### 1.6 News and news AI

| Table | Rows | Range | Source / job |
|---|---|---|---|
| `news_item` (+ `news_item_observation`, `news_poll`, `news_story`, `news_story_member`, `news_entity_mention`, `news_entity_link`, `news_classification`, `news_decision`, `news_assessment`) | 46,073 items: NSE_ANNOUNCEMENTS 34,664, CNBCTV18 5,620, ET 2,209, Mint 1,542, BL 1,007, BS 838, SEBI 205 | published 2026-09-27 → now | `news_collect.sh` every 15 min, 06:00–23:15 (RSS, sitemaps and NSE corporate announcements; **no Upstox**). Production since 2026-09-29 |
| `news_article` + `news_instrument` | 1,046 / 2,820 | 2026-09-17 → now | **Upstox `/v2/news`**: `news_poll.sh` (09:30–15:30), 07:00 morning job and 16:05 close job |
| `news_ai_enrichment` | 12,272 | 2026-10-22 → 2026-10-27 | `news_ai.sh` (Bedrock Nova Pro). Stopped since the Bedrock key went invalid on 2026-10-27 |
| `news_audit`, `news_content` (0) | n/a | n/a | n/a |
| view `canon_news` | n/a | n/a | PIT (`knowable_at` = discovered/processed time) |

### 1.7 Calendars and holidays

| Table | Rows | Range | Source |
|---|---|---|---|
| `trading_session` | 2,677 | 2019-09-03 → **2026-12-31** | Upstox `/v2/market/holidays` + `/v2/market/timings/{date}` (monthly job, +60 days). Includes `preopen_start_ist` |
| Drishti `trading_holiday` | n/a | 2026 | Drishti's own (weekly) |

### 1.8 Model and experiment outputs (all derived; PAPER)

| Group | Tables | Rows | Range | Notes |
|---|---|---|---|---|
| Stage 3 features | `feature_value` (7.4 M, 3.6 GB) | 7.4 M | 2026-09-29 → 2026-10-23 | features-v5, PRE_SESSION / PRE_OPEN snapshots |
| Stage 4 training | `training_feature_value` (173.7 M, **74 GB**), `training_label` (24.5 M, 9.5 GB), `training_sample` (2.2 M, 1.4 GB), `training_*_run`, `training_split`, `training_session_done`, `training_policy_param`, schemas `train_asif`, `train_asif2`, `train_strict` (views) | n/a | samples 2025-09-24 → 2026-10-28; datasets from 2019-12 | Prajna's intraday datasets. **Not usable by Drishti** (different features and labels) |
| E5 | `model_version` (1), `model_prediction` (1,000) | n/a | 2026-10-22 → 23 | 40-session forward test |
| P1 shadow / P2 reviewer | `shadow_policy`, `shadow_run`, `shadow_trade` (22), `shadow_skip` (69), `llm_reviewer`, `llm_review` (22) | n/a | forward from 2026-10-27 | Blind until the look |
| Stage 7–8 paper | `paper_run`, `paper_day` (192), `paper_order` / `paper_fill` (1,579), `paper_risk_decision` (1,800) | n/a | 2026-07-22 → 2026-10-26 | n/a |
| Strategy Lab / Buy List | `lab_run` (139), `strategy_day` (396), `trade_forensic` (3,464), `day_forensic` (399), `daily_brief`, `regime_snapshot`, `strategy_switch` | n/a | 2026-07-22 → now | Unblinded by the user |
| Research Desk | `desk_run`, `desk_verdict` (1,214), `desk_dossier`, `desk_watch_event`, `desk_track_event` | n/a | 2026-10-27 → now | n/a |
| Stage 3 events | `stage3_event` (92) | n/a | n/a | n/a |
| E6 PEAD | files `var/research/e6` | n/a | from 2026-10-23 | Not in the DB |

### 1.9 Pre-open and tick recordings

| Table | Rows / size | Range | Source |
|---|---|---|---|
| `preopen_tick` | 8.5 M / 4.4 GB | 22 sessions, 2026-09-24 → 2026-10-29, 3,602 keys | Upstox V3 WebSocket, **both connections**, 08:55–09:20 (`preopen_day.sh`) |
| `preopen_book` | 42.6 M / 4.6 GB | same (depth rungs per tick) | same |
| `preopen_session_status` | 396 | same | same |
| view `canon_preopen` | n/a | n/a | iep/ieq/tbq/tsq/ltp (**no circuit limits**) |
| `tick_archive` | 0 | n/a | never used |

**Neither can be backfilled:** the vendor serves no pre-open history.

### 1.10 Logs and ops

| Table / files | Rows / size | Notes |
|---|---|---|
| `raw_payload` | 0.87 M / 3.9 GB | Index of raw vendor payloads (2026-09-23 →); the files are in `var/archive` (6.1 GB) |
| `ingest_run` | 0.73 M / 1.7 GB | One row per ingest run |
| `ingest_watermark` | 18 k | Per-stream checkpoints |
| `ingest_anomaly` | 1,942 | n/a |
| `canon_coverage` | 262 k | Coverage by state (1.1) |
| `gate_scan_checkpoint` | 15 | n/a |
| `alembic_version` | n/a | n/a |
| `var/logs` | 648 MB | n/a |
| `var/venv-broken-py311-2026-09-22` | 7.8 GB | A dead venv; disposable, the user's call |

---

## Part 2: Drishti needs → Prajna coverage

| Drishti need | Prajna source | How far back | Gap / caveat |
|---|---|---|---|
| **Adjusted 1d bars** (universe, indicators, 52-week high, RS 6M/12M, SMA200) | `ohlcv_bar` 1d NSE_EQ + `ohlcv_payload_basis` + `canon_ca_factor` (Drishti `bars.official()`) | 2019-09-03 | The 12M RS score needs 250 + 250 sessions, so the first usable RS date is ~2021-08. CA factors exist only from 2025-09-24; older splits rely on the vendor's own adjustment at fetch time (1.1). Survivorship: delisted names are absent (P0 probe) |
| Indices + VIX | Prajna: Nifty 50, Bank, VIX. **Drishti `index_bar`: all 21** | 2019 | None (Drishti already owns this) |
| CA factors | `corporate_action`, `ca_factor` | 2025-09-24 | Older events are unrecoverable from Upstox (1-year window). **Must be preserved** |
| Results dates (results gate, PEAD flag) | `results_event` INTIMATION (forward calendar), HISTORICAL_FILING (`event_at` = filing time, for backtests) | Filings 2015 →; intimations 2026-08 → | Intimations come from Prajna's NSE_ANNOUNCEMENTS collector (Part 3, decision D2). For backtests, the filing date is the date results became public (fine for the "no position across results" gate) |
| News for the veto | `news_item` family (7 sources), `news_article` (Upstox) | 2026-09-17/27 → | Forward only, which matches the plan: LLM signals are validated forward only |
| Holidays | `trading_session` (2019-09 → 2026-12-31), Drishti `trading_holiday` | 2019-09 | `trading_session` counts Muhurat/DR as trading days, so apply SC1 when reading it |
| Symbol changes, listing dates | `instrument_attribute_version` (since 2026-09-23); Drishti manual `symbolchange.csv`, `namechange.csv`, `EQUITY_L.csv` (listing dates) | Master since 2026-09-23 | Use the NSE files the user downloaded for history; Prajna's master history is one month long |
| Series / segment (EQ vs BE/SM) | `instrument.instrument_type` | Current only | The series history is not PIT. Drishti uses EQUITY_L (series EQ) by file date |
| **f1 features + labels** (5-session triple barrier) | 1d bars + indices + results + news flags | as above | Labels need the bars T+1..T+5, covered for every date up to the stop date − 5 |
| **P7 intraday** 1m / 15m / 1h | `ohlcv_bar` | 1m 2026-07-20 →; 15m/1h 2022-01 →; 5m 2025-10-21 → 2026-10-21 | 1m backfill 2026-04-29 → 07-17 is still pending. The 5m year is a bonus |
| Pre-open / tick recordings | `preopen_tick`, `preopen_book` | 22 sessions | Stops at the switch (not backfillable). Drishti's tick recorder is off until the disk is enlarged |
| **Not in the plan yet but useful** | `canon_instrument.sector` (154 sectors) → the S1–S3 sector cap (≤ 2 per sector) and the Scout sector rule; `fundamental_snapshot` (ratios, shareholding) → possible later features (forward-PIT only); `macro_observation` FII/DII; `5m` bars; past results as benchmarks: E4 gap fade (ACCEPTED-EXPLORATORY), E5-H (NOT_ACCEPTED), E1/E2 docs, Lab `strategy_day` / `trade_forensic` | n/a | There is no F&O list and no delivery % anywhere in Prajna (so delivery stays out) |

### Look-ahead risks in Prajna data, and how Drishti handles them

| # | Risk | Where | Handling |
|---|---|---|---|
| L1 | **Vendor-adjusted history:** Upstox serves 1d history already adjusted for all splits and bonuses up to the fetch date (2026-09/10). A 2019 bar therefore "knows" a 2024 split | `ohlcv_payload_basis` VENDOR_ADJUSTED | For returns and indicators this is the standard back-adjusted series and is fine. **Price-level rules are not PIT** on it: ₹50 minimum price, ₹ traded value, pre-split levels. Traded value (close × volume) is invariant only if volume is adjusted too (Upstox adjusts both; Drishti scales volume by the same factor). Prajna's PIT API marks rows older than the CA horizon (2025-09-24) **LOW confidence** and refuses them by default; Drishti's live loader does not apply that refusal (documented in `bars.py`). P3 backtests must label it (already SURVIVORSHIP-BIASED, EXPLORATORY) |
| L2 | Revised / re-served bars | `ohlcv_observation` (CA_ADJUSTMENT, REOBSERVED) | Every row keeps `fetched_at` / `knowable_at`, and canon picks the row knowable at `as_of`. The SC3 provisional-vs-official recheck covers the T bar |
| L3 | A CA applied with future knowledge | `canon_ca_factor.knowable_at` (CA-OBSERVED) | `bars.official(as_of=…)` applies only factors with `knowable_at < as_of` and `ex_date ≤ as_of` (copied from Prajna) |
| L4 | **Restated fundamentals:** statements back to 2003 were fetched in 2026-09/10 as restated today | `fundamental_snapshot.knowable_at` = fetch time | Usable **forward only**; any backtest use leaks. Not in f1 |
| L5 | Results filings loaded on 2026-10-23 | `results_event` HISTORICAL_FILING (`knowable_at` = 2026-10-23, `event_at` = filing time) | For backtests use `event_at` (when the filing was public). That is acceptable, but only filings in that CSV exist (coverage of 2,340 names) |
| L6 | Sector = today's profile | `canon_instrument.sector` | Use `pit.sector(key, as_of)` logic (snapshots from 2026-09) forward; in backtests the sector is today's (minor leak, label it) |
| L7 | Series/instrument master is current only | `instrument` (since 2026-09-23) | Survivorship-style: use EQUITY_L listing dates; series history is unknown |
| L8 | Muhurat/DR days counted as sessions | `trading_session`, 1d bars on those dates | SC1: dropped by Drishti (`SHORT_SESSIONS`) |
| L9 | News `knowable_at` | `canon_news` | `knowable_at` = discovered time; forward only, so no backtest leak |

---

## Part 3: What stops when Prajna stops

Crontab as of 2026-10-29 (Prajna lines 1–149; Drishti's are separate).

**Key:**
- **U** = calls Upstox.
- **Keep?** gives my recommendation; D2 marks the lines that depend on the news decision in `SWITCH_TO_B.md`.

| # | Cron (IST) | Job | U | Feeds | Lost going forward if stopped | Keep? |
|---|---|---|---|---|---|---|
| 1 | 06:30 daily | `daily.sh --login refresh` | **U** (TOTP login, master download, new-listing 1d / fundamentals / CA) | `instrument*`, `ohlcv_bar` 1d, `fundamental_snapshot`, `corporate_action` | Daily master and new listings, which Drishti then ingests itself | STOP |
| 2 | 07:00 Mon–Fri | `daily.sh --login morning` | **U** (1d of T−1 for all NSE, FII/DII, `/v2/news`) | `ohlcv_bar` 1d, `macro_observation`, `news_article` | Official 1d; FII/DII; Upstox news | STOP (Drishti 1d) |
| 3 | 08:25 Mon–Fri | `preopen_day.sh --login` | **U** (WS × 2) | `preopen_*`, `instrument_universe_membership` | Pre-open recordings (not backfillable) | STOP (frees both WS) |
| 4 | 06:40 daily | `maintenance.sh all` | no | `ca_factor`, `ohlcv_payload_basis`, global contracts, status | Nothing new to derive | STOP |
| 5 | 09:10 Mon–Fri | `maintenance.sh canon` | no | Stage 2 canon state | n/a | STOP |
| 6 | 12:40, 21:10 | `global_refresh.sh` | **U** | 1d GLOBAL_* | Global indices (Drishti does not use them) | STOP |
| 7 | 09:30–15:30 /30 min, weekend 11/19 | `news_poll.sh` | **U** (`/v2/news`) | `news_article`, `news_instrument` | Upstox news (~30/day) | STOP |
| 8 | 09:21, 16:10 | `timing_monitor.sh` | **U** | var/acceptance b1b2 | Timing study | STOP |
| 9 | 16:05 Mon–Fri | `close_then_backfill.sh --no-backfill` | **U** (1m/15m/1h, news) | `ohlcv_bar` intraday | Intraday history from the stop date (backfillable later from Upstox V3; depth to be verified in P7) | STOP |
| 10 | 23:55 | `maintenance.sh status` | no | status snapshot | n/a | STOP |
| 11 | Sat 10:00 | `daily.sh --login weekly` | **U** (CA) | `corporate_action` | **CA history (1-year vendor window)**, which Drishti must ingest from day 1 | STOP (Drishti CA) |
| 12 | 1st 11:00 | `daily.sh --login monthly` | **U** (calendar +60 d, fundamentals) | `trading_session`, `fundamental_snapshot` | `trading_session` stops at 2026-12-31 (Drishti has holidays) | STOP |
| 13 | 05:30 | `acceptance stage2 --run-tests` | no | var/acceptance/stage2.json | Needed only by the news collector's `stage2_pass` lock | KEEP only if news is kept (D2) |
| 14 | */15 06–23 | `news_collect.sh` (7 sources) + long-running collector | no (RSS, sitemaps, **NSE corporate announcements**) | `news_item` family | News for the veto; the **results calendar** (INTIMATION) | **D2** |
| 15 | 23:45 | `news_reconcile.sh` | no | ops status | n/a | with D2 |
| 16 | */15, 07:15, hourly | `backfill_supervisor.sh` tick / report / hourly | **U** | `ohlcv_bar` backfills | Pending 1m backfill (2026-04-29 → 07-17) | STOP |
| 17 | 06:45 / 08:40 | `ca_refresh.sh targeted` / `selfheal` | **U** | `corporate_action`, 1d re-fetch (vendor-applied detection) | CA freshness | STOP (Drishti CA) |
| 18 | 15:40 | `paper_daily.sh` | no (reads bars) | `paper_*` | Stage 7–8 paper | STOP |
| 19 | 06:35 | `stage1_checkpoint.sh` | no | `gate_scan_checkpoint` | n/a | STOP |
| 20 | 09:00, 09:05 | `stage3_snapshot.sh` PRE_SESSION / PRE_OPEN | no (would refuse: Stage 1 stale) | `feature_value` | Stage 3 | STOP |
| 21 | 09:06 | `model_predict.sh` | no | `model_prediction` | **E5 forward test** | STOP (deviation note) |
| 22 | 13:15 | `fundamentals_events.sh` | **U** (the `refresh` step) | `results_event`, `fundamental_snapshot` | Results calendar extraction + fundamentals refresh | STOP. Under D2 = keep news, replace it with an events-only line (6.1) |
| 23 | :10, :40 | `news_ai.sh` | no (Bedrock; no-op while the key is invalid) | `news_ai_enrichment` | AI news tags | with D2 (useful only with a new key) |
| 24 | 10:05 | `e51_data.sh daily` | no | `training_*` | E5.1 datasets | STOP |
| 25 | @reboot, */10 | `dashboard.sh start` (process `app.dashboard` :8600) | **U** (live quote loop) | n/a | Prajna dashboard | STOP (and stop the process) |
| 26 | 08:45, 16:20 | `market_report.sh` | no (dashboard facts + Bedrock + Telegram) | Telegram | Market report | STOP |
| 27 | 16:45 | `signals_snapshot.sh` | no (dashboard) | files | Dashboard plan record | STOP |
| 28 | 18:20, Sat 10:20 | `yahoo_research.sh` | no (Yahoo, files only) | var/research/yahoo | Research consensus | STOP (Drishti does not use Yahoo) |
| 29 | 08:50 | `e6_daily.sh` | no (reads results + bars) | var/research/e6 | **E6 PEAD forward** | STOP (deviation note) |
| 30 | 21:30 | `shadow_daily.sh` | no | `shadow_*` | **P1 shadow forward** | STOP (deviation note) |
| 31 | 22:15 | `shadow_review.sh` | no (Bedrock) | `llm_review` | **P2 reviewer forward** | STOP (deviation note) |
| 32 | 15:55, 22:45 | `lab_daily.sh` | no | `lab_run`, `strategy_day` … | Strategy Lab, Buy List P&L | STOP |
| 33 | 08:30, 09:10 | `lab_brief.sh` | no (Telegram) | `daily_brief` | 09:10 Buy List message | STOP |
| 34 | 16:50 | `lab_eod.sh` | no (Telegram) | n/a | 16:50 "did the picks make money" | STOP |
| 35 | 08:40, 11:00, 13:30, 19:45 | `desk_run.sh` | **U** (dossier quotes) + Bedrock | `desk_*` | Research Desk | STOP |

**Long-running processes today:**
- `app.dashboard` (pid 1044991, :8600, Upstox live quotes);
- the news collector (pid 1648217, until 23:30);
- transient ones, such as a `fundamentals refresh` run.

**Dashboards and messages that go stale:**
- the Prajna dashboard http://127.0.0.1:8600 (every tab: Market, Movers, Sectors, stock pages, Buy List, Lab, Desk, About);
- Telegram: the 09:10 Buy List, 16:50 EOD report, market reports, lab brief, P1/P2/E6 counts, paper reports;
- Stage 1/2 acceptance status (unless D2 keeps Stage 2).

**Upstox is fully freed** when rows 1–3, 6–9, 11, 12, 16, 17, 22, 25 and 35 are off. Rows 13–15 and 23 never call Upstox.
