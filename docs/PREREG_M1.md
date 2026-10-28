# Pre-registration: model #1 (swing), DRAFT

**Status:** DRAFT. This document is committed and hashed at the freeze, before the forward start. Nothing in it changes after the freeze; a change means a new model version with a new forward window.

## Model

- **Rules:** `drishti-swing-v1` (`SPEC_drishti-swing-v1.md`), features `f1`.
- **Model:** LightGBM binary classifier, P(TP before SL within 5 sessions). Isotonic calibration is fitted on the out-of-fold predictions of the training CV only.
- **Training data:** sessions from 2019-12 (after a 200-session warm-up) up to the **data cut-off C**. C = the last session whose label window (T+1..T+5) closes before the freeze date.
- **CV (change 3): purged + embargoed walk-forward.**
  - Expanding train; test blocks of 3 calendar months.
  - Training rows are purged when their window [T+1, T+5] overlaps the test block.
  - Embargo: 5 sessions after each test block.
  - Hyper-parameters and the confirmation threshold p* are chosen on the CV folds only, and frozen.
- **Frozen artefact:** the model file sha256, the feature list sha, the code git sha, the spec sha and p*.

## Samples and reporting (change 4)

- **(a) all-bar:** every eligible stock-day.
- **(b) events:** S1a/S1b/S2/S3 signal days.
- Metrics are always reported separately for (a) and (b).
- **Paper decisions use (b) only:** a (b) event with p ≥ p* is "confirmed" and becomes a paper order (subject to the risk rules).

## Hypotheses (at most 3; change 5)

| ID | Hypothesis (forward window, set (b), base slippage 10 bps/side) | Statistic | Pass |
|---|---|---|---|
| **H1** | Confirmed events have positive net expectancy | Mean net R of confirmed closed events | The lower bound of the CI > 0 |
| **H2** | Confirmed events beat all rule events (rules-only) | Mean net R(confirmed) − mean net R(all (b) events) | The lower bound of the CI > 0 |
| **H3** | The model is calibrated better than the base rate on (b) | Brier skill score = 1 − Brier(model) / Brier(base rate of the training data) | The lower bound of the CI > 0 |

**Every other number is EXPLORATORY** and labelled so: set (a), per strategy, per regime, other slippage levels, the trailing-exit variant, the placebo comparison.

## Look rule (user P0 decision 5)

- **Forward start:** the first session after the freeze commit.
- **The single look happens when all three are true:**
  - ≥ 40 forward sessions;
  - ≥ 100 closed (b) events (TP/SL/TIME);
  - ≥ 30 closed **confirmed** events.
- **Hard cap: 120 forward sessions.**
  - If the cap comes first, the look happens at session 120.
  - H1/H2 are then reported as **UNDERPOWERED** when the confirmed count < 30, which is not a pass.
  - H3 is evaluated as usual if (b) ≥ 100. Otherwise it is UNDERPOWERED too.
- Still exactly **one look**.
- **Before the look, only these counts may be checked:** sessions, closed (b) events, closed confirmed events.

## Test (fix 3)

- **Bootstrap:** seeded (seed 20261028), 10,000 resamples, a block bootstrap by session (all events of a session move together) to respect overlap and same-day correlation.
- **Bonferroni** over 3 hypotheses: two-sided 98.33 % CIs. "95 %" in the user's fix is the family-wise level.
- **Comparisons shown with the look** (EXPLORATORY): a random-entry placebo (same names and days, random trigger) and the rules-only book.

## Blinding (user P0 decision 4)

- **Blind on outcomes until the look:**
  - no P&L, hit rate, expectancy, Brier score or win/loss count is shown or computed in any report, dashboard or Telegram message;
  - neither for model #1's forward window nor for its paper book.
- **What is allowed: an ops-health view only:**
  - data freshness (last bar date, provisional vs official status, token valid, last run status);
  - signal count per strategy per day;
  - order counts (placed / filled / cancelled with reason);
  - open position count;
  - errors.
- The grading job writes grades to the DB, but every read path that would expose outcomes refuses until the look ledger says LOOK_TAKEN (enforced in code and tested).

## Not allowed

- No look at forward outcomes before the look rule is met (see Blinding).
- No retrain of the champion inside the forward window. Weekly challengers train only on data ≤ C plus their own shadow period, and never replace the champion before its look.
- No change to `drishti-swing-v1` or `f1` inside the window.
