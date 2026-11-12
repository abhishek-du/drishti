#!/usr/bin/env bash
# Switch to Option B (2026-11-20): one step at a time, in the order of docs/SWITCH_CHECKLIST.md.
#   ops/switch/switch.sh preflight | cron-diff | final-dump | delta | flip | restart | after | rollback
# REHEARSE=1: the same steps on drishti_test / a scratch .env / a dump of drishti_test (Prajna,
# prod and the real .env are not touched; nothing is restarted). Every step logs SWITCH_<STEP>_OK
# or _FAIL to backend/var/logs/cron.log and to the switch log. Never prints a secret.
# PAPER ONLY. The user installs the crontab and runs sudo; this script never does either.
set -uo pipefail
umask 077                                  # archives, logs and .env backups are 0600
STEP="${1:?step}"
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
B=$ROOT/backend; cd "$B"
PY=.venv/bin/python
R=${REHEARSE:-0}
if [ "$R" = 1 ]; then
  TARGET=drishti_test; OUT=/windows/drishti_data/prajna_copy_dryrun; MONTHS="--months 2026-09"
  TFS="--tf 1d"; SRC_DB=drishti_test; ARCH=${SWITCH_SCRATCH:?set SWITCH_SCRATCH for a rehearsal}
  ENVF=$ARCH/rehearse.env; TAG=REHEARSE
else
  TARGET=drishti; OUT=/windows/drishti_data/prajna_copy; MONTHS=""; TFS=""
  SRC_DB=prajna; ARCH=/windows/prajna_archive/final_$(TZ=Asia/Kolkata date +%Y%m%d); ENVF=$B/.env; TAG=SWITCH
fi
mkdir -p "$ARCH" var/logs
SLOG=$ARCH/switch.log
say(){ echo "[$(TZ=Asia/Kolkata date '+%F %T IST')] ${TAG}_${STEP^^}_$*" | tee -a "$SLOG" >> var/logs/cron.log; }
fail(){ say "FAIL $*"; exit 1; }
psqlq(){ docker exec -i prajna-postgres sh -c "psql -U \"\$POSTGRES_USER\" -d $1 -At -v ON_ERROR_STOP=1"; }
prajna_busy(){ pgrep -f "ingest candles|close_then_backfill|prajna_catchup|daily.sh (morning|close)|app\.cli" >/dev/null; }

case "$STEP" in
preflight)
  ok=1
  [ -z "$(git -C "$ROOT" status --porcelain)" ] || { echo "drishti git tree not clean"; ok=0; }
  st=$($PY -m drishti.switch.flip status --env "$ENVF") || fail "flip status"
  echo "$st" | grep -q MISSING && { echo "login credentials missing in $ENVF (names only):"; echo "$st" | grep MISSING; ok=0; }
  free=$(df -BG --output=avail /windows | tail -1 | tr -dc 0-9)
  [ "$free" -ge 30 ] || { echo "/windows free ${free} GB < 30 GB (final dump ~15 GB + raw tar ~4 GB)"; ok=0; }
  [ -f "$OUT/copy_state.json" ] || { echo "no copy_state.json in $OUT"; ok=0; }
  last=$(ls -t $OUT/verify_*.json 2>/dev/null | head -1)
  [ -n "$last" ] && grep -q '"ok": true' "$last" || { echo "last copy verify not ok: $last"; ok=0; }
  echo "select count(*) from history.cutover" | psqlq "$TARGET" >/dev/null 2>&1 \
    || { echo "history views missing on $TARGET (repoint history)"; ok=0; }
  shm=$(docker inspect prajna-postgres --format '{{.HostConfig.ShmSize}}')
  echo "postgres shm: $((shm/1024/1024)) MB (raise to 1 GB after Prajna stops: SWITCH_TO_B 6.2.4.5)"
  [ $ok = 1 ] && say "OK free=${free}G" || fail "see above"
  ;;
cron-diff)
  # Prajna lines commented (SWITCH_TO_B 6.1, D2 = stop news), Drishti block replaced by
  # ops/cron_after_switch.txt. Writes files only; the user reviews and installs.
  D=${CRON_DIR:-$HOME}; crontab -l > "$D/crontab_before_B.txt" || fail "crontab -l"
  awk '
    /drishti/ { next }
    /^[[:space:]]*#/ || /^[[:space:]]*$/ || /^[A-Z_]+=/ { print; next }
    /cd \$B|prajna/ { print "# B-STOP 2026-11-20: " $0; next }
    { print }' "$D/crontab_before_B.txt" > "$D/crontab_B.txt"
  cat "$ROOT/ops/cron_after_switch.txt" >> "$D/crontab_B.txt"
  left=$(grep -vE '^\s*#|^[A-Z_]+=' "$D/crontab_B.txt" | grep -v drishti | grep -cE 'cd \$B|prajna|app\.cli' || true)
  [ "$left" = 0 ] || fail "$left active Prajna lines left in crontab_B.txt"
  diff "$D/crontab_before_B.txt" "$D/crontab_B.txt" > "$D/crontab_B.diff"
  say "OK review $D/crontab_B.diff, then the user runs: crontab $D/crontab_B.txt"
  ;;
