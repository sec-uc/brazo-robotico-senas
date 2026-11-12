#!/usr/bin/env bash
# Levanta toda la plataforma SIN Docker (desarrollo/CI): nats-server + 8 servicios
# con uvicorn en puertos locales. Requiere: nats-server en PATH, pip install -e libs/rsp_common
# y las dependencias de services/*/requirements.txt.
#
#   scripts/run_local.sh start   # arranca todo en segundo plano (logs en .local/logs)
#   scripts/run_local.sh stop
#
# Puertos: gateway 8080, auth 8001, voice-capture 8002, stt 8003, nlp 8004,
#          sign-translation 8005, actuator-control 8006, telemetry 8007
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$ROOT/.local/logs"; PID="$ROOT/.local/pids"; DATA="$ROOT/.local/data"
mkdir -p "$LOG" "$PID" "$DATA"

export NATS_URL="${NATS_URL:-nats://127.0.0.1:4222}"
export AUTH_SECRET="${AUTH_SECRET:?defina AUTH_SECRET (32 bytes o más)}"
export AUTH_ENABLED="${AUTH_ENABLED:-true}"
export SIGNS_DB_PATH="$DATA/signs.db"
export SIGNS_SEED_PATH="$ROOT/data/signs_seed.json"
export TELEMETRY_DB_PATH="$DATA/telemetry.db"
export KICHWA_DICTIONARY_PATH="$ROOT/data/kichwa_dictionary.json"
export AUTH_URL=http://127.0.0.1:8001 VOICE_CAPTURE_URL=http://127.0.0.1:8002 STT_URL=http://127.0.0.1:8003
export NLP_URL=http://127.0.0.1:8004 SIGN_TRANSLATION_URL=http://127.0.0.1:8005
export ACTUATOR_URL=http://127.0.0.1:8006 TELEMETRY_URL=http://127.0.0.1:8007

declare -A PORTS=([gateway]=8080 [auth]=8001 [voice-capture]=8002 [stt]=8003 [nlp]=8004 [sign-translation]=8005 [actuator-control]=8006 [telemetry]=8007)

start() {
  if ! pgrep -x nats-server >/dev/null; then
    setsid nohup nats-server -p 4222 -m 8222 >"$LOG/nats.log" 2>&1 < /dev/null & echo $! >"$PID/nats.pid"
    sleep 1
  fi
  for svc in "${!PORTS[@]}"; do
    ( cd "$ROOT/services/$svc" && setsid nohup python3 -m uvicorn app.main:app --host 127.0.0.1 --port "${PORTS[$svc]}" \
        >"$LOG/$svc.log" 2>&1 < /dev/null & echo $! >"$PID/$svc.pid" )
  done
  echo "esperando servicios..."
  for i in $(seq 1 30); do
    ok=0
    for p in "${PORTS[@]}"; do curl -fs "http://127.0.0.1:$p/health" >/dev/null 2>&1 && ok=$((ok+1)); done
    [ "$ok" -eq "${#PORTS[@]}" ] && { echo "todos arriba ($ok/${#PORTS[@]}). Gateway: http://127.0.0.1:8080/docs"; return; }
    sleep 1
  done
  echo "algunos servicios no respondieron; revisa $LOG"; exit 1
}

stop() {
  for f in "$PID"/*.pid; do [ -f "$f" ] && { kill "$(cat "$f")" 2>/dev/null || true; rm -f "$f"; }; done
  echo "detenido"
}

case "${1:-start}" in start) start;; stop) stop;; *) echo "uso: $0 start|stop";; esac
