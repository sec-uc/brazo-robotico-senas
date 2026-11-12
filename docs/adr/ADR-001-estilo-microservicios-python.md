# ADR-001: Estilo arquitectónico de microservicios con un único lenguaje (Python)

Estado: Aceptado · Fecha: 2026-09-12

## Contexto
El sistema heredado son dos monolitos (API FastAPI y app Flutter) que duplican responsabilidades y no se integran (P1, P2, P4, P11). La tesis debe producir una arquitectura de referencia cuyo aporte sea la separación de responsabilidades por etapa del pipeline voz→seña, evaluable en modificabilidad, rendimiento y disponibilidad. El equipo es pequeño y el pipeline Kichwa existente está en Python.

## Decisión
Descomponer el sistema en nueve microservicios (captura, STT, PLN, señas, actuadores, telemetría, monitoreo, gateway, auth), cada uno con su propio despliegue, contrato y datos. Implementar todos en Python 3.11 + FastAPI compartiendo únicamente `rsp_common` (contratos, bus, utilidades).

## Alternativas consideradas
- **Monolito modular** (un proceso, módulos bien separados): más simple de operar, pero no permite escalar STT de forma independiente ni aislar fallos de ML (P5, P6), y no evidencia la contribución de la tesis.
- **Políglota (Python IA + .NET gateway/auth)**: demuestra heterogeneidad, pero duplica tooling y no aporta a los atributos evaluados; la referencia lo permite (los contratos son JSON), la instancia no lo necesita.
- **ROS 2 como marco integrador**: natural para robótica, pero impone su middleware (DDS) a servicios que no son robóticos (auth, gateway, PLN) y complica el despliegue en un mini‑PC; se deja como driver del servicio de actuadores.

## Consecuencias
+ Cada etapa se sustituye/escala/observa por separado; el pipeline Kichwa se reutiliza como proveedor.
− Más piezas que operar (9 contenedores + NATS + monitoreo); mitigado con Docker Compose y `Service` común.
− Latencia de red entre etapas; medida en QA‑01 (≈ 5 ms totales en el prototipo).
