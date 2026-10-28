# Spec `drishti-swing-v1` (DRAFT, frozen at the end of P0)

**Sources:**
- the "Drishti Trading Strategies Plan" PDF (2026-10-28), summarised in `second-module.md` §8;
- the user's plan changes 1–8 and fixes 1–6, in `DESIGN.md`.

**Change rule:** the rules here are frozen. Any change is `drishti-swing-v2`, with its own forward window. Numbers marked *VERIFY* must be confirmed before the freeze, and that confirmation is recorded in the freeze commit.

## 1. Time, data, calendar

- **Sessions:** NSE trading days, from the Upstox holidays API, merged with Prajna's calendar up to the stop date.
- **Daily bar of T:**
  - **provisional:** the Upstox quote OHLC at 15:40 IST;
  - **official:** Upstox historical 1d (or Prajna canon up to the stop date).
  - Signals are computed from the provisional bar at 19:30. If the official bar differs by more than 0.1 % in any OHLC field, the signal is recomputed and flagged `BAR_REVISED`.
- **Prices** are split/bonus-adjusted (Prajna canon basis). Volume is adjusted with the inverse factor.
- **Point-in-time rule:** every feature on T uses bars ≤ T only. Every event (results, news, ASM list) must have `knowable_at` ≤ T 19:30.

## 2. Universe (point-in-time)

On each session d, a stock is eligible when all of these hold:

| Rule | Value |
|---|---|
| Instrument | NSE equity, EQ series, from the instrument master/security class. SME and BE/T2T are excluded |
| Listing age | ≥ 200 sessions with bars before d |
| Liquidity | 20-session mean of close × volume ≥ ₹20 crore |
| Price | close(d) ≥ ₹50 |
| Rank | Top 500 by that 20-session traded value, computed on d (the "Nifty 500 proxy") |
| Surveillance | Not in the latest ASM/GSM CSV with file date ≤ d (`drishti_data/manual/`). A CSV older than 7 sessions raises the warning `ASM_STALE` |

Names that later delisted or were suspended stay in the history on the dates they were eligible (fix 4). Survivorship status: see `DESIGN.md`, "Survivorship".

**Survivorship rule (user P0 decision 2):**
- The user puts the NSE delisted and suspended lists in `/windows/drishti_data/manual/`.
- P1 probes Upstox historical 1d for those names.
- If Upstox has no history for them, **every backtest and every historical scorecard is labelled "SURVIVORSHIP-BIASED, EXPLORATORY"**, and the evidence rests on the forward test only.

## 3. Indicators (all on adjusted daily bars)

| Name | Definition |
|---|---|
| SMA50, SMA200, EMA20 | Close-based, standard |
| ATR | Wilder ATR(14) |
| ATR % | ATR / close |
| RSI | Wilder RSI(14) |
| VolR50 | volume(T) / SMA50(volume) |
| VolR20 | volume(T) / SMA20(volume) |
| 52-week high | max(high) over the last 250 sessions |
| RS score | Nifty200 Momentum 30 method on the eligible universe. For L in {6M = 125, 12M = 250} sessions: MR_L = return_L / σ_annual(daily returns, 250). Then z-score MR_L across the universe; z = 0.5·z6 + 0.5·z12; score = 1 + z if z ≥ 0, else 1 / (1 − z). `RS_top` = score in the top 25 % of the universe on T |
| Pivots | High/low pivot = the extreme of a 5-session-each-side window, known 5 sessions later |
| S/R zones | Pivots of the last 250 sessions, clustered greedily within 0.5 × ATR(T). A zone needs ≥ 2 pivots at least 10 sessions apart. Zone price = cluster mean; zone band = mean ± 0.25 × ATR |
| Resistance above x | The lowest zone with band bottom > x |
| Support below x | The highest zone with band top < x |
| Trend | UP when close > SMA50 > SMA200 and the last two pivot highs and pivot lows are rising; DOWN when the mirror holds; else SIDE |

## 4. Candle flags (26; used as features; S1b/S2/S3 triggers use the bold ones)

**Notation:**
- B = |close − open|; Rg = high − low; Uw = high − max(open, close); Lw = min(open, close) − low.
- "Long body" = B ≥ 0.6 × ATR; "small body" = B ≤ 0.3 × Rg; "same level" = within 0.1 × ATR.
- "Gap" (body gap) = the open is ≥ 0.1 × ATR beyond the previous body.

