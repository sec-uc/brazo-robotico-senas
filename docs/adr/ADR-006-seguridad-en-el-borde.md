# ADR-006: API Gateway como frontera de seguridad y JWT compartido

Estado: Aceptado · Fecha: 2026-09-12

## Contexto
El sistema heredado exponía 21 endpoints sin autenticación y llevaba una clave de Google Translate dentro de la app móvil (P8).

## Decisión
Solo el gateway se publica fuera de la red Docker. El servicio `auth` emite JWT (HS256, secreto compartido `AUTH_SECRET`); el gateway y los endpoints mutadores (vocabulario, actuador) los verifican con `rsp_common.auth.require_user`. Los secretos de proveedores externos viven en el servicio que los usa (PLN), nunca en clientes.

## Alternativas consideradas
- **Keycloak/OIDC + RS256/JWKS**: más robusto; se pospone porque añade un contenedor pesado y no aporta a los atributos evaluados. `decode_token` es el único punto a cambiar.
- **mTLS entre servicios**: innecesario en una red Docker privada de un solo host.

## Consecuencias
+ Ningún cliente porta secretos; superficie de ataque = un puerto.
− HS256 obliga a compartir el secreto con cada verificador; aceptable en el prototipo, no en producción multi‑tenant. Solo los cuatro servicios que verifican tokens (gateway, auth, sign-translation y actuator-control) reciben `AUTH_SECRET`; los demás no lo tienen. Una clave asimétrica (RS256/EdDSA) lo dejaría solo en `auth`.
