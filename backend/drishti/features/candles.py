"""The 26 candle flags of spec section 4 on one stock's daily bars (oldest first, with an `atr`
column). Each flag on row i uses rows <= i only."""

from __future__ import annotations

import pandas as pd

FLAGS = ("doji", "dragonfly_doji", "gravestone_doji", "hammer", "inverted_hammer", "hanging_man",
         "shooting_star", "spinning_top", "marubozu_bull", "marubozu_bear", "engulfing_bull",
         "engulfing_bear", "piercing", "dark_cloud", "harami_bull", "harami_bear",
         "tweezer_bottom", "tweezer_top", "morning_star", "evening_star", "three_white_soldiers",
         "three_black_crows", "three_inside_up", "three_inside_down", "rising_three",
         "falling_three")
BULLISH = ("hammer", "engulfing_bull", "morning_star")       # the S1b/S2/S3 trigger flags


def flags(df: pd.DataFrame, at_support: pd.Series | None = None, by: str | None = None
          ) -> pd.DataFrame:
    """`by` names the stock column when `df` holds many stocks (sorted by stock, then date)."""
    g = df[by].to_numpy() if by else None
    o, h, lo, c, a = df.open, df.high, df.low, df.close, df.atr
    B, Rg = (c - o).abs(), h - lo
    top, bot = pd.concat([o, c], axis=1).max(axis=1), pd.concat([o, c], axis=1).min(axis=1)
    Uw, Lw = h - top, bot - lo
    green, red = c > o, c < o
    long_, small = B >= 0.6 * a, B <= 0.3 * Rg
    def p(s: pd.Series, k: int = 1) -> pd.Series:
        r = s.groupby(g).shift(k) if g is not None else s.shift(k)
        return r.fillna(False).astype(bool) if s.dtype == bool else r
    fall3 = (p(c) < p(c, 2)) & (p(c, 2) < p(c, 3)) & (p(c, 3) < p(c, 4))
    rise3 = (p(c) > p(c, 2)) & (p(c, 2) > p(c, 3)) & (p(c, 3) > p(c, 4))
    sup = at_support.reindex(df.index).fillna(False).astype(bool) if at_support is not None \
        else pd.Series(False, index=df.index)
    f = pd.DataFrame(index=df.index)
    f["doji"] = (B <= 0.1 * Rg) & (Rg > 0)
    f["dragonfly_doji"] = f.doji & (Uw <= 0.1 * Rg)
    f["gravestone_doji"] = f.doji & (Lw <= 0.1 * Rg)
    hammer_shape = small & (Lw >= 2 * B) & (Uw <= B) & (bot >= lo + 2 / 3 * Rg) & (Rg > 0)
    inv_shape = small & (Uw >= 2 * B) & (Lw <= B) & (top <= lo + 1 / 3 * Rg) & (Rg > 0)
    f["hammer"] = hammer_shape & (fall3 | sup)
    f["inverted_hammer"] = inv_shape & fall3
    f["hanging_man"] = hammer_shape & rise3
    f["shooting_star"] = inv_shape & rise3
    f["spinning_top"] = small & (Uw >= B) & (Lw >= B) & (B > 0)
    f["marubozu_bull"] = long_ & green & (Uw <= 0.05 * Rg) & (Lw <= 0.05 * Rg)
    f["marubozu_bear"] = long_ & red & (Uw <= 0.05 * Rg) & (Lw <= 0.05 * Rg)
    f["engulfing_bull"] = p(red) & green & (o <= p(c)) & (c >= p(o))
    f["engulfing_bear"] = p(green) & red & (o >= p(c)) & (c <= p(o))
    pmid = (p(o) + p(c)) / 2
    f["piercing"] = p(red & long_) & (o < p(lo)) & (c > pmid) & (c < p(o))
    f["dark_cloud"] = p(green & long_) & (o > p(h)) & (c < pmid) & (c > p(o))
    f["harami_bull"] = p(red & long_) & green & small & (top <= p(o)) & (bot >= p(c))
    f["harami_bear"] = p(green & long_) & red & small & (top <= p(c)) & (bot >= p(o))
    f["tweezer_bottom"] = p(red) & ((lo - p(lo)).abs() <= 0.1 * a)
    f["tweezer_top"] = p(green) & ((h - p(h)).abs() <= 0.1 * a)
    mid2 = (p(o, 2) + p(c, 2)) / 2
    f["morning_star"] = (p(red & long_, 2) & p(small)
                         & (p(top) <= p(bot, 2) - 0.1 * a) & green & (c > mid2))
    f["evening_star"] = (p(green & long_, 2) & p(small)
                         & (p(bot) >= p(top, 2) + 0.1 * a) & red & (c < mid2))
    soldier = long_ & green & (Uw <= 0.25 * Rg) & (Lw <= 0.25 * Rg)
    crow = long_ & red & (Uw <= 0.25 * Rg) & (Lw <= 0.25 * Rg)
    in_prev = (o >= p(bot)) & (o <= p(top))
    f["three_white_soldiers"] = (soldier & p(soldier) & p(soldier, 2) & in_prev & p(in_prev)
                                 & (c > p(c)) & (p(c) > p(c, 2)))
    f["three_black_crows"] = (crow & p(crow) & p(crow, 2) & in_prev & p(in_prev)
                              & (c < p(c)) & (p(c) < p(c, 2)))
    f["three_inside_up"] = p(f.harami_bull) & (c > p(o, 2))
    f["three_inside_down"] = p(f.harami_bear) & (c < p(o, 2))
    inside1 = lambda k: (p(h, k) <= p(h, 4)) & (p(lo, k) >= p(lo, 4))  # noqa: E731
    f["rising_three"] = (p(green & long_, 4) & long_ & green & (c > p(c, 4))
                         & p(red & small, 1) & p(red & small, 2) & p(red & small, 3)
                         & inside1(1) & inside1(2) & inside1(3))
    f["falling_three"] = (p(red & long_, 4) & long_ & red & (c < p(c, 4))
                          & p(green & small, 1) & p(green & small, 2) & p(green & small, 3)
                          & inside1(1) & inside1(2) & inside1(3))
    return f[list(FLAGS)].fillna(False).astype(bool)


def bullish_candle(df: pd.DataFrame, f: pd.DataFrame) -> pd.Series:
    """S1b's "bullish candle": engulfing_bull, hammer, or green closing in the top 40 % of the
    range."""
    rg = (df.high - df.low).where(df.high > df.low)
    return f.engulfing_bull | f.hammer | ((df.close > df.open)
                                          & ((df.close - df.low) / rg >= 0.6).fillna(False))
