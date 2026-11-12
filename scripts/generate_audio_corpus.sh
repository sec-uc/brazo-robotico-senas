#!/usr/bin/env bash
# Regenera los corpus de voz (data/audio_tts: 60 audios; data/audio_ctrl: 120) a partir de sus
# manifiestos, con las voces del sistema de macOS (say) y conversión a WAV 16 kHz mono (afconvert).
# Los WAV no se distribuyen: la licencia de las voces de macOS no permite redistribuirlas.
# La salida puede diferir ligeramente según la versión de macOS y de las voces.
#   scripts/generate_audio_corpus.sh
set -euo pipefail
cd "$(dirname "$0")/.."
for dir in data/audio_tts data/audio_ctrl; do
  tail -n +2 "$dir/manifest.tsv" | while IFS=$'\t' read -r file text voice _; do
    say -v "$voice" -o "$dir/${file%.wav}.aiff" "$text"
    afconvert -f WAVE -d LEI16@16000 -c 1 "$dir/${file%.wav}.aiff" "$dir/$file"
    rm "$dir/${file%.wav}.aiff"
  done
  echo "$dir: $(ls "$dir"/*.wav | wc -l | tr -d ' ') audios"
done
