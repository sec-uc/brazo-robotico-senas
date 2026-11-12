# Plan de la replicación de QA-01 con el mismo código de reconocimiento (R-31)

Registrado en este commit, con la etiqueta `plan-replica-qa01`, **antes** de ejecutar la replicación.
Ningún dato de la replicación existe al fijarlo. Lo que se decida después se reportará como análisis
posterior al plan.

## Pregunta
¿Es la latencia percibida de la arquitectura propuesta equivalente, dentro de ±200 ms en la mediana,
a la de la arquitectura actual cuando ambas ejecutan el mismo código de reconocimiento?

## Sistemas
- **Actual**: `kichwa-app-api` en `3dc0738`, nativo (uvicorn, puerto 8090), con `config.yaml` modificado
  solo en `fp16: false` y `use_distil_whisper: false` (Whisper base en CPU). Flujo de su aplicación:
  subir → `/process/es` → sondeo cada 1 s → resultados.
- **Propuesta**: la plataforma de este commit en contenedores, salvo el servicio de reconocimiento, que
  corre nativo con el proveedor `kichwa` (el mismo pipeline de la API actual, camino español), el mismo
  entorno de Python (`.venv-legacy`) y el mismo `config.yaml`. Sesión: SSE → audio → plan aceptado en
  vista previa (`dry_run`).
- Ambos reconocedores corren fuera de contenedores, en el mismo equipo y con el mismo entorno: mismo
  régimen de despliegue para la parte que domina la latencia.

## Diseño
- Corpus: las 60 grabaciones de `data/audio_tts/` (20 expresiones × 3 voces).
- k = 5 repeticiones por archivo y sistema: 600 mediciones.
- Intercalado: en cada repetición, los 60 archivos en un orden aleatorio (semilla = 1000 + repetición) y,
  para cada archivo, los dos sistemas uno detrás del otro, con el orden de los sistemas al azar (misma
  semilla). Ambos sistemas están levantados durante toda la corrida.
- Una medición de calentamiento por sistema antes de empezar, que no se analiza.

## Variables
- Latencia percibida (`wall_ms`): desde que el cliente empieza a enviar hasta que tiene el resultado (texto
  en la actual; plan aceptado en la propuesta, que incluye además glosado, plan y aceptación).
- Medición válida: terminó y produjo una transcripción no vacía.

## Análisis principal
1. Para cada archivo y sistema, la mediana de sus mediciones válidas. Un archivo entra en el análisis si
   tiene al menos 3 mediciones válidas en **ambos** sistemas.
2. Efecto = mediana de las diferencias por archivo (propuesta − actual), con IC 95 % y 90 % por bootstrap
   (10 000 remuestreos de archivos, semilla 0).
3. Equivalencia (TOST, α = 0,05): equivalentes si el IC 90 % queda dentro de ±200 ms.
4. Wilcoxon de rangos con signo sobre las diferencias por archivo.

## Análisis secundarios (descriptivos)
- Con todas las mediciones (válidas o no) en el paso 1.
- p95 de cada sistema sobre sus mediciones válidas, con IC 95 % por bootstrap. No se contrasta
  equivalencia en el p95.
- Efecto por repetición (las 5 estimaciones por separado), para ver la estabilidad.
- Tasa de transcripciones vacías por sistema.

## Qué se reporta pase lo que pase
El efecto, sus IC, el resultado de TOST y los secundarios, aunque la equivalencia no se sostenga.
Si la replicación contradice la comparación anterior (+68 ms), prevalece la replicación.

## Desviación registrada antes de la corrida válida (12 nov)
El primer intento (registro en `results/replica_qa01_intento1.log`) no midió la propuesta: el servicio
de reconocimiento nativo cargó el paquete `app` de la API heredada en lugar del suyo (ambos se llaman
igual), así que ninguna sesión de la propuesta obtuvo transcripción (tiempo agotado a los 120 s), y el
token caducó a mitad de la repetición 1. Se descarta entero, sin analizarlo. Correcciones del
instrumento, sin tocar el diseño ni el análisis: el STT se lanza sin `-m` y con su propio paquete
primero en `PYTHONPATH`, el guion comprueba que en 8095 responde el servicio `stt`, y la propuesta pide
un token nuevo en cada medición, fuera del tiempo medido.
El segundo intento (`results/replica_qa01_intento2.log`) se detuvo antes de medir, por la comprobación
nueva: el paquete `app` del STT no tiene `__init__.py` y el heredado se impone sea cual sea el orden de
`PYTHONPATH`. El STT se importa ahora como `rspstt` a través de un enlace simbólico.
