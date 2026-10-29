# Parallel tracks (user, 2026-10-29)

There are three Claude Code sessions, one per git worktree, working on the same repo.

| Track | Worktree | Branch | Scope |
|---|---|---|---|
| **A** | `/home/cis/windows/drishti` | `main` | P1c (1d ingest, CA + factors, master, compare1d, shadow overlap), switch prep for 2026-11-20, **all merges** |
| **B** | `/home/cis/windows/drishti-trackB` | `track-b` | P3 (features, strategies, replay/backtest, scorecards, pre-P6 power check, EXPLORATORY close-confirm variant), then P4 (labels, purged CV, LightGBM, calibration) |
| **C** | `/home/cis/windows/drishti-trackC` | `track-c` | Chart UI: timeframes, indicator toggles/panes, hover tooltip, pattern labels, trend badges, rule-based bias box ("NOT a prediction") |

## Ownership

**Track A alone owns:**
- `backend/migrations/` and every schema change, on `drishti` and `drishti_test`;
- cron, `ops/`, the systemd units, runbooks;
- the frozen spec, `SPEC_CLARIFICATIONS.md`, `PREREG_M1.md`, `FREEZE_*`, `DEVIATIONS.md`, `SWITCH_TO_B.md`;
- `.env` files, token files, the Upstox login;
- **every Upstox call** (REST or WebSocket);
- `backend/drishti/{core,broker,data,live,run,scout}`, `backend/tests/conftest.py`;
- merges to `main`.

**Track B owns** `backend/drishti/{features,strategies,replay,ml,judge}/` and their tests (`backend/tests/test_b_*.py`).
- Track B **reads Prajna only through `drishti_ro`**, plus the prod `drishti` DB read-only (role `drishti_tracks`).
- Its tests write only to `drishti_test`. Research outputs are files under `/windows/drishti_data/research/track-b/` (Parquet/JSON), never prod tables.
- **No Upstox calls, no migrations.** For a table, Track B asks Track A.
- It may use `drishti.data.bars`, `holidays`, `manual` (read functions) as they are. A change it needs there is requested from Track A.

**Track C owns** `web/` and **new read-only** API endpoints in `backend/drishti/api/chart_*.py`, mounted from `api/app.py` with a minimal include line that Track A merges.
- **No DB writes, no Upstox calls.** The role `drishti_tracks` is read-only on prod.
- Indicator and pattern code comes **only from Track B's modules** (`drishti.features.*`), so there is one implementation. Until Track B's code is merged, Track C codes against the **same interface** with stubs in `backend/drishti/api/chart_stubs.py`, and deletes the stubs when Track B lands.
- Its dev servers run on **API 8710 / web 3110**. Ports 8700/3100 are the systemd services from `main`.

**Shared, already on `main` (do not fork):**
- `drishti/features/{indicators,candles,levels}.py`. Track B extends them in place; Track C only imports them.
- `drishti/data/bars.py`.

**Nobody changes** `drishti-swing-v1`, its signals, Scout's thresholds or the forward-test plan.

## Environment per worktree

- **Venv:** each worktree has its own `backend/.venv`, because the editable install points at its own tree.
- **`backend/.env`:** each worktree has one (0600, gitignored), written by Track A:
  - `DRISHTI_DATABASE_URL` = role `drishti_tracks` on `drishti` (**read only**: `default_transaction_read_only` + SELECT-only grants);
  - `DRISHTI_TEST_DATABASE_URL` = `drishti_tracks` on `drishti_test` (tests);
  - `DRISHTI_PRAJNA_RO_URL` = `drishti_ro`;
  - `DRISHTI_DATA_DIR=/windows/drishti_data`;
  - `DRISHTI_UPSTOX_TOKEN_FILE` points at a path that does not exist, so no Upstox call can work there.
- **Migrations:** they are applied to `drishti_test` by Track A as soon as they land on `main`. Tracks merge `main` in daily.

## Merge process

1. **Each track keeps its branch current:** `git merge main` at the start of each day, and resolves conflicts in its own paths.
2. **Before asking for a merge:** the full test suite passes in the track's worktree (`cd backend && .venv/bin/python -m pytest -q`), and `npx next build` passes for Track C. The track then reports to the user what changed, the tests and the files touched.
3. **Track A merges once a day:**
   - `git merge --no-ff track-b` / `track-c` on `main`;
   - runs the **full** suite plus the web build on `main`;
   - restarts the systemd units when the API or web changed.
   - A failing merge is reverted on `main` and handed back.
4. **No force-push and no history rewrite** on `main` or a track branch.
