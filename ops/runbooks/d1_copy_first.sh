#!/usr/bin/env bash
# D1 copy #1 into prod (user 2026-11-12: "D1 copy aur re-point aaj raat shuru karo").
# Waits until Prajna's candle jobs are idle and it is >= 20:00 IST, takes a globals + drishti
# backup with sha256 (rule: backup before any DB change), then runs the full copy with its
# verification (count / OHLCV sums / md5 hash per month, every table). Read-only on Prajna.
set -uo pipefail
cd "$(dirname "$0")/../../backend"
OUT=/windows/drishti_data/prajna_copy
LOG=$OUT/run_$(TZ=Asia/Kolkata date +%Y%m%d).log
say(){ echo "[$(TZ=Asia/Kolkata date '+%F %T IST')] $*" | tee -a "$LOG" >> var/logs/cron.log; }
say "D1COPY waiting (Prajna candle jobs idle and >= 20:00 IST)"
while pgrep -f "ingest candles|close_then_backfill|prajna_catchup|daily.sh (morning|close)" >/dev/null \
      || [ "$(TZ=Asia/Kolkata date +%H%M)" -lt 2000 ]; do sleep 60; done
S=$(TZ=Asia/Kolkata date +%Y%m%dT%H%M); A=/windows/prajna_archive
docker exec prajna-postgres sh -c 'pg_dumpall -U "$POSTGRES_USER" --globals-only' > $A/globals_${S}_pre_d1copy.sql \
 && docker exec prajna-postgres sh -c 'pg_dump -U "$POSTGRES_USER" -Fc drishti' > $A/drishti_${S}_pre_d1copy.dump \
 && (cd $A && sha256sum globals_${S}_pre_d1copy.sql drishti_${S}_pre_d1copy.dump > pre_d1copy_${S}.sha256 \
     && sha256sum -c pre_d1copy_${S}.sha256) >> "$LOG" 2>&1
if [ $? -ne 0 ]; then say "D1COPY_FAIL backup failed; nothing copied"; exit 1; fi
say "D1COPY backup OK ($S); copy starts"
.venv/bin/python -m drishti.switch.d1copy copy --target drishti --out "$OUT" >> "$LOG" 2>&1
rc=$?
ok=$(ls -t $OUT/verify_*.json 2>/dev/null | head -1 | xargs -r python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['ok'])")
say "D1COPY_DONE rc=$rc verify_ok=$ok (report: $(ls -t $OUT/verify_*.json 2>/dev/null | head -1))"
