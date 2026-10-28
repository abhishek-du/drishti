# Deviations from the approved plan

## D1 (2026-10-28): instance change before its backup

**Plan rule:** back up and verify sha256 before any change on the `prajna-postgres` instance.

**What happened:**
- On 2026-10-28 ~19:05 IST, `drishti` (owner `drishti_app`), `drishti_test`, the role `drishti_ro` and its grants were created **without a backup first**. The user had said "backup nahi lo".
- `GRANT CONNECT ON DATABASE prajna`, `USAGE ON SCHEMA public` and `SELECT ON ALL TABLES` were given to `drishti_ro`.
- Prajna's data was not modified: no table, row or setting of Prajna's changed except the ACL entries for `drishti_ro`.

**Correction (user 2026-10-28, ~19:49 IST):** backups taken after the change, mode 0600, sha256 verified:

| File | Content |
|---|---|
| `/windows/prajna_archive/globals_20261028T1949_post_drishti_create.sql` | `pg_dumpall --globals-only` (4 roles, incl. drishti_app / drishti_ro) |
| `/windows/prajna_archive/drishti_20261028T1949_schema_only.sql` | `pg_dump -d drishti --schema-only` (8 tables, migration 0001) |

Each file has a `.sha256` next to it.

**To undo:**
1. `REVOKE ALL ON ALL TABLES IN SCHEMA public FROM drishti_ro; REVOKE USAGE ON SCHEMA public FROM drishti_ro; REVOKE CONNECT ON DATABASE prajna FROM drishti_ro;` (in prajna)
2. `DROP DATABASE drishti; DROP DATABASE drishti_test; DROP ROLE drishti_ro; DROP ROLE drishti_app;`

**From now on:** every change on the instance is preceded by a globals dump plus a dump of the affected database, with sha256.

## D2 (2026-10-28): ASM/GSM staleness threshold

- The spec says `ASM_STALE` applies after 7 sessions.
- The user asked for a warning when the newest ASM/GSM file is older than **7 calendar days** (`drishti/data/manual.py: surveillance_freshness`).
- This is stricter, and it is an ops warning only: no trading rule changes, so the spec is unchanged.
- Scout records the freshness dict (file dates, age, status) in every watchlist run, and sends a Telegram warning once P5 exists.
