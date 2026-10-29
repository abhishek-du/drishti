# Disk cleanup proposal (2026-10-29)

**Status: ANALYSIS + PROPOSAL. Nothing has been deleted, moved or truncated.**

- Everything below was measured **read only**: `df`, `du` as user `cis`, `docker system df -v`, and psql with `default_transaction_read_only=on`.
- Each tier waits for the user's approval; each Tier-3 item is approved on its own.
- For every approved delete I first print the exact command and the expected size, then run it, then report the size actually freed.

## Part 1: Analysis

### Mounts

| Mount | Device | Size | Used | Free | Use |
|---|---|---|---|---|---|
| `/` | nvme0n1p1 ext4 | 377 GB | 308 GB | **51 GB** | 86 % |
| `/windows` | nvme0n1p5 ext4 | 71 GB | 34 GB | **34 GB** | 51 % (it was 39 GB before today's archive) |
| `/boot` | nvme0n1p3 | 1.8 GB | 0.1 GB | 1.6 GB | 8 % |
| swap | nvme0n1p4 | 18.6 GB | n/a | n/a | n/a |

### Where root's 308 GB is

| Path | Size | What |
|---|---|---|
| `/var/lib/docker/volumes/prajna_prajna_pgdata` | **223 GB** | The Postgres data dir (`/var/lib/postgresql/18/docker`, 222.96 GB) |
| `/var/lib/docker` other | ~5 GB | Images 3.7 GB, build cache 2.4 GB (partly shared) |
| `/home/cis` | 72.8 GB | (table below) |
| `/var/www/html/showiq` | 9.2 GB | Another project: `backend/models` 6.97 GB |
| `/var/lib/snapd` | 6.7 GB | Snaps 5.0 GB, of which **11 disabled old revisions = 2.69 GB**; seed 1.6 GB |
| `/usr` | 7.2 GB | System |
| `/opt` | 1.2 GB | Edge, Chrome |
| `/tmp` | 1.2 GB | 0.74 GB this Claude session's scratch; 2 × 0.19 GB old headless-Chrome profiles (2026-10-19); metro-cache |
| `/var/log` | 0.27 GB | journald 176 MB, rotated logs 37 MB |
| `/var/lib/kdump` | 0.25 GB | Kernel crash-dump tooling |
| `/var/crash` | 0.06 GB | 2 crash reports (Chrome 10-23, gnome-shell 10-27) |
| `/var/cache/apt` | 0.11 GB | apt cache |

**`/home/cis` (72.8 GB):**

| Path | Size | What |
|---|---|---|
| `/home/cis/windows/auto-trade-pro` | 15.5 GB | **V1, never touch** |
| `/home/cis/windows/prajna/backend` | 14.8 GB | `var/venv-broken-py311-2026-09-22` **8.07 GB**, `var/archive` 4.13 GB (raw payloads, now also in the archive), `.venv` 1.2 GB, `var/logs` 0.67 GB, `var/stage4` 0.35 GB, `.venv-research` 0.18 GB |
| `/home/cis/.android/avd/Pixel_7.avd` | 10.2 GB | Android emulator image |
| `/home/cis/Android/Sdk` | 10.2 GB | `system-images` 8.8 GB, emulator 0.9 GB |
| `/home/cis/.cache` | 6.1 GB | Chrome 1.57, **uv 1.55**, Google/AndroidStudio 0.70, **pip 0.76**, tracker3 0.58, dotslash 0.41 |
| `/home/cis/.npm/_cacache` | **1.84 GB** | npm cache |
| `/home/cis/.config` | 1.9 GB | Chrome profile 1.1, VS Code 0.66 (`CachedExtensionVSIXs` 0.55) |
| `/home/cis/windows/{download, Learnova, realstate, project-article-viewer}` | 1.6 / 1.5 / 1.2 / 1.1 GB | Other projects (incl. `realstate/node_modules` 0.79) |
| `__pycache__` outside the Tier-4 paths | 0.78 GB | Prajna backend 0.59, Learnova 0.10, download 0.07 … |

**`/windows` (34 GB used):** all of it is `/windows/prajna_archive` (36.3 GB, sizes in GB):

| File(s) | Size | sha256 | Content |
|---|---|---|---|
| `irreplaceable_20261029/` | 4.90 | OK | Today's D1 archive (Tier 4) |
| `prajna_20261027T1854_pre_0031_research_desk.dump` | 10.12 | OK | **Full Prajna DB** as of 2026-10-27 18:54: every table including `ohlcv_bar` and all `training_*` (TOC checked; not test-restored) |
| `prajna_20261027T1625_pre_0030_strategy_lab.dump` | 10.11 | OK | Full DB, 2.5 h older |
| `prajna_20261016T1631_pre_0022_paper.dump` | 5.15 | OK | Full DB as of 10-16 |
| 13 × `prajna_202609{25,28,29}*.dump` | 6.62 (0.42–0.76 each) | all OK | Pre-migration dumps from 09-25 → 09-29 (0007 … 0015) |
| `drishti_*`, `globals_*` | ~0.01 | OK | Drishti migration backups (Tier 4) |

- There are no byte-identical duplicates: the two 09-25 "phase0 baseline" dumps have the same size but different sha256.
- There are no core dumps.
- No file over 1 GB has been unaccessed for 60+ days.

### Postgres (instance `prajna-postgres`, PG 18)

| Database | Size |
|---|---|
| `prajna` | **222.55 GB** (earlier reports said "207 GB": that was GiB) |
| `prajna_test` | 0.06 GB |
| `drishti` | 0.03 GB |
| others | < 0.01 GB |

- **WAL:** 17 segments = 0.29 GB.
- **WAL settings:** max_wal_size 1 GB, wal_keep_size 0, archive_mode off, **no replication slots**. Nothing holds WAL back.
- **Bloat:** close to zero. Prajna's tables are append-only: `n_dead_tup` = 0 on every large table, 17 k on `ingest_run`, and autovacuum is current. **VACUUM FULL would return almost nothing.**

**Top tables** (sizes in GB):

| Table | Heap | Indexes | TOAST | **Total** | Rows | Drishti needs it? (`PRAJNA_INVENTORY.md`) |
|---|---|---|---|---|---|---|
| `ohlcv_bar` | 84.72 | 33.91 | 0 | **118.66** | 233.4 M | **Yes** (D1 copy: 1d/1h/15m/1m/5m) |
| `training_feature_value` | 45.22 | 28.77 | 0 | **73.99** | 174.7 M | No (Prajna Stage 4 intraday datasets) |
| `training_label` | 5.92 | 3.61 | 0 | **9.54** | 24.6 M | No |
| `preopen_book` | 3.26 | 1.35 | 0 | 4.60 | 42.6 M | Archived (D1) |
| `preopen_tick` | 3.17 | 1.26 | 0 | 4.43 | 8.5 M | Archived (D1) |
| `raw_payload` | 0.70 | 0.18 | 3.00 | 3.88 | 0.92 M | Provenance index (files archived) |
| `feature_value` | 2.35 | 1.21 | 0 | **3.57** | 7.4 M | No (Prajna Stage 3 features-v5) |
| `ingest_run` | 1.57 | 0.09 | 0.01 | 1.67 | 0.74 M | No (ops log) |
| `training_sample` | 1.04 | 0.39 | 0 | **1.43** | 2.2 M | No |
| everything else | n/a | n/a | n/a | < 0.2 each | n/a | Small (in the D1 copy where needed) |

The `training_*` family plus `feature_value` is **≈ 88.6 GB**.
- **No view depends on them** (pg_depend checked).
- Foreign keys stay inside the family: `training_label` → `training_label_run`, `training_feature_value` / `training_session_done` → `training_dataset_run`. `feature_value` points out to `instrument` / `ingest_run`; nothing points in.
- **They are read or written by these Prajna jobs:** `e51_data.sh` (10:05), `stage3_snapshot.sh` (09:00/09:05), `model_predict.sh` (09:06), the dashboard (`dashboard/data.py`, `lab_data.py`) and `desk/dossier.py`. **All of them stop at the 2026-11-20 switch.**

### Docker

| Type | Total | Active | Reclaimable |
|---|---|---|---|
| Images | 5 (3.70 GB) | 2 (`postgres:18`, `portainer`) | 2.18 GB: three unused `mediapad-intelligent-file-intake-*` images (548 + 523 + 450 MB, 3 weeks old) |
| Containers | 2, both running | n/a | 0 |
| Volumes | 2: `prajna_prajna_pgdata` 223 GB, one portainer volume 0.26 MB | both in use | 0 |
| Build cache | 44 entries, 2.42 GB, none in use | n/a | 0.90 GB reported (the rest shared) |

---

## Part 2: Proposal

### Tier 1: SAFE (regenerable caches)

**Action:** delete. One approval covers the tier; I run the commands without sudo, and the user runs the sudo ones.

| # | Path | Size | What | Used by | Regenerates | Risk | Command |
|---|---|---|---|---|---|---|---|
| 1.1 | `~/.cache/uv` | 1.55 GB | uv package cache | Drishti/Prajna installs only | yes, on the next install | none (installs re-download; needs `--system-certs` here) | `/home/cis/.local/share/uv-bootstrap/bin/uv cache clean` |
| 1.2 | `~/.cache/pip` | 0.76 GB | pip HTTP cache | installs | yes | none | `rm -r -- /home/cis/.cache/pip` |
| 1.3 | `~/.npm/_cacache` | 1.84 GB | npm cache | installs | yes | none | `npm cache clean --force` |
| 1.4 | Docker build cache | 0.90–2.42 GB | Old build layers (mediapad builds) | nothing running | yes | none | `docker builder prune -f` |
| 1.5 | `__pycache__` outside the Tier-4 paths | 0.78 GB | Python bytecode | recompiled on import | yes | none (Prajna's 0.59 GB is done after the switch, so as not to touch a running codebase) | `find` with an explicit path list, printed before running |
| 1.6 | `/tmp/tmp7n9ekjcl`, `/tmp/tmpp4f94yog` | 0.38 GB | Old headless-Chrome profiles (2026-10-19) | nothing | n/a | none | `rm -r -- /tmp/tmp7n9ekjcl /tmp/tmpp4f94yog` |
| 1.7 | `/var/crash/*.crash` (2 files) | 0.06 GB | Crash reports | apport | n/a | none | `rm -- /var/crash/_opt_google_chrome_chrome.1000.crash /var/crash/_usr_bin_gnome-shell.1000.crash` |
| 1.8 | **sudo** disabled snap revisions (11) | **2.69 GB** | Old revisions kept for rollback: android-studio 247 (1.66), mesa-2404 1165, firefox 9057, sublime-text 245, core24 1587 … | snapd rollback only | the current revisions stay | low | User: `sudo snap remove android-studio --revision=247` (one line per revision; list in the summary) + `sudo snap set system refresh.retain=2` |
| 1.9 | **sudo** journald | ~0.13 GB | journal | n/a | n/a | none | User: `sudo journalctl --vacuum-size=50M` |
| 1.10 | **sudo** apt cache | 0.11 GB | `.deb` cache | apt | yes | none | User: `sudo apt-get clean` |
| n/a | Chrome cache 1.57 GB, `~/.cache/Google/AndroidStudio*` 0.70, tracker3 0.58, VS Code `CachedExtensionVSIXs` 0.55 | 3.4 GB | App-managed caches | running apps | yes | deleting under a running app is messy | **Not proposed:** use each app's own "clear cache" if wanted |

**Tier 1 total:**
- Without sudo: **≈ 6.2–7.7 GB**.
- With the user's sudo items: **≈ 9.1–10.6 GB**.

### Tier 2: LOW RISK (stale artifacts and logs)

| # | Path | Size | What | Used by | Regenerable | Risk | Proposed action |
|---|---|---|---|---|---|---|---|
| 2.1 | `prajna/backend/var/venv-broken-py311-2026-09-22` | **8.07 GB** | The dead virtualenv kept after the 2026-09-22 host-env loss (memory "Prajna runtime rebuilt"). **No runbook, app file or cron line references it** (grep: 0) | nothing | not needed (the live venv is `.venv`) | low (no data) | Delete: `rm -r -- /home/cis/windows/prajna/backend/var/venv-broken-py311-2026-09-22`. Not compressed: a broken venv has no value as an archive |
| 2.2 | `prajna/backend/var/logs` | 0.67 GB | Prajna logs; the big live ones are `backfill/supervisor_child.log` 143 MB and `cron.log` 137 MB | Prajna jobs (live until 11-20) | n/a | low | **Now:** zstd every log file older than 7 days and not open (`zstd --rm`), keeping 30 days uncompressed-equivalent. **After the switch:** compress all of them. Expected ≈ 0.5 GB back |
| 2.3 | **sudo** `/var/log/*.gz`, `*.1` (29 files) | 0.04 GB | Rotated system logs | n/a | n/a | none | User: `sudo find /var/log -name '*.gz' -mtime +30 -delete` |

**Tier 2 total: ≈ 8.6 GB**, on root.

### Tier 3: DATA (each item approved on its own; archived to the second location first)

**Common rule for every Tier-3 item:**
1. zstd archive (`pg_dump -Fc` for tables) → sha256;
2. copy to the **second location** → sha256 verified **at the destination**;
3. only then delete, and only after 2026-11-20 for anything a Prajna job still reads.

**The second location is still unknown** (the last message had a placeholder).

| # | Item | Size freed | What / who uses it | Archive step | Delete step | Lock / downtime |
|---|---|---|---|---|---|---|
| **3.1** | Prajna tables `training_feature_value`, `training_label`, `training_sample`, `training_session_done`, `training_sample_quarantine`, `training_split`, `training_policy_param`, `training_label_run`, `training_dataset_run`, `feature_value` | **≈ 88.6 GB on root** (DROP unlinks the files at once; no VACUUM FULL needed) | Prajna Stage 3/4 derived datasets. **Drishti does not use them:** different features, labels and horizon (INVENTORY 1.8). Read by e51_data, stage3, model_predict, the dashboard and the desk, which **all stop at the switch** | After the switch (Prajna frozen): globals dump + `pg_dump -Fc -t <those 10 tables>` (estimated 4–8 GB; the full 10-27 dump holding everything is 10 GB) **streamed straight to the second location** (/windows is too small for both), sha256 there, plus a `pg_restore -l` TOC check | `BEGIN; DROP TABLE training_feature_value, training_session_done, training_sample, training_sample_quarantine, training_split, training_policy_param, training_label, training_label_run, training_dataset_run, feature_value; COMMIT;` (children and parents in one statement) | ACCESS EXCLUSIVE on those tables only, for milliseconds; nothing reads them after the switch. Done after 11-20, never before |
| **3.2** | Old Prajna dumps on /windows: 13 × 2026-09-25/28/29 (6.62 GB) + 10-16 (5.15 GB) + 10-27 16:25 (10.11 GB) | **≈ 21.9 GB on /windows** | Rollback points for Prajna migrations 0007–0030; superseded by the newer verified full dump 10-27 18:54. Rolling those migrations back is moot once Prajna is frozen | Copy to the second location, sha256 there | `rm -- <each file and its .sha256>` (exact list printed) | none |
| **3.3** | `prajna_20261027T1854_pre_0031_research_desk.dump` | 10.1 GB on /windows | The latest full Prajna backup | Keep until the **final frozen full dump** (switch night) is at the second location and verified | then `rm` | none |
| **3.4** | The whole `prajna` database (`ohlcv_bar` 119 GB etc.) | **≈ 222 GB on root** | After the D1 copy is verified, Drishti does not read it. It stays as the archive of record (SWITCH_TO_B D1) | A full `pg_dump -Fc` of the frozen DB (~10–12 GB) to the second location, sha256 there + a test restore somewhere with space | `DROP DATABASE prajna;` | Only after P6, or when the user decides. **Not proposed now** |
| 3.5 | `/var/www/html/showiq/backend/models` | 6.97 GB | **Another project** (showiq), unknown owner | The user decides | n/a | n/a |
| 3.6 | Android: `~/.android/avd/Pixel_7.avd` 10.2 GB, `~/Android/Sdk/system-images` 8.8 GB | up to 19 GB | The user's Android emulator; re-downloadable through the SDK manager | not needed (regenerable) | The user decides | n/a |
| 3.7 | Docker images `mediapad-intelligent-file-intake-{backend, frontend, goengine}` | 1.52 GB | Another project, no container, 3 weeks old | not needed if its source can rebuild them | `docker image rm <the 3 ids>` | none |
| 3.8 | Other projects' `node_modules` / builds (realstate 0.79 GB …) | ~1–2 GB | The user's projects | regenerable with `npm install` | The user decides | n/a |

**Not proposed:** shrinking `ohlcv_bar` partly (for example dropping the 5m rows, ~28 GB). The space only comes back through `VACUUM FULL`, which rewrites the whole 119 GB table, needs about that much free space and holds an exclusive lock for hours. Item 3.4 (dropping the whole DB later) is the clean path.

### Tier 4: NEVER TOUCH

- `/home/cis/windows/auto-trade-pro`;
- Prajna `frontend/` and `backend/app/api/`;
- `/windows/prajna_archive/irreplaceable_*` and every `drishti_*` / `globals_*` backup;
- everything in the D1 copy list (until copied and verified, then as decided);
- the live Postgres data files;
- the Drishti repo (including `web/.next` and `node_modules`, which the running web needs) and `/windows/drishti_data/`;
- `.env` files and token files;
- `/tmp/claude-1000` (this session's scratch).

---

## Totals and projected free space

| After | Freed | Root free | /windows free |
|---|---|---|---|
| Now | n/a | 51 GB | 34 GB |
| Tier 1 (my part / + sudo) | 6.2–7.7 / 9.1–10.6 GB root | ~57–58 / ~60–61 GB | 34 GB |
| + Tier 2 | +8.6 GB root | **~66–70 GB** | 34 GB |
| + Tier 3.2 (old dumps, after copying them out) | +21.9 GB /windows | ~66–70 GB | **~56 GB** |
| + Tier 3.1 (training tables, after the switch) | +88.6 GB root | **~155–159 GB** | ~56 GB |
| + Tier 3.3 (after the final dump is out) | +10.1 GB /windows | ~155–159 GB | **~66 GB** |
| (3.5–3.8, the user's projects) | up to +30 GB root | up to ~185 GB | n/a |

**Drishti's needs:**
- the D1 copy: ~3–5 GB on /windows + ~1.5 GB on root;
- 60-day ticks: ~60 GB, with a 10 GB guard.

After Tiers 1–2 plus 3.2 and 3.3, /windows has ~66 GB free, which is just enough for the copy and the ticks. After 3.1, root has ~155 GB free, which is roomier; **I recommend putting the tick store on root** (for example `/home/cis/drishti_ticks`) once 3.1 is done. Either needs the user's OK when ticks are enabled.

## Approvals needed

| Approval | Covers |
|---|---|
| **Tier 1** | Items 1.1–1.7 run by me; 1.8–1.10 run by the user (sudo) |
| **Tier 2** | 2.1, 2.2 now; 2.3 by the user |
| **Tier 3, per item** | 3.1 (after 11-20), 3.2, 3.3 (after the final dump), 3.5–3.8 (the user's own projects). **3.1–3.3 also need the second-location path** |

---

## Done 2026-10-29 (user approval: Tier 1 items 1.1–1.7, Tier 2 items 2.1 and 2.2 partly)

**Free space before:** root 53.95 GB, /windows 35.98 GB (decimal GB; `df -h` showed 51G/34G in GiB).

| # | Command | Expected | **Freed on root** | Note |
|---|---|---|---|---|
| 1.1 | `uv cache clean` | 1.55 GB | **0.06 GB** | The cache directory is gone, but uv **hardlinks** cached files into the venvs, so blocks shared with Drishti's and Prajna's `.venv` stay (both venvs checked: they import fine) |
| 1.2 | `rm -r -- /home/cis/.cache/pip` | 0.76 GB | **0.77 GB** | n/a |
| 1.3 | `npm cache clean --force` | 1.85 GB | **1.88 GB** | n/a |
| 1.4 | `docker builder prune -f` | 0.9–2.4 GB | **0.90 GB** | n/a |
| 1.5 | `xargs -0 rm -r --` over the printed list of 1,071 `__pycache__` dirs (Learnova, "download 22-09-26", cars, my-workbuddy, goengine; **Prajna, prajna-lab, Drishti, auto-trade-pro excluded**) | 0.17 GB | **0.20 GB** | Prajna's 0.59 GB follows after the switch |
| 1.6 | `rm -r -- /tmp/tmp7n9ekjcl /tmp/tmpp4f94yog` | 0.38 GB | **0 GB on disk** | `/tmp` is tmpfs: 0.38 GB of RAM returned |
| 1.7 | `rm --` of the 2 `/var/crash/*.crash` files | 0.06 GB | **0.06 GB** | n/a |
| 2.1 | `rm -r -- /home/cis/windows/prajna/backend/var/venv-broken-py311-2026-09-22` | 8.07 GB | **8.32 GB** | n/a |
| 2.2 (part) | `zstd -q --rm -19` on 414 Prajna log files older than 7 days and not open (`.log/.err/.out/.txt/.md`) | ~0.30 GB in | **0.27 GB** | `zstd -t` sample OK. **The 770 JSON outputs are left until after the switch** (Prajna code reads some `var/logs/daily` reports) |
| **Total** | n/a | n/a | **12.46 GB** | /windows unchanged |

**Free space after:** root **62 GB** (83 %), /windows 34 GB (51 %). Sanity checks: Drishti and Prajna venvs import, Drishti web 200.

**Still open:**
- **The user's (sudo):**
  - 1.8: the 11 disabled snaps, ~2.69 GB;
  - 1.9: journald, ~0.13 GB;
  - 1.10: apt, ~0.11 GB;
  - 2.3: rotated `/var/log`, ~0.04 GB.
- **After the 2026-11-20 switch:**
  - Prajna `__pycache__` (0.59 GB) and the rest of the Prajna logs including JSON (~0.35 GB);
  - 3.1 training tables, only after the 10-table dump is verified at the second location (sha256 + `pg_restore -l`).
- **When the second location exists:**
  - 3.2: old dumps;
  - 3.3: the 10-27 full dump, after the final frozen full dump is verified there.
- **Not touched:** 3.4 (revisit after P6) and **3.5 showiq (never)**. 3.6–3.8 are the user's own decisions.
- **Ticks:** the planned store is `/home/cis/drishti_ticks` on root, after 3.1. The user approves it when ticks are enabled.
