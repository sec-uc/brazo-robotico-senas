#!/usr/bin/env bash
# Evaluación completa del artículo, en orden, sobre el commit actual (que debe estar limpio).
#   scripts/run_evaluation.sh            # ~40 min en un Apple M4 Pro; resultados en results/
# Los estados de comparación («antes» en e467743 y la arquitectura actual) se miden aparte con
#   scripts/run_baseline_states.sh, que registra el commit de cada sistema medido.
# Requiere: Docker en marcha, .venv con requirements (ver README) y el corpus data/audio_tts/.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
DC="docker compose"
WH="-f docker-compose.yml -f docker-compose.whisper.yml"
WO="-f docker-compose.yml -f docker-compose.whisper-openai.yml"

if [ -n "$(git status --porcelain -- . ':!results' ':!data/audio_tts' ':!data/audio_ctrl')" ]; then
  echo "el árbol tiene cambios sin commit: la evaluación debe correr sobre un commit limpio"; exit 1
fi
echo "commit evaluado: $(git rev-parse --short HEAD)"

wait_healthy() {  # espera a que ningún servicio esté arrancando, enfermo o caído
  sleep 3
  for _ in $(seq 1 150); do
    if ! $DC ps -a --format '{{.Status}}' | grep -qE 'starting|unhealthy|Restarting|Exited|Created'; then return 0; fi
    sleep 3
  done
  echo "servicios no sanos"; $DC ps -a; exit 1
}
step() { echo; echo "==== $*"; }

step "1. plataforma con STT echo y brazo simulado"
$DC up -d --build >/dev/null; wait_healthy
step "QA-01/QA-08 texto (n=60)";        $PY scripts/benchmark_latency.py --n 60 --out results/bench_text_sim.csv --label text_sim | tail -3
step "QA-09 seguridad";                 $PY scripts/experiments.py qa09 | tail -3
step "QA-12 fiabilidad";                $PY scripts/experiments.py qa12 --label final | tail -2
step "QA-04 vocabulario";               $PY scripts/experiments.py qa04 | grep -E "planned_as_sign|add_time" || true
step "QA-06 fallo del proceso";         $PY scripts/experiments.py qa06 --label final_crash --mode crash | tail -2; wait_healthy
step "QA-06 corte prolongado (16 s)";   $PY scripts/experiments.py qa06 --label final_outage --mode outage --outage 15 --observe 20 | tail -2; wait_healthy
step "carga en modo texto";             $PY scripts/experiments.py load --label text --levels 1 2 4 8 16 32 --per-level 64 | tail -7

step "2. driver por lotes (QA-05)"
ACTUATOR_DRIVER=batch $DC up -d actuator-control >/dev/null; wait_healthy
$PY scripts/benchmark_latency.py --n 60 --out results/bench_text_batch.csv --label text_batch | tail -2
$DC up -d actuator-control >/dev/null; wait_healthy

step "3a. faster-whisper base (int8) sin tope de salida: estado anterior a ADR-013, solo por configuración"
WHISPER_MAX_TOKENS=0 $DC $WH up -d --build stt >/dev/null; wait_healthy
$PY scripts/experiments.py qa01 --label whisper_base_sin_tope --n 60 | tail -3
$PY scripts/experiments.py qa01 --label ctrl_sin_tope --corpus audio_ctrl | tail -3

step "3b. con el vocabulario de señas como sesgo y haz 5 (opción de ADR-013, no activa por defecto)"
SIGNS_VOCAB_URL=http://sign-translation:8000/signs WHISPER_BEAM=5 $DC $WH up -d stt >/dev/null; wait_healthy
$PY scripts/experiments.py qa01 --label whisper_base_sesgo --n 60 | tail -3
$PY scripts/experiments.py qa01 --label ctrl_sesgo --corpus audio_ctrl | tail -3

step "3. faster-whisper base (int8) con tope de salida (ADR-013): configuración del despliegue"
$DC $WH up -d stt >/dev/null; wait_healthy
$PY scripts/experiments.py qa01 --label whisper_base --n 60 | tail -3
$PY scripts/experiments.py qa01 --label ctrl --corpus audio_ctrl | tail -3
$PY scripts/experiments.py qa01 --label whisper_base_n300 --n 300 | tail -3
step "QA-06 con reconocedor real: 30 terminaciones abruptas y ráfagas de audio durante una parada"
$PY scripts/experiments.py qa06r --label ciclos --mode ciclos --cycles 30 | tail -12; wait_healthy
$PY scripts/experiments.py qa06r --label rafaga --mode rafaga --reps 3 --burst 30 --outage 15 | tail -8; wait_healthy
$PY scripts/experiments.py load --audio --label voice_r1 --levels 1 2 4 8 --per-level 32 | tail -5
$DC $WH up -d --scale stt=2 stt >/dev/null; wait_healthy
$PY scripts/experiments.py load --audio --label voice_r2 --levels 1 2 4 8 --per-level 32 | tail -5
$DC $WH up -d --scale stt=1 stt >/dev/null; wait_healthy

step "4. faster-whisper small"
WHISPER_MODEL=small $DC $WH up -d stt >/dev/null; wait_healthy
$PY scripts/experiments.py qa01 --label whisper_small --n 60 | tail -3

step "5. openai-whisper base fp32 (mismo motor que la arquitectura actual)"
$DC $WO up -d --build stt >/dev/null; wait_healthy
$PY scripts/experiments.py qa01 --label whisper_openai --n 60 | tail -3

step "6. vuelta a echo, análisis y datos de figuras"
$DC up -d --build stt >/dev/null; wait_healthy
$PY scripts/coupling_analysis.py | tail -2
$PY scripts/analysis.py
$PY -m pytest services libs -q --cov=services --cov=libs/rsp_common --cov-report=json:results/coverage.json --junitxml=results/junit.xml | tail -1
$PY scripts/export_figure_data.py
echo; echo "evaluación terminada sobre $(git rev-parse --short HEAD)"
