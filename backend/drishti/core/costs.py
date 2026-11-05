"""Delivery costs, spec drishti-swing-v1 section 7 (frozen 2026-10-28). Rupees, per order leg."""

from __future__ import annotations

from dataclasses import dataclass

STT = 0.001                 # buy and sell
STAMP_BUY = 0.00015
NSE_TXN = 0.0000307
SEBI_PER_CRORE = 10.0
BROKERAGE = 20.0            # per executed order (Upstox tariff assumption)
GST = 0.18
DP = 20.0                   # per ISIN per sell day, + GST (Upstox tariff assumption)
SLIPPAGE_BPS = (5, 10, 20)  # per side; base = 10
BASE_SLIPPAGE_BPS = 10
MODEL = "costs drishti-swing-v1 s7 (frozen 2026-10-28), base slippage 10 bps/side"  # a label only


@dataclass(frozen=True)
class Leg:
    turnover: float
    brokerage: float
    txn: float
    sebi: float
    stt: float
    stamp: float
    dp: float
    gst: float

    @property
    def total(self) -> float:
        return self.brokerage + self.txn + self.sebi + self.stt + self.stamp + self.dp + self.gst


def _r(x: float) -> float:
    return round(x + 1e-9, 2)


def buy(turnover: float) -> Leg:
    brk, txn, sebi = BROKERAGE, _r(turnover * NSE_TXN), _r(turnover * SEBI_PER_CRORE / 1e7)
    return Leg(turnover, brk, txn, sebi, _r(turnover * STT), _r(turnover * STAMP_BUY), 0.0,
               _r(GST * (brk + txn + sebi)))


def sell(turnover: float, dp: bool = True) -> Leg:
    """The sell leg; with dp=True the ledger's DP charge (+ its GST) is included."""
    brk, txn, sebi = BROKERAGE, _r(turnover * NSE_TXN), _r(turnover * SEBI_PER_CRORE / 1e7)
    d = DP if dp else 0.0
    return Leg(turnover, brk, txn, sebi, _r(turnover * STT), 0.0, d,
               _r(GST * (brk + txn + sebi)) + _r(GST * d))


def round_trip(entry: float, exit_: float, qty: int, slippage_bps: float = BASE_SLIPPAGE_BPS
               ) -> float:
    """Rupee cost of a delivery round trip: charges on both legs plus slippage on both sides."""
    b, s = buy(entry * qty), sell(exit_ * qty)
    return b.total + s.total + (entry + exit_) * qty * slippage_bps / 1e4


# ---- intraday (MIS) legs (user 2026-11-05, PREREG_PAPER_PORTFOLIO section 4) --------------
# Additive: the delivery model above (spec drishti-swing-v1 section 7) is unchanged.
# NSE equity intraday: STT 0.025 % on the sell side only, stamp 0.003 % on the buy side, the
# same exchange/SEBI rates, GST on brokerage + exchange + SEBI, no DP charge.
# ASSUMPTION (to verify against an intraday contract note, like BROKERAGE/DP above): the
# Upstox intraday brokerage is the lower of Rs 20 and 0.1 % of the order value.
STT_MIS_SELL = 0.00025
STAMP_MIS_BUY = 0.00003
BROKERAGE_MIS_PCT = 0.001
MODEL_MIS = "costs MIS (pf-v1 2026-11-05): STT 0.025% sell, stamp 0.003% buy, brokerage " \
            "min(Rs 20, 0.1%) ASSUMED, base slippage 10 bps/side"


def mis_buy(turnover: float) -> Leg:
    brk = _r(min(BROKERAGE, BROKERAGE_MIS_PCT * turnover))
    txn, sebi = _r(turnover * NSE_TXN), _r(turnover * SEBI_PER_CRORE / 1e7)
    return Leg(turnover, brk, txn, sebi, 0.0, _r(turnover * STAMP_MIS_BUY), 0.0,
               _r(GST * (brk + txn + sebi)))


def mis_sell(turnover: float) -> Leg:
    brk = _r(min(BROKERAGE, BROKERAGE_MIS_PCT * turnover))
    txn, sebi = _r(turnover * NSE_TXN), _r(turnover * SEBI_PER_CRORE / 1e7)
    return Leg(turnover, brk, txn, sebi, _r(turnover * STT_MIS_SELL), 0.0, 0.0,
               _r(GST * (brk + txn + sebi)))


def leg(side: str, product: str, turnover: float) -> Leg:
    """The cost leg of one fill: CNC = delivery (buy / sell with DP), MIS = intraday."""
    if product == "MIS":
        return mis_buy(turnover) if side == "BUY" else mis_sell(turnover)
    return buy(turnover) if side == "BUY" else sell(turnover)
