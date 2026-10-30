"""Model #1 (PREREG_M1): LightGBM binary classifier P(class +1) on f1, isotonic calibration on
out-of-fold predictions only, purged + embargoed walk-forward CV. Metrics for (a) all-bar and
(b) signal events separately. SURVIVORSHIP-BIASED, EXPLORATORY: history only; no forward
window data, no looks. p* is proposed here and frozen only at P6 (FREEZE_M1).

    .venv/bin/python -m drishti.ml.train --replay /windows/drishti_data/research/track-b/replay/<id>
        [--first-test 2021-07-01] [--jobs 28]
"""

from __future__ import annotations

import datetime as _dt
import json
import sys

import numpy as np
import pandas as pd

from drishti.features import candles
from drishti.ml import cv_purged, labels, registry
from drishti.replay.load import LABEL, research_dir
from drishti.strategies.rules import STRATEGIES

FEATURES = (
    [f"{x}_{k}" for k in range(3) for x in candles.FLAGS]
    + [f"{s}_atr_{k}" for s in ("B", "Uw", "Lw", "Rg") for k in range(5)]
    + ["dist_res_atr", "res_touches", "dist_sup_atr", "sup_touches", "at_support", "brk_state",
       "brk_days", "trend", "close_sma50", "close_sma200", "sma50_sma200", "ema20_dist_atr",
       "sma200_slope20", "rsi", "atr_pct", "volr20", "volr50", "rs_score", "rs_pct",
       "dist_52w", "ret1", "ret5", "ret20", "ret60", "bb_pos", "bb_bw_pct", "fib_depth",
       "fib_zone", "n500_vs_sma200", "vix", "vix_chg5", "breadth", "sector_strength", "dow",
       "days_to_results", "news_n72", "news_sent"]
    + list(STRATEGIES) + ["no_signal"])
GRID = ({"num_leaves": 15, "n_estimators": 300}, {"num_leaves": 31, "n_estimators": 300},
        {"num_leaves": 15, "n_estimators": 600}, {"num_leaves": 63, "n_estimators": 300})
BASE = {"learning_rate": 0.03, "min_child_samples": 200, "subsample": 0.8, "subsample_freq": 1,
        "colsample_bytree": 0.7, "reg_lambda": 1.0, "verbose": -1, "random_state": 20261028}
THRESHOLDS = tuple(np.round(np.arange(0.30, 0.71, 0.025), 3))


def matrix(panel: pd.DataFrame) -> pd.DataFrame:
    X = panel.assign(no_signal=~panel.signal)
    return X[FEATURES].astype(float)


def _model(params: dict, jobs: int):
    import lightgbm as lgb
    return lgb.LGBMClassifier(**BASE, **params, n_jobs=jobs)


def brier(p, y) -> float:
    return float(np.mean((np.asarray(p) - np.asarray(y)) ** 2))


def auc(p, y) -> float | None:
    from sklearn.metrics import roc_auc_score
    return float(roc_auc_score(y, p)) if len(set(y)) == 2 else None


def oof(X, y, w, fl: list[dict], params: dict, jobs: int) -> np.ndarray:
    p = np.full(len(y), np.nan)
    for f in fl:
        m = _model(params, jobs).fit(X.iloc[f["train"]], y[f["train"]],
                                     sample_weight=w[f["train"]])
        p[f["test"]] = m.predict_proba(X.iloc[f["test"]])[:, 1]
    return p


def walk_forward_isotonic(p, y, fl: list[dict]) -> np.ndarray:
    """Calibrated OOF: each block is calibrated by an isotonic fit on the OOF predictions of the
    earlier blocks only (the first block has none and stays NaN)."""
    from sklearn.isotonic import IsotonicRegression
    out, seen = np.full(len(y), np.nan), np.array([], dtype=int)
    for f in fl:
        if len(seen) >= 1000:
            iso = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(p[seen], y[seen])
            out[f["test"]] = iso.predict(p[f["test"]])
        seen = np.r_[seen, f["test"]]
    return out


