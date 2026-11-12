"""Drivers de conexión con el brazo (patrón Strategy, como el
`RobotConnectionService` heredado de la app Flutter, que era el único punto
del sistema antiguo con una abstracción correcta).

  simulator — sin hardware; simula el tiempo de movimiento. Es el driver por
              defecto y el que usan las pruebas y los benchmarks.
  serial    — USB/serial con pyserial; misma trama que el firmware actual.
  batch     — simula un firmware que recibe la pose completa en una trama
              ([201][n][servo, ángulo]×n) y la aplica de una vez: una espera por
              gesto en lugar de una por servo (propuesta de trabajo futuro).
  Añadir ROS 2, Bluetooth o un brazo distinto = una clase nueva aquí.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Protocol

from .hardware import BAUD_RATE, HEADER_BYTE, clamp, initial_positions

log = logging.getLogger("actuator.drivers")


class Driver(Protocol):
    name: str
    positions: dict[int, int]

    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def move(self, servo_id: int, angle: int, inter_command_ms: int) -> None: ...
    def is_connected(self) -> bool: ...


class SimulatorDriver:
    name = "simulator"

    def __init__(self, inter_command_ms: int | None = None) -> None:
        self.positions = initial_positions()
        self._connected = False
        # el firmware real necesita ~150 ms entre comandos; el simulador respeta
        # ese tiempo por defecto para que las latencias medidas sean realistas
        self.inter_command_ms = inter_command_ms if inter_command_ms is not None else int(
            os.getenv("SIM_INTER_COMMAND_MS", "150"))

    async def connect(self) -> None:
        self._connected = True
        log.info("simulador conectado")

    async def close(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    async def move(self, servo_id: int, angle: int, inter_command_ms: int) -> None:
        await asyncio.sleep(min(inter_command_ms, self.inter_command_ms) / 1000)
        self.positions[servo_id] = clamp(servo_id, angle)


class SerialDriver:
    name = "serial"

    def __init__(self, port: str, baud: int = BAUD_RATE) -> None:
        import serial  # dependencia opcional (pyserial)

        self._serial_mod = serial
        self.port = port
        self.baud = baud
        self._ser = None
        self.positions = initial_positions()

    async def connect(self) -> None:
        self._ser = self._serial_mod.Serial(self.port, self.baud, timeout=1)
        self._ser.dtr = True
        self._ser.rts = True
        await asyncio.sleep(2.0)  # el Arduino se reinicia al abrir el puerto
        log.info("serial conectado en %s @ %d", self.port, self.baud)

    async def close(self) -> None:
        if self._ser and self._ser.is_open:
            self._ser.close()

    def is_connected(self) -> bool:
        return bool(self._ser and self._ser.is_open)

    async def move(self, servo_id: int, angle: int, inter_command_ms: int) -> None:
        assert self._ser
        safe = clamp(servo_id, angle)
        loop = asyncio.get_running_loop()
        # misma secuencia que RobotController.sendCommand (Dart): cabecera, espera, comando
        await loop.run_in_executor(None, self._ser.write, bytes([HEADER_BYTE]))
        await asyncio.sleep(inter_command_ms / 1000)
        await loop.run_in_executor(None, self._ser.write, bytes([servo_id, safe]))
        self.positions[servo_id] = safe


class BatchSimulatorDriver(SimulatorDriver):
    """Pose completa en una sola trama; el firmware actual no la soporta (QA-05)."""

    name = "batch"
    BATCH_HEADER = 201

    def frame(self, targets: list[tuple[int, int]]) -> bytes:
        return bytes([self.BATCH_HEADER, len(targets), *(b for sid, a in targets for b in (sid, clamp(sid, a)))])

    async def move_many(self, targets: list[tuple[int, int]], inter_command_ms: int) -> None:
        self.last_frame = self.frame(targets)
        for sid, a in targets:
            self.positions[sid] = clamp(sid, a)
        await asyncio.sleep(min(inter_command_ms, self.inter_command_ms) / 1000)  # una espera por pose


async def move_all(driver: Driver, targets: list[tuple[int, int]], inter_command_ms: int) -> None:
    """Pose completa: en una trama si el driver lo admite, si no servo a servo."""
    if hasattr(driver, "move_many"):
        await driver.move_many(targets, inter_command_ms)
        return
    for servo_id, angle in targets:
        await driver.move(servo_id, angle, inter_command_ms)


def build_driver() -> Driver:
    kind = os.getenv("ACTUATOR_DRIVER", "simulator").lower()
    if kind == "batch":
        return BatchSimulatorDriver()
    if kind == "serial":
        return SerialDriver(os.getenv("SERIAL_PORT", "/dev/ttyUSB0"), int(os.getenv("SERIAL_BAUD", str(BAUD_RATE))))
    return SimulatorDriver()


def ms() -> int:
    return int(time.time() * 1000)
