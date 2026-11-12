# Switch checklist: Fri 2026-11-20 (Option B: Prajna stops, Drishti owns Upstox)

Every step is one command of `ops/switch/switch.sh <step>`. It logs `SWITCH_<STEP>_OK` or `_FAIL` to `backend/var/logs/cron.log` and to `/windows/prajna_archive/final_<date>/switch.log`, and it never prints a secret.

**Who does what:**
- **A** runs the script steps.
- **U** installs the crontab, revokes keys and does anything that needs sudo.

**Rehearsal:** `REHEARSE=1 SWITCH_SCRATCH=<dir> CRON_DIR=<dir> ops/switch/switch.sh <step>` runs the same steps on `drishti_test`, a scratch `.env` and a dump of `drishti_test`.

## Before the day

| # | Item | Who | Check |
|---|---|---|---|
| P1 | The Drishti app's Upstox keys are in `backend/.env`: `DRISHTI_UPSTOX_API_KEY`, `_API_SECRET`, `_REDIRECT_URL`, `_USERNAME`, `_PIN`, `_TOTP_SECRET` | U | `flip status` shows every field as `set` |
| P2 | One test login on a weekend (with no session to disturb). Set `LOGIN_ENABLED=true` and the own token file in a temporary env, run `drishti_job.sh login`, then put the flags back | A | `DRISHTI_LOGIN_OK MINTED` |
| P3 | D1 copy #1 + verify + re-point verify + history views (11-12 night) | A | `D1COPY_DONE verify_ok=True`, `REPOINT_VERIFY rc=0`, `HISTORY_VIEWS rc=0` |
| P4 | Union check: `repoint history --target drishti --cutover <a date ~2 weeks back>`, then `repoint verify`. Drishti's own bars after that date must give the same adjusted series as Prajna. Then `repoint history` again, which puts the cutover back to the copy end | A | `ok: true` |
| P5 | Shadow days: Scout + EOD scan on both sources, compared | A | no unexplained difference |
| P6 | Board-meetings CSV Mon 11-16 and Thu 11-19 | U | `manual` job OK, not STALE |
| P7 | Second archive location decided (an external disk or an rclone crypt remote) | U | path known |
| P8 | Rehearsal of every step below | A | `REHEARSE_*_OK` for every step |

## Switch night: Fri 11-20

