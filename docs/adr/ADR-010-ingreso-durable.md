# ADR-010: Ingreso de audio durable con JetStream

Estado: Aceptado · Fecha: 2026-10-28

## Contexto
En NATS básico, un mensaje sin suscriptor se descarta: el audio enviado mientras el servicio de reconocimiento estaba caído se perdía (QA-06), aunque el usuario ya hubiera hablado.

## Decisión
Los asuntos listados en `BUS_DURABLE_SUBJECTS` (en el despliegue, `voice.audio.chunk`) se publican en un stream JetStream de cola de trabajo con antigüedad máxima `BUS_DURABLE_MAX_AGE_S` (30 s). El servicio de reconocimiento lo consume con un consumidor durable compartido por sus réplicas, confirma cada mensaje al terminar de procesarlo y limita los mensajes sin confirmar a `BUS_MAX_INFLIGHT`. El resto de los asuntos sigue en NATS básico, sin persistencia.

## Alternativas consideradas
- **Persistir todos los asuntos**: más latencia y almacenamiento sin beneficio para eventos que se regeneran.
- **Reintentos en voice-capture**: acopla la captura a la salud del reconocimiento.

## Consecuencias
+ Ante la terminación del proceso del STT no se perdió ninguna de 263 sesiones. En un corte de 16 s, el audio enviado durante el corte se procesó al volver el servicio (1 de 1; antes de esta decisión, en e467743, 0 de 2). La muestra es pequeña: demuestra el mecanismo, no una tasa.
+ La antigüedad máxima evita ejecutar señas de un enunciado ya irrelevante para la conversación.
− El servidor NATS debe arrancar con JetStream (`-js`) y conserva estado en disco.