final-dump)
  # Mandatory final dump of the frozen Prajna DB (DEVIATIONS D3) + globals + raw payloads,
  # sha256 + pg_restore -l + exact row counts per table taken from the frozen source.
  [ "$R" = 1 ] || { crontab -l | grep -vE '^\s*#|^[A-Z_]+=' | grep -v drishti | grep -qE 'cd \$B|prajna|app\.cli' \
    && fail "Prajna cron lines still active: install crontab_B.txt first"; }
  while [ "$R" != 1 ] && prajna_busy; do sleep 60; done
  S=$(TZ=Asia/Kolkata date +%Y%m%dT%H%M); F=$ARCH/${SRC_DB}_final_$S.dump
  docker exec prajna-postgres sh -c 'pg_dumpall -U "$POSTGRES_USER" --globals-only' > "$ARCH/globals_final_$S.sql" || fail "globals"
  echo "select format('%s.%s', schemaname, relname) from pg_stat_user_tables order by 1" | psqlq "$SRC_DB" \
    | while read -r t; do echo "$t|$(echo "select count(*) from $t" | psqlq "$SRC_DB")"; done > "$ARCH/${SRC_DB}_rows_$S.txt" \
    || fail "row counts"
  docker exec prajna-postgres sh -c "pg_dump -U \"\$POSTGRES_USER\" -Fc -Z 6 $SRC_DB" > "$F" || fail "pg_dump"
  docker exec -i prajna-postgres pg_restore -l < "$F" > "$F.list" || fail "pg_restore -l"
  n=$(grep -c 'TABLE DATA' "$F.list")
  if [ "$R" != 1 ]; then
    tar -C /home/cis/windows/prajna/backend/var -I 'zstd -3 -T0' -cf "$ARCH/prajna_raw_payloads_$S.tar.zst" archive \
      && zstd -tq "$ARCH/prajna_raw_payloads_$S.tar.zst" || fail "raw payload tar"
  fi
  (cd "$ARCH" && sha256sum $(ls | grep -E "_$S\.(dump|sql|txt|tar\.zst)$|\.list$") > "MANIFEST_$S.sha256" \
     && sha256sum -c --quiet "MANIFEST_$S.sha256") || fail "sha256"
  say "OK $F ($(du -h "$F" | cut -f1), $n TABLE DATA, $(wc -l < "$ARCH/${SRC_DB}_rows_$S.txt") tables counted)"
  ;;
delta)
  $PY -m drishti.switch.d1copy copy --target "$TARGET" --out "$OUT" --delta $TFS $MONTHS >> "$SLOG" 2>&1 || fail "delta copy"
  last=$(ls -t $OUT/verify_*.json | head -1); grep -q '"ok": true' "$last" || fail "delta verify not ok: $last"
  $PY -m drishti.switch.repoint verify --target "$TARGET" --out "$OUT" --n 50 $MONTHS >> "$SLOG" 2>&1 || fail "repoint verify"
  rv=$(ls -t $OUT/repoint_verify_*.json | head -1)
  if ! grep -q '"ok": true' "$rv"; then
    # a rehearsal copy holds one month only; a payload spanning months loses its later bars, so
    # a vendor-applied CA can differ there (2026-11-12: 1 key, explained). Prod copies everything.
    [ "$R" = 1 ] && echo "WARN repoint verify not ok on the partial rehearsal copy: $rv" | tee -a "$SLOG" \
      || fail "repoint verify not ok: $rv"
  fi
  h=$($PY -m drishti.switch.repoint history --target "$TARGET" | tr -d '\n ') || fail "history views"
  say "OK verify=$last repoint=$rv history=$h"
  ;;
flip)
  if [ "$R" = 1 ] && [ ! -f "$ENVF" ]; then          # a scratch .env with dummy credentials
    $PY - "$ENVF" <<'EOF'
import sys
from drishti.core.config import LOGIN_FIELDS
open(sys.argv[1], "w").write("".join(f"{k}=dummy\n" for k in LOGIN_FIELDS))
EOF
    chmod 600 "$ENVF"
  fi
  out=$($PY -m drishti.switch.flip apply --env "$ENVF") || fail "flip apply refused"
  echo "$out" | grep -q '"DRISHTI_HISTORY_SOURCE": "copy"' || fail "flag not set"
  say "OK backup=$(echo "$out" | grep -o '"backup": "[^"]*"' | cut -d'"' -f4)"
  ;;
restart)
  [ "$R" = 1 ] && { say "OK (rehearsal: no restart)"; exit 0; }
  systemctl --user restart drishti-api && sleep 5 && curl -fsS -m 10 http://127.0.0.1:8700/api/health >/dev/null \
    || fail "drishti-api not healthy after restart"
  say "OK drishti-api restarted (copy mode)"
  ;;
after)
  # S+1 checks (SWITCH_TO_B 6.4, the Drishti half): own login, own token, own 1d, copy-mode reads.
  $PY -m drishti.switch.flip status --env "$ENVF" | grep -q '"token_file": "own"' || fail "token file is not Drishti's"
  grep -E "DRISHTI_LOGIN_(OK|FAIL)" var/logs/cron.log | tail -1 | grep -q _OK || fail "last login not OK"
  echo "select max(session_date) from bar_1d" | psqlq "$TARGET"
  $PY - <<'EOF' || fail "copy-mode read"
import asyncio, datetime as dt
from drishti.core import db
from drishti.data import bars
async def go():
    async with db.prajna_sessionmaker()() as s:
        b = await bars.official(s, dt.date.today(), 5)
        print("copy-mode official 1d: rows", len(b), "last session", b.session_date.max())
asyncio.run(go())
EOF
  say "OK"
  ;;
rollback)
  bak=$(ls "$(dirname "$ENVF")"/.env.pre_switch_* 2>/dev/null | sort | tail -1)   # newest by stamp
  [ -n "$bak" ] || fail "no .env.pre_switch_* backup"
  $PY -m drishti.switch.flip rollback --env "$ENVF" --backup "$bak" >/dev/null || fail "flip rollback"
  [ "$R" = 1 ] || systemctl --user restart drishti-api
  say "OK .env restored from $bak; the user restores the crontab: crontab ~/crontab_before_B.txt"
  ;;
*) echo "unknown step $STEP"; exit 2 ;;
esac
