#!/usr/bin/env bash
# After D1 copy #1 finishes (D1COPY_DONE in its log), run the re-point verify on prod.
set -uo pipefail
cd "$(dirname "$0")/../../backend"
OUT=/windows/drishti_data/prajna_copy
LOG=$OUT/run_$(TZ=Asia/Kolkata date +%Y%m%d).log
until grep -qE "D1COPY_(DONE|FAIL)" "$LOG" 2>/dev/null; do sleep 120; done
if grep -q "D1COPY_DONE rc=0 verify_ok=True" "$LOG"; then
  .venv/bin/python -m drishti.switch.repoint verify --target drishti --out "$OUT" --n 50 >> "$LOG" 2>&1
  echo "[$(TZ=Asia/Kolkata date '+%F %T IST')] REPOINT_VERIFY rc=$? (report: $(ls -t $OUT/repoint_verify_*.json 2>/dev/null | head -1))" | tee -a "$LOG" >> var/logs/cron.log
else
  echo "[$(TZ=Asia/Kolkata date '+%F %T IST')] REPOINT_VERIFY skipped: the copy did not finish OK" | tee -a "$LOG" >> var/logs/cron.log
fi
