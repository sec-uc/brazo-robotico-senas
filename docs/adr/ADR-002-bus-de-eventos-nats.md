# ADR-002: NATS como bus de eventos

Estado: Aceptado, enmendado por la ADR-010 (ingreso de audio durable con JetStream) · Fecha: 2026-09-12

## Contexto
El pipeline voz→seña es una cadena de etapas con productores y consumidores distintos. Se necesita comunicación asíncrona, baja latencia, colas de trabajo para escalar STT y un broker que quepa en el hardware edge junto al robot.

## Decisión
Usar NATS (core pub/sub + queue groups; JetStream habilitado para persistencia futura). Cada etapa publica en un asunto propio (`voice.audio.chunk`, `voice.text.transcribed`, `nlp.gloss.sequence`, `signs.gesture.plan`, `actuator.state`). El cliente está encapsulado en `rsp_common.bus.EventBus`.

## Alternativas consideradas
- **MQTT (Mosquitto)**: estándar IoT, ideal si el firmware hablara MQTT; carece de queue groups nativos y de request/reply; se reconsiderará si el brazo migra a ESP32/MQTT.
- **RabbitMQ**: colas robustas y UI, pero ~10× más memoria y latencia mayor; excesivo para un nodo edge.
- **Kafka**: throughput masivo innecesario; muy pesado.
- **HTTP síncrono entre servicios**: acopla temporalmente las etapas; un STT lento bloquea la captura.

## Consecuencias
+ Latencia sub‑ms, binario de 20 MB, `STT_REPLICAS=n` escala sin código.
− Sin persistencia por defecto: si el consumidor cae, el evento se pierde (aceptable para conversación en vivo; JetStream disponible si se requiere). **Enmienda (ADR-010):** el asunto `voice.audio.chunk` se persiste en JetStream con antigüedad máxima de 30 s; los demás siguen sin persistencia. Los asuntos son seis: a los cinco de arriba se suma `telemetry.event`.
− Cambiar de broker implica reescribir `bus.py` (un archivo).
