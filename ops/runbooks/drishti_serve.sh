#!/usr/bin/env bash
# Drishti API (127.0.0.1:8700) and web (127.0.0.1:3100), local only. Read only; PAPER ONLY.
#   ops/runbooks/drishti_serve.sh start|stop|status       (web needs `npx next build` first)
set -uo pipefail
cd "$(dirname "$0")/../../backend"
mkdir -p var/logs var/run
up() { [ -f "var/run/$1.pid" ] && kill -0 "$(cat "var/run/$1.pid")" 2>/dev/null; }
case "${1:?start|stop|status}" in
  start)
    up api || { nohup .venv/bin/uvicorn drishti.api.app:app --host 127.0.0.1 --port 8700 \
                  >> var/logs/api.log 2>&1 & echo $! > var/run/api.pid; }
    up web || { (cd ../web && NODE_ENV=production nohup /home/cis/.local/bin/node node_modules/next/dist/bin/next start -H 127.0.0.1 -p 3100 \
                  >> ../backend/var/logs/web.log 2>&1 & echo $! > ../backend/var/run/web.pid); } ;;
  stop)
    for s in web api; do
      up "$s" && { pkill -P "$(cat "var/run/$s.pid")"; kill "$(cat "var/run/$s.pid")"; }
      rm -f "var/run/$s.pid"
    done ;;
  status)
    for s in api web; do up "$s" && echo "$s up pid $(cat "var/run/$s.pid")" || echo "$s down"; done ;;
esac
