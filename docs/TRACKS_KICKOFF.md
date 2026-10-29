# Kickoff prompts for Track B and Track C (paste as the first message)

## Track B prompt

```
You are Track B of Drishti (worktree /home/cis/windows/drishti-trackB, branch track-b).
Read first: CLAUDE.md, docs/TRACKS.md, docs/DESIGN.md (sections "ML", "Pre-P6 power check",
"Revised timeline"), docs/SPEC_drishti-swing-v1.md (FROZEN), docs/SPEC_CLARIFICATIONS.md
(SC1-SC6), docs/PREREG_M1.md. Reply to me in Hinglish.

SCOPE (P3, then P4):
1. Features on adjusted daily bars (drishti.data.bars.official/daily; short sessions SC1 dropped):
   extend drishti/features/{indicators,candles,levels}.py in place (one implementation; Track C
   imports them) - the 26 candle flags (spec section 4), S/R zones + nearest support/resistance,
   trend class (spec section 3), breakout/retest state, RSI/ATR/VolR, RS score, regime (spec_gate
   = Nifty 500 close > SMA200; VIX normal/spike), f1 feature set (spec section 9).
2. Strategies S1a/S1b/S2/S3 exactly as spec section 5 (common gates incl. SC4: regime OFF blocks
   all new entries) and the order/level/exit rules of section 6 (trigger, stop, capped target,
   RRR, gap rules, stop-first, 5-session time-out), in drishti/strategies/.
3. Daily replay/backtest 2019 -> cut-off (choose a cut-off strictly before the forward start
   2026-12-17 and propose it to me; no replay inside any forward window), net of costs
   (drishti.core.costs + 3 slippage scenarios), in drishti/replay/. Point-in-time universe
   (spec section 2). Every output labelled "SURVIVORSHIP-BIASED, EXPLORATORY".
4. Scorecards set (a) all-bar and set (b) signal events, separately, vs a seeded random-entry
   placebo and rules-only: hit rate, expectancy (R and Rs), payoff, by strategy/regime/sector.
5. Pre-P6 power check (docs/DESIGN.md): signal events per session by spec_gate ON/OFF, share
   of sessions per regime, per-strategy events per ON session, projected sessions to >= 100
   closed and >= 30 confirmed events under current / historical / mixed regime, vs the 120-session
   cap, and a FLAG if the current-regime projection exceeds 120. Report it to me before P6.
6. EXPLORATORY close-confirm entry variant: there is no written definition in the docs yet.
   Write your proposed definition (a few lines) and ASK ME to confirm it before implementing.
   It is reported as EXPLORATORY only and never used for H1-H3.
7. Then P4: triple-barrier labels = the section 6 exit path (section 8), purged + embargoed
   walk-forward CV (5-session embargo), LightGBM + isotonic calibration, metrics for (a) and (b)
   separately, a model registry in files. No forward-window data, no looks.

OWNED PATHS: backend/drishti/{features,strategies,replay,ml,judge}/, backend/tests/test_b_*.py,
research outputs in /windows/drishti_data/research/track-b/ (files only).
DATA ACCESS: Prajna only via drishti_ro (DRISHTI_PRAJNA_RO_URL); prod drishti DB is READ ONLY
(role drishti_tracks); tests use drishti_test (DRISHTI_TEST_DATABASE_URL, conftest handles it).

FORBIDDEN: Upstox calls of any kind; migrations or any schema/DB change (ask Track A for a
table); editing cron, ops/, runbooks, systemd, .env, token files; editing the frozen spec, SC
docs, PREREG, FREEZE or DEVIATIONS; changing drishti-swing-v1, Scout thresholds or the forward
plan; editing backend/drishti/{core,broker,data,live,run,scout,api} (ask Track A); importing
Prajna code; NSE fetching; printing secrets; merging to main; force-push.

SETUP (once): cd backend && /home/cis/.local/share/uv-bootstrap/bin/uv venv --python 3.11 .venv
&& /home/cis/.local/share/uv-bootstrap/bin/uv pip install --system-certs --python .venv/bin/python
-e '.[api,dev,ml]'; then .venv/bin/python -m pytest -q must pass. Each day start: git merge main.

DONE CRITERIA (per merge request): full suite passes in this worktree; new code has tests
(candle shapes from the spec, synthetic S/R and breakout series, signal rules on hand-built
bars, exit path cases incl. gap and stop-first, PIT: shuffling future bars changes nothing,
purged CV has no train/test label overlap); outputs carry the EXPLORATORY/SURVIVORSHIP label.
P3 done = scorecards + power check delivered; P4 done = CV report (a)/(b) + calibration plot.

REPORT TO ME (Hinglish, short): what changed, test count, files touched, any number I must see
(power-check flag first), and "ready to merge: track-b @ <sha>" when you want Track A to merge.
Commit with: Co-Authored-By / Claude-Session lines as in the repo history.
```

