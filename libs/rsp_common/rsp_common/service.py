"""Esqueleto común de microservicio: FastAPI + bus + salud + métricas + logging.

Cada servicio hace:

    svc = Service("stt")
    app = svc.app

    @svc.on_startup
    async def start():
        await svc.bus.subscribe(Subjects.AUDIO_CHUNK, handle, queue="stt")

Y obtiene gratis: GET /health (liveness/readiness con estado del bus),
GET /metrics (Prometheus), logging estructurado y ciclo de vida ordenado.
Esto es lo que ISO 25010 llama *analizabilidad* y lo que el ATAM medirá
como capacidad de monitoreo: sin un contrato de salud uniforme, el
servicio de monitoreo no puede razonar sobre el sistema.
"""
from __future__ import annotations

import logging
import os
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from .bus import EventBus

EVENTS_PROCESSED = Counter(
    "rsp_events_processed_total", "eventos procesados", ["service", "subject", "outcome"]
)
STAGE_LATENCY = Histogram(
    "rsp_stage_latency_seconds",
    "latencia de procesamiento por etapa",
    ["service", "stage"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)


def setup_logging(name: str) -> logging.Logger:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [" + name + "] %(name)s: %(message)s",
    )
    return logging.getLogger(name)


class Service:
    def __init__(self, name: str, version: str = "0.1.0", needs_bus: bool = True) -> None:
        self.name = name
        self.version = version
        self.log = setup_logging(name)
        self.nats_url = os.getenv("NATS_URL", "nats://localhost:4222")
        self.bus = EventBus(self.nats_url, name)
        self.needs_bus = needs_bus
        self._startup: list[Callable[[], Awaitable[None]]] = []
        self._shutdown: list[Callable[[], Awaitable[None]]] = []
        self.started_at = time.time()

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            if self.needs_bus:
                await self.bus.connect()
            for fn in self._startup:
                await fn()
            self.log.info("%s listo", name)
            yield
            for fn in self._shutdown:
                await fn()
            if self.needs_bus:
                await self.bus.close()

        self.app = FastAPI(title=f"rsp-{name}", version=version, lifespan=lifespan)
        self._register_common_routes()

    def on_startup(self, fn: Callable[[], Awaitable[None]]):
        self._startup.append(fn)
        return fn

    def on_shutdown(self, fn: Callable[[], Awaitable[None]]):
        self._shutdown.append(fn)
        return fn

    def _register_common_routes(self) -> None:
        @self.app.get("/health", tags=["ops"])
        async def health():
            bus_ok = (not self.needs_bus) or self.bus.connected
            return {
                "service": self.name,
                "version": self.version,
                "status": "ok" if bus_ok else "degraded",
                "bus_connected": self.bus.connected,
                "uptime_s": round(time.time() - self.started_at, 1),
            }

        @self.app.get("/metrics", tags=["ops"])
        async def metrics():
            return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    def track(self, subject: str, outcome: str = "ok") -> None:
        EVENTS_PROCESSED.labels(self.name, subject, outcome).inc()

    def timer(self, stage: str):
        return STAGE_LATENCY.labels(self.name, stage).time()
