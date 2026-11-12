# ADR-003: Pipeline asíncrono por eventos con trazas de tiempo embebidas

Estado: Aceptado · Fecha: 2026-09-12

## Contexto
El flujo heredado era batch (subir archivo → procesar → polling) y no permitía medir latencia por etapa (P7, P9). La evaluación de la tesis exige latencias end‑to‑end y por etapa con n ≥ 30 muestras, reproducibles.

## Decisión
Cada evento hereda `session_id` y un diccionario `trace` con marcas de tiempo (`captured`, `transcribed`, `glossed`, `planned`, `accepted`, `executing`, `done`) que cada servicio extiende al publicar. Telemetría reconstruye la línea de tiempo sin instrumentación adicional. El gateway reexpone los eventos al cliente por Server‑Sent Events, eliminando el polling.

## Alternativas consideradas
- **OpenTelemetry con propagación de contexto**: más estándar, pero exige collector + backend de trazas y sincronización; se deja como extensión (los `/metrics` Prometheus ya están).
- **Marcas solo en telemetría (hora de recepción)**: mide latencia de bus, no de procesamiento; menos preciso.

## Consecuencias
+ Medición gratuita y determinista; la app muestra progreso en vivo.
− Los relojes de productores deben estar sincronizados si corren en hosts distintos (NTP); en un solo host no aplica.
