# Drishti: an agentic chart-reading, prediction and paper-trading system (MVP = swing)

## Context

The user (2026-10-28) wants a new system, separate from Prajna. It should:
- test strategies on historical charts;
- pick stocks daily into a watchlist;
- read charts and candles, and predict up/down;
- learn from reward/penalty with ML;
- record every prediction;
- paper trade.

**Research and specs:** `/home/cis/windows/second-module.md` §1–§9. The strategy spec is §8, the "Drishti Trading Strategies Plan" PDF.

**The user approved the plan with 8 changes (2026-10-28):**
1. **MVP:** model #1 is swing, 1–5 sessions, on **daily bars only**. Model #2 is intraday 15m, after swing's forward test starts. There is no scalp model; ticks are recorded only.
2. **P3:** candle flags, S/R zones, trend, breakout/retest state. The 16 chart-pattern detectors are later, and become features only if they beat the baseline in walk-forward.
3. **CV:** purged and embargoed walk-forward, because triple-barrier labels overlap.
4. **Metrics are reported separately** for (a) all-bar predictions and (b) strategy-signal events. Paper decisions use (b) only.
5. **At most 2–3 pre-registered hypotheses** per forward test. Every other scorecard is labelled EXPLORATORY.
6. **Storage:**
   - ticks kept for 60 days with a disk guard;
   - 1m history in Parquet, not Postgres;
   - the estimated GB is shown before any backfill.
7. **ASM/GSM** comes from a CSV the user downloads by hand into `drishti_data/manual/`. **Holidays** come from the Upstox holidays API.
8. **Before the Prajna cron diff:** can a shared Upstox token keep Prajna's EOD jobs running, and what is lost by stopping P1/P2/E5/E6? This is answered below, and the decision is the user's.

Name and users: `drishti` at `/home/cis/windows/drishti`, single user, local only. Web on 3100, API on 8700.

**Rules that still hold:**
- PAPER ONLY: the order adapter is a no-network stub.
- Never print secrets.
- Stored market data is Upstox only, plus the user's manual ASM/GSM CSV. No NSE scraping.
- Back up and verify the sha256 before any prod DB change.
- No training or looks inside a forward window.
- Never touch Prajna's `frontend/`, Prajna's `backend/app/api/`, or `auto-trade-pro`.

## Answer to change 8: shared token vs stopping Prajna

### What I checked (read-only)

- **One token per account per day.** Prajna caches it at `backend/var/upstox_token.json` (0600). `app/vendor/upstox/auth.py` probes it against `/v2/user/profile` and logs in by TOTP only when the probe fails (06:30 `daily.sh --login refresh`).
- **Two systems logging in separately is the risk.** Whether a second login invalidates the first token is UNVERIFIED. A shared token removes this risk.
- **Rate limits are per API per user:** 50/s, 500/min, 2000/30 min (`app/vendor/upstox/rest.py`). The historical-candle API and the quote API have **separate** budgets.
  - Prajna's heavy job is the 16:05 close job: historical candles for all instruments, at fraction 0.9, ~16:05–19:15 (10-27: done 19:17).
  - Its other REST jobs are short morning ones (06:30–08:40).
- **WebSocket:** Prajna's pre-open recorder takes **both** connections (`--connections 2 --per-connection 2000`), from 08:55 to 09:20. It runs at no other time.

### Shared token: yes, Prajna's EOD jobs can keep running

How it would work:
- **The token has one owner:** Prajna's existing 06:30 login, which is already under the standing permission. Drishti **only reads** the token file, never logs in, and stops with an alert if the token is invalid.
- **No Prajna code changes.** Drishti gets its own copy of the probe code.
- **Rate-limit split by time and API:**
  - Drishti uses the quote API, a separate budget, and in the swing MVP only after 15:35.
  - Drishti uses the historical-candle API only from 22:00 to 05:30, at fraction ≤ 0.5. That is when Prajna is idle (its backfill supervisor ticks are the only exception; they get coordinated through a shared lock file).
- **WebSocket:** Drishti's tick recorder connects at 09:20, after Prajna's recorder has closed. That loses the 09:15–09:20 ticks. This only matters for intraday model #2, and gets decided before that phase.
- **Biggest gain:** Drishti reads Prajna's **split/bonus-adjusted** daily and intraday bars, CA factors, results, news and the calendar, read-only. Otherwise Drishti would have to rebuild ingest, corporate-action adjustment and gates first, which is weeks of work and a source of errors.

### What stopping P1/P2/E5/E6 would lose

