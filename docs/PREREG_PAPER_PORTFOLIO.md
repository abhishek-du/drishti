# Pre-registration: Drishti paper portfolio (`pf-v1`)

**Status: FROZEN 2026-11-05 by user decision.** Changing any value below after the forward start counts as a **new trial**: it goes in `TRIAL_LEDGER.md` and raises the N used for the DSR/SPA test.

**Sources:** the spec "Drishti Paper Portfolio: Build Spec" (2026-11-04) and the review in `PAPER_PORTFOLIO_REVIEW.md`.

**PAPER ONLY. Deterministic and seeded. No LLM makes a trade, sizing or veto decision.** The portfolio is an executor and a measurer, not a predictor: detections are not validated edges.

## 1. Capital and books

- **Main portfolio:** ₹10,00,000 starting cash. It is shared by two sub-books, `swing` (CNC, from 1d scanner alerts) and `intraday` (MIS, from 15m alerts). Equity = cash + the mark-to-market of open positions, net of every cost.
- **Intraday book:** runs in **observe mode** (decisions logged, no paper orders) until the 15m odds engine exists. Its graduation is a separate, recorded decision.
- **Shadow books:**
  - **placebo** (the comparison book): separate ₹10,00,000;
  - **benchmark**: separate ₹10,00,000;
  - **observational pair** (research only, see §5).

## 2. Entry gate (a candidate is taken only if ALL pass, in this order)

1. **Regime:** Nifty 500 above its SMA200 (`watchlist_run.regime.status = ON` and `spec_gate = ON`). OFF means no new entries; open positions still manage their exits.
2. **Odds gate (Time Machine, `odds_latest`, basis `alert`, `odds-1d-v1`):**

   `gate_pass = mc_avg_r_net ≥ +0.05 AND replay_n ≥ 30 AND NOT thin_sample`

   - `mc_avg_r_net` is the average net R **over all Monte Carlo paths**. A path that hits neither barrier is valued at the horizon close; stop exits are booked at ≥ −1.2 R; it is net of `core.costs`.
   - **target-first % is display only**, never part of the gate.
3. **Risk gate (§3).**
4. **Results/news veto, deterministic:** skip when a results / board meeting for the stock falls inside the holding window. The sources are Prajna `results_event` (`INTIMATION`, `knowable_at` < decision time) and Drishti's board-meetings CSV (`manual_file` kind BOARD_MEETINGS; STALE > 4 days = treated as unknown → skip). **No LLM gate.**

Every candidate gets a `pf_decision` row (take/skip, each gate's result, the reason code).

## 3. Sizing and risk caps

| Rule | Value |
|---|---|
| Risk per trade | 1 % of current equity; qty = floor(risk ₹ / (entry − stop)), whole shares |
| Max position | 20 % of equity (qty reduced to fit; skip if it then falls below 1 share) |
| Max open positions | 5 (all books of the main portfolio) |
| Cash buffer | 10 % of equity always kept in cash |
| Max open risk | 3 % of equity: Σ over open positions of (entry − stop) × qty |
| Daily loss limit | −2 % of the day's opening equity: no new entries for the rest of the session |
| Kill-switch | −10 % drawdown from the starting capital: new entries paused and an alert sent; resuming is a recorded user decision |
| Sector cap | **Deferred** until the 2026-11-20 switch (canon sector data); Phases 1–2 ship without it |

## 4. Fills and exits

- **Swing (CNC):**
  - entry is the alert's buy-stop `entry_ref` on session T+1 (filled at the open if it gaps above, else at `entry_ref`; no fill if T+1's high < `entry_ref`);
  - exits: the alert's stop and its actionable target (`detect.choose_target`: R1 if its net R:R ≥ 1, else R2, else 2R); stop first on a bar that touches both; gap-through at the open;
  - time stop at the close of session 5;
  - exit at the next open if the regime turns OFF.
- **Intraday (MIS):**
  - entry is the next 15m bar's buy-stop;
  - the same stop/target rule;
  - **flat by 15:20 everywhere:** the last bar is 15:00–15:15, and the exit is at its close.

  The grader, the live tracker and Telegram use this one rule from 2026-11-05.
- **Costs:** `core.costs` delivery legs for CNC (`buy` / `sell`, base slippage 10 bps/side). MIS legs are in `core.costs.mis_buy` / `mis_sell` (STT 0.025 % on the sell side only, stamp 0.003 % on the buy side, no DP). The brokerage is an Upstox tariff assumption, to be checked against a contract note.
- **Exit reasons:** target / stop / trail / time / regime / end-of-day, as `reason_code` + `reason_text`.

## 5. Placebo and observational books

- **Placebo (the comparison book):**
  - **the same gate-pass candidates** as the main portfolio;
  - the entry price is a seeded random point in the next bar's range (seed = `20261103 + alert_id`, as the grader);
  - **the same capital, caps and exits.**
- **Observational pair (RESEARCH ONLY, labelled so; not part of the test):**
  - an **all-alerts** book (every alert taken by its rule entry, the same caps);
  - its random-entry twin.

## 6. Benchmark

The **Nifty 500 price index** (`index_bar`, `NSE_INDEX|Nifty 500`) for now, buy-and-hold from the start date: benchmark equity = ₹10,00,000 × index close / index close at the start. **It excludes dividends**, so it understates a real holding by roughly the dividend yield. It will be upgraded to an investable ETF or total-return proxy after the switch; that is a recorded change, not a new trial.

## 7. Evaluation (one look)

- **Look condition:** ≥ 40 sessions **and** ≥ 100 closed trades in the swing book.
- **Hypotheses (≤ 3):**
  1. Net expectancy per trade > 0.
  2. The main portfolio beats the placebo book (net).
  3. The main portfolio beats the benchmark (net).
- **Test:** a seeded block bootstrap by session, 95 % CI, Bonferroni over the 3; plus the Deflated Sharpe ratio at the trial ledger's N at the look.
- **Odds calibration:** predicted vs realised outcomes once ~100 graded plans exist. It is reported, not a hypothesis.
- **The forward start** is the first session after Phase 3 runs hands-off. It is recorded in `FREEZE_PF.md` with the code sha.
- If trades stay too few for the look, the result is reported as "insufficient trades". Loosening the gate is a new trial.

## 8. What does not change

Scout (`scout-b-v1`), the scanner detectors (`scan-v1`), the EOD watchlist, drishti-swing-v1 and the closed research families. No real order path exists or will be added by this work.


## Build notes (not changes to the frozen values)

- **2026-11-05, Phase 2 build:**
  - The caps reduce qty to fit (position, open risk, cash buffer); a qty below 1 is rejected with the binding cap as the reason (`SKIP_CAP_*`).
  - Pending entries (decided, not yet filled) count toward the open positions and the open risk, and their planned value is reserved from cash.
  - With 1 % per trade, the 3 % open-risk cap means at most 3 full-size positions at a time.
- **Swing decisions:**
  - A swing decision is made at the alert's time (its 19:45 scan) on the latest snapshot.
  - The fill is simulated on the next session's official bar; its time of day is unknown, so it is recorded at 09:15 IST.
  - Stop / target / time exits are recorded at 15:30 of the exit day; a regime exit at the 09:15 open.
- **Results gate:** with no fresh board-meetings CSV (missing or older than 4 days) the results calendar is UNKNOWN, so the candidate is skipped (`SKIP_RESULTS_UNKNOWN`), as frozen.