def base_rate_pred(y, is_b, fl: list[dict], subset) -> np.ndarray:
    """The training base rate of each fold (of (a), or of (b) for the (b) rows): H3's reference."""
    out = np.full(len(y), np.nan)
    for f in fl:
        tr = f["train"][subset[f["train"]]]
        te = f["test"][subset[f["test"]]]
        if len(tr):
            out[te] = y[tr].mean()
    return out


def scores(p, y, ref, r) -> dict:
    ok = ~np.isnan(p) & ~np.isnan(ref)
    p, y, ref, r = p[ok], y[ok], ref[ok], r[ok]
    if not len(y):
        return {"n": 0}
    b, b0 = brier(p, y), brier(ref, y)
    return {"n": int(len(y)), "base_rate": float(y.mean()), "auc": auc(p, y), "brier": b,
            "brier_base_rate": b0, "bss": 1 - b / b0 if b0 > 0 else None,
            "mean_p": float(p.mean()), "mean_net_r": float(np.mean(r))}


def thresholds(p, r, n_total) -> list[dict]:
    out = []
    for t in THRESHOLDS:
        m = p >= t
        out.append({"p_star": float(t), "confirmed": int(m.sum()),
                    "confirm_share": float(m.sum() / n_total) if n_total else 0.0,
                    "mean_net_r": float(r[m].mean()) if m.any() else None,
                    "hit_rate": float((r[m] > 0).mean()) if m.any() else None})
    return out


def reliability(p, y, bins: int = 10) -> list[dict]:
    ok = ~np.isnan(p)
    p, y = p[ok], y[ok]
    e = np.linspace(0, 1, bins + 1)
    k = np.clip(np.digitize(p, e) - 1, 0, bins - 1)
    return [{"bin": [float(e[i]), float(e[i + 1])], "n": int((k == i).sum()),
             "mean_p": float(p[k == i].mean()), "frac_pos": float(y[k == i].mean())}
            for i in range(bins) if (k == i).any()]


def svg_calibration(rel: dict[str, list[dict]], title: str) -> str:
    W, H, P = 520, 520, 60
    sx = lambda v: P + v * (W - 2 * P)  # noqa: E731
    sy = lambda v: H - P - v * (H - 2 * P)  # noqa: E731
    colors = {"(a) all-bar": "#2563eb", "(b) events": "#dc2626"}
    L = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         'font-family="sans-serif" font-size="12">', f'<rect width="{W}" height="{H}" fill="#fff"/>',
         f'<text x="{W / 2}" y="20" text-anchor="middle" font-size="13">{title}</text>',
         f'<text x="{W / 2}" y="38" text-anchor="middle" fill="#b45309">{LABEL}</text>',
         f'<line x1="{sx(0)}" y1="{sy(0)}" x2="{sx(1)}" y2="{sy(1)}" stroke="#999" '
         'stroke-dasharray="4 4"/>',
         f'<rect x="{sx(0)}" y="{sy(1)}" width="{sx(1) - sx(0)}" height="{sy(0) - sy(1)}" '
         'fill="none" stroke="#333"/>']
    for v in np.linspace(0, 1, 6):
        L += [f'<text x="{sx(v)}" y="{sy(0) + 16}" text-anchor="middle">{v:.1f}</text>',
              f'<text x="{sx(0) - 8}" y="{sy(v) + 4}" text-anchor="end">{v:.1f}</text>']
    L += [f'<text x="{W / 2}" y="{H - 15}" text-anchor="middle">mean calibrated p (OOF)</text>',
          f'<text x="15" y="{H / 2}" transform="rotate(-90 15 {H / 2})" text-anchor="middle">'
          'observed share of class +1</text>']
    for j, (name, pts) in enumerate(rel.items()):
        c = colors.get(name, "#111")
        xy = " ".join(f"{sx(q['mean_p']):.1f},{sy(q['frac_pos']):.1f}" for q in pts)
        L.append(f'<polyline points="{xy}" fill="none" stroke="{c}" stroke-width="2"/>')
        L += [f'<circle cx="{sx(q["mean_p"]):.1f}" cy="{sy(q["frac_pos"]):.1f}" r="3.5" '
              f'fill="{c}"><title>n={q["n"]}</title></circle>' for q in pts]
        L.append(f'<text x="{sx(0) + 10}" y="{sy(1) + 18 + 16 * j}" fill="{c}">{name}</text>')
    return "\n".join(L + ["</svg>"])