| Test | State today | Lost if stopped |
|---|---|---|
| **P1 shadow** (ORB + VWAP, 4 variants) | Forward from 2026-10-27, ~2 sessions; looks at 60 and 250 sessions | The only forward test of ORB/VWAP, which are Drishti S4/S5. It would be a free benchmark for model #2. The pre-registration would be wasted |
| **P2 LLM reviewer** | Forward from 10-27, reads P1 trades. Bedrock key invalid since 10-27 23:10 | The value of the LLM veto, which is the same question as Drishti's news veto |
| **E5 ML ensemble** | 40-session forward (to ~17 Dec). E5-H out-of-time look already NOT_ACCEPTED (10-26) | The least: the answer is very likely negative already. It only gives a negative-control benchmark |
| **E6 PEAD monitor** | Frozen 10-23, one look ~Apr 2027 | The forward evidence for the "results gap hold = plus" screener rule in Drishti |
| **Prajna data pipeline** | Daily 1m/15m/1h for all instruments, CA refresh, Stage 1–3 | Drishti's history would freeze at the stop date. Drishti would need its own ingest + CA adjustment before the MVP |
| **Lab / Buy List / Desk / dashboard live** | Running | All stop being fed |

**My recommendation was the shared token.** **User's provisional decision (2026-10-28): stop Prajna's Upstox jobs.** The final choice is confirmed at the end of P0, after the side-by-side (cost in days and lost tests). Until then nothing in Prajna changes. The plan below assumes "stop"; with "shared", P1b shrinks to index 1d + holidays, and the login/cron items go away.

### What follows from that decision

- **Drishti owns the daily TOTP login** through its own runbook. Its token cache is `drishti/backend/var/upstox_token.json` (0600). This needs the user's **written OK** for one automated login per day, like Prajna's 2026-09-24 permission. Approving this plan is that request.
- **Prajna cron diff** (I prepare it, the user applies it):
  - **Comment out every line that calls Upstox:** `daily.sh --login refresh|morning|weekly|monthly`, `preopen_day.sh --login`, `close_then_backfill.sh`, `ca_refresh.sh` (targeted, selfheal), `backfill_supervisor.sh` (tick/hourly/report), `stage1_checkpoint.sh`, `stage3_snapshot.sh` ×2, `model_predict.sh`, `e6_daily.sh`, `e51_data.sh`, `timing_monitor.sh` ×2, `global_refresh.sh` ×2 (checked in P0 for Upstox use), `maintenance.sh canon`.
  - **The jobs that read the stopped data also go off:** `shadow_daily.sh`, `shadow_review.sh`, `paper_daily.sh`, `lab_daily.sh`, `lab_brief.sh`, `market_report.sh`, `signals_snapshot.sh`.
  - **Kept:** `news_collect`/`news_reconcile`/`news_ai`, `fundamentals_events` (checked for Upstox in P0), `acceptance stage2`, `maintenance.sh status|all` (checked), `yahoo_research`, the dashboard.
  - **The dashboard's live quote loop** calls Upstox. With no Prajna token it fails closed (probe invalid → no quotes). I check that it never tries a login.
  - **Verification:** `crontab -l | grep -E -- '--login|close_then|preopen|backfill_supervisor'` finds nothing active, and no Prajna process calls Upstox for 1 session.
