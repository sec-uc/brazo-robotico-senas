# ADR-011: Autorización por rol en la pasarela

Estado: Aceptado · Fecha: 2026-10-28

## Contexto
La pasarela exigía un token válido en todas las rutas protegidas, pero no distinguía roles: cualquier usuario autenticado podía mover el brazo o modificar el vocabulario.

## Decisión
`rsp_common.auth.require_role` añade una comprobación de rol (HTTP 403 si falta). Crear sesiones, enviar voz o texto, mover el actuador (`POST /v1/actuator/*`) y escribir el vocabulario (`PUT`/`DELETE /v1/signs/*`) exigen `operator` o `admin`; las lecturas, la telemetría y la vista previa de planes admiten cualquier rol, incluido `viewer`.

## Alternativas consideradas
- **Autorización en cada servicio**: repite la política; los servicios internos no son alcanzables desde fuera de la red de contenedores.
- **Listas de control por usuario**: excesivas para los perfiles del dominio (operador, lingüista, observador).

## Consecuencias
+ Un token de solo lectura recibe 403 en las 8 rutas que mueven el brazo o cambian el vocabulario.
− Los roles viajan en el token: revocarlos requiere su expiración o una lista de revocación.
