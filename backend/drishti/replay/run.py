"""P3 daily replay of drishti-swing-v1 on official adjusted bars (SC3: backtests use official
bars), 2019 -> cut-off C. SURVIVORSHIP-BIASED, EXPLORATORY. Files only, no DB writes.

    .venv/bin/python -m drishti.replay.run --data-end 2026-10-28 [--cutoff 2026-10-21] [--draft]
        [--jobs 30] [--refresh]

--cutoff defaults to the last session whose label window T+1..T+5 closes by --data-end. No row
with T > C is kept, and the replay reads no bar after --data-end.
Writes /windows/drishti_data/research/track-b/replay/<run_id>/: panel.parquet (set (a) rows =
every eligible stock-day; set (b) = rows with signal), scorecard.json/.md, power.json/.md,
manifest.json.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import subprocess
import sys
import time

import pandas as pd

from drishti.features import f1, indicators
from drishti.judge import power, scorecard
from drishti.replay import load

def git_sha() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                          cwd=load.Path(__file__).parent).stdout.strip()


def calendar(b: pd.DataFrame) -> list:
    n = b.groupby("session_date").size()
    return sorted(n[n >= 100].index)


def build(inp: dict, cutoff: _dt.date, jobs: int) -> pd.DataFrame:
    b = inp["bars"].sort_values(["instrument_key", "session_date"]).reset_index(drop=True)
    reg = indicators.regime(inp["index"])
    rows = f1.single_stock(b, reg.vix_spike.to_dict(), cutoff, jobs)
    print(f"single-stock rows {len(rows)}", file=sys.stderr, flush=True)
    panel = f1.cross_section(rows, reg, set(inp["eq"]["isin"]), inp["results"],
                             dict(zip(inp["sectors"].instrument_key, inp["sectors"].sector)),
                             calendar(b))
    return panel


async def main(args: list[str]) -> dict:
    opt = lambda k, d=None: args[args.index(k) + 1] if k in args else d  # noqa: E731
    data_end = _dt.date.fromisoformat(opt("--data-end"))
    inp = await load.cached(data_end, refresh="--refresh" in args)
    cal = calendar(inp["bars"])
    cutoff = _dt.date.fromisoformat(opt("--cutoff")) if "--cutoff" in args else cal[-6]
    assert cal.index(cutoff) + 5 <= len(cal) - 1, "label windows of C must close by data_end"
    t0 = time.time()
    panel = build(inp, cutoff, int(opt("--jobs", 30)))
    panel = panel[panel.eligible & (panel.session_date <= cutoff)].reset_index(drop=True)
    reg = indicators.regime(inp["index"])
    draft = "--draft" in args
    run_id = f"{'draft_' if draft else ''}C{cutoff}_{_dt.datetime.now():%Y%m%dT%H%M%S}"
    out = load.research_dir() / "replay" / run_id
    out.mkdir(parents=True)
    panel.to_parquet(out / "panel.parquet", index=False)
    sc = scorecard.build(panel)
    pw = power.build(panel, reg, cal, cutoff)
    repo = load.Path(__file__).resolve().parents[3]
    manifest = {"label": load.LABEL, "draft": draft, "run_id": run_id, "cutoff": str(cutoff),
                "data_end": str(data_end), "git_sha": git_sha(),
                "spec_sha256": load.sha256(repo / "docs" / "SPEC_drishti-swing-v1.md"),
                "seed": f1.SEED, "inputs": inp["meta"], "rows_set_a": int(len(panel)),
                "events_set_b": int(panel.signal.sum()),
                "first_eligible": str(panel.session_date.min()),
                "panel_sha256": load.sha256(out / "panel.parquet"),
                "seconds": round(time.time() - t0, 1),
                "notes": ["results-gate proxy: HISTORICAL_FILING dates assumed known at T",
                          "EQ series and sector = current lists; ASM/GSM not applied",
                          "vendor corporate-action adjustment accepted for old rows",
                          "S1b day B = S1a rule row + RS_top (no common gates on B)",
                          "news features not loaded (NaN, allowed missing)",
                          "a (b) event = a stock-day with >= 1 signal; one order per stock-day"]}
    for name, obj in (("scorecard", sc), ("power", pw), ("manifest", manifest)):
        (out / f"{name}.json").write_text(json.dumps(obj, indent=1, default=str))
    (out / "scorecard.md").write_text(scorecard.markdown(sc, manifest))
    (out / "power.md").write_text(power.markdown(pw, manifest))
    return {"out": str(out), "flag": pw["flag"], "rows_a": manifest["rows_set_a"],
            "events_b": manifest["events_set_b"], "seconds": manifest["seconds"]}


if __name__ == "__main__":
    print(json.dumps(asyncio.run(main(sys.argv[1:])), indent=1, default=str))
