# ADR-007: Proveedores intercambiables (Strategy) en STT, traducción y actuadores

Estado: Aceptado · Fecha: 2026-09-12

## Contexto
La app heredada acumuló siete variantes de servicio de voz porque no había un punto único de sustitución (P2). La evaluación de modificabilidad necesita que "cambiar el motor de STT" sea una operación acotada y medible.

## Decisión
Cada servicio con dependencia externa define una interfaz (`STTProvider`, `translate_qu_es`, `Driver`) y selecciona la implementación por variable de entorno (`STT_PROVIDER`, `TRANSLATION_PROVIDER`, `ACTUATOR_DRIVER`). El proveedor `echo`/`simulator` existe siempre para pruebas y benchmarks deterministas.

## Alternativas consideradas
- **Un servicio por proveedor** (stt-whisper, stt-kichwa…): más aislamiento, pero multiplica despliegues y la selección pasa al enrutamiento del bus.

## Consecuencias
+ Cambio de proveedor = un archivo + una variable; medible en la Fase 5 (archivos tocados, líneas, tiempo).
− El servicio carga solo un proveedor a la vez; usar dos en paralelo requiere dos instancias con configuración distinta.
