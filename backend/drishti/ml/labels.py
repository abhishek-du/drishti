"""Model #1 labels (spec drishti-swing-v1 section 8): the triple-barrier class is the section 6
exit path of `drishti.strategies.orders` (one implementation), read from the replay panel.

- class +1: TP, or TIME with net R > 0 at base slippage; -1: SL, or TIME with net R <= 0;
- NOT_TRIGGERED / GAP_CHASE / SKIP_RRR / CANCEL_RRR_AT_FILL / OPEN: no barrier label;
- y = 1 for class +1 (the binary target), weight = |net R| at base slippage (DESIGN "ML").
The label window is [T+1, T+5] (t1_date, t5_date): it sets the CV purge and embargo.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from drishti.core.costs import BASE_SLIPPAGE_BPS
from drishti.strategies.orders import CLOSED


def labels(panel: pd.DataFrame, v: str = "o", bps: int = BASE_SLIPPAGE_BPS) -> pd.DataFrame:
    st, r = panel[f"{v}_status"], panel[f"{v}_net_r_{bps}"].astype(float)
    closed = st.isin(CLOSED) & panel.t5_date.notna()
    cls = np.where(st == "TP", 1, np.where(st == "SL", -1, np.where(r > 0, 1, -1)))
    return pd.DataFrame({"closed": closed, "cls": np.where(closed, cls, 0),
                         "y": (closed & (cls == 1)).astype(int),
                         "weight": r.abs().where(closed, 0.0), "net_r": r.where(closed)},
                        index=panel.index)
