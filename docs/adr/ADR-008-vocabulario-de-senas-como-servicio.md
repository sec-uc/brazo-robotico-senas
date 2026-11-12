# ADR-008: Vocabulario de señas en base de datos propia del servicio

Estado: Aceptado · Fecha: 2026-09-12

## Contexto
Las señas vivían en `abecedario.xlsx` empaquetado en la app: ampliar el vocabulario exigía recompilar y redistribuir (P3). El equipo lingüístico no es técnico.

## Decisión
El servicio Traducción a señas posee una base de datos (SQLite en el prototipo, esquema portable) sembrada por `scripts/migrate_excel_signs.py` y editable por API (`PUT/DELETE /v1/signs/{gloss}`). Glosas sin seña se deletrean con el alfabeto dactilológico; si tampoco existe la letra se reportan como desconocidas (métrica de cobertura en telemetría).

## Alternativas consideradas
- **Mantener el Excel y leerlo desde el servicio**: evita migración, pero sigue sin API de edición ni validación.
- **Base de datos compartida entre servicios**: viola "una base de datos por servicio" y reintroduce acoplamiento.

## Consecuencias
+ Vocabulario editable en caliente; cobertura medible.
− SQLite limita a una réplica con escritura; migrar a PostgreSQL si se replica.
