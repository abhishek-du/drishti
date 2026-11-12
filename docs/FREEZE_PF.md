# pf-v1 freeze: DRAFT (2026-11-12); final at the forward start, Mon 2026-11-23

PAPER ONLY. This freezes the paper portfolio rules (`docs/PREREG_PAPER_PORTFOLIO.md`) and the code that applies them for the forward test.

**Changing any frozen file after the start counts as a new trial (pf-v2), with a new start.** Bug fixes that change no decision go in a "build notes" line with their sha. A fix that changes any decision counts as a change.

## To fill on the start day (A, at the forward start)

| Item | Value |
|---|---|
| Start session | 2026-11-23 (the first Drishti-only session) |
| Code sha (`git rev-parse HEAD`, clean tree) | _to fill_ |
| `paper.run init --start 2026-11-23` | _run id, benchmark base close and date_ |
| History source | `copy` (after the 11-20 switch) |
| The cron lines `pf_session` 08:45 + 10:30 Tue–Sat installed | _yes / date_ |

## Frozen files (sha256; the draft values are from 2026-11-12 and are recomputed at the start)

| File | sha256 |
|---|---|
| docs/PREREG_PAPER_PORTFOLIO.md | 41d67cff5e645c5ed80f6e3ed95b5df84bda64119821e079538e4fa2fac03b46 |
| backend/drishti/paper/ledger.py | ff390beaef87db9bdefd62b0be9c0c4b41711b49ff8cd49fbf988c3afa2e9eec |
| backend/drishti/paper/risk.py | afb9ed9ec902f07800cd07ce70e0a8e881e2cbb757436d5fa4c725499f0e5686 |
| backend/drishti/paper/gates.py | 68572a1be103a113a21c8adf0b0b0ce87f000f195c4431c64d80358f56a17ac1 |
| backend/drishti/paper/trader.py | 845ea6404a642e7198ebb91cd9ddf948da82e2e51674cdf9e477c46b7932dce0 |
| backend/drishti/paper/loop.py | d67b46636a9ad3d7e80818d6217aefb8a276ff6dc28b25efc9ae4dae54e9bc03 |
| backend/drishti/scanner/detect.py (scan-v1 detectors, `choose_target`) | 8daa3d7bde73cf5dc03d91fb3fcda5f1baaef5434b1f3e684830f52cc7b0273c |
| backend/drishti/scanner/grade.py (exit horizon, seeds) | b2974080ceca8d909e84bcfd718070baa0d7a2397ac5822676453df7f0d8b0be |
| backend/drishti/strategies/orders.py (`exit_path`) | ac495f58ab78de9406d1da316b78cda024186f8055d8fee0abc79f4152c89ffa |
| backend/drishti/core/costs.py | ed6376b2efb42c12c3dabfaf90506d71a2b7ceeb2dc3d51e8421449027035788 |
| backend/drishti/judge/odds.py (odds-1d-v1, the odds gate input) | cb061d26957df6c15216324c137abf2e3fdf0fa4dd3b12ddcca19b62c3cf95cc |

**Note:** merging Track B's odds v2 (`9374b73`) **before** the start changes `judge/odds.py`. The final table then takes the merged file. After the start, odds v2 can only run beside pf-v1 as a separate book, never inside it.

## What runs every day (Phase 3)

1. **19:45 scan_eod:** the 1d alerts of T.
2. **20:15 odds:** the odds of those alerts, as known at 19:45.
3. **T+1 06:30 daily1d:** the official 1d of T.
4. **T+1 08:45 `pf_session`** (a retry at 10:30), which does these steps in order:
   1. the fills of T−1's takes on T's bar;
   2. the exits;
   3. mark to market at T's close;
   4. the decisions for T's alerts;
   5. the pf_equity snapshot;
   6. a Telegram summary.

   A session is processed once, sessions are processed in order, a missed day is caught up, and nothing runs before `init`.
5. **Display only:** the stats and the curve on `/portfolio`. The one look and its test are as pre-registered (PREREG section 7). There are no interim decisions on the numbers.

## Measurement books (as pre-registered; unchanged)

main, benchmark (Nifty 500 price index), obs_all, and the placebo / obs_placebo books. They are recorded as the pre-registration requires and are not discussed day to day.
