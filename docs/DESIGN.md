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

**FINAL DECISION (user, P0, 2026-10-28): A, the shared token.**
- Prajna keeps its 06:30 login and all of its jobs. Drishti only reads `prajna/backend/var/upstox_token.json` and never logs in.
- B (stopping Prajna's Upstox jobs) is rejected:
  - results/news ingest also run on Upstox;
  - 6–8 extra days of work;
  - risk in the corporate-action adjustment;
  - P1/P2/E6 would be lost.
- See `P0_PRAJNA_DECISION.md`.

### What follows (A)

- **Token:** Drishti's `token_reader` opens the file read-only and probes `/v2/user/profile`. If the token is invalid, Drishti stops with an alert and **never** calls the login endpoints (tested).
- **Rate limits:** Drishti uses the quote API after 15:35 (separate budget). It uses the historical-candle API only 22:00–05:30 at fraction ≤ 0.5, under a shared lock file with Prajna's backfill supervisor.
- **WebSocket:** Prajna's pre-open recorder keeps both connections 08:55–09:20. Drishti ticks (OFF until the disk is enlarged) would start at 09:20; this is decided again before intraday model #2.
- **Data:** Drishti reads Prajna's adjusted 1d/15m/1h/1m bars, CA factors, calendar, results and news read-only (role `drishti_ro`). Drishti ingests only what Prajna lacks: the Nifty 500 and sector indices (1d), and the Upstox holidays API.
- **Prajna** is unchanged: no cron diff and no protocol-deviation notes.

## Architecture (MVP)

```
Prajna DB (read-only role drishti_ro): adjusted bars, CA, results, calendar, news(+AI), kept current by Prajna
Upstox (shared token, read-only): EOD quotes 15:40, holidays, indices 1d (Nifty 500 + sectors); WS ticks from 09:20 (OFF until the disk grows)
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
  - Prajna's official 1d bar for T lands at T+1 07:00 and is compared. Any OHLC difference > 0.1 % is flagged, and that day is re-graded.
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
| **P0** | Frozen specs + PREREG_M1; survivorship probe; token decision = **A (shared)** | 1–2 d |
| **P1** | DB `drishti` + `drishti_ro` (after backup), read-only token reader, holidays API, EOD quotes, Nifty 500/sector index 1d, ASM/GSM + delisted CSV loaders, survivorship probe/backfill, contract-note cost check. Tick recorder code ready but **OFF** until the disk is enlarged | 3–4 d |
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

1. Files in `/windows/drishti_data/manual/`: the NSE delisted + suspended lists, the ASM/GSM list, and one Upstox delivery contract note (buy + sell + DP).
2. Enlarge the disk for 60-day ticks (intraday phase).
3. Install the systemd user units (prepared in P1).
4. Mask suspend.
5. A new Bedrock key (optional, for the Narrator).

## Momentum portfolio automation CLOSED (user, 2026-11-03)

**Decision:** no momentum forward test, and no automated momentum book. C1a stays a research record only.

**Why** (Track B after-tax check, `research/track-b/sprint/after_tax_check/after_tax_check.json`, merged helper `drishti/judge/dsr.py` @ `1470d85`; all survivorship-biased and EXPLORATORY):
- **After tax it ties with or loses to a Nifty200 Momentum 30 index fund.**
  - Holdout (21 months; the window already used once): C1a after-tax CAGR **−5.1 %** vs the fund **−6.2 %**, a tie within noise. C1a's max drawdown was deeper: **−20.3 %** vs **−15.9 %**.
  - The C1a book pays short-term capital gains every rebalance; the fund defers tax.
- **No edge survives deflation.** The Sharpe of the *excess* return over the index, deflated for N≈30 trials (TRIAL_LEDGER):
  - development window: DSR **0.27** (0.28 at N=27);
  - all months: DSR **0.23** (0.24 at N=27).

  Both are far below the 0.95 bar. The raw-return DSR (0.71–0.90) only measures the momentum *factor* itself, and an index fund gives that factor more cheaply.
- So the earlier holdout "PASS by +1.6 pp" (pre-tax, vs the price index) does not hold after tax and deflation.

**What stays:** `judge/dsr.py` as an EXPLORATORY helper; any future candidate must clear DSR/SPA at N≈30 (Hammer section above). This decision does not touch the decision-support scanner (user tool, not an edge claim).

## Live Radar (user, 2026-11-04)

**Label everywhere: "Intraday radar - decision support, not validated edge".** Part of the decision-support scanner. It does not change Scout `scout-b-v1`, the EOD watchlist, drishti-swing-v1 or any test.

- **What it does (`drishti/scanner/radar.py`, `radar-v1`):** every 15 min, inside `scan_intraday`, it re-runs Scout's criteria with the PARAMS unchanged:
  - price band;
  - trend: close > SMA50 > SMA200, with SMA200 rising;
  - RS in the top quantile;
  - beats Nifty 500 over 3 months;
  - close ≥ 0.85 × the 52-week high;
  - ATR 2–6 %.
- **Universe:** the liquid/F&O set, fixed for the day from T-1. It is the top 500 by 20-session traded value plus every eligible F&O stock (11-04: 500; the 213 eligible F&O names were all inside the top 500).
- **Point in time:**
  - History is the adjusted official 1d up to T-1.
  - Today is a **partial daily bar** built only from data known when the last 15m bar closed. It uses the recorder's closed 1m bars, plus the day OHLC at the key's first tick if that tick came before the bar's close.
  - Regime and price bands are T-1's.
  - The RS cut is the 0.75 quantile over the top-500 set (Scout's spec universe).
  - Parity with Scout's `screen()` is tested on the same bars.
- **Output:**
  - A stock that now passes, is not on the latest EOD watchlist and is not on today's radar yet gets one `radar_event` row (migration 0009, append-only). The row has the timestamp (the bar used), the trigger reason (the criteria that turned true since T-1) and the metrics.
  - From then on `scan_intraday` watches it for setups. Its alerts carry `context.source = "radar"` plus the label and the caveat.
  - New names are sent to Telegram, or logged.
- **Rate budget:** no REST call. The live data comes from the recorder's WebSocket, which from 11-05 also subscribes the radar universe (Parquet 1m only; no live chart JSON for those keys). Ticks grow about 10× (≈0.3 GB/day zstd); the 10 GB disk guard still applies.
- **API:** `GET /api/radar?date=` gives label, caveat, criteria and rows (with `alerts_since`). The UI tab is a Track C task.
- **Caveat (DEVIATIONS D5):** setups on radar names use unadjusted 15m bars until the 15m path is adjusted (the re-point); the 11-20 switch alone does not fix it.
- **Starts:** 11-04, with live bars only for the watchlist, so nothing can be added. The first full day is 11-05, after the recorder's 09:21 start with the new key list.

## Decision-support scanner + journal (user, 2026-11-03)

**Label everywhere: "Decision support - detections are NOT validated edges. Your call. Paper only."**

A tool for the user's own discretionary paper trading. It is **not** a strategy, not a forward test and not a v2 candidate. It changes nothing in `drishti-swing-v1`, Scout `scout-b-v1`, the closed research families, P6 or the switch prep.

- **Detectors (`scan-v1`, `drishti/scanner/detect.py`)** reuse `drishti.features` only (no new entry research): `HAMMER_CONFIRM` (hammer on t-1, close of t above its high), `BREAKOUT_52W` (close above the prior 250-bar high; on 15m that is the 250-bar high, about 10 sessions), `SUPPORT_REVERSAL` (S3-style), `EMA20_PULLBACK` (S2-style). The hammer family was CLOSED as an edge on 2026-11-02; here it is only a prompt.
- **Levels:** entry = buy-stop 0.05 % above bar t's high; stop = the wider of (structure low - 0.1 ATR) and (entry - 1 ATR); R1/R2 = the bottoms of the next two resistance zones above the entry; RR to R1; qty at `DRISHTI_SCAN_RISK_PCT` (1 %) of `DRISHTI_SCAN_CAPITAL` (10 lakh); round-trip cost in R. RR is shown, never filtered: a small RR is information for the user.
- **Jobs (`drishti/scanner/run.py`):** `scan_eod` 19:45 on the eligible top 500 (Scout's bars, provisional T); `scan_intraday` every 15 min on the watchlist, last closed 15m bar (Prajna's 15m history until the switch + the recorder's 1m); `scan_grade` 09:45 (after Prajna's official 1d of T-1; moves after `daily1d` at the switch).
- **Grading (`drishti/scanner/grade.py`):** triple barrier with the frozen `exit_path` (stop first, gap rules): R1 (or entry + 2R when no resistance) / stop / time-out (1d: 5 sessions; 15m: 25 bars). Three bases for every alert: `alert` (fills only if the next bar trades through the entry), `took` (the user's actual entry) and `placebo` (seeded random entry in the next bar's range, same stop/target/time-out). Net of costs, in R.
- **Tables (migration 0008):** `scan_alert`, `journal_event`, `journal_outcome` append-only; `journal_entry` editable by the user through the API (DEVIATIONS D4).
- **API (`drishti/api/scan_api.py`):** `GET /api/scan/alerts`, `GET /api/scan/alert/{id}`, `POST /api/journal/{alert_id}` (the only write endpoint), `GET /api/journal/stats` (took vs took_base vs all alerts vs placebo, by detector). The blind rule covers v1 and the forward tests, not this journal: the user asked to see their own results.
- **Telegram:** the day's EOD list and each new intraday alert, when `DRISHTI_TELEGRAM_BOT_TOKEN` and `DRISHTI_TELEGRAM_CHAT_ID` are set in `.env`; otherwise the message is logged.

## STATUS (user, 2026-10-29): drishti-swing-v1 is NOT taken to the forward test

**P6 (freeze of model #1) and the forward test are ON HOLD** until a research sprint decides on a documented `drishti-swing-v2`.

**What is unchanged:**
- `drishti-swing-v1` stays frozen as written. It is not edited, and it is not run forward.
- P1c, the switch to Option B on 2026-11-20 (`SWITCH_TO_B.md`), the live view, the tick recorder, the chart UI and Scout keep going as planned.

**Evidence** (P3/P4 draft by Track B, merged as code at `0c0f327`; the outputs stay as files in `/windows/drishti_data/research/track-b/`). All of it is **SURVIVORSHIP-BIASED, EXPLORATORY**. The replay runs 2020-09-07 → cut-off C = 2026-10-21 on official bars, 10 bps/side base slippage.

- **Rules vs placebo** (set (b), 5,836 events, 2,381 closed):
  - rules-only net expectancy **−0.100 R**;
  - seeded random-entry placebo **−0.090 R**: the rules show no edge over random entry;
  - by strategy: S1a −0.046, S1b −0.326, S2 −0.101, S3 −0.114 R;
  - it is negative at 5 bps (−0.077) and 20 bps (−0.147), and negative in every year except 2020 (+0.107).
- **Set (a), all-bar:** −0.129 R (spec_gate ON −0.125, OFF −0.148).
- **Close-confirm variant (EXPLORATORY):** worse, −0.176 R versus −0.100 for the frozen buy-stop.
- **Model #1** (LightGBM + isotonic, purged/embargoed walk-forward, 22 folds):
  - (b) calibrated out-of-fold **AUC 0.505, BSS +0.003**;
  - (a) calibrated AUC 0.494, BSS −0.004;
  - no p* gives a positive mean net R.
- **Pre-P6 power check: FLAG_CURRENT_REGIME_OVER_120.**
  - spec_gate is OFF today (Nifty 500 21,734.9 < SMA200 22,808.7), so under the current regime the look is **never** reached (SC4 blocks entries).
  - Historical ON share is 79.2 %, which would take ~69–101 sessions.
  - 2026 so far has an ON share of 43.9 %.

**Next:**
- A research sprint, the user's call on scope, ends in either a documented v2 (its own spec, pre-registration and freeze) or no forward test.
- The forward-start date below is void until then.

## Hammer / candle-entry family CLOSED (user confirmed 2026-11-02)

**What was tried** (Track B, merged at `d4d1251`; outputs in `/windows/drishti_data/research/track-b/sprint/`; all SURVIVORSHIP-BIASED, EXPLORATORY):
- **RJ1–RJ6:** the Rajat hammer + volume setup on daily and 15m bars.
- **RJ7–RJ9:** a 15m minimum reward:risk test, at 1:1 and 1:1.5.
- **SW-a…SW-d:** exits on the hammer entry:
  - level-aware partial exit + breakeven trail;
  - an obstruction filter + 2R;
  - an ATR triple barrier;
  - stop + trail after +1R.

**Result:** every variant failed the pass rule against same-exit placebos. SW-c was +0.08 R vs placebo on only 38 trades; that meets the +0.05 margin numerically but is not significant.

**Decision:** the family is closed, together with v1. It is not re-opened under a new name.

**Trial count and gate:** `TRIAL_LEDGER.md` (research folder) counts **27 rule sets, or 30 counting the model grid**. **Any future candidate must clear the deflated Sharpe ratio and the SPA test at N ≈ 30** (plus every later trial) against a same-exit placebo before any forward test.

**Also recorded in the ledger:**
- C1a (monthly momentum book) had a one-time holdout run by user override on 2026-11-02: PASS vs the Nifty200 Momentum 30 price index after costs, by +1.6 pp.
- **This is the holdout window that had already been used once** (C3b). Treat it as supporting evidence only, not as a clean out-of-sample test. A C1a forward test, if wanted, needs the user's decision and its own pre-registration. **Superseded 2026-11-03: momentum automation CLOSED, no forward test (section "Momentum portfolio automation CLOSED").**
- Descriptive NSE level base rates (swing high, previous-day high, opening-range high, high of day) are in `research/track-b/sprint/baserates/`.

## Research sprint result (user, 2026-10-29): no v2 frozen, forward test stays ON HOLD

**The sprint** is Track B's, merged as code at `7aaa614`. Its outputs stay as files in `/windows/drishti_data/research/track-b/sprint/`, and the variant log `VARIANTS.md` was written before each run. All of it is **SURVIVORSHIP-BIASED, EXPLORATORY**.
- Development window: 2019-09 → 2024-12-31.
- Holdout: 2025-01-01 → C (2026-10-21), touched once by the single finalist.
- Base slippage 10 bps/side.

**Outcome:**
- **No candidate's entry beat random entry.** Every rule result is compared with a seeded placebo that uses the same exits and holding period.
- **No v2 is frozen. The forward test stays ON HOLD.**

**Step 1: v1 diagnosis.** Same v1 entries, only the exit changes (V01–V05: time-out 5/10/20/60, trailing SMA20). The rules are at or below placebo in every variant: net R @10 bps rules / placebo:

| Variant | Rules | Placebo |
|---|---|---|
| V01 | −0.072 | −0.054 |
| V02 | −0.073 | −0.040 |
| V03 | −0.056 | −0.030 |
| V04 | −0.021 | +0.009 |
| V05 | −0.056 | −0.027 |

Longer holds help both sides equally, so the difference comes from the exits and the market, not from the entry.

**Step 2: candidates.** The user approved them, and each was logged before its run.

| Candidate | What | Development result | Finalist? |
|---|---|---|---|
| C1a–C1c | Monthly momentum book | Fails the finalist rule (excess vs Nifty 500 price + equal-weight PIT benchmarks) | no |
| C2a | S1a + trailing SMA20 | rules −0.050 vs placebo +0.167 R | no |
| C2b | S1a + trailing SMA50 | rules +0.324 vs placebo +0.465 R | no |
| C3a | PEAD, 20 sessions | rules +0.217 vs placebo +0.206 R (+0.012) | no |
| **C3b** | **PEAD, 60 sessions** | rules +0.650 vs placebo +0.537 R (**+0.113**), net > 0 in 4/5 years | **the only finalist** |

**Step 3: holdout. C3b failed.** Rules +0.065 R vs placebo +0.267 R, a margin of **−0.202 R** (2025: +0.016 vs +0.147; 2026: +0.128 vs +0.422). The development edge did not carry over.

**Consequences:**
- `drishti-swing-v1` stays frozen and is not taken forward.
- There is no v2 spec, pre-registration or freeze. P6 and the forward start stay ON HOLD.
- Unchanged and still running:
  - P1c and the 2026-11-20 switch;
  - the live view and ticks;
  - the chart UI;
  - Scout (context only).
- Any next research round needs a new written scope from the user, and it uses fresh variants within a new budget. **The holdout window 2025-01-01 → 2026-10-21 has now been used once**, so a later candidate needs a new untouched window (for example the forward data), not this one again.

## Option B (user, 2026-10-29): new phase P1c before P3

Prajna stops, and Drishti owns Upstox; see `SWITCH_TO_B.md`. P1c covers:
- Drishti's TOTP login;
- the instrument master;
- the nightly official 1d;
- corporate actions + factor derivation + vendor-applied detection, checked against Prajna on a ≥ 5-session overlap;
- the results calendar and news per D2;
- a one-time copy of Prajna's history (D1);
- re-pointing every Drishti read away from Prajna's live DB;
- the switch itself.

**Estimate:** ≈ 8–10 working days. P3 onward shift by that much.

The user's decisions D1–D6 are in `SWITCH_TO_B.md`: copy plus an archive, no NSE fetching, top-500 intraday, separate Upstox app. The switch is on **Friday 2026-11-20**, after Prajna's close job. P1/P2/E5/E6 end on the switch day with protocol-deviation notes and no look.

### Revised timeline (2026-10-29)

| Phase | Dates (dev days) | Notes |
|---|---|---|
| P0, P1 | done (2026-10-28/29) | n/a |
| P2 part 1 | done (2026-10-29) | Scout, API, web |
| **P1c** | 10-29 → 11-12 | Option B ingest, copy, archive |
| Shadow overlap | sessions 11-12, 13, 16, 17, 18, (19) | Rest of P2 in parallel |
| **Switch** | Fri 11-20 evening | Drishti-only from Mon 11-23 |
| P3 | **done early (draft, Track B, 2026-10-29)** | Features, strategies, replay, scorecards, power check: no edge, flag set (STATUS above) |
| P4 | **done early (draft, Track B, 2026-10-29)** | Labels, purged CV, LightGBM, calibration: AUC ~0.50 |
| P5 | 12-08 → 12-11 | Risk Manager (incl. SC6 BAND_VETO), paper trader, SC3 recheck, Narrator, Telegram |
| P6 | **ON HOLD** | No freeze: v1 not taken forward; research sprint 2026-10-29 froze no v2 (C3b failed its holdout) |
| **Forward test start** | **ON HOLD (was Thu 2026-12-17)**: v1 is not taken forward | Earliest look ≥ 40 sessions ≈ mid-Feb 2027; 120-session cap ≈ early June 2027 (2027 holidays to confirm) |

The forward-start date also depends on the pre-P6 power check. If the projection under the current regime is beyond 120 sessions, the user decides between "start as frozen" and a documented v2 before the freeze.

## Pre-P6 power check (user, 2026-10-29)

The P3 replay (2019 → cut-off, official bars, SURVIVORSHIP-BIASED, EXPLORATORY) must report the following **before P6**:

1. **Signal events per session**, split by `spec_gate` ON and OFF (SC4: with the gate OFF there are no entries, so the OFF count is 0 by construction). Also the share of sessions in each regime, and the events per ON session by strategy (S1a/S1b/S2/S3).
2. **Projected sessions to the look:** how many sessions it takes to reach ≥ 100 closed and ≥ 30 confirmed events (PREREG_M1), under three regime mixes:
   - the current regime;
   - the historical ON share;
   - a range between them.

   The 120-session cap is shown against each projection.
3. **A flag** if the projection under the current regime is beyond 120 sessions. The user then decides, **before the freeze**, between starting the forward test as frozen and a documented `drishti-swing-v2`.

These are replay numbers on history, not forward results, so they do not break the blind rule. No forward-window data is used.