def run(panel: pd.DataFrame, calendar: list, first_test: _dt.date, cutoff: _dt.date,
        jobs: int = 28) -> dict:
    lab = labels.labels(panel)
    keep = lab.closed.to_numpy()
    P, L = panel[keep].reset_index(drop=True), lab[keep].reset_index(drop=True)
    X, y, w = matrix(P), L.y.to_numpy(), L.weight.to_numpy()
    r, is_b = L.net_r.to_numpy(), P.signal.to_numpy()
    fl = cv_purged.folds(P.session_date, P.t5_date, calendar, first_test, cutoff)
    grid = []
    for g in GRID:
        p = oof(X, y, w, fl, g, jobs)
        ok = ~np.isnan(p)
        grid.append({"params": g, "brier_b": brier(p[ok & is_b], y[ok & is_b]),
                     "brier_a": brier(p[ok], y[ok]), "_p": p})
    best = min(grid, key=lambda g: (g["brier_b"], g["brier_a"]))
    p_raw = best["_p"]
    p_cal = walk_forward_isotonic(p_raw, y, fl)
    ref_a = base_rate_pred(y, is_b, fl, np.ones(len(y), bool))
    ref_b = base_rate_pred(y, is_b, fl, is_b)
    tested = ~np.isnan(p_cal)
    nb = int((tested & is_b).sum())
    out = {
        "label": LABEL, "cutoff": str(cutoff), "first_test": str(first_test),
        "folds": [{"block": [str(f["block"][0]), str(f["block"][1])], "train": int(len(f["train"])),
                   "test": int(len(f["test"])),
                   "test_b": int(is_b[f["test"]].sum())} for f in fl],
        "grid": [{"params": g["params"], "oof_brier_b": g["brier_b"], "oof_brier_a": g["brier_a"]}
                 for g in grid],
        "chosen": best["params"],
        "set_a": {"raw": scores(p_raw, y, ref_a, r), "calibrated": scores(p_cal, y, ref_a, r)},
        "set_b": {"raw": scores(p_raw[is_b], y[is_b], ref_b[is_b], r[is_b]),
                  "calibrated": scores(p_cal[is_b], y[is_b], ref_b[is_b], r[is_b])},
        "set_b_by_strategy": {s: scores(p_cal[is_b & P[s].to_numpy()],
                                        y[is_b & P[s].to_numpy()],
                                        ref_b[is_b & P[s].to_numpy()],
                                        r[is_b & P[s].to_numpy()]) for s in STRATEGIES},
        "p_star_table_b": thresholds(p_cal[tested & is_b], r[tested & is_b], nb),
        "reliability": {"(a) all-bar": reliability(p_cal, y),
                        "(b) events": reliability(p_cal[is_b], y[is_b])},
        "n_closed_a": int(len(y)), "n_closed_b": int(is_b.sum())}
    return out | {"_fit": (X, y, w, p_raw, best["params"])}


def main(args: list[str]) -> dict:
    from drishti.replay.run import calendar as cal_of
    from drishti.replay import load
    import asyncio
    opt = lambda k, d=None: args[args.index(k) + 1] if k in args else d  # noqa: E731
    rd = research_dir() / "replay" / opt("--replay") if "/" not in opt("--replay") \
        else __import__("pathlib").Path(opt("--replay"))
    man = json.loads((rd / "manifest.json").read_text())
    panel = pd.read_parquet(rd / "panel.parquet")
    for c in ("session_date", "t1_date", "t5_date"):
        panel[c] = pd.to_datetime(panel[c]).dt.date
    inp = asyncio.run(load.cached(_dt.date.fromisoformat(man["data_end"])))
    cutoff = _dt.date.fromisoformat(man["cutoff"])
    assert panel.session_date.max() <= cutoff
    res = run(panel, cal_of(inp["bars"]), _dt.date.fromisoformat(opt("--first-test",
                                                                     "2021-07-01")),
              cutoff, int(opt("--jobs", 28)))
    X, y, w, p_raw, params = res.pop("_fit")
    return registry.save(res, X, y, w, p_raw, params, man, int(opt("--jobs", 28)))


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1:]), indent=1, default=str))