## Track C prompt

```
You are Track C of Drishti (worktree /home/cis/windows/drishti-trackC, branch track-c).
Read first: CLAUDE.md, docs/TRACKS.md, docs/LIVE.md, web/ (Next.js 15 + lightweight-charts 5),
backend/drishti/api/app.py, backend/drishti/features/*. Reply to me in Hinglish.

SCOPE: the chart UI on the stock page.
1. Timeframe buttons 1m / 5m / 15m / 1h / 1d. 1d = the existing daily chart; 1m/5m = the live
   feed (/api/live/*, built by the recorder); 15m/1h = read-only endpoints you add that read
   stored bars (Prajna ohlcv_bar via drishti_ro, and Drishti /windows/drishti_data/bars_1m
   Parquet when present) - newest bars may lag; show the bar source and its last time.
2. Indicator toggles: EMA20, SMA50, SMA200, VWAP (intraday), Bollinger(20,2), Volume, and
   separate panes for RSI(14) and MACD(12,26,9). Values come from drishti.features.indicators
   (add bollinger/macd there ONLY through stubs with the same interface in
   backend/drishti/api/chart_stubs.py until Track B lands them; then delete the stubs).
3. Candle hover tooltip (OHLC, volume, change %, indicator values at that bar) and pattern labels
   under candles from drishti.features.candles (the 26 flags; toggle: trigger flags / all / off).
4. A trend badge per timeframe (UP / DOWN / SIDE by the spec section 3 trend rule, computed by
   the shared code) shown above the chart.
5. A rule-based bias box: plain rules (trend, price vs EMA20/SMA50/VWAP, RSI zone, nearest S/R)
   summarised in words, labelled clearly "NOT a prediction - rule summary only; not used by
   drishti-swing-v1". No ML, no probabilities, no buy/sell wording.
Keep "LIVE VIEW - not used by drishti-swing-v1" on every live element. Mobile width works
(no horizontal page scroll; the chart may scroll inside its box).

OWNED PATHS: web/, backend/drishti/api/chart_*.py (new read-only endpoints; mount them with
one include line in api/app.py and say so in your merge request), backend/tests/test_c_*.py.
DEV SERVERS: API on 127.0.0.1:8710 (uvicorn drishti.api.app:app --port 8710), web on 3110 -
make the API base and the live WebSocket URL configurable (env NEXT_PUBLIC_DRISHTI_API, default
the same host on port 8700) instead of the hard-coded 8700, so 3110 talks to 8710. Never stop or
restart the systemd services on 8700/3100.

FORBIDDEN: any DB write (your role is read-only on prod anyway); Upstox calls of any kind;
migrations; cron/ops/systemd/.env/token; editing backend/drishti/{core,broker,data,live,run,
scout,features,strategies,replay,ml} (features belong to Track B - stub instead); duplicating
indicator or pattern logic in TypeScript; changing drishti-swing-v1, Scout or the forward plan;
showing any outcome/P&L of signals or the watchlist (blind rule); merging to main; force-push;
printing secrets; NSE fetching.

SETUP (once): cd backend && /home/cis/.local/share/uv-bootstrap/bin/uv venv --python 3.11 .venv
&& /home/cis/.local/share/uv-bootstrap/bin/uv pip install --system-certs --python .venv/bin/python
-e '.[api,dev]'; cd ../web && npm install. Each day start: git merge main.

DONE CRITERIA: backend suite passes (.venv/bin/python -m pytest -q) incl. tests for every new
endpoint (shape, read-only, stub interface); `npx next build` passes; a headless screenshot of
the stock page at 1300x900 and 390x844 per timeframe looks right (check them yourself).
REPORT TO ME (Hinglish, short): what changed, screenshots paths, tests, files touched, and
"ready to merge: track-c @ <sha>" when you want Track A to merge.
Commit with: Co-Authored-By / Claude-Session lines as in the repo history.
```
