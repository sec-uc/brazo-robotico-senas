# ADR-012: Traza acumulativa en todos los eventos

Estado: Aceptado · Fecha: 2026-10-28

## Contexto
Cada evento llevaba la traza recibida más su propia marca, pero el actuador construía cada estado a partir de la traza del plan: el evento final perdía la marca `accepted`, y en vista previa la latencia de software no podía calcularse desde el propio evento (QA-08).

## Decisión
El actuador acumula las marcas en la traza del plan (`accepted`, la primera de `executing`, `done` o `failed`) y publica una copia completa en cada estado. Cualquier consumidor puede reconstruir la línea de tiempo de la sesión desde el último evento, sin consultar la telemetría.

## Consecuencias
+ 60/60 sesiones con las siete marcas también en vista previa; los experimentos miden la latencia desde el evento final, sin carreras con la escritura de telemetría.
− Los eventos crecen unas decenas de bytes.
