# ADR-009: Consumo concurrente en el bus e inferencia fuera del bucle de eventos

Estado: Aceptado · Fecha: 2026-10-28

## Contexto
Una primera prueba de carga, sobre un árbol con cambios sin commit, sugirió que la latencia crecía linealmente con las sesiones concurrentes. La revisión del código confirmó dos debilidades de diseño: el cliente NATS entrega los mensajes de una suscripción de uno en uno y espera a que termine el handler, de modo que cada servicio atendía una sola sesión a la vez. Además, el proveedor Whisper ejecutaba la inferencia de forma síncrona dentro de una corrutina, bloqueando el bucle de eventos del servicio (el mismo antipatrón diagnosticado en la API heredada, P7).

## Decisión
`EventBus` despacha cada mensaje como una tarea propia, con un cupo de mensajes en vuelo por servicio (`BUS_MAX_INFLIGHT`, 64 por defecto) que se toma en el callback antes de crear la tarea, para que la contrapresión vuelva a los límites del cliente NATS. La inferencia de los proveedores de reconocimiento corre en hilos (`asyncio.to_thread`) con un límite de inferencias simultáneas por réplica (`STT_WORKERS`). El actuador conserva su cola serial: un brazo ejecuta un plan a la vez.

## Alternativas consideradas
- **Solo réplicas con grupos de cola**: escala, pero cada réplica seguiría atendiendo en serie y la inferencia seguiría bloqueando su bucle.
- **Procesos múltiples (workers de uvicorn)**: multiplica conexiones al bus y modelos en memoria.

## Consecuencias
= Medido después sobre commits limpios (scripts/run_baseline_states.sh, e467743 frente a 44139e5), en un solo equipo no hay ganancia: con 32 sesiones de texto, mediana 64 frente a 73 ms y rendimiento máximo 316 frente a 329 sesiones/s; con voz (faster-whisper base), 2,6 frente a 2,4 sesiones/s, porque la inferencia satura la CPU. La cifra inicial (313 → 73 ms) no se reprodujo.
+ El servicio de reconocimiento responde a salud y métricas mientras transcribe.
− Los handlers deben ser seguros ante concurrencia (estado compartido mínimo).
