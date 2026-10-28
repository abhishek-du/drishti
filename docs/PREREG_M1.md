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

## Look rule (fix 3)

- **Forward start:** the first session after the freeze commit.
- **The single look happens when both are true:**
  - ≥ 40 forward sessions;
  - ≥ 100 closed (b) events (TP/SL/TIME).
- If 100 is not reached at 40 sessions, the look waits until it is. The only number checked before the look is the closed-event count.
- **Open question (to settle before the freeze):** H1 uses only the *confirmed* subset, which may be much smaller than 100. Proposal: also require ≥ 30 closed confirmed events, otherwise H1/H2 are reported "UNDERPOWERED" (not a pass).

## Test (fix 3)

- **Bootstrap:** seeded (seed 20261028), 10,000 resamples, a block bootstrap by session (all events of a session move together) to respect overlap and same-day correlation.
- **Bonferroni** over 3 hypotheses: two-sided 98.33 % CIs. "95 %" in the user's fix is the family-wise level.
- **Comparisons shown with the look** (EXPLORATORY): a random-entry placebo (same names and days, random trigger) and the rules-only book.

## Open question: daily visibility (to settle before the freeze)

For Prajna's Strategy Lab (2026-10-27), the user chose to see daily P&L, with "no rule change because of the daily numbers". For Drishti, two options:
- **(i) blind:** counts and health only until the look. This is the strongest test.
- **(ii) visible:** paper P&L and grades shown daily, the look report states "outcomes visible daily", and no rule or threshold changes inside the window.

The user decides. This draft assumes (i).

## Not allowed

- No look at forward outcomes before the look rule is met. The daily dashboard shows counts and health only for the model #1 forward test.
- No retrain of the champion inside the forward window. Weekly challengers train only on data ≤ C plus their own shadow period, and never replace the champion before its look.
- No change to `drishti-swing-v1` or `f1` inside the window.