| Flag | Rule |
|---|---|
| doji | B ≤ 0.1 × Rg and Rg > 0 |
| dragonfly_doji | doji and Uw ≤ 0.1 × Rg |
| gravestone_doji | doji and Lw ≤ 0.1 × Rg |
| **hammer** | small body, Lw ≥ 2 × B, Uw ≤ B, the body sits in the top third of the range, after ≥ 3 sessions of lower closes or at support |
| inverted_hammer | small body, Uw ≥ 2 × B, Lw ≤ B, after a decline |
| hanging_man | hammer shape after ≥ 3 sessions of higher closes |
| shooting_star | inverted-hammer shape after a rise |
| spinning_top | small body and both wicks ≥ B |
| marubozu_bull / marubozu_bear | long body, both wicks ≤ 0.05 × Rg |
| **engulfing_bull** | previous red, current green, current body covers the previous body |
| engulfing_bear | mirror |
| piercing | previous long red; current open below the previous low (gap), close above the previous body's midpoint and below its open |
| dark_cloud | mirror |
| harami_bull / harami_bear | previous long body of one colour; current small opposite body inside the previous body |
| tweezer_bottom / tweezer_top | two lows / highs at the same level, previous red / green |
| **morning_star** | long red; then a small body gapping below it; then a green closing above the midpoint of the first body |
| evening_star | mirror |
| three_white_soldiers / three_black_crows | 3 long bodies of one colour, each opening inside the previous body and closing beyond the previous close, wicks ≤ 0.25 × Rg |
| three_inside_up / three_inside_down | harami + a 3rd bar closing beyond the 1st bar's open |
| rising_three / falling_three | long body; 3 small opposite bodies inside its range; a 5th long body closing beyond the 1st |

A "bullish candle" (used by S1b) = engulfing_bull, or hammer, or (green with close in the top 40 % of the range).

## 5. Signals (computed on T's close; orders for T+1)

**Common gates** (all must hold on T):
- eligible;
- RSI(14) between 40 and 75;
- regime OK: Nifty 500 close > its SMA200 (index 1d from Upstox; until it is ingested, fallback = ≥ 50 % of the universe above its SMA200);
- no results/board-meeting event for that stock between T+1 and T+6 (the label window + 1) as known at T;
- not halted by the VIX rule for that strategy.

| ID | Rule on T |
|---|---|
| **S1a** momentum breakout (close) | Trend UP; RS_top; close ≥ 0.85 × 52-week high; close > the top of the band of the nearest resistance zone that had ≥ 2 touches, and the previous close was ≤ that band top; VolR50 ≥ 1.5 |
| **S1b** breakout retest | A S1a breakout on day B, with T − B in 2..10 sessions; on T, low ≤ band top + 0.25 × ATR, close > band top; bullish candle on T; trend UP; RS_top |
| **S2** 20 EMA pullback | close > SMA50 > SMA200; low(T) ≤ EMA20 × 1.005 and close(T) ≥ EMA20 × 0.99; hammer or engulfing_bull on T |
| **S3** support + reversal | close > SMA200; low(T) inside or ≤ 0.25 × ATR below the band of a support zone that had ≥ 2 touches; hammer, engulfing_bull or morning_star on T; VolR20 > 1.0 |

**VIX rule:**
- normal: India VIX < 20;
- spike: VIX ≥ 25, or the 5-session change ≥ +20 %;
- during a spike: S3 is off (mean-reversion), and every other size is halved.

## 6. Orders, levels, risk (paper only)

- **Entry:** a buy-stop at trigger = high(T) × 1.0005, valid on T+1 only.
  - If open(T+1) ≥ trigger, the fill is at open(T+1) (gap).
  - If open(T+1) > trigger + 0.5 × ATR, the order is cancelled as `GAP_CHASE`.
  - No fill if high(T+1) < trigger (`NOT_TRIGGERED`).
