# Review: "Drishti Paper Portfolio: Build Spec" (2026-11-04)

**Reviewed:** the spec by @Rajat, 8 pages (`~/Downloads/Drishti Paper Portfolio Build Spec.pdf`).
**Reviewer:** Track A (Claude Code), 2026-11-04. No code was changed for the review.
**Outcome:** the user's decisions of 2026-11-05 are frozen in `PREREG_PAPER_PORTFOLIO.md`.

## What the spec asks for

- **A virtual ₹10,00,000 portfolio that runs itself end-to-end:** it selects, sizes, paper-orders, exits, grades and reports. Every order, fill, trade and reason is recorded Upstox-style.
- **Books:**
  - two sub-books share the capital: **swing** (CNC, from 1d alerts) and **intraday** (MIS, from 15m alerts);
  - two shadow books: a **benchmark** (Nifty 500 buy-and-hold) and a **placebo** (the same stocks, random entries, the same exits).
- **Entry gate, deterministic:** regime → Time Machine odds → risk → an optional news veto. An LLM may only phrase the reason or veto on news.
- **Tables:**
  - logs: `pf_order`, `pf_fill`, `pf_trade`, `pf_equity`, `pf_decision`, with skips logged together with their reason;
  - mutable state: `pf_portfolio`, `pf_position`.
- **Phases:**
  1. ledger;
  2. paper trader + risk;
  3. the autonomous loop;
  4. evaluation;
  5. intraday graduation.

  Intraday stays in observe mode until the 15m odds engine exists.

## What fits the existing system

- **The framing matches the research.** There is no new signal; the gate executes and records, and the portfolio measures itself against a placebo and a benchmark.
- **Already built:**
  - candidates (scanner + Live Radar);
  - stop/target levels with the broken-R1 rule;
  - point-in-time swing odds (`odds_result` / `odds_latest`);
  - the fill/exit rule (`strategies.orders.exit_path`: stop first, gap rules);
  - a seeded placebo entry;
  - `core.costs`;
  - regime / spec gate;
  - Telegram dedupe;
  - DSR + the trial ledger.
- **Not built:** `drishti/paper` and `drishti/risk` are empty, and no `pf_*` table exists (Phase 1–2).

## Main issue: very few trades, possibly none

1. **The regime gate:** it was OFF until 11-04. It flipped ON on 11-04 by ~9 points (Nifty 500 22,769 vs SMA200 22,760). Its ON share in 2026 is ~44 %.
2. **The odds gate**, on the 24 swing alerts of 11-02/11-03:

   | Variant | Passed |
   |---|---|
   | raw target-first ≥ break-even | **0 / 24** |
   | resolved-path target-first ≥ break-even + 5 pp | 1 / 24 |
   | average net R > 0 | 6 / 24 |

   Most paths reach neither barrier within 5 sessions.
3. **The forward-test condition** (≥ 40 sessions and ≥ 100 closed trades) may therefore take a very long time. A "no trade" result is honest, but the time limit and what happens if trades stay scarce must be fixed in advance. Loosening the gate later is a new trial.

## Gaps that needed a decision (all decided 2026-11-05, see the prereg)

4. The exact odds-gate definition: raw vs resolved paths, and the buffer.
5. Intraday flat by 15:20 (spec) vs 15:30 (the grader then). One rule is needed.
6. There is no MIS cost model; `core.costs` is delivery only.
7. The risk numbers: max position % (essential: a tight stop at 1 % risk can size a position at 100 % of equity), max open positions, cash buffer, daily loss, kill-switch.
8. The placebo definition: which candidates, the entry timing, and whether the same caps apply.
9. The benchmark: a price index (no dividends) vs an investable proxy.
10. The sector cap needs sector data; Prajna's `canon_instrument` will come with the D1 copy.
11. The news veto: the Bedrock key has been invalid since 10-27. A deterministic results gate is possible from `results_event` and the board-meetings CSV.
12. `pf_position` / `pf_portfolio` are mutable, which deviates from the append-only rule. It needs a DEVIATIONS note and an append-only position event log.

## Order and ownership

- Phase 1 (ledger, read APIs, `/portfolio`) can be built before the 11-20 switch.
- Phase 3 (the autonomous loop) is better after the switch.
- **Ownership:**
  - Track A: tables, migrations, service, cron;
  - Track B: placebo/benchmark evaluation and the forward-test report;
  - Track C: the `/portfolio` UI beyond the read-only Phase 1 page.
