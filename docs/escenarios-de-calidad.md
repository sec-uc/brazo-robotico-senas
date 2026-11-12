# Escenarios de calidad

Formato de seis partes (Bass, Clements & Kazman). Cada escenario tiene un método de medición reproducible con los scripts del repositorio. Línea base as‑is = sistema heredado (`kichwa-app-api` + `robot_conection_app`); cuando el as‑is no permite medir, se indica.

| ID | Atributo | Fuente | Estímulo | Artefacto | Entorno | Respuesta esperada | Medida de respuesta | Cómo medir |
|---|---|---|---|---|---|---|---|---|
| QA‑01 | Rendimiento | Usuario | Enunciado de 1–3 palabras (texto o audio ≤ 3 s) | Pipeline completo | Operación normal, 1 robot, 1 host | El plan de gestos queda aceptado por el actuador | **Latencia de software** (captured→accepted) p95 ≤ 300 ms sin STT; ≤ 1 500 ms con Whisper `base` CPU | `scripts/benchmark_latency.py --n 30` (echo) y con `STT_PROVIDER=whisper --audio-dir` |
| QA‑02 | Rendimiento | Usuario | Idem | Actuadores | Idem | La seña se ejecuta | Latencia mecánica por gesto ≈ 15×150 ms + hold (≈ 2,9 s) — igual al as‑is; la arquitectura no la empeora | idem, columna `mechanical_ms` |
| QA‑03 | Modificabilidad | Desarrollador | Sustituir el motor STT (echo → whisper → kichwa) | Servicio STT | Desarrollo | Sin cambios en otros servicios ni contratos | Archivos tocados ≤ 2, ninguno fuera de `services/stt`; tiempo ≤ 2 h | Experimento de cambio documentado (diff, `git diff --stat`) vs as‑is (7 archivos de STT en la app + pantalla) |
| QA‑04 | Modificabilidad | Equipo lingüístico | Añadir 10 señas nuevas | Traducción a señas | Operación | Disponibles de inmediato | 0 despliegues, 0 recompilaciones; `PUT /v1/signs/{gloss}` ×10 | Script + verificación `GET /v1/signs`; as‑is: editar Excel + recompilar app |
| QA‑05 | Modificabilidad | Desarrollador | Sustituir el brazo por un simulador o ROS 2 | Control de actuadores | Desarrollo | Un driver nuevo | Archivos tocados ≤ 2 dentro de `actuator-control`; ninguna tabla de límites duplicada | `grep` de límites de servos en el repo: 1 definición (as‑is: 3) |
| QA‑06 | Disponibilidad | Operador | Cae el servicio STT (kill) | Plataforma | Operación | Los demás servicios siguen respondiendo; modo texto operativo; al volver STT, continúa | `/v1/system/health` reporta solo `stt` caído; sesión en modo texto termina `done`; recuperación < 20 s | `docker compose stop stt` + e2e_demo en modo texto + `start` |
| QA‑07 | Escalabilidad | Carga | 5 enunciados concurrentes | STT | Operación | Se reparten entre réplicas | Con `STT_REPLICAS=3`, p95 de captured→transcribed baja ≥ 40 % respecto a 1 réplica (con Whisper) | benchmark con concurrencia (extensión de `benchmark_latency.py`) |
| QA‑08 | Observabilidad | Operador | Consulta qué pasó en una sesión | Telemetría/Monitoreo | Operación | Línea de tiempo completa por etapa | 100 % de sesiones con 7 marcas de `trace`; tablero Grafana muestra p95 e2e | `GET /v1/sessions/{id}`; Grafana `rsp-pipeline` |
| QA‑09 | Seguridad | Atacante | Petición sin token a `/v1/actuator/reset` | Gateway | Operación | Rechazo | HTTP 401 en 100 % de intentos; ningún secreto de proveedor en clientes | Prueba automatizada; `grep -r AIza` en repos = 0 (as‑is: 3) |
| QA‑10 | Interoperabilidad | Cliente nuevo | Un cliente web (curl/JS) usa la plataforma | Gateway | Operación | Funciona sin cambios de servidor | Demo completa con `curl` + SSE | `scripts/e2e_demo.py` no depende de Flutter |
| QA‑11 | Mantenibilidad (estructural) | — | — | Código | — | Módulos pequeños y cohesionados | Ningún módulo > 400 líneas (as‑is: 2 057 y 2 076); 0 servicios que importen código de otro servicio | `wc -l`, `grep import` |
| QA‑12 | Fiabilidad | Usuario | Palabra sin seña ni letras | Traducción a señas | Operación | El plan se ejecuta con lo conocido y se registra la brecha | `unknown_glosses` en telemetría; sesión `done` | e2e_demo con "xyz123" |

## Resultados

Los resultados de cada escenario, con sus gráficas y los límites de la evaluación, están en [evaluacion.md](evaluacion.md).
