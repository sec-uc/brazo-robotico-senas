#!/usr/bin/env bash
# Ejecuta el servicio de control de actuadores FUERA de Docker, con el brazo real.
#
# Docker Desktop en macOS (y Windows) no puede pasar puertos USB/serial a los
# contenedores, así que el actuador corre nativo en el host y se conecta al NATS
# publicado por docker compose (localhost:4222). El resto de la plataforma sigue
# en contenedores. En Linux sí se puede usar `devices:` en docker-compose.yml.
#
#   scripts/run_actuator_native.sh /dev/tty.usbserial-1420      # macOS
#   scripts/run_actuator_native.sh /dev/ttyUSB0                  # Linux
#   scripts/run_actuator_native.sh sim                           # simulador nativo
#
# Antes: docker compose stop actuator-control   (para que no haya dos actuadores)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${1:-}"
[ -z "$PORT" ] && { echo "uso: $0 <puerto-serial|sim>"; ls /dev/tty.usb* /dev/ttyUSB* /dev/ttyACM* 2>/dev/null; exit 1; }

if [ ! -d "$ROOT/.venv" ]; then
  python3 -m venv "$ROOT/.venv"
  "$ROOT/.venv/bin/pip" install -q -e "$ROOT/libs/rsp_common" -r "$ROOT/services/actuator-control/requirements.txt"
fi
BUS_TOKEN="${BUS_TOKEN:-$(grep -E '^BUS_TOKEN=' "$ROOT/.env" 2>/dev/null | cut -d= -f2- || true)}"
export BUS_TOKEN="${BUS_TOKEN:-dev-bus-token-change-me}"
export NATS_URL="${NATS_URL:-nats://127.0.0.1:4222}"
export AUTH_SECRET="${AUTH_SECRET:-$(grep -E '^AUTH_SECRET=' "$ROOT/.env" 2>/dev/null | cut -d= -f2- || echo dev-secret-change-me)}"
export INTER_COMMAND_MS="${INTER_COMMAND_MS:-150}"
if [ "$PORT" = "sim" ]; then export ACTUATOR_DRIVER=simulator; else export ACTUATOR_DRIVER=serial SERIAL_PORT="$PORT"; fi

docker compose -f "$ROOT/docker-compose.yml" stop actuator-control >/dev/null 2>&1 || true
# el gateway debe enrutar /v1/actuator/* al proceso nativo
( cd "$ROOT" && ACTUATOR_URL=http://host.docker.internal:8006 docker compose -f docker-compose.yml -f docker-compose.native.yml up -d nats gateway >/dev/null 2>&1 ) || true
echo "actuator-control nativo → driver=$ACTUATOR_DRIVER puerto=${SERIAL_PORT:-n/a} nats=$NATS_URL (http://127.0.0.1:8006/docs)"
cd "$ROOT/services/actuator-control" && exec "$ROOT/.venv/bin/python" -m uvicorn app.main:app --host 0.0.0.0 --port 8006
