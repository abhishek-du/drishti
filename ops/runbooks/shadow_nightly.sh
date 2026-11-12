#!/usr/bin/env bash
# Shadow days 2026-11-13 .. 11-19 (PLAN_TO_2026-11-20): after Prajna's close job, a delta copy
# into prod's copy schema (+ its verify; the history views are rebuilt by the copy), then Scout
# and the EOD 1d scan on both sources for today's session (drishti.switch.shadow, read only).
# Logs SHADOW_DELTA / SHADOW_COMPARE to cron.log. Does nothing after 2026-11-19 or before copy #1.
set -uo pipefail
cd "$(dirname "$0")/../../backend"
OUT=/windows/drishti_data/prajna_copy
say(){ echo "[$(TZ=Asia/Kolkata date '+%F %T IST')] $*" >> var/logs/cron.log; }
[ "$(TZ=Asia/Kolkata date +%Y%m%d)" -le 20261119 ] || exit 0
[ -f $OUT/copy_state.json ] || { say "SHADOW_SKIP no copy #1 yet"; exit 0; }
exec 9> var/run/shadow.lock; flock -n 9 || exit 0
while pgrep -f "ingest candles|close_then_backfill|prajna_catchup|daily.sh (morning|close)|d1copy" >/dev/null; do sleep 60; done
D=$(TZ=Asia/Kolkata date +%F)
.venv/bin/python -m drishti.switch.d1copy copy --target drishti --out $OUT --delta > var/logs/shadow_delta_$D.json 2>&1
rc=$?
ok=$(ls -t $OUT/verify_*.json | head -1 | xargs -r python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['ok'])")
say "SHADOW_DELTA rc=$rc verify_ok=$ok"
[ "$ok" = True ] || exit 1
.venv/bin/python -m drishti.switch.shadow compare --day "$D" > var/logs/shadow_$D.json 2>&1
rc=$?
say "SHADOW_COMPARE rc=$rc $(tr -d '\n ' < var/logs/shadow_$D.json | cut -c1-220)"
