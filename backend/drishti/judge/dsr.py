"""Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014). EXPLORATORY research helper.

DSR = Phi( (SR - SR0) sqrt(T - 1) / sqrt(1 - g3 SR + (g4 - 1)/4 SR^2) ), with
SR0 = sqrt(V) ((1 - gamma) Z(1 - 1/N) + gamma Z(1 - 1/(N e))), the expected maximum Sharpe of
N independent trials under the null. Without the other trials' Sharpe ratios, V is the
sampling variance of the Sharpe estimate itself, (1 - g3 SR + (g4 - 1)/4 SR^2) / (T - 1).
All Sharpe ratios are per period (not annualised); g4 is the raw (non-excess) kurtosis.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import stats

EULER = 0.5772156649015329


def deflated_sharpe(r, n_trials: int) -> dict:
    r = np.asarray(r, float)
    T = len(r)
    sr = r.mean() / r.std(ddof=1)
    g3, g4 = float(stats.skew(r)), float(stats.kurtosis(r, fisher=False))
    den = 1 - g3 * sr + (g4 - 1) / 4 * sr ** 2
    v = den / (T - 1)
    z = stats.norm.ppf
    sr0 = math.sqrt(v) * ((1 - EULER) * z(1 - 1 / n_trials)
                          + EULER * z(1 - 1 / (n_trials * math.e)))
    psr0 = float(stats.norm.cdf(sr * math.sqrt(T - 1) / math.sqrt(den)))
    dsr = float(stats.norm.cdf((sr - sr0) * math.sqrt(T - 1) / math.sqrt(den)))
    return {"T": T, "sr_period": float(sr), "sr_annual": float(sr * math.sqrt(12)),
            "skew": g3, "kurtosis": g4, "sr0_period": float(sr0),
            "psr_vs_0": psr0, "dsr": dsr, "n_trials": n_trials}
