# Auditoría manual de la métrica de impacto de cambio (R11)

Valida a mano `results/coupling.json`, que generó `scripts/coupling_analysis.py` contando por regex los archivos que mencionan el conocimiento afectado. El detalle por archivo, con veredicto, justificación y líneas, está en `results/coupling_audit.json`.

**Criterio.** Se aplicó igual en los dos sistemas:
- **requiere_cambio:** hay que editar el archivo para que el sistema siga siendo correcto.
- **solo_mencion:** el archivo contiene el término, pero no se edita.
- **dudoso:** depende de una decisión. Puede ser un valor por defecto que se cambia en tiempo de ejecución, código muerto o el nombre de una clave de configuración.

Como falsos negativos se cuentan los archivos que no son código y que también habría que tocar: requirements, Dockerfile, YAML, pubspec y *assets*.

| Escenario | Sistema | Regex | VP | FP | Dudosos | FN | Precisión | Requieren cambio (rango con dudosos) | Unidades |
|---|---|---|---|---|---|---|---|---|---|
| Motor de voz | actual | 11 | 7 | 2 | 2 | 3 (+1) | 0,78 | **10** (10–13) | 2 |
| Motor de voz | propuesta | 1 | 1 | 0 | 0 | 2 (+1) | 1,00 | **3** (3–4) | 1 |
| Vocabulario | actual | 9 | 0 | 9 | 0 | 1 (+1) | 0 | **1 asset + recompilar la app** (1–2) | 1 |
| Vocabulario | propuesta | 1 | 0 | 1 | 0 | 0 (+1) | 0 | **0** (0–1) | 0 |
| Límites de servos | actual | 4 | 1 | 2 | 1 | 2 | 0,33 | **3** (3–4) | 1 |
| Límites de servos | propuesta | 1 | 1 | 0 | 0 | 1 | 1,00 | **2** | 2 |
| IP en el código | actual | 5 | 0 | 2 | 3 | 0 | 0 | **0** (0–3) | 0 |
| IP en el código | propuesta | 2 | 0 | 2 | 0 | 0 (+1) | 0 | **0** (0–1) | 0 |

**Hallazgos que cambian la lectura del artículo**

1. **Motor de voz: de "11 frente a 1" a "10 frente a 3".** La regex sobrestima un poco el sistema actual, porque cuenta dos *scripts* de instalación que no se ejecutan en producción. También subestima la propuesta, porque no cuenta `requirements-<motor>.txt` ni el `Dockerfile` del servicio. La conclusión cualitativa se mantiene: en la propuesta el cambio queda confinado a 1 unidad desplegable, `stt`; en el actual afecta 2, la API y la app. Pero con 3 archivos la meta de QA-03 (≤ 2) no se cumple para un motor nuevo. Solo se cumple para el cambio echo → Whisper que se midió, porque ese proveedor ya existía.
2. **Vocabulario: la métrica "9 frente a 1" no es válida.** Los 9 archivos del sistema actual solo leen el Excel, y el único de la propuesta es una docstring. El contraste correcto es de despliegue, no de archivos:
   - **Actual:** editar `assets/abecedario.xlsx`, recompilar y redistribuir el APK.
   - **Propuesta:** 0 archivos y una llamada a la API.

   Coincide con QA-04.
3. **Límites de servos: la propuesta no tiene una sola definición.** `rsp-mobile-client/.../ServoTestPanel.dart` duplica los rangos en `servoMins`/`servoMaxs`. La seguridad sigue en el servidor, que recorta los ángulos con su tabla, pero la interfaz quedaría desalineada. En el sistema actual, de las "5 definiciones": 3 están vivas (`servo_configuration.dart`, `RobotConnectionService.dart` y la del modelo de transcripción), 1 está en código muerto y 1 está comentada. Queda 3–4 frente a 2. Recomendación: que el cliente obtenga los límites del actuador por API.
4. **IP en el código: sin diferencia real en el código vivo.** En la rama `dev-pp`, la app ya lee la IP desde Ajustes. Las IP escritas en el código son valores por defecto o iniciales que se sobrescriben, o están en código muerto.
5. **Código muerto en el sistema actual.** `sign_language_screen.dart`, de 2 565 líneas y el "módulo más grande" que cita el diagnóstico, no se referencia desde ninguna parte. La ruta `/sign_language` abre `TranscriptionGesturesView`. Lo mismo pasa con `lib/debug/excel_*.dart`. Esto matiza P12 ("la misma orquestación existe dos veces") y la cifra de 2 565 líneas: el duplicado existe, pero no se ejecuta.
6. **La regex distingue mayúsculas y minúsculas.** Se le escapan `_getServoLimits` en `RobotConnectionService`, `servoMins` y `localhost`. Por eso tiene falsos negativos en los dos sistemas.

**Limitaciones de la auditoría.** La hizo una sola persona revisora, un asistente de IA, sin un segundo codificador ni cálculo de acuerdo. "Requiere cambio" es un juicio sobre el diseño, no el resultado de implementar el cambio. Las líneas citadas corresponden al estado en disco el 28-10-2026.
