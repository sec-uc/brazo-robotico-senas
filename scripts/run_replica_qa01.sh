#!/usr/bin/env bash
# Replicación de QA-01 con el mismo código de reconocimiento (R-31), según docs/plan-replica-qa01.md.
#   scripts/run_replica_qa01.sh        # ~30 min; deja la plataforma levantada sin el STT en contenedor
# Actual: kichwa-app-api nativo (8090). Propuesta: plataforma en contenedores con el STT nativo (proveedor
# kichwa), mismo entorno de Python y mismo config.yaml que la actual.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD PY=$PWD/.venv/bin/python
LEGACY_REPO=${LEGACY_REPO:-$ROOT/../kichwa-app-api}
LEGACY_VENV=${LEGACY_VENV:-$ROOT/.venv-legacy}
REPS=${REPS:-5}
if [ -n "$(git status --porcelain -- . ':!results' ':!data/audio_tts' ':!data/audio_ctrl')" ]; then
  echo "el árbol tiene cambios sin commit"; exit 1
fi
[ -z "$(git -C "$LEGACY_REPO" status --porcelain)" ] || { echo "kichwa-app-api tiene cambios sin commit"; exit 1; }
git rev-parse -q --verify plan-replica-qa01 >/dev/null || { echo "falta la etiqueta plan-replica-qa01"; exit 1; }
WORK=$(mktemp -d)
trap 'pkill -f "uvicorn app.main:app --host 127.0.0.1 --port 8090" || true; pkill -f "rspstt.main:app --host 127.0.0.1 --port 8095" || true
      git -C "$LEGACY_REPO" worktree remove --force "$WORK/legacy" >/dev/null 2>&1 || true' EXIT

git -C "$LEGACY_REPO" worktree add -q --detach "$WORK/legacy" HEAD
CFG=$WORK/legacy/config/config.yaml
sed -i '' -e 's/^\(  *\)fp16: true.*/\1fp16: false/' -e 's/^\(  *\)use_distil_whisper: true.*/\1use_distil_whisper: false/' "$CFG"
mkdir -p "$WORK/legacy"/{uploads,results,logs,temp}

echo "==== plataforma con el bus en la interfaz local y sin el STT en contenedor"
DC="docker compose -f docker-compose.yml -f docker-compose.native.yml"
$DC up -d --build >/dev/null
$DC stop stt >/dev/null
BUS_TOKEN=${BUS_TOKEN:-$(grep -E '^BUS_TOKEN=' .env 2>/dev/null | cut -d= -f2- || true)}
export BUS_TOKEN=${BUS_TOKEN:?defina BUS_TOKEN en .env}

echo "==== STT nativo con el proveedor kichwa (pipeline de la API actual)"
# los dos paquetes se llaman `app` y el del STT es de espacio de nombres (sin __init__.py), así que el
# heredado gana siempre: el STT se importa con otro nombre (rspstt) a través de un enlace
mkdir -p "$WORK/stt" && ln -s "$ROOT/services/stt/app" "$WORK/stt/rspstt"
(cd "$WORK/legacy" && NATS_URL=nats://127.0.0.1:4222 STT_PROVIDER=kichwa KICHWA_CONFIG_PATH=$CFG \
   BUS_DURABLE_SUBJECTS=voice.audio.chunk BUS_DURABLE_MAX_AGE_S=30 PYTHONPATH=$WORK/stt:$ROOT/libs:$WORK/legacy \
   "$LEGACY_VENV/bin/uvicorn" rspstt.main:app --host 127.0.0.1 --port 8095 >"$WORK/stt.log" 2>&1 &)
echo "==== arquitectura actual ($(git -C "$LEGACY_REPO" rev-parse --short HEAD)) en 8090"
(cd "$WORK/legacy" && "$LEGACY_VENV/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port 8090 >"$WORK/legacy.log" 2>&1 &)
for port in 8095 8090; do
  for _ in $(seq 1 120); do curl -fs "http://127.0.0.1:$port/health" >/dev/null 2>&1 && break; sleep 2; done
  curl -fs "http://127.0.0.1:$port/health" >/dev/null || { echo "el servicio de $port no arrancó"; tail -30 "$WORK"/*.log; exit 1; }
done
grep -q "Servidor iniciado correctamente" "$WORK/stt.log" && { echo "en 8095 arrancó la API heredada, no el STT"; exit 1; }
curl -fs http://127.0.0.1:8095/health | grep -q '"stt"' || { echo "8095 no es el servicio stt"; curl -s http://127.0.0.1:8095/health; exit 1; }

export RSP_STT_PROVIDER=kichwa-nativo
export RSP_SUT_NOTE="propuesta $(git rev-parse --short HEAD) con STT nativo (kichwa, .venv-legacy); actual kichwa-app-api $(git -C "$LEGACY_REPO" rev-parse --short HEAD); config.yaml con fp16=false y use_distil_whisper=false en ambos"
$PY scripts/experiments.py replica --reps "$REPS" --url http://127.0.0.1:8090 | tail -12
$PY scripts/analysis_replica.py
echo "replicación terminada; plan en $(git rev-parse --short plan-replica-qa01), medida sobre $(git rev-parse --short HEAD)"
