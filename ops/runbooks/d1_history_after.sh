#!/usr/bin/env bash
# Re-point step 3 on prod: after REPOINT_VERIFY ok, build the history union views (read-only
# views over prajna_copy + Drishti's own tables; nothing reads them until DRISHTI_HISTORY_SOURCE=copy).
set -uo pipefail
cd "$(dirname "$0")/../../backend"
OUT=/windows/drishti_data/prajna_copy
LOG=$OUT/run_$(TZ=Asia/Kolkata date +%Y%m%d).log
until grep -qE "REPOINT_VERIFY" "$LOG" 2>/dev/null; do sleep 120; done
REP=$(ls -t $OUT/repoint_verify_*.json 2>/dev/null | head -1)
if grep -q "REPOINT_VERIFY rc=0" "$LOG" && [ -n "$REP" ] && grep -q '"ok": true' "$REP"; then
  .venv/bin/python -m drishti.switch.repoint history --target drishti >> "$LOG" 2>&1
  echo "[$(TZ=Asia/Kolkata date '+%F %T IST')] HISTORY_VIEWS rc=$?" | tee -a "$LOG" >> var/logs/cron.log
else
  echo "[$(TZ=Asia/Kolkata date '+%F %T IST')] HISTORY_VIEWS skipped: re-point verify not ok" | tee -a "$LOG" >> var/logs/cron.log
fi
