#!/usr/bin/env bash
# Estados de comparación del artículo, cada uno sobre un commit limpio y con el commit registrado:
#   1a) 645439a, antes de las correcciones de QA-12 (letras omitidas, ñ) y QA-06 (texto a través del STT).
#   1b) e467743, antes de ADR-009..012: audio durante un corte (ADR-010) y concurrencia de texto y voz (ADR-009).
#   2) arquitectura actual: kichwa-app-api en su commit, con Whisper base en CPU (fp16 y distil-whisper
#      desactivados para usar el mismo modelo que la propuesta), contra el mismo corpus.
# Los instrumentos de medida son los scripts de este commit; el sistema medido queda en sut_commit.
#   scripts/run_baseline_states.sh        # ~30 min; detiene y vuelve a levantar la plataforma
#   scripts/run_baseline_states.sh carga-antes   # solo la carga de 1b (~5 min)
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD PY=$PWD/.venv/bin/python
LEGACY_REPO=${LEGACY_REPO:-$ROOT/../kichwa-app-api}
LEGACY_VENV=${LEGACY_VENV:?ruta a un venv con los requirements de kichwa-app-api}

if [ -n "$(git status --porcelain -- . ':!results' ':!data/audio_tts')" ]; then
  echo "el árbol tiene cambios sin commit"; exit 1
fi
[ -z "$(git -C "$LEGACY_REPO" status --porcelain)" ] || { echo "kichwa-app-api tiene cambios sin commit"; exit 1; }
WORK=$(mktemp -d)
trap 'docker compose -p rsp-antes -f "$WORK/antes/docker-compose.yml" down -v >/dev/null 2>&1 || true;
      git worktree remove --force "$WORK/antes" >/dev/null 2>&1 || true; git worktree prune;
      pkill -f "uvicorn app.main:app --host 127.0.0.1 --port 8090" || true' EXIT

wait_healthy() {
  sleep 3
  for _ in $(seq 1 150); do
    docker compose ps -a --format '{{.Status}}' | grep -qE 'starting|unhealthy|Restarting|Exited|Created' || return 0
    sleep 3
  done
  echo "servicios no sanos"; docker compose ps -a; exit 1
}

state_up() {  # $1 = commit; levanta esa versión de la plataforma como sistema bajo prueba
  docker compose -p rsp-antes -f "$WORK/antes/docker-compose.yml" down -v >/dev/null 2>&1 || true
  git worktree remove --force "$WORK/antes" >/dev/null 2>&1 || true
  git worktree add -q --detach "$WORK/antes" "$1"
  # único cambio admitido: permisos del volumen de datos (defecto de despliegue corregido en e467743);
  # queda registrado en sut_diff. Sin él, sign-translation y telemetry no arrancan.
  sed -i '' 's#mkdir -p /data && chown#mkdir -p /data/db \&\& chown#' "$WORK/antes"/services/{sign-translation,telemetry}/Dockerfile
  # puerto publicado configurable (no cambia el sistema medido), por si el 8080 está ocupado
  sed -i '' 's#"8080:8000"#"${GATEWAY_PORT:-8080}:8000"#' "$WORK/antes/docker-compose.yml"
  cp .env "$WORK/antes/.env"
  export COMPOSE_PROJECT_NAME=rsp-antes COMPOSE_FILE=$WORK/antes/docker-compose.yml
  export RSP_SUT_DIR=$WORK/antes RSP_SUT_NOTE="plataforma en $1"
  docker compose up -d --build >/dev/null; wait_healthy
}

load_antes() {  # carga de texto y voz sobre e467743 (sección 1b)
  $PY scripts/experiments.py load --label echo --levels 1 2 4 8 16 32 --per-level 64 | tail -7
  export COMPOSE_FILE=$WORK/antes/docker-compose.yml:$WORK/antes/docker-compose.whisper.yml
  docker compose up -d --build stt >/dev/null; wait_healthy
  $PY scripts/experiments.py load --audio --label voice_antes --levels 1 2 4 8 --per-level 32 | tail -5
  docker compose down -v >/dev/null
  unset COMPOSE_PROJECT_NAME COMPOSE_FILE
}

docker compose down >/dev/null
if [ "${1:-}" = carga-antes ]; then  # solo la carga de 1b, p. ej. tras cambiar el instrumento de carga
  state_up e467743; load_antes
  docker compose up -d >/dev/null; wait_healthy
  $PY scripts/export_figure_data.py; exit 0
fi
echo "==== 1a. 645439a (antes de las correcciones de QA-12 y QA-06)"
state_up 645439a
$PY scripts/experiments.py qa12 --label antes | tail -2
$PY scripts/experiments.py qa06 --label antes --mode crash | tail -2; wait_healthy
$PY scripts/experiments.py qa06 --label antes_outage --mode outage --outage 15 --observe 20 | tail -2; wait_healthy

echo "==== 1b. e467743 (antes de ADR-009..012)"
state_up e467743
$PY scripts/experiments.py qa06 --label bus_antes_outage --mode outage --outage 15 --observe 20 | tail -2; wait_healthy
load_antes

echo "==== 2. arquitectura actual ($(git -C "$LEGACY_REPO" rev-parse --short HEAD))"
git -C "$LEGACY_REPO" worktree add -q --detach "$WORK/legacy" HEAD
sed -i '' -e 's/^\(  *\)fp16: true.*/\1fp16: false/' -e 's/^\(  *\)use_distil_whisper: true.*/\1use_distil_whisper: false/' "$WORK/legacy/config/config.yaml"
export RSP_SUT_DIR=$WORK/legacy RSP_SUT_NOTE="kichwa-app-api; único cambio: config.yaml con fp16=false y use_distil_whisper=false (Whisper base en CPU)"
(cd "$WORK/legacy" && mkdir -p uploads results logs temp && "$LEGACY_VENV/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port 8090 >"$WORK/legacy.log" 2>&1 &)
for _ in $(seq 1 90); do curl -fs http://127.0.0.1:8090/health >/dev/null 2>&1 && break; sleep 2; done
$PY scripts/experiments.py legacy --label whisper_base --n 60 | tail -3
pkill -f "uvicorn app.main:app --host 127.0.0.1 --port 8090" || true
git -C "$LEGACY_REPO" worktree remove --force "$WORK/legacy"
unset RSP_SUT_DIR RSP_SUT_NOTE

echo "==== 3. plataforma de vuelta, análisis y datos de figuras"
docker compose up -d >/dev/null; wait_healthy
$PY scripts/analysis.py
$PY scripts/export_figure_data.py
echo "estados de comparación medidos con los instrumentos de $(git rev-parse --short HEAD)"
