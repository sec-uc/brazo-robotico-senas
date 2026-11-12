"""Utilidades compartidas por las pruebas de todos los servicios (importables
como `from rsp_testkit import ...`; este conftest añade la raíz al sys.path).

- `FakeBus`: sustituye al EventBus (NATS) y registra lo publicado, para
  probar los manejadores de eventos sin bus real.
- `make_token`: emite JWT con el mismo secreto/algoritmo que `auth`.
- `fresh_registry`: quita de Prometheus las métricas que un `app.main`
  registra a nivel de módulo, para poder importarlo de nuevo en cada prueba.
"""
from __future__ import annotations

import time

import jwt
from prometheus_client import REGISTRY

SECRET = "secreto-de-pruebas-de-32-bytes-o-mas!!"


class FakeBus:
    def __init__(self, connected: bool = True) -> None:
        self.published: list[tuple[str, object]] = []
        self.subscriptions: list[str] = []
        self._connected = connected

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self, *a, **k) -> None:
        self._connected = True

    async def close(self) -> None:
        self._connected = False

    async def publish(self, subject, event) -> None:
        self.published.append((subject, event))

    async def subscribe(self, subject, handler, queue=None, model=None) -> None:
        self.subscriptions.append(subject)

    async def subscribe_raw(self, subject, handler) -> None:
        self.subscriptions.append(subject)

    def events(self, subject: str) -> list:
        return [e for s, e in self.published if s == subject]


def make_token(sub: str = "operator", roles=("operator",), secret: str = SECRET, ttl: int = 3600) -> str:
    now = int(time.time())
    return jwt.encode({"sub": sub, "roles": list(roles), "iat": now, "exp": now + ttl}, secret, algorithm="HS256")


def fresh_registry(*prefixes: str) -> None:
    for collector, names in list(REGISTRY._collector_to_names.items()):  # noqa: SLF001
        if any(n.startswith(p) for n in names for p in prefixes):
            REGISTRY.unregister(collector)
