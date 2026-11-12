# Robot Signs Platform (RSP)

> Material de réplica del artículo «Diseño de una arquitectura de referencia basada en microservicios para sistemas robóticos de traducción de voz a lengua de señas» (Wachapa Yankur y Timbi Sisalima, Universidad Politécnica Salesiana). Código bajo licencia MIT (`LICENSE`); datos y resultados propios bajo CC BY 4.0 (`LICENSE-DATA.md`). Este repositorio no incluye los datos del proyecto PICCG24-16 de la Universidad Católica de Cuenca ni los audios del corpus: ver `data/README.md`.

Prototipo de la **arquitectura de referencia basada en microservicios para sistemas robóticos de traducción automática de voz a lengua de señas**, instanciada sobre el brazo robótico de la Universidad Católica de Cuenca. Sustituye a los repositorios heredados `kichwa-app-api` y `robot_conection_app`.

```
voz/texto ─▶ voice-capture ─▶ stt ─▶ nlp ─▶ sign-translation ─▶ actuator-control ─▶ brazo
                 │             │       │            │                  │
                 └────────── NATS (eventos con trace de tiempos) ───────┘──▶ telemetry ──▶ Prometheus/Grafana
   clientes ─▶ gateway (JWT, SSE) ◀─ auth
```

| Servicio | Puerto local | Función |
|---|---|---|
| `gateway` | 8080 | Entrada única: JWT, proxy, SSE por sesión, salud agregada |
| `auth` | 8001 | Emisión/validación de tokens |
| `voice-capture` | 8002 | Ingesta de audio/texto → `voice.audio.chunk` |
| `stt` | 8003 | Audio → texto (`echo` \| `whisper` \| `kichwa`) |
| `nlp` | 8004 | Traducción qu→es y glosado → `nlp.gloss.sequence` |
| `sign-translation` | 8005 | Glosas → plan de gestos; CRUD de vocabulario |
| `actuator-control` | 8006 | Ejecución segura en el brazo (`simulator` \| `serial`) |
| `telemetry` | 8007 | Línea de tiempo por sesión, latencias, métricas |
| Prometheus / Grafana | 9090 / 3000 | Monitoreo |

Documentación: `docs/arquitectura.md` (C4 + arc42), `docs/adr/` (decisiones), `docs/escenarios-de-calidad.md` (plan de evaluación), `contracts/asyncapi.yaml` (contratos de eventos).

## Arranque rápido (Docker)

```bash
cp .env.example .env
docker compose up --build            # simulador de brazo, STT en modo echo
python scripts/e2e_demo.py "hola mamá"
```

Con el brazo real en Linux: `ACTUATOR_DRIVER=serial SERIAL_PORT=/dev/ttyUSB0` en `.env` y descomentar `devices:` en `docker-compose.yml`; en macOS ver la sección siguiente. Con Whisper local: `STT_PROVIDER=whisper` y construir `stt` con `requirements-whisper.txt`.

Con la tablet (voz real): `make up-voz` levanta la plataforma con Whisper `small` sesgado con el vocabulario kichwa del diccionario (`KICHWA_HOTWORDS_PATH`); sin él, «yaku» se transcribe «y aco». `make up` y los scripts de evaluación dejan STT en `echo` a propósito, así que después de una evaluación hay que volver a ejecutar `make up-voz`. Si el brazo va conectado por USB a la tablet, la app `rsp-mobile-client` hace de puente: reenvía al brazo cada gesto que la plataforma marca como `executing`.

## Brazo real en macOS (USB)

Docker Desktop en macOS no expone puertos USB a los contenedores, así que el servicio de actuadores corre **nativo** y el resto en Docker:

```bash
python3 scripts/serial_probe.py                      # lista puertos; el brazo suele ser /dev/tty.usbserial-XXXX
python3 scripts/serial_probe.py /dev/tty.usbserial-XXXX --servo 12 --angle 100   # prueba directa
scripts/run_actuator_native.sh /dev/tty.usbserial-XXXX                            # actuador nativo + gateway reapuntado
python3 scripts/e2e_demo.py "hola"                                                # ahora mueve el brazo
```

`scripts/doctor.sh` comprueba requisitos, construye, levanta y ejecuta la demo en un solo paso.

## Arranque sin Docker (desarrollo / CI)

