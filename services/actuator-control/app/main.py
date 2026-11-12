"""Servicio de control de actuadores.

Consume `signs.gesture.plan` y ejecuta cada gesto en el brazo a través del
driver configurado, publicando `actuator.state` en cada transición
(accepted → executing → done | failed). Es el único servicio con acceso al
hardware y el único que conoce sus límites físicos: cualquier ángulo que
llegue fuera de rango se recorta aquí, nunca antes.

Los planes se ejecutan en serie (una cola interna): un brazo no puede hacer
dos señas a la vez, y el orden de llegada es el orden de ejecución.
"""
from __future__ import annotations

import asyncio
import os

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from rsp_common.auth import require_user
from rsp_common.events import ActuatorState, ActuatorStatus, GesturePlan, Subjects, now_ms
from rsp_common.service import Service

from .drivers import build_driver, move_all, ms
from . import hardware
from .hardware import CHANNEL, EXECUTION_ORDER, INACTIVE, SERVOS, initial_positions, order_targets, to_channels

svc = Service("actuator-control")
app = svc.app

INTER_COMMAND_MS = int(os.getenv("INTER_COMMAND_MS", "150"))
driver = build_driver()
queue: asyncio.Queue[GesturePlan] = asyncio.Queue()
_worker: asyncio.Task | None = None
_lock = asyncio.Lock()


async def _publish(plan: GesturePlan, status: ActuatorStatus, **extra) -> None:
    # ADR-012: la traza es acumulativa; el evento final lleva todas las marcas (accepted incluido).
    # `executing` guarda la del primer gesto, no la del último.
    mark = now_ms()
    if status == ActuatorStatus.EXECUTING:
        plan.trace.setdefault(status.value, mark)
    else:
        plan.trace[status.value] = mark
    ev = ActuatorState(
        session_id=plan.session_id,
        status=status,
        servo_positions=dict(driver.positions),
        driver=driver.name,
        trace=dict(plan.trace),
        **extra,
    )
    await svc.bus.publish(Subjects.ACTUATOR_STATE, ev)


async def execute_plan(plan: GesturePlan) -> None:
    async with _lock:
        started = ms()
        try:
            for idx, gesture in enumerate(plan.gestures):
                targets = {t.servo_id: t.angle for t in gesture.targets}
                await _publish(plan, ActuatorStatus.EXECUTING, gesture_index=idx, gesture_gloss=gesture.gloss)
                with svc.timer("gesture"):
                    await move_all(driver, list(order_targets(to_channels(targets))), INTER_COMMAND_MS)
                    for pose in gesture.keyframes:  # seña con movimiento: el resto del trazo
                        await move_all(driver, list(order_targets(to_channels({t.servo_id: t.angle for t in pose}))),
                                       INTER_COMMAND_MS)
                    await asyncio.sleep(gesture.hold_ms / 1000)
            await _publish(plan, ActuatorStatus.DONE, execution_ms=ms() - started,
                           gesture_index=len(plan.gestures) - 1 if plan.gestures else None)
            svc.track(Subjects.GESTURE_PLAN)
        except Exception as exc:  # noqa: BLE001
            svc.log.exception("fallo ejecutando plan")
            await _publish(plan, ActuatorStatus.FAILED, error=str(exc), execution_ms=ms() - started)
            svc.track(Subjects.GESTURE_PLAN, "error")


async def worker() -> None:
    while True:
        plan = await queue.get()
        try:
            await execute_plan(plan)
        finally:
            queue.task_done()


async def handle_plan(event: GesturePlan):  # type: ignore[override]
    await _publish(event, ActuatorStatus.ACCEPTED)
    if event.options.get("dry_run"):
        # vista previa: el pipeline completo corre pero el brazo no se mueve
        await _publish(event, ActuatorStatus.DONE, execution_ms=0,
                       gesture_index=len(event.gestures) - 1 if event.gestures else None)
        svc.track(Subjects.GESTURE_PLAN, "dry_run")
        return
    await queue.put(event)


@svc.on_startup
async def start():
    global _worker
    if os.getenv("ACTUATOR_DATABASE_URL"):  # tabla del hardware y calibración en la base propia (ADR-014)
        from rsp_common.db import Database
        hardware.load_from(Database(os.environ["ACTUATOR_DATABASE_URL"]))
    await driver.connect()
    _worker = asyncio.create_task(worker())
    # sin queue group: si hubiera 2 brazos, cada instancia ejecutaría todo; un
    # brazo = una instancia (ADR-005)
    await svc.bus.subscribe(Subjects.GESTURE_PLAN, handle_plan)
    svc.log.info("driver: %s", driver.name)


@svc.on_shutdown
async def stop():
    if _worker:
        _worker.cancel()
    await driver.close()


# ----------------------------------------------------------------- API de operación
@app.get("/actuator/status", tags=["actuator"])
async def status():
    return {
        "driver": driver.name,
        "connected": driver.is_connected(),
        "queue_depth": queue.qsize(),
        "busy": _lock.locked(),
        "positions": driver.positions,
    }


@app.get("/actuator/servos", tags=["actuator"])
async def servos():
    return {"execution_order": EXECUTION_ORDER, "servos": [s.__dict__ for s in SERVOS.values()],
            "channels": CHANNEL,  # articulación lógica de las señas → canal físico
            "inactive": sorted(INACTIVE)}  # canales sin efecto: no enviarles comandos


class ServoCommand(BaseModel):
    servo_id: int = Field(ge=1, le=15)
    angle: int = Field(ge=0, le=180)


@app.post("/actuator/servo", tags=["actuator"])
async def move_servo(cmd: ServoCommand, user: dict = Depends(require_user)):
    """Prueba manual de un servo (equivale a ServoTestScreen de la app heredada)."""
    if _lock.locked():
        raise HTTPException(409, "el brazo está ejecutando un plan")
    async with _lock:
        await driver.move(cmd.servo_id, cmd.angle, INTER_COMMAND_MS)
    return {"servo_id": cmd.servo_id, "angle": driver.positions[cmd.servo_id]}


class PoseCommand(BaseModel):
    targets: list[ServoCommand] = Field(min_length=1, max_length=15)
    hold_ms: int = Field(default=0, ge=0, le=5000)


@app.post("/actuator/pose", tags=["actuator"])
async def move_pose(cmd: PoseCommand, user: dict = Depends(require_user)):
    """Mueve varios servos en el orden seguro del brazo (editor de señas)."""
    if _lock.locked():
        raise HTTPException(409, "el brazo está ejecutando un plan")
    async with _lock:
        for servo_id, angle in order_targets(to_channels({t.servo_id: t.angle for t in cmd.targets})):
            await driver.move(servo_id, angle, INTER_COMMAND_MS)
        if cmd.hold_ms:
            await asyncio.sleep(cmd.hold_ms / 1000)
    return {"positions": driver.positions}


@app.post("/actuator/reset", tags=["actuator"])
async def reset(user: dict = Depends(require_user)):
    async with _lock:
        for servo_id, angle in order_targets(initial_positions()):
            await driver.move(servo_id, angle, INTER_COMMAND_MS)
    return {"positions": driver.positions}
