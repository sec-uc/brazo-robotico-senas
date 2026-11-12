#!/usr/bin/env bash
# Verifica el entorno, levanta la plataforma con Docker y ejecuta la demo end-to-end.
#   scripts/doctor.sh            # comprobar + levantar + demo
#   scripts/doctor.sh check      # solo comprobar requisitos
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
ok()   { printf "  \033[32m✔\033[0m %s\n" "$1"; }
bad()  { printf "  \033[31m✘\033[0m %s\n" "$1"; FAIL=1; }
FAIL=0

echo "== Requisitos"
command -v docker >/dev/null && ok "docker $(docker --version | cut -d' ' -f3 | tr -d ,)" || bad "docker no está en PATH (instala Docker Desktop y ábrelo)"
docker compose version >/dev/null 2>&1 && ok "docker compose $(docker compose version --short)" || bad "docker compose no disponible"
docker info >/dev/null 2>&1 && ok "el daemon de Docker responde" || bad "Docker Desktop no está corriendo"
command -v python3 >/dev/null && ok "python3 $(python3 --version | cut -d' ' -f2)" || bad "python3 no encontrado"
python3 -c "import httpx" 2>/dev/null && ok "python: httpx" || bad "falta httpx → pip3 install httpx"
[ -f .env ] && ok ".env presente" || { cp .env.example .env && ok ".env creado desde .env.example"; }
for p in 8080 4222 3000 9090; do
  if (command -v lsof >/dev/null && lsof -iTCP:$p -sTCP:LISTEN >/dev/null 2>&1); then bad "puerto $p ocupado"; else ok "puerto $p libre"; fi
done
[ "${1:-}" = "check" ] && exit $FAIL
[ $FAIL -ne 0 ] && { echo "corrige lo marcado y vuelve a ejecutar"; exit 1; }

echo "== Construyendo y levantando (la primera vez tarda varios minutos)"
docker compose up --build -d || exit 1

echo "== Esperando servicios"
for i in $(seq 1 60); do
  if curl -fs http://localhost:8080/v1/system/health >/dev/null 2>&1; then break; fi; sleep 2
done
python3 - <<'EOF'
import json, urllib.request
h = json.load(urllib.request.urlopen("http://localhost:8080/v1/system/health"))
bad = [k for k, v in h.items() if v.get("status") not in ("ok",)]
for k, v in h.items(): print(f"  {'✔' if v.get('status')=='ok' else '✘'} {k}: {v.get('status')}")
raise SystemExit(1 if bad else 0)
EOF
[ $? -ne 0 ] && { echo "algún servicio no está sano: docker compose logs <servicio>"; exit 1; }

echo "== Demo end-to-end (modo texto, simulador)"
python3 scripts/e2e_demo.py "hola" && echo "
Todo listo.
  API/Swagger:  http://localhost:8080/docs
  Grafana:      http://localhost:3000  (admin / admin)
  Prometheus:   http://localhost:9090
  Benchmark:    python3 scripts/benchmark_latency.py --n 30 --out results/bench.csv
  Brazo real:   scripts/run_actuator_native.sh /dev/tty.usbserial-XXXX   (ver README, sección macOS)"
