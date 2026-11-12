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

## D3 (2026-10-29): no full Prajna backup until the switch-night final dump

**Rule affected:** keep a verified backup before any change. Prajna's own rule is "Back up before any production migration".

**What happened.** By explicit user decision on 2026-10-29 (DISK_CLEANUP 3.2 + 3.3, without the second location), all 15 old Prajna dumps in `/windows/prajna_archive` and their `.sha256` files were deleted: **30 files, 31.374 GB freed on /windows**. These were:
- 13 pre-migration dumps from 2026-09-25/28/29 (0007–0015);
- the 10-16 dump;
- the 10-27 16:25 and **10-27 18:54 full dumps**.

The user accepted in writing that **there is no full backup of the `prajna` database** from now until the final frozen dump on the switch night.

**What still exists:**
- `irreplaceable_20261029/`: CA, basis/observations, news + news AI + results events, pre-open recordings, raw vendor payloads. All 6 sha256 checks re-verified OK after the delete.
- The Drishti and globals backups.

**What is lost if the `prajna` volume fails before the switch:**
- the bars (re-fetchable from Upstox, slowly);
- the training/feature tables (Prajna-derived, regenerable only by re-running Prajna);
- the experiment outputs.

**Binding from now on:**
1. **No change of any kind to the `prajna` database before the switch-night final dump.** That covers migrations, DROPs (DISK_CLEANUP 3.1), VACUUM FULL and role or setting changes. Prajna's own running jobs continue as before.
2. **The final full `pg_dump -Fc` of the frozen `prajna` database is mandatory on the switch night (2026-11-20).**
   - It comes after the cron diff, before the delta copy's verification is signed off, and before 3.1 or any other Prajna change.
   - It gets a sha256 and a `pg_restore -l` TOC check. A test restore into a scratch database is also done if space allows.
   - It is stored on /windows (63 GB free after this delete; the 10-27 full dump was 10.1 GB) and on the second location once that exists.

## D4 (2026-11-03): an editable table and a write endpoint, for the user's journal

**Rule affected:** Drishti tables are append-only (trigger `drishti_append_only`), and the API is read only.

**What changed (user request 2026-11-03, the decision-support scanner):**
- `journal_entry` (migration 0008) has no append-only trigger: the user edits status (watching / took / skipped), actual entry/qty and notes.
- `POST /api/journal/{alert_id}` is the only write endpoint of the API.

**Safeguards:**
- Every change is also appended to `journal_event` (append-only), so the edit history is complete.
- The grades (`journal_outcome`) and the alerts (`scan_alert`) stay append-only; a changed actual entry gets a new `took` grade, and the latest row wins.
- Backups before the migration: `globals_20261103T1205_pre_drishti_0008.sql` and `drishti_20261103T1205_pre_0008.dump` in `/windows/prajna_archive` (sha256 verified).
- PAPER ONLY: nothing in the scanner or the journal places, changes or cancels an order.

## D5 (2026-11-04): intraday scanner alerts use unadjusted 15m bars (known caveat, user-acknowledged)

**Rule affected:** prices must be split/bonus-adjusted point in time (spec; `bars.official` applies CA factors).

**What is true now:**
- `scan_intraday` builds its 15m bars (last ~25 days) from Prajna's stored `ohlcv_bar` 15m rows plus the recorder's 1m bars.
- **Neither is CA-adjusted.** For a stock with a split or bonus ex-date inside that window, the bars before the ex-date are on the old price scale.
- So that stock's indicators (EMA20, SMA50/200, ATR, the 250-bar high) and its support/resistance zones, and with them R1/R2, the stop and the RR, **can be wrong**.
- This is a level error, not lookahead: every bar used is still at or before the alert's bar.
- EOD (1d) alerts are not affected; they use the adjusted official bars.

**User instruction (2026-11-04):** do not rely on intraday alerts for split- or bonus-affected stocks until this is fixed.

**Fix:**
- The 15m bars must use adjusted data: CA factors applied in `scanner.run.bars15`, as `bars.official` does for 1d.
- **The 11-20 switch alone does not fix it.** After the switch the 15m bars come from Drishti's nightly intraday Parquet plus the recorder, which are also unadjusted.
- The fix belongs in the re-point work (P1c item 4): the intraday read path applies Drishti's own CA factors.
- Until then an alert could carry a `ca_in_window` flag; this is not built yet.

## D6 (2026-11-04): Telegram delivery: an editable status column, Prajna's Telegram off, keys from V1's .env

**What changed (user requests of 2026-11-04):**

1. **`notify_event`** (migration 0012) is not append-only.
   - Each message is claimed (inserted) before it is sent, so a re-run never repeats it.
   - Its `status` / `attempts` / `sent_at` / `error` are updated after the send.
   - The message `text` is never changed.

2. **Prajna's Telegram messages are off** (user: "baaki jo prajna ke alert jaa rahe hai telegram par unko band krdo").
   - In `prajna/backend/.env` the two lines `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` are commented out with the prefix `#DISABLED_2026-11-04_user#`.
   - Every Prajna message goes through `ops/runbooks/telegram_notify.sh`, which reads `.env` on every call and logs `TELEGRAM_SKIPPED` without them (verified).
   - No Prajna code, cron or DB change was made; the Prajna git tree is clean (`.env` is gitignored).
   - **To undo:** remove the prefix.

3. **Drishti's `DRISHTI_TELEGRAM_BOT_TOKEN` / `DRISHTI_TELEGRAM_CHAT_ID`** were copied into `drishti/backend/.env` (0600, gitignored) from `auto-trade-pro/autotrade-backend/.env`, on the user's explicit instruction.
   - This was the only access to that directory. The file was read only, never written.
   - No value was printed; the copies were checked by a sha256 prefix.
   - It is the same bot and chat Prajna used.

4. **Tests can never send:** `tests/conftest.py` forces both variables empty.

## D7 (2026-11-05): paper portfolio, mutable snapshot and positions

**Rule affected:** Drishti tables are append-only.

**What changed (user decision 2026-11-05, `PREREG_PAPER_PORTFOLIO.md`):**
- `pf_portfolio` (the live snapshot: cash, holdings value, equity) and `pf_position` (open positions: qty, average price, last price, unrealised P&L, stop, target) are updated in place. They are the only mutable state of the paper portfolio.

**Safeguards:**
- Every position change is also written to **`pf_position_event`** (append-only: open / add / reduce / close / mark, with before/after qty and price and the fill or mark that caused it). The positions can therefore be rebuilt from the log.
- `pf_order`, `pf_fill`, `pf_trade`, `pf_equity` and `pf_decision` are append-only (trigger `drishti_append_only`).
- A paper order is decided and filled (or rejected) in one step from known bars, so each `pf_order` row is written once, with its final status. It is never updated.
- **PAPER ONLY:** the paper trader writes only `pf_*` tables; no code path places a real order.

**D5 closed 2026-11-12:**
- Intraday history is now split/bonus adjusted point in time: `bars.intraday` → `bars.adjust`, the same rule as the daily bars.
- It is used by the chart's 15m/1h/1m history and the scanner's `bars15`.
- Checked on a 1:2 split (ex 2026-11-04): the raw/adjusted ratio is exactly 2.0 before the ex-date and 1.0 after it.
- Today's recorder bars need no adjustment.
