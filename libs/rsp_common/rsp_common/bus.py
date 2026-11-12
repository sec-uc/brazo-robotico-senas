"""Cliente de bus de eventos (NATS) con serialización JSON y reconexión.

Se eligió NATS (ADR-002) por su latencia sub-milisegundo, tamaño mínimo
(apto para un mini-PC junto al brazo) y semántica pub/sub + request/reply.
Este módulo es la única pieza que sabe que el bus es NATS: si mañana se
cambia a MQTT, solo se reescribe este archivo.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from typing import TypeVar

import nats
from nats.aio.client import Client as NATSClient
from nats.aio.msg import Msg
from pydantic import BaseModel, ValidationError

from .events import SUBJECT_TO_MODEL, BaseEvent

log = logging.getLogger("rsp.bus")
E = TypeVar("E", bound=BaseEvent)
Handler = Callable[[BaseEvent], Awaitable[None]]


def _env_subjects(name: str) -> set[str]:
    return {s.strip() for s in os.getenv(name, "").split(",") if s.strip()}


class EventBus:
    """Bus de eventos.

    - Consumo concurrente (ADR-009): nats-py entrega los mensajes de una
      suscripción de uno en uno y espera al handler; aquí cada mensaje se
      procesa como una tarea propia, con un máximo de `BUS_MAX_INFLIGHT` en
      vuelo por servicio. Sin esto, un servicio atiende una sesión a la vez.
    - Ingreso durable (ADR-010): los asuntos de `BUS_DURABLE_SUBJECTS` se
      publican en un stream JetStream de cola de trabajo con antigüedad máxima
      `BUS_DURABLE_MAX_AGE_S`; si el consumidor cae, los mensajes esperan y se
      entregan al volver, en lugar de perderse. El resto sigue en NATS básico.
    """

    STREAM = "RSP_DURABLE"

    def __init__(self, url: str, service_name: str) -> None:
        self.url = url
        self.service_name = service_name
        self._nc: NATSClient | None = None
        self._js = None
        self._subs = []
        self._tasks: set[asyncio.Task] = set()
        self.max_inflight = int(os.getenv("BUS_MAX_INFLIGHT", "64"))
        self._sem = asyncio.Semaphore(self.max_inflight)
        self.durable_subjects = _env_subjects("BUS_DURABLE_SUBJECTS")

    async def _ensure_stream(self) -> None:
        from nats.js.api import RetentionPolicy, StreamConfig
        from nats.js.errors import BadRequestError, NotFoundError

        cfg = StreamConfig(name=self.STREAM, subjects=sorted(self.durable_subjects),
                           retention=RetentionPolicy.WORK_QUEUE,
                           max_age=float(os.getenv("BUS_DURABLE_MAX_AGE_S", "30")))
        try:
            await self._js.stream_info(self.STREAM)
            await self._js.update_stream(cfg)
        except NotFoundError:
            try:
                await self._js.add_stream(cfg)
            except BadRequestError:  # otro servicio lo creó a la vez
                pass

    async def _dispatch(self, fn, *args, on_done=None) -> None:
        """Ejecuta `fn` como tarea propia sin superar `max_inflight`.

        El cupo se toma AQUÍ, en el callback de NATS y antes de crear la tarea:
        cuando está lleno, el callback espera, y la contrapresión vuelve a los
        límites de pendientes de nats-py en lugar de acumular tareas en memoria.
        """
        await self._sem.acquire()

        async def run() -> None:
            try:
                await fn(*args)
            except Exception:  # noqa: BLE001
                log.exception("error procesando mensaje")
            finally:
                self._sem.release()
                if on_done is not None:
                    await on_done()

        task = asyncio.create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def connect(self, retries: int = 30, delay: float = 1.0) -> None:
        last: Exception | None = None
        for attempt in range(retries):
            try:
                self._nc = await nats.connect(
                    self.url,
                    token=os.getenv("BUS_TOKEN") or None,  # nunca en la URL: la URL se registra
                    name=self.service_name,
                    max_reconnect_attempts=-1,
                    reconnect_time_wait=1,
                )
                log.info("conectado a NATS %s", self.url)
                if self.durable_subjects:
                    self._js = self._nc.jetstream()
                    await self._ensure_stream()
                    log.info("asuntos durables (JetStream): %s", sorted(self.durable_subjects))
                return
            except Exception as exc:  # noqa: BLE001
                last = exc
                log.warning("NATS no disponible (%s/%s): %s", attempt + 1, retries, exc)
                await asyncio.sleep(delay)
        raise RuntimeError(f"no se pudo conectar a NATS en {self.url}: {last}")

    async def close(self) -> None:
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=5)
        if self._nc and not self._nc.is_closed:
            await self._nc.drain()

    @property
    def connected(self) -> bool:
        return bool(self._nc and self._nc.is_connected)

    async def publish(self, subject: str, event: BaseEvent) -> None:
        assert self._nc, "bus no conectado"
        if event.producer == "unknown":
            event.producer = self.service_name
        payload = event.model_dump_json().encode()
        if self._js is not None and subject in self.durable_subjects:
            await self._js.publish(subject, payload, stream=self.STREAM)
        else:
            await self._nc.publish(subject, payload)

    async def subscribe(
        self,
        subject: str,
        handler: Handler,
        queue: str | None = None,
        model: type[BaseEvent] | None = None,
    ) -> None:
        """Suscribe `handler` a `subject`.

        `queue` habilita balanceo: varias réplicas del mismo servicio comparten
        la cola y cada mensaje lo procesa solo una (escalabilidad horizontal).
        """
        assert self._nc, "bus no conectado"
        model = model or SUBJECT_TO_MODEL.get(subject)

        durable = self._js is not None and subject in self.durable_subjects

        async def _cb(msg: Msg) -> None:
            try:
                data = json.loads(msg.data)
                event = model.model_validate(data) if model else BaseEvent.model_validate(data)
            except (ValidationError, json.JSONDecodeError) as exc:
                log.error("evento inválido en %s: %s", msg.subject, exc)
                if durable:
                    await msg.term()  # nunca será válido: no reintentar
                return
            # se confirma (ack) al terminar el handler: si el proceso muere antes, JetStream lo reentrega
            await self._dispatch(handler, event, on_done=msg.ack if durable else None)

        if durable:
            from nats.js.api import ConsumerConfig

            name = queue or self.service_name
            # sin confirmar no habrá más de max_inflight; ack_wait cubre la inferencia más lenta
            cfg = ConsumerConfig(max_ack_pending=self.max_inflight,
                                 ack_wait=float(os.getenv("BUS_ACK_WAIT_S", "60")))
            sub = await self._js.subscribe(subject, queue=name, durable=name, cb=_cb, manual_ack=True,
                                           stream=self.STREAM, config=cfg)
        else:
            sub = await self._nc.subscribe(subject, queue=queue or "", cb=_cb)
        self._subs.append(sub)
        log.info("suscrito a %s (queue=%s)", subject, queue)

    async def subscribe_raw(self, subject: str, handler: Callable[[str, dict], Awaitable[None]]) -> None:
        """Suscripción sin validar modelo (para telemetría/monitoreo)."""
        assert self._nc, "bus no conectado"

        async def _cb(msg: Msg) -> None:
            try:
                data = json.loads(msg.data)
            except json.JSONDecodeError:
                log.error("mensaje no JSON en %s", msg.subject)
                return
            await self._dispatch(handler, msg.subject, data)

        sub = await self._nc.subscribe(subject, cb=_cb)
        self._subs.append(sub)


def dump(event: BaseModel) -> dict:
    return json.loads(event.model_dump_json())