| # | Time | Step | Who | Done when |
|---|---|---|---|---|
| S1 | 19:00 | `switch.sh preflight`: clean git tree, credentials set, /windows ≥ 30 GB free, the last copy verify OK, the history views present | A | `SWITCH_PREFLIGHT_OK` |
| S2 | 19:05 | `switch.sh cron-diff`: writes `~/crontab_B.txt` (all 52 Prajna lines commented `# B-STOP`, the Drishti block replaced by `ops/cron_after_switch.txt`) and `~/crontab_B.diff` | A | `SWITCH_CRON-DIFF_OK` |
| S3 | after Prajna's close job ends (~19:30) | Review the diff, then `crontab ~/crontab_B.txt`. Stop Prajna's dashboard: `cd /home/cis/windows/prajna/backend && ops/runbooks/dashboard.sh stop` | U | `crontab -l` has no active Prajna line |
| S4 | ~19:45 | `switch.sh final-dump`: it refuses while a Prajna cron line is active and waits for Prajna jobs to go idle. It writes the globals, exact row counts per table, `pg_dump -Fc -Z6 prajna` (~12–15 GB estimated), `pg_restore -l`, the raw-payload tar (`zstd -t`) and a sha256 MANIFEST that is checked | A | `SWITCH_FINAL-DUMP_OK`. **Prajna is frozen from here.** |
| S5 | ~21:00 | `switch.sh delta`: delta copy (fetched after copy #1) + per-month verify + re-point verify (50 random + every CA stock) + history views rebuilt. The cutover moves to 11-20 | A | `SWITCH_DELTA_OK` |
| S6 | ~21:30 | `switch.sh flip`: backs up `.env` to `.env.pre_switch_<stamp>`, then sets `DRISHTI_HISTORY_SOURCE=copy`, `DRISHTI_UPSTOX_LOGIN_ENABLED=true` and the own token file. It refuses without credentials or when already flipped | A | `SWITCH_FLIP_OK` |
| S7 | ~21:31 | `switch.sh restart`: drishti-api restarts and `/api/health` answers | A | `SWITCH_RESTART_OK` |
| S8 | ~21:40 | Copy the final archive to the second location; check the sha256 there | A + U | `sha256sum -c` OK there |
| S9 | ~22:00 | Postgres `/dev/shm` 64 MB → 1 GB (`shm_size: 1g`, recreate the container; Prajna is stopped, so this is safe) | U | `docker inspect` ShmSize = 1 GB |
| S10 | 11-20 | Protocol-deviation notes into Prajna's P1/P2/E5/E6 docs (text in SWITCH_TO_B 6.3); update the memory notes | A | committed in Prajna (docs only) |

## After the switch

| # | When | Step | Done when |
|---|---|---|---|
| A1 | Sat 11-21 06:10 | Drishti's first own login (cron) | `DRISHTI_LOGIN_OK` |
| A2 | Sat 06:30 | Official 1d of 11-20 (daily1d); CA at 23:00; news every 30 min | `_OK` lines |
| A3 | Sat ~08:00 | `switch.sh after`: own token file, last login OK, own `bar_1d` max date, copy-mode `bars.official` reads | `SWITCH_AFTER_OK` |
| A4 | Mon 11-23 | First Drishti-only session: SWITCH_TO_B 6.4 (no Prajna process or login; Prajna DB unchanged; both WebSocket connections free) | all lines OK |
| A5 | Mon 11-23 | Paper portfolio forward start (pf-v1, paper only) + `FREEZE_PF.md` | committed with the code sha |
| A6 | after A4 passes | The user revokes Prajna's Upstox app keys | done |

## Rollback

| Situation | Action |
|---|---|
| S4 or S5 fails | Do not flip. Prajna's data is unchanged. Re-install `~/crontab_before_B.txt` (U) and move the switch one week |
| After the flip, reads are wrong or the API fails | `switch.sh rollback`: `.env` is restored from the newest `.env.pre_switch_*` and the API restarts. U: `crontab ~/crontab_before_B.txt`. Prajna resumes from its own data (frozen only since S4) |
| The first own login fails (S+1) | Saturday has no session: fix the credentials and re-run `drishti_job.sh login` (the guard allows one attempt a day: delete that day's `var/run/upstox_login_<date>.json` only after the cause is fixed). The fallback is a manual token from the official authorize dialog |

## Rehearsal log (2026-11-12, drishti_test)

| Step | Result |
|---|---|
| cron-diff | OK. 52 Prajna lines commented, 18 Drishti lines from `cron_after_switch.txt`, 0 active Prajna lines left |
| flip | OK. Values not printed; backup 0600 |
| final-dump | OK. `drishti_test` 113 MB, 100 TABLE DATA, 100 tables counted, MANIFEST sha256 checked |
| delta | **First run FAILED** (`cannot drop table prajna_copy.ca_factor because other objects depend on it`). Fixed: the swap drops with cascade, and the copy rebuilds the prajna_copy and history views at the end. Re-run: copy verify OK; re-point verify 1 key differs (INE0CJD01037, split 11-06), explained below |
| restart | rehearsal skips it |
| rollback | OK. A second finding: the newest backup was picked by mtime (`copy2` keeps the old mtime). Fixed to pick by stamp, and a second `flip apply` is now refused |
| preflight | FAIL on the dirty git tree only (the code was uncommitted at the time) |

**Why INE0CJD01037 differs:** the rehearsal copy holds only 2026-09. One Upstox payload carries that stock's September-to-November bars. The observation saying "the vendor served this payload raw" is on the November bars, which are not in that copy. Prod copies every month, so this cannot happen there; tonight's full re-point verify covers every CA stock.
