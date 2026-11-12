# ADR-004: Contratos de eventos como única dependencia entre servicios

Estado: Aceptado · Fecha: 2026-09-12

## Contexto
La modificabilidad se evalúa como el costo de sustituir un proveedor (STT, traducción, hardware). Para que ese costo se limite a un servicio, los demás solo pueden depender del contrato, nunca de la implementación.

## Decisión
Los esquemas Pydantic de `rsp_common/events.py` (y su espejo `contracts/asyncapi.yaml`) son el contrato. Reglas: campos nuevos siempre opcionales; cambios incompatibles → nuevo asunto versionado; ningún servicio importa código de otro servicio.

## Alternativas consideradas
- **Protobuf/gRPC**: contratos más estrictos y rápidos, pero peor legibilidad para telemetría/depuración y para el tribunal; JSON basta para el volumen (< 100 eventos/s).
- **Sin biblioteca compartida (copiar esquemas)**: elimina el acoplamiento por paquete, pero multiplica errores de deriva; `rsp_common` solo contiene contratos y utilidades, no dominio.

## Consecuencias
+ Un servicio puede reescribirse en otro lenguaje.
− `rsp_common` es una dependencia compartida que debe versionarse con disciplina.

## Excepciones (añadido tras la evaluación)
Las etapas del pipeline dependen entre sí solo de los eventos. Hay llamadas HTTP síncronas fuera de esa regla: las de la pasarela a los servicios que expone (vocabulario, actuadores, telemetría, PLN) y la consulta opcional del vocabulario desde STT que introdujo la ADR-013 (`SIGNS_VOCAB_URL`, desactivada por defecto).