- **Stop:** S = min(low(T) − 0.1 × ATR, E_est − 1.0 × ATR), where E_est = trigger. The stop is never moved down.
- **Target:** TP = min(E_est + 2 × R_est, the bottom of the band of the nearest resistance zone above E_est), where R_est = E_est − S.
- **RRR check:** RRR = (TP − E_est) / R_est must be ≥ 1.5, else `SKIP_RRR`. It is checked again at the actual fill E with the same S and TP; below 1.5 → `CANCEL_RRR_AT_FILL`.
- **Size:**
  - paper capital ₹10,00,000;
  - risk per trade 1 % (× 0.5 in a VIX spike);
  - shares = floor(risk ₹ / (E − S)).
- **Portfolio limits:**
  - open risk ≤ 3 % of capital;
  - ≤ 6 open positions;
  - ≤ 2 per sector (`canon_instrument.sector`).
  - When there are more signals than slots: rank by model p (when the model is live), else by RS score.
- **Exit (= the label; fix 1)**, on the path of daily bars from the fill day (session 1) to session 5:
  1. On any bar with low ≤ S, exit at S, or at the open when open < S (gap through).
  2. Otherwise, on any bar with high ≥ TP, exit at TP, or at the open when open > TP.
  3. If both happen on one bar, the stop comes first.
  4. On the fill day, a low ≤ S counts as the stop (conservative: intraday order unknown).
  5. Otherwise, exit at the close of session 5 (`TIME`).

  The trailing-20DMA and 10-session-hold variants are recorded as EXPLORATORY only.

## 7. Costs (delivery, per side unless stated) *VERIFY against the user's Upstox contract note (buy + sell + DP) in `/windows/drishti_data/manual/` before the freeze*

| Charge | Value |
|---|---|
| STT | 0.1 % buy + 0.1 % sell |
| Stamp duty | 0.015 % buy |
| NSE transaction | 0.00307 % each side |
| SEBI fee | ₹10 per crore each side |
| Brokerage | ₹20 per order *VERIFY (Upstox delivery)* |
| GST | 18 % on brokerage + transaction + SEBI |
| DP charge | ₹20 + GST per scrip per sell day *VERIFY* |
| Slippage scenarios | 5 / 10 / 20 bps per side (base = 10) |

**Net R** = (exit − E) / R − costs_per_share / R.

## 8. Labels (model #1)

- **(b) events:** each S1a/S1b/S2/S3 signal on T gets the label from the §6 path:
  - `TP` (+1), `SL` (−1), `TIME` (sign of net R), or `NOT_TRIGGERED` / `GAP_CHASE` / `SKIP_RRR` (recorded, no barrier label).
  - The reward = net R at base slippage.
- **(a) all-bar:** every eligible stock-day gets the same order/level/exit rules with the generic trigger (high(T) buy-stop) and the generic stop. The classes are as in (b).
- **Overlap:** a label window spans T+1..T+5. These windows set the CV purge and embargo (`PREREG_M1.md`).

## 9. Features `f1` (point-in-time on T; one row per stock-day)

- **Candles:** the 26 flags (§4) for T, T−1, T−2; B, Uw, Lw and Rg in ATR units for T..T−4.
- **Levels:** distance to the nearest support/resistance in ATR; touches of each; the breakout state (none / broke on day k / retesting); days since the breakout.
- **Trend:** the Trend class; close/SMA50, close/SMA200, SMA50/SMA200, EMA20 distance in ATR; SMA200 slope (20 sessions).
- **Momentum and volatility:**
  - RSI(14), ATR %, VolR20, VolR50;
  - RS score and its rank percentile;
  - distance to the 52-week high;
  - returns over 1, 5, 20 and 60 sessions.
- **Plus points (PDF):**
  - Bollinger: position in the 20/2σ band and bandwidth percentile (squeeze);
  - Fibonacci: the pullback depth inside the last pivot swing (38.2–61.8 % zone flag).
- **Context:**
  - Nifty 500 vs SMA200; India VIX level and 5-session change;
  - universe breadth (% above SMA200);
  - sector strength (sector median 20-session return minus the universe median);
  - day of week; days to the next results event.
- **News** (Prajna news tables, read-only, knowable ≤ T 19:30): the count of linked items in 72 h, and the mean AI sentiment when present (missing is allowed).
- **Strategy:** one-hot S1a/S1b/S2/S3, plus "no signal" for set (a).

The 16 chart-pattern detectors are **not** in f1 (change 2).
