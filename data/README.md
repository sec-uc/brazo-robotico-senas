# Datos

| Archivo | Contenido | Origen |
|---|---|---|
| `signs_seed.json` | 25 letras y 6 palabras con ángulos **sintéticos** | Ejemplo creado para este repositorio; no son señas reales ni de la LSEC |
| `kichwa_dictionary.json` | 8 entradas kichwa–español | Ejemplo creado para este repositorio |
| `audio_tts/manifest.tsv` | 60 audios: 20 expresiones × 3 voces | Corpus de QA-01 |
| `audio_ctrl/manifest.tsv` | 120 audios: 40 palabras × 3 voces | Corpus de control de la ADR-013 |
| `audio_sha256.txt` | SHA-256 de los 180 audios originales | Para verificar una regeneración |

## Lo que no se incluye

- **Vocabulario de señas y diccionario kichwa–español originales.** Con ellos se obtuvieron los
  resultados del artículo; para usarlos, basta con sustituir los dos archivos de ejemplo por los
  originales (mismo formato).
- **Audios del corpus.** Se generaron con las voces del sistema de macOS (Paulina, Mónica y Eddy), cuya
  licencia no permite redistribuirlos. `scripts/generate_audio_corpus.sh` los regenera en macOS a
  partir de los manifiestos; el formato y la duración coinciden con los originales, pero el contenido
  puede no ser idéntico byte a byte según la versión del sistema (compárese con `audio_sha256.txt`).
