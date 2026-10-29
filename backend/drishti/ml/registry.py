"""Model registry in files: /windows/drishti_data/research/track-b/models/<model_id>/ with the
LightGBM model, the isotonic map, the feature list, the CV report (a)/(b), the calibration plot
and a manifest of sha256s (model file, feature list, code git sha, spec sha, replay panel).
Nothing here is frozen: the freeze (FREEZE_M1, p*) is P6. SURVIVORSHIP-BIASED, EXPLORATORY.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json

import numpy as np

from drishti.replay.load import LABEL, research_dir, sha256

MIN_CONFIRM_SHARE, MIN_CONFIRMED = 0.2, 100


def propose_p_star(table: list[dict]) -> dict | None:
    """A proposal only: the threshold with the highest OOF mean net R on (b) among those that
    confirm >= 20 % of events and >= 100 events."""
    ok = [t for t in table if t["mean_net_r"] is not None and t["confirm_share"] >=
          MIN_CONFIRM_SHARE and t["confirmed"] >= MIN_CONFIRMED]
    return max(ok, key=lambda t: t["mean_net_r"]) if ok else None


def markdown(rep: dict) -> str:
    f = lambda v, k=4: "" if v is None else f"{v:.{k}f}"  # noqa: E731
    L = [f"# Model #1 CV report: {LABEL}", "",
         f"Model {rep['model_id']}, C = {rep['cutoff']}, purged + embargoed walk-forward "
         f"(3-month blocks from {rep['first_test']}, embargo 5 sessions), {len(rep['folds'])} "
         f"folds. Chosen on the CV folds only: {rep['chosen']}.", "",
         "| set | n | base rate | AUC | Brier | Brier (train base rate) | BSS | mean p |",
         "|---|---|---|---|---|---|---|---|"]
    for name, key in (("(a) all-bar", "set_a"), ("(b) events", "set_b")):
        for kind in ("raw", "calibrated"):
            s = rep[key][kind]
            L.append(f"| {name} {kind} | {s['n']} | {f(s.get('base_rate'), 3)} | "
                     f"{f(s.get('auc'), 3)} | {f(s.get('brier'))} | "
                     f"{f(s.get('brier_base_rate'))} | {f(s.get('bss'))} | "
                     f"{f(s.get('mean_p'), 3)} |")
    L += ["", "(b) by strategy (calibrated OOF):", "", "| strategy | n | AUC | BSS |",
          "|---|---|---|---|"]
    L += [f"| {k} | {s['n']} | {f(s.get('auc'), 3)} | {f(s.get('bss'))} |"
          for k, s in rep["set_b_by_strategy"].items()]
    L += ["", "p* table on (b), calibrated OOF (EXPLORATORY; the freeze is P6):", "",
          "| p* | confirmed | share | mean net R | hit rate |", "|---|---|---|---|---|"]
    L += [f"| {t['p_star']} | {t['confirmed']} | {t['confirm_share']:.1%} | "
          f"{f(t['mean_net_r'], 3)} | {f(t['hit_rate'], 3)} |" for t in rep["p_star_table_b"]]
    ps = rep["p_star_proposal"]
    L += ["", f"Proposal (rule: highest mean net R with >= {MIN_CONFIRM_SHARE:.0%} and >= "
          f"{MIN_CONFIRMED} confirmed): " + (f"p* = {ps['p_star']}, confirm share "
                                              f"{ps['confirm_share']:.1%}" if ps else "none"),
          "", "Calibration plot: calibration.svg. Folds:", "",
          "| block | train | test | test (b) |", "|---|---|---|---|"]
    L += [f"| {f_['block'][0]} → {f_['block'][1]} | {f_['train']} | {f_['test']} | "
          f"{f_['test_b']} |" for f_ in rep["folds"]]
    return "\n".join(L + [""])


def save(rep: dict, X, y, w, p_raw, params: dict, replay_manifest: dict, jobs: int) -> dict:
    from sklearn.isotonic import IsotonicRegression

    from drishti.ml import train
    mid = f"m1_C{rep['cutoff']}_{_dt.datetime.now():%Y%m%dT%H%M%S}"
    d = research_dir() / "models" / mid
    d.mkdir(parents=True)
    model = train._model(params, jobs).fit(X, y, sample_weight=w)
    model.booster_.save_model(str(d / "model.txt"))
    ok = ~np.isnan(p_raw)
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(p_raw[ok], y[ok])
    (d / "isotonic.json").write_text(json.dumps({"x": iso.X_thresholds_.tolist(),
                                                 "y": iso.y_thresholds_.tolist()}))
    feats = json.dumps(list(train.FEATURES))
    (d / "features.json").write_text(feats)
    rep = rep | {"model_id": mid, "p_star_proposal": propose_p_star(rep["p_star_table_b"])}
    (d / "cv_report.json").write_text(json.dumps(rep, indent=1, default=str))
    (d / "cv_report.md").write_text(markdown(rep))
    (d / "calibration.svg").write_text(train.svg_calibration(
        rep["reliability"], f"Model #1 calibration (OOF, walk-forward isotonic), C = {rep['cutoff']}"))
    man = {"label": LABEL, "model_id": mid, "cutoff": rep["cutoff"],
           "model_sha256": sha256(d / "model.txt"),
           "isotonic_sha256": sha256(d / "isotonic.json"),
           "feature_list_sha256": hashlib.sha256(feats.encode()).hexdigest(),
           "git_sha": replay_manifest["git_sha"], "spec_sha256": replay_manifest["spec_sha256"],
           "replay_run": replay_manifest["run_id"],
           "replay_panel_sha256": replay_manifest["panel_sha256"], "params": params,
           "trained_rows": int(len(y)), "frozen": False,
           "note": "proposal only; the freeze, p* and FREEZE_M1 are P6 (Track A)"}
    (d / "manifest.json").write_text(json.dumps(man, indent=1))
    return {"model_dir": str(d), "p_star_proposal": rep["p_star_proposal"],
            "set_a": rep["set_a"]["calibrated"], "set_b": rep["set_b"]["calibrated"]}