- **Protocol-deviation notes** (committed in Prajna docs, which is allowed): P1, P2, E5 and E6 end at the stop date "by user decision 2026-10-28: Upstox moved to Drishti". No look is taken, and the specs are unchanged. Memory is updated.
- **Prajna's stored history stays** and is read by Drishti read-only up to the stop date (adjusted 1d 2019+, 15m/1h 2022+, 1m 2026-07+, CA, results, calendar). **From the stop date on, Drishti ingests itself** (P1b):
  - 1d bars for its universe (Upstox historical V3) every evening;
  - corporate actions (Upstox, as Prajna's `ca_refresh` does) + its own split/bonus adjustment factors, tested against Prajna's `canon` adjusted bars on the overlap period;
  - index 1d (Nifty 50/500, Bank, sectors, India VIX);
  - the holidays API;
  - news comes from Prajna's still-running news collector (read-only).
- **WebSocket:** both connections are Drishti's, from 09:00 to 15:35 (no 09:20 gap).
- **Rate limits:** the whole budget is Drishti's, with its own client at fraction ≤ 0.8.

## Architecture (MVP)

```
Prajna DB (read-only role drishti_ro): history up to the stop date (adjusted bars, CA, results, calendar) + live news(+AI)
Upstox (Drishti's own login): EOD quotes 15:40, 1d bars + CA nightly, holidays, indices 1d, WS ticks 09:00–15:35
User CSV: drishti_data/manual/asm_gsm_*.csv
        │
 Scout (EOD 19:30) → Chart Reader (daily) → Strategist S1a/S1b/S2/S3 → Predictor (model #1)
        → Risk Manager → Paper Trader (T+1, daily-bar fills) → Judge (grades, scorecards, weekly challenger)
 Narrator (LLM, Hinglish + news veto; off until the Bedrock key works)
        │
 Postgres DB `drishti` (same instance, new database) · Parquet/zstd files on /windows/drishti_data · FastAPI 8700 · Next.js 3100
```

- **Agents** are Python async workers coordinated by `drishti/run/eod.py`. In the swing MVP the agents are a nightly pipeline. A Redis event bus is added for intraday model #2 (P7).
- **Daily bar of day T:**
  - Drishti makes a provisional bar from the 15:40 Upstox quote OHLC.
  - Signals and paper decisions for T+1 are made at 19:30 from the provisional bar.
  - Drishti's own 1d fetch for T runs in the night/next morning (Upstox historical) and is compared. Any OHLC difference > 0.1 % is flagged, and that day is re-graded.
- **Paper fills (swing):**
  - buy-stop / limit at T+1, filled from T+1's daily bar (open gap rule: if it opens beyond the trigger, fill at the open);
  - when the stop and target both fall inside one bar, assume the stop first (conservative);
  - costs are per charge (STT 0.1 % both sides, stamp, exchange, SEBI, GST, DP) plus 3 slippage scenarios.

## Strategies and rules (§8 of second-module.md, frozen in P0 as `drishti-swing-v1`)

- **S1a** momentum breakout on close, and **S1b** breakout retest candle. Both need: RS top 20–25 % (Nifty200 Momentum 30-style normalised 6M+12M score on the turnover-ranked Nifty 500); within 15 % of the 52-week high; close > 50 DMA > 200 DMA; volume ≥ 1.5× the 50-day average.
- **S2** 20 EMA pullback with an engulfing or hammer.
- **S3** support zone + hammer / engulfing / morning star, with volume above average.
- **Filters:** RSI 40–75; Bollinger/Fib zone as plus points; VIX regime (< 20 normal; a spike is +20 % in 5 days or > 25: size halved, no mean reversion); results gate; ASM/GSM exclusion from the CSV. OI/PCR is off in the swing MVP.
- **Risk:**
  - stop = the wider of (candle low − 0.1 ATR) and (entry − 1 ATR);
  - **target = min(entry + 2R, the nearest resistance zone above the entry)**;
  - **RRR ≥ 1.5 is checked on that capped target**, or skip;
  - 1 % risk, ≤ 3 % open risk;
  - 4–6 positions, ≤ 2 per sector;
  - no trade across results.
- **Exits (forward test, model #1) = exactly the label:**
  - capped target / −1R stop / **5-session time-out** at the close of session 5.
  - Trailing-20DMA and 10-session holds are EXPLORATORY variants only. They are recorded, never used for H1–H3.
- **Universe is point-in-time:**
  - on each date, the turnover rank uses only the 20 sessions before it;
  - it includes names that later delisted or were suspended;
  - a name enters only after its listing + 200 sessions (for the 200 DMA).

## ML (model #1)

- **Universe** of samples: (a) all-bar = every eligible stock-day; (b) events = S1a–S3 signal days.
- **Label:** triple-barrier from the T+1 entry: the capped target (min(2R, resistance)) before −1R (stop) within 5 sessions, else time-out at the session-5 close. It is identical to the paper exit rule. The reward is net R after costs, used as the sample weight.
- **Features (PIT):**
  - candle shapes (body/wick in ATR), 26 candle flags;
  - S/R zone distances, trend state (50/200 DMA, HH-HL), breakout/retest state;
  - RSI, ATR %, volume ratio, RS score, 52-week distance;
  - Nifty/VIX/breadth, sector strength;
  - results/news flags.
  - No chart-pattern detectors (change 2).
- **Model:** LightGBM classifier + isotonic calibration. A regression on net R is secondary.
- **CV:** purged + embargoed walk-forward:
  - expanding train, 3-month test blocks;
  - purge training samples whose label window [T+1, T+5] overlaps the test block;
  - embargo 5 sessions after each test block.
  - Data runs 2019-12 → cut-off; the cut-off is set in the pre-registration and sits before the forward start.
- **Reporting:** metrics separately for (a) and (b): hit rate, expectancy (₹, R), Brier/calibration, by strategy/regime/sector, against a random-entry placebo and rules-only. Paper decisions use (b) only.
- **Forward test pre-registration (≤ 3 hypotheses):**
  - H1: confirmed signals (p ≥ threshold) have net expectancy > 0.
  - H2: confirmed signals beat all rule signals (rules-only).
  - H3: calibration on (b): the Brier score beats the base rate.

  Everything else is EXPLORATORY.
- **The look happens only when both are true:** ≥ 40 sessions **and** ≥ 100 closed signal events (set (b), confirmed or not, as each hypothesis needs). If 100 events are not reached at 40 sessions, the look waits; the event count is the only number checked before the look.
- **Test:**
  - H1/H2: a seeded bootstrap 95 % CI (10,000 resamples, block bootstrap by session to respect overlap) on the net expectancy (H1) and on the difference (H2). Pass = the lower bound > 0.
  - H3: the Brier skill score with a bootstrap CI, lower bound > 0.
  - Bonferroni over the 3 hypotheses (98.33 % CI).
  - One look.
- **Learning loop:** nightly grading of every prediction; weekly retrain of a **challenger** that runs in shadow. The forward-test champion stays frozen until its look.

## Data and storage

- **Postgres:** new database `drishti` on `prajna-postgres` and the role `drishti_ro` (SELECT on the needed Prajna tables/views). Do the backup + sha256 first. Tables are append-only: `run`, `watchlist_event`, `signal`, `prediction`, `grade`, `paper_order`, `paper_fill`, `paper_position`, `agent_event`, `model_version`, `index_bar` (Nifty 500 / sectors from Upstox).
- **Files:** `/windows/drishti_data/` (39 GB free):
  - `ticks/YYYY-MM-DD/*.zst`, 60-day retention by a deletion job of its own; the guard stops tick writing below 10 GB free and alerts;
  - `bars_1m/*.parquet`;
  - `manual/`.
- **Tick estimate:** ~1 GB/day for 300 keys in full mode (estimated; measured on day 1 and shown). 60 days is about 60 GB, against 39 GB free on /windows today.
  - **User decision:** the user enlarges the disk, and retention stays at 60 days, full mode.
  - Until then, the guard stops tick writing at < 10 GB free (bars and the rest continue) and sends a Telegram alert.
  - **The tick recorder stays OFF until the disk is enlarged** (user 2026-10-28). The swing MVP does not need it. When it starts, it begins with a 1-day size measurement.

### Survivorship: checked 2026-10-28 (read-only), the "27" figure was wrong as a delisting count

- **The 27 `REMOVED_FROM_MASTER` names** are only the ones that dropped off Upstox's master **since Prajna began tracking on 2026-09-23**. They are not 2019–2026 delistings.
- **In Prajna's 1d history** (3,595 NSE_EQ names with bars), only **16** names have a last bar before 2026-10-01 (2024: 1, 2025: 1, 2026: 14). 1,993 names start after 2019-10 (IPOs + coverage).
- **So the history is close to survivor-only.** Prajna's backfill came from the current instrument master, so names that delisted or were suspended in 2019–2025 are mostly missing. NSE had many hundreds of these over that period (to be counted from a source).
- **P0 task:**
  - (1) the user downloads NSE's delisted/suspended securities list by hand (like the F4 results CSVs) into `drishti_data/manual/`;
  - (2) Drishti probes Upstox historical 1d for a sample of those ISINs (UNVERIFIED whether expired keys still serve data);
  - (3) if available, it backfills them into its own store; if not, the bias is measured on what exists, the backtest universe is limited to the liquid top-500 (where delistings are rarer), and every backtest scorecard carries a "survivorship-biased" label.
  - The forward test is unaffected, since it is PIT by construction.
- **1m Parquet backfill** (for model #2; not needed for the MVP):
  - first compute and show rows/GB (≈ 0.45 M bars per stock for 2022→now; ~2–4 GB per 500 stocks, estimated) and the request count;
  - then run at night, at fraction ≤ 0.5, resumable.

## Code layout (new repo `/home/cis/windows/drishti`)

```
backend/drishti/{core(config, clock, costs, db, calendar), broker/upstox(token_reader, rest, ws, proto),
  broker/paper(stub), data(prajna_ro, eod_quotes, indices, asm_gsm, holidays),
  features/(candles, levels, trend, context), strategies/(s1a, s1b, s2, s3), risk/, paper/,
  ml/(labels, cv_purged, train, calibrate, registry, predict), judge/, narrator/, agents/, run/(eod, nightly, weekly),
  api/(FastAPI)} + migrations(alembic) + tests/
web/ (Next.js + lightweight-charts)   ops/ (runbooks + systemd user units)   docs/ (DESIGN, PREREG_M1, RUNBOOK)
```

**Copied, not imported, from Prajna (with provenance):**
- the token probe idea (`app/vendor/upstox/auth.py`);
- the rate-limited REST client (`app/vendor/upstox/rest.py`);
- V3 protobuf + parser (`app/vendor/upstox/proto/`, `app/parsers/upstox_feed_v3.py`);
- the WS liveness design (`app/sources/upstox_preopen_ws.py`);
- the candle checks (`app/shadow/patterns.py`, `app/dashboard/analysis.py`);
- PIT bar access (`app/canon/pit.bars_adjusted`): Drishti queries the same views read-only.

## Phases

| Phase | Deliverable | Est. |
|---|---|---|
| **P0** | Frozen specs: `drishti-swing-v1` rules, costs, label = exits, features, CV, PIT universe; PREREG_M1 draft (H1–H3, ≥ 100 events, bootstrap CI); survivorship probe. **Before P0 ends: a side-by-side for the user of stopping Prajna vs the shared token (days of extra work, tests lost, risks), and the user confirms the final choice.** Only then the Prajna cron diff + protocol-deviation notes. Repo skeleton | 1–2 d |
| **P1a** | DB `drishti` + `drishti_ro` (after backup), Drishti login runbook + token cache, holidays, EOD quotes, ASM/GSM CSV loader | 2 d |
| **P1b** | Own ingest from the stop date: nightly 1d bars, CA + adjustment factors (checked against Prajna canon on the overlap), index 1d (Nifty 50/500, Bank, sectors, VIX); delisted-name backfill if the probe succeeds. Tick recorder code is ready but **OFF** until the disk is enlarged | 4–5 d |
| **P2** | Scout (screener B, 100–300 names), watchlist log, FastAPI, Next.js daily chart + overlays (S/R, EMAs, candle flags, signals) | 4–5 d |
| **P3** | Candle flags, S/R zones, trend, breakout/retest state; S1a/S1b/S2/S3; daily replay/backtest 2019→cut-off with costs; scorecards (a)/(b) vs placebo, all EXPLORATORY | 5–6 d |
| **P4** | Labels, purged/embargoed CV, LightGBM + calibration, registry; predictions logged + auto-graded | 4–5 d |
| **P5** | Risk Manager, Paper Trader (T+1 daily fills), Narrator/news veto (if a key exists), Telegram | 3–4 d |
| **P6** | Judge: nightly grades, weekly challenger in shadow, scorecard UI. **Freeze model #1 and start the forward test** (≥ 40 sessions, one look) | 3 d |
| **P7** | Intraday model #2 (15m): 1m Parquet backfill (after the GB estimate), live WS candles, S4/S5, Redis bus, its own pre-registration | 6–8 d |
| **Later** | 16 chart-pattern detectors, as features only if they beat the baseline in walk-forward | — |

## Verification

- **Unit tests:**
  - candle flags on the guide's example shapes;
  - S/R zones and breakout/retest on synthetic series;
  - triple-barrier labels;
  - purged CV (no train label window overlaps a test block; embargo respected);
  - PIT features (shuffling the future does not change features);
  - costs against a worked delivery contract note;
  - risk rules (stop max-rule, RRR skip, sector cap);
  - T+1 fills (gap, stop first);
  - append-only triggers;
  - the order stub with the network blocked;
  - the token reader never calls the login endpoints.
- **Replay parity:** the nightly pipeline on day T gives the same signals as a replay of day T.
- **Data checks:** the provisional vs official 1d diff report; tick files against the disk guard; real GB/day measured and shown.
- **ML:** walk-forward only, (a) and (b) reported separately, placebo and rules-only baselines, calibration plot; the pre-registration is committed before the forward start.
- **Prajna:**
  - the backup sha256 is verified before the `drishti` DB is created;
  - after the user's cron change, `crontab -l` has no active Upstox line, and for one session no Prajna process calls Upstox or logs in (cron.log, `ps`);
  - Prajna's tables are untouched apart from the protocol-deviation docs;
  - Drishti's CA-adjusted 1d matches Prajna's canon on the overlap period (median |diff| < 0.01 %).

## Needed from the user

1. **A written OK for Drishti's one automated Upstox TOTP login per day** (approving this plan counts).
2. Apply the Prajna cron diff (prepared in P0) **on the day Drishti's login goes live**, so no day is left without data.
3. Enlarge the disk for 60-day ticks.
4. The ASM/GSM CSV in `drishti_data/manual/` (format noted in P1a).
5. Install the systemd user units (prepared in P1).
6. Mask suspend.
7. A new Bedrock key (optional, for the Narrator).
