# ADR-013: Tope de salida del reconocimiento; sesgo por vocabulario como opción

Estado: Aceptado · Fecha: 2026-11-11

## Contexto
El robot solo ejecuta las señas de su vocabulario (104 entradas) y deletrea el resto; los enunciados son cortos. Con *faster-whisper base*, QA-01 quedaba en el límite de su meta (p95 de 1 551 ms frente a 1 500 ms) y la seña correcta se obtenía en el 63 % del corpus sintético, con muchos casi-aciertos de palabras del vocabulario («mecero» por «mesero»). La cola de latencia venía de decodificaciones que se repiten («uno uno uno…») hasta agotar la ventana del modelo.

## Decisión
1. **Tope de salida.** El servicio de reconocimiento limita la transcripción a 32 tokens (unas 20 palabras; `WHISPER_MAX_TOKENS`, 0 lo desactiva), impide repetir trigramas y no condiciona cada segmento al anterior. Activo en el despliegue con voz.
2. **Sesgo por el vocabulario de señas, como opción.** El servicio puede pasar al motor las palabras del vocabulario como sesgo (`hotwords`) con búsqueda en haz (`SIGNS_VOCAB_URL`, `WHISPER_BEAM=5`). Lee el vocabulario del servicio de traducción a señas, su único dueño, y lo refresca cada 60 s; si ese servicio no responde conserva lo último leído (QA-06). **No se activa por defecto**: su mejora de exactitud no quedó demostrada.

## Lo medido (commits `bf0fef7` y `3480299`, 180 audios: corpus original y control)
- Primera versión, sesgo con haz 5 y sin tope (`results/adr013_v1/`): el p95 subió de 975 a 2 124 ms (4 400 ms con 300 sesiones) y el rendimiento con voz bajó a 0,9 sesiones/s.
- Con tope (`results/adr013_v2/`): p95 de 840 ms (1 001 ms con 300 sesiones; máximo 1 246 ms) frente a 1 387 ms sin sesgo ni tope.
- Exactitud del sesgo frente a no sesgar: corpus original, 32 frente a 36 de 60; palabras del vocabulario no incluidas en el corpus, 43 frente a 29 de 60; palabras de fuera del vocabulario conservadas, 34 frente a 32 de 60. Sobre los 180 audios arregla 33 y rompe 21 (McNemar exacto, p = 0,13): corrige casi-aciertos («mecero» → mesero), pero deforma palabras comunes que antes salían bien («hola» → «ole», «ocho» → «ojo»).

## Alternativas descartadas
- **Corregir cada palabra desconocida hacia la seña más parecida** (distancia de edición, umbral 0,7). En una sonda fuera de la plataforma subió las palabras del vocabulario a 55/60, pero convirtió palabras de fuera en señas equivocadas («dormir» → MORIR, «trabajo» → ABAJO; 25 correcciones falsas en 60 audios). Para quien lee la seña, un signo equivocado es peor que un deletreo.
- **Indicación inicial (`initial_prompt`) con el vocabulario**: misma exactitud que `hotwords` en la sonda, con más transcripciones vacías y una cola de latencia mayor.

## Consecuencias
+ QA-01 con voz deja de depender de un p95 inestable: la sesión más lenta queda acotada.
− Un enunciado de más de unas 20 palabras se trunca; el tope es configurable.
− Las sondas fuera de la plataforma sobrestimaron el sesgo en el corpus original (39 de 60 frente a 32 en la plataforma): solo cuentan las mediciones de `results/`.