```bash
pip install -e libs/rsp_common -r services/gateway/requirements.txt pyserial
# nats-server: https://github.com/nats-io/nats-server/releases
scripts/run_local.sh start
python scripts/e2e_demo.py "tengo 4 manzanas"
python scripts/benchmark_latency.py --n 30 --out results/bench.csv
scripts/run_local.sh stop
```

## Pruebas

```bash
python -m pytest services              # unitarias (glosado, planificación, hardware)
```

## Uso de la API (vía gateway)

```bash
TOKEN=$(curl -s -X POST localhost:8080/auth/token -H 'content-type: application/json' \
  -d '{"username":"operator","password":"operator123"}' | jq -r .access_token)
SID=$(curl -s -X POST localhost:8080/v1/sessions -H "authorization: Bearer $TOKEN" | jq -r .session_id)
curl -N localhost:8080/v1/sessions/$SID/events -H "authorization: Bearer $TOKEN" &   # SSE
curl -s -X POST localhost:8080/v1/sessions/$SID/text -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{"text":"hola","language_hint":"es"}'
curl -s -X POST localhost:8080/v1/sessions/$SID/audio -H "authorization: Bearer $TOKEN" -F file=@audio.wav
curl -s localhost:8080/v1/telemetry/summary -H "authorization: Bearer $TOKEN"
curl -s -X PUT localhost:8080/v1/signs/GRACIAS -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"gloss":"GRACIAS","angles":[90,90,90,90,90,90,90,90,90,90,90,90,90,90,90]}'
```

## Estructura

```
libs/rsp_common/      contratos de eventos, cliente NATS, Service base, auth
services/<svc>/app    código de cada microservicio (+ tests/, Dockerfile, requirements)
contracts/            AsyncAPI de los eventos
data/                 datos de ejemplo, manifiestos de los corpus de voz (ver data/README.md)
scripts/              migración, demo e2e, benchmark, arranque local
monitoring/           Prometheus + Grafana (tablero rsp-pipeline)
docs/                 arquitectura, ADRs, escenarios de calidad
results/              salidas de benchmarks
```

## Migración desde los sistemas heredados

| Heredado | Destino |
|---|---|
| `kichwa_translator/` (Whisper + fonética Kichwa) | proveedor `kichwa` de `stt` (adapter) + diccionario en `nlp` |
| `abecedario.xlsx` (no incluido) | `scripts/migrate_excel_signs.py` → `data/signs_seed.json` → BD de `sign-translation` |
| `RobotController` / límites de servos | `actuator-control/app/hardware.py` (única fuente) |
| `RobotConnectionService` USB/BT/Mock | `actuator-control/app/drivers.py` |
| Google Translate en la app (API key) | `nlp` con `GOOGLE_TRANSLATE_API_KEY` (secreto del servidor) |
| App Flutter | cliente ligero del gateway (audio + SSE); pendiente de adaptar |

## Publicación y réplica

Este repositorio es una instantánea, sin historial, del repositorio de desarrollo. Los *commits* que cita el artículo (Tabla S1 del suplemento) pertenecen a ese historial; `results/` conserva en cada archivo el *commit* de los instrumentos y del sistema medido. Diferencias con el repositorio de desarrollo:

- `data/signs_seed.json` y `data/kichwa_dictionary.json` son datos de ejemplo sintéticos, no los del proyecto. La plataforma arranca y las pruebas pasan con ellos, pero los resultados de exactitud y de QA-12 del artículo requieren los datos originales.
- Las dos pruebas que comprobaban el tamaño de esos datos (`test_seed_file_is_valid` y `test_gloss_and_dictionary_endpoints`) se ajustaron al tamaño de los de ejemplo.
- Los audios del corpus se regeneran con `scripts/generate_audio_corpus.sh` (macOS); `data/audio_sha256.txt` da las sumas de los originales.
- Incluye un endurecimiento posterior a la evaluación: la clave de firma (`AUTH_SECRET`, 32 bytes o más), el token del bus (`BUS_TOKEN`) y los usuarios (`AUTH_USERS`) son obligatorios en `.env` (ver `.env.example`), cada sesión ---y su telemetría--- solo la usa o la consulta quien la creó (o un admin), la lista de sesiones es solo para admin, el texto dicho ya no se registra a nivel INFO y la clave del traductor externo viaja en cabecera.
- El proveedor `kichwa` del reconocimiento y la línea base de la arquitectura actual necesitan `kichwa-app-api`, que no es público.

Prototipo de investigación: no está pensado para producción.
