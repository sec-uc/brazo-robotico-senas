# ADR-014: Cada servicio maneja su propia base de datos

Estado: Aceptado · Fecha: 2026-11-12

## Contexto
La ADR-008 ya exigía que el vocabulario fuera propiedad de un único servicio, pero la persistencia no se veía en el despliegue: señas y telemetría guardaban archivos SQLite dentro de su contenedor, los usuarios vivían en una variable de entorno, el diccionario kichwa en un archivo JSON y la tabla del hardware en el código. Al abrir `docker-compose.yml` no aparecía ninguna base de datos, y la regla «cada servicio es dueño de sus datos» no se podía comprobar.

## Decisión
Cada servicio con datos maneja su propia base PostgreSQL, en un contenedor aparte y en una red privada que solo comparte con ella:

| Servicio | Base | Datos |
|---|---|---|
| Autenticación | `auth-db` | Usuarios, roles y contraseñas derivadas con scrypt y sal por usuario |
| Procesamiento lingüístico | `nlp-db` | Diccionario kichwa–español |
| Traducción a señas | `signs-db` | Vocabulario de señas |
| Control de actuadores | `actuator-db` | Tabla del hardware y su calibración: límites, orden, cableado y canales sin respuesta |
| Telemetría | `telemetry-db` | Sesiones y eventos |

La pasarela, la captura de voz y el reconocimiento no guardan nada: son servicios sin estado y no tienen base. La primera vez, cada base se siembra desde su fuente anterior (variable, JSON, semilla o constantes del módulo); después manda la base. El acceso pasa por `rsp_common.db.Database`, que admite PostgreSQL en el despliegue y un archivo SQLite en las pruebas y en el desarrollo sin contenedores, con el mismo SQL. La contraseña de cada base va en `PGPASSWORD`, no en la URL.

## Alternativas descartadas
- **Una base PostgreSQL compartida con un esquema por servicio**: menos contenedores, pero un servicio podría leer el esquema de otro y un fallo de la base detendría a todos.
- **Mantener SQLite embebido**: cumple la propiedad de los datos, pero no admite más de una réplica del servicio y la persistencia no es visible ni operable (respaldo, inspección) desde el despliegue.
- **Dar una base también a los servicios sin estado**: no tienen datos que guardar; sería una base vacía.

## Consecuencias
+ La propiedad de los datos se comprueba en el despliegue: cada base solo es alcanzable por su servicio.
+ Recalibrar un límite, recablear un canal o dar de baja uno que no responde es un cambio de datos en `actuator-db`, no de código.
+ Las contraseñas de los usuarios dejan de guardarse en claro.
− El despliegue pasa de 11 a 16 contenedores: más memoria, más tiempo de arranque y cinco bases que respaldar.
− La semilla del vocabulario se actualizó a las 173 señas que estaban en uso: la base evaluada hasta entonces no se podía reconstruir desde el repositorio (la semilla tenía 104).
