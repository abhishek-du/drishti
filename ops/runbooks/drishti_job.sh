#!/usr/bin/env bash
# Drishti job wrapper for cron: one job at a time per name, a log line at start and end.
#   ops/runbooks/drishti_job.sh holidays|indices|manual|eod|barcheck|scout|master|daily1d|compare1d|intraday|live
#   ops/runbooks/drishti_job.sh scan_eod|scan_intraday|scan_grade|odds   (decision-support scanner)
#   ops/runbooks/drishti_job.sh pf_session   (paper portfolio daily loop, PAPER ONLY)
#   ops/runbooks/drishti_job.sh login   (after the switch only: guarded, once a day, never prints the token)
# live: started every 5 min in market hours; a running recorder or a run outside its window
# (09:21/09:15-15:35) is skipped silently, so cron restarts it within 5 min after a crash.
# PAPER ONLY. Read-only market data. Before the switch Drishti never logs in (decision A); the
# login job refuses until DRISHTI_UPSTOX_LOGIN_ENABLED=true (set at the 2026-11-20 switch).
set -uo pipefail
JOB="${1:?job}"
shift; ARGS=("$@")                        # extra args go to the job (e.g. intraday --missing)
cd "$(dirname "$0")/../../backend"
mkdir -p var/logs var/run
LOG=var/logs/cron.log
say() { echo "[$(TZ=Asia/Kolkata date '+%F %T IST')] $*" >> "$LOG"; }
exec 9> "var/run/${JOB}.lock"
flock -n 9 || { [ "$JOB" = live ] || say "DRISHTI_${JOB^^}_SKIP already running"; exit 0; }
if [ "$JOB" = live ]; then
  OUT="var/logs/live_$(TZ=Asia/Kolkata date +%F_%H%M).json"
  .venv/bin/python -m drishti.live.recorder > "$OUT" 2>&1; rc=$?
  if grep -q '"skipped"' "$OUT"; then rm -f -- "$OUT"; exit 0; fi
  [ $rc -eq 0 ] && say "DRISHTI_LIVE_OK $(tr -d '\n ' < "$OUT" | cut -c1-200)" \
    || say "DRISHTI_LIVE_FAIL rc=$rc $(tail -1 "$OUT" | cut -c1-200)"
  exit $rc
fi
OUT="var/logs/${JOB}_$(TZ=Asia/Kolkata date +%F_%H%M).json"
case "$JOB" in scout) MOD=drishti.run.p2 ;; status) MOD=drishti.run.status ;;
  scan_*) MOD=drishti.scanner.run ;; odds) MOD=drishti.scanner.odds_job ;;
  login) MOD=drishti.broker.upstox.login ;;
  pf_*) MOD=drishti.paper.run ;;
  *) MOD=drishti.run.p1 ;; esac
if [ "$JOB" = scan_intraday ]; then          # every 15 min: log only real runs
  .venv/bin/python -m "$MOD" "$JOB" > "$OUT" 2>&1; rc=$?
  if [ $rc -eq 0 ] && grep -q '"skipped"' "$OUT"; then rm -f -- "$OUT"; exit 0; fi
  [ $rc -eq 0 ] && say "DRISHTI_SCAN_INTRADAY_OK $(tr -d '\n ' < "$OUT" | cut -c1-200)" \
    || say "DRISHTI_SCAN_INTRADAY_FAIL rc=$rc $(tail -1 "$OUT" | cut -c1-200)"
  exit $rc
fi
say "DRISHTI_${JOB^^}_START"
if .venv/bin/python -m "$MOD" "$JOB" "${ARGS[@]}" > "$OUT" 2>&1; then
  say "DRISHTI_${JOB^^}_OK $(tr -d '\n ' < "$OUT" | cut -c1-200)"
else
  rc=$?; say "DRISHTI_${JOB^^}_FAIL rc=$rc $(tail -1 "$OUT" | cut -c1-200)"; exit $rc
fi
