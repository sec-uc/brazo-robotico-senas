# ADR-001: Estilo arquitectónico de microservicios con un único lenguaje (Python)

Estado: Aceptado · Fecha: 2026-09-12

## Contexto
El sistema heredado son dos monolitos (API FastAPI y app Flutter) que duplican responsabilidades y no se integran (P1, P2, P4, P11). La tesis debe producir una arquitectura de referencia cuyo aporte sea la separación de responsabilidades por etapa del pipeline voz→seña, evaluable en modificabilidad, rendimiento y disponibilidad. El equipo es pequeño y el pipeline Kichwa existente está en Python.

## Decisión
Descomponer el sistema en ocho microservicios por capacidad (captura, STT, PLN, señas, actuadores, telemetría, gateway, auth), más un monitoreo de terceros, cada uno con su propio despliegue y contrato, y con datos propios los que guardan estado (ADR-014). *Nota: la versión inicial de esta ADR contaba el monitoreo como noveno servicio.* Implementar todos en Python 3.11 + FastAPI compartiendo únicamente `rsp_common` (contratos, bus, utilidades).

## Alternativas consideradas
- **Monolito modular** (un proceso, módulos con las mismas fronteras por capacidad y los mismos contratos): el más simple de operar y el que cumpliría la mayoría de los escenarios de modificabilidad, seguridad, mantenibilidad y fiabilidad, porque dependen de dónde se trazan las fronteras y no del número de procesos. No aísla los fallos del modelo de reconocimiento ni permite escalarlo por separado (P5, P6).
- **Monolito modular con el reconocimiento en un proceso aparte**: conserva casi toda la simplicidad del anterior y aísla los fallos del reconocimiento, que es lo único que la evaluación mostró dependiente del despliegue separado (QA-06); es la alternativa más fuerte.
- **Microservicios** (la elegida): además de lo anterior, permite desplegar, escalar y observar cada capacidad por separado, a cambio de más piezas que operar. Se eligió también porque el tema aprobado del trabajo exigía una arquitectura de este estilo; la evaluación separa por eso lo que aportan las fronteras de lo que aporta el despliegue.
- **Políglota (Python IA + .NET gateway/auth)**: demuestra heterogeneidad, pero duplica tooling y no aporta a los atributos evaluados; la referencia lo permite (los contratos son JSON), la instancia no lo necesita.
- **ROS 2 como marco integrador**: natural para robótica, pero impone su middleware (DDS) a servicios que no son robóticos (auth, gateway, PLN) y complica el despliegue en un mini‑PC; se deja como driver del servicio de actuadores.

## Revisión tras la evaluación
Solo QA-06 (disponibilidad ante la caída del reconocimiento) dependió del despliegue separado; QA-07 no mostró ganancia de las réplicas en un equipo. La referencia mantiene los microservicios, que fija el objetivo aprobado del trabajo: una unidad desplegable por capacidad. Lo que varía entre instancias es el orquestador, el número de réplicas y los nodos. La evaluación no comparó la propuesta con un monolito modular de las mismas fronteras, y esa comparación queda como trabajo futuro.

## Consecuencias
+ Cada etapa se sustituye y observa por separado, y puede escalarse por separado (no demostrado en un equipo, QA-07); el pipeline Kichwa se reutiliza como proveedor.
− Más piezas que operar (16 contenedores en la instancia final: 8 servicios, bus, Prometheus, Grafana y 5 bases de datos); mitigado con Docker Compose y `Service` común.
− Latencia añadida: las etapas propias suman unos 2 ms en la mediana; frente al sistema anterior, con el mismo código de reconocimiento, la latencia típica fue 66 ms mayor, casi toda en el protocolo HTTP de la sesión (QA‑01).
