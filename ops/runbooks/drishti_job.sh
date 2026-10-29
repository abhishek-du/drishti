#!/usr/bin/env bash
# Drishti job wrapper for cron: one job at a time per name, a log line at start and end.
#   ops/runbooks/drishti_job.sh holidays|indices|manual|eod|barcheck|scout
# PAPER ONLY. Read-only market data. Drishti never logs in to Upstox (decision A).
set -uo pipefail
JOB="${1:?job}"
cd "$(dirname "$0")/../../backend"
mkdir -p var/logs var/run
LOG=var/logs/cron.log
say() { echo "[$(TZ=Asia/Kolkata date '+%F %T IST')] $*" >> "$LOG"; }
exec 9> "var/run/${JOB}.lock"
flock -n 9 || { say "DRISHTI_${JOB^^}_SKIP already running"; exit 0; }
say "DRISHTI_${JOB^^}_START"
OUT="var/logs/${JOB}_$(TZ=Asia/Kolkata date +%F_%H%M).json"
case "$JOB" in scout) MOD=drishti.run.p2 ;; *) MOD=drishti.run.p1 ;; esac
if .venv/bin/python -m "$MOD" "$JOB" > "$OUT" 2>&1; then
  say "DRISHTI_${JOB^^}_OK $(tr -d '\n ' < "$OUT" | cut -c1-200)"
else
  rc=$?; say "DRISHTI_${JOB^^}_FAIL rc=$rc $(tail -1 "$OUT" | cut -c1-200)"; exit $rc
fi
