# Documentación

| Documento | Contenido |
|---|---|
| [arquitectura.md](arquitectura.md) | Objetivos, restricciones, contexto, contenedores, ejecución, despliegue, decisiones, riesgos y glosario |
| [adr/](adr/) | Catorce registros de decisiones de arquitectura (ADR-001 a ADR-014) |
| [escenarios-de-calidad.md](escenarios-de-calidad.md) | Los doce escenarios de calidad (QA-01 a QA-12) y su método de medición |
| [evaluacion.md](evaluacion.md) | Resultados por escenario, gráficas y límites de la evaluación |
| [operacion.md](operacion.md) | Configuración, despliegue, reconocimiento de voz, brazo real, API, observabilidad, seguridad y solución de problemas |
| [plan-replica-qa01.md](plan-replica-qa01.md) | Plan de la replicación de QA-01, registrado antes de medir |
| [auditoria-impacto-cambio.md](auditoria-impacto-cambio.md) | Auditoría por archivo del impacto de cambio (QA-03 a QA-05) |
| [../contracts/asyncapi.yaml](../contracts/asyncapi.yaml) | Contratos de los eventos del bus |

## Diagramas y figuras

Todas las figuras están en [`img/`](img/), en PNG (para la documentación) y SVG (vectorial, para publicaciones).

| Figura | Archivo |
|---|---|
| Contexto del sistema | `contexto` |
| Contenedores y eventos | `contenedores` |
| Secuencia de una sesión de voz | `secuencia-voz` |
| Despliegue en un solo equipo | `despliegue` |
| De la arquitectura anterior a la propuesta | `antes-despues` |
| Latencia por motor de reconocimiento | `grafica-motores` |
| Diferencia de latencia entre arquitecturas | `grafica-replicacion` |
| Rendimiento con sesiones simultáneas | `grafica-concurrencia` |
| Recuperación del reconocedor | `grafica-recuperacion` |
| Documentación interactiva de la API | `captura-api` |
| Tablero de Grafana | `captura-grafana` |
