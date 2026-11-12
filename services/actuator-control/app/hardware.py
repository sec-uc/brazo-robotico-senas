"""Modelo del hardware del brazo: ÚNICA fuente de verdad.

En el sistema heredado los límites de cada servo estaban copiados en tres
archivos Dart distintos (P4 del diagnóstico). Aquí viven una sola vez, junto
con el orden de ejecución que el equipo del robot definió empíricamente
(dedos → pulgar → meñique → articulaciones) y el protocolo serial.

Protocolo serial (Arduino, 9600 8N1), heredado sin cambios:
    byte 200            → cabecera "siguiente comando"
    byte servo_id       → 1..15
    byte ángulo         → 0..180
No hay ACK desde el firmware: el driver serial confirma "enviado", no
"ejecutado". Añadir ACK requiere tocar el firmware (ver ADR-005 / trabajo futuro).
"""
from __future__ import annotations

from dataclasses import dataclass

HEADER_BYTE = 200
BAUD_RATE = 9600
SERVO_COUNT = 15


@dataclass(frozen=True)
class ServoSpec:
    servo_id: int
    name: str
    min_angle: int
    max_angle: int
    initial: int


# Tabla consolidada de robot_controller.dart + excel_dataset_service.dart. Nombres verificados
# canal a canal en el brazo el 2026-10-29 con todos los cables conectados: coinciden con los del
# equipo (A = punta del dedo, B = base). Límites e iniciales, los del equipo: bajar el mínimo del
# medio a 37° lo bloqueó; calibrar de 5 en 5 antes de tocarlos.
SERVOS: dict[int, ServoSpec] = {
    1: ServoSpec(1, "Pulgar, punta", 15, 135, 135),
    2: ServoSpec(2, "Anular, punta", 24, 144, 144),
    3: ServoSpec(3, "Medio, punta (no responde)", 24, 144, 144),
    4: ServoSpec(4, "Índice, punta (no responde)", 32, 142, 142),
    5: ServoSpec(5, "Meñique, punta", 65, 130, 130),
    6: ServoSpec(6, "Índice, base (menor = hacia la palma)", 50, 130, 130),
    7: ServoSpec(7, "Medio, base (menor = hacia la palma)", 47, 120, 120),  # calibrando: 52 del equipo, -5
    8: ServoSpec(8, "Anular, base (menor = hacia la palma)", 40, 145, 145),
    9: ServoSpec(9, "Meñique, base (menor = hacia la palma)", 50, 135, 135),
    10: ServoSpec(10, "Pulgar, base (menor = hacia la palma)", 70, 130, 130),
    11: ServoSpec(11, "Pulgar C (no responde)", 24, 140, 50),
    12: ServoSpec(12, "Brazo lateral (mayor = hacia el pulgar)", 56, 126, 90),
    13: ServoSpec(13, "Muñeca lateral (menor = hacia el meñique)", 64, 112, 90),
    14: ServoSpec(14, "Muñeca adelante/atrás (menor = hacia el dorso)", 70, 126, 100),
    15: ServoSpec(15, "Servo 15 (fijo)", 90, 90, 90),
}

# Orden de ejecución seguro definido por el equipo del robot
EXECUTION_ORDER: list[int] = [4, 6, 3, 7, 2, 8, 1, 10, 11, 5, 9, 12, 13, 14, 15]

# Cableado: canal físico de cada articulación de las señas. Las señas usan meñique 1/6,
# anular 2/7, medio 3/8, índice 4/9 (punta/base) y pulgar 5/10/11; el brazo (verificado el
# 2026-10-29) tiene la punta del pulgar en 1, la del meñique en 5 y las bases de índice a
# meñique en 6-9 en orden inverso. Si se recablea el brazo se cambia solo esta tabla.
CHANNEL: dict[int, int] = {1: 5, 5: 1, 6: 9, 9: 6, 7: 8, 8: 7}

# Canales que no responden (verificados uno a uno el 2026-10-29): puntas del medio (3) y del
# índice (4), y el 11. Revisar servo o tendón; quitarlos de aquí cuando funcionen. Los clientes
# no les envían comandos: el firmware admite ~16 comandos/s y gastarlos aquí frena el movimiento.
INACTIVE: set[int] = {3, 4, 11}


def load_from(db) -> None:
    """Base propia del servicio (ADR-014): la tabla del hardware y su calibración viven en ella.

    La primera vez se siembra con los valores de este módulo; después manda la base, de modo que
    recalibrar un límite, recablear un canal o dar de baja uno que no responde es un cambio de datos.
    Las tablas del módulo se actualizan en el sitio: quienes las importaron ven los valores nuevos.
    """
    with db.conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS servos (
            servo_id INTEGER PRIMARY KEY, name TEXT NOT NULL, min_angle INTEGER NOT NULL, max_angle INTEGER NOT NULL,
            initial INTEGER NOT NULL, exec_order INTEGER NOT NULL, active INTEGER NOT NULL)""")
        c.execute("CREATE TABLE IF NOT EXISTS wiring (joint INTEGER PRIMARY KEY, channel INTEGER NOT NULL)")
        if c.execute("SELECT COUNT(*) AS n FROM servos").fetchone()["n"] == 0:
            for s in SERVOS.values():
                c.execute("INSERT INTO servos (servo_id, name, min_angle, max_angle, initial, exec_order, active) "
                          "VALUES (?, ?, ?, ?, ?, ?, ?)",
                          (s.servo_id, s.name, s.min_angle, s.max_angle, s.initial,
                           EXECUTION_ORDER.index(s.servo_id) if s.servo_id in EXECUTION_ORDER else len(EXECUTION_ORDER),
                           0 if s.servo_id in INACTIVE else 1))
            for joint, channel in CHANNEL.items():
                c.execute("INSERT INTO wiring (joint, channel) VALUES (?, ?)", (joint, channel))
        servos = c.execute("SELECT * FROM servos ORDER BY exec_order, servo_id").fetchall()
        wiring = c.execute("SELECT joint, channel FROM wiring").fetchall()
    SERVOS.clear()
    SERVOS.update({r["servo_id"]: ServoSpec(r["servo_id"], r["name"], r["min_angle"], r["max_angle"], r["initial"])
                   for r in sorted(servos, key=lambda r: r["servo_id"])})
    EXECUTION_ORDER[:] = [r["servo_id"] for r in servos]
    INACTIVE.clear()
    INACTIVE.update(r["servo_id"] for r in servos if not r["active"])
    CHANNEL.clear()
    CHANNEL.update({r["joint"]: r["channel"] for r in wiring})


def to_channels(pose: dict[int, int]) -> dict[int, int]:
    """Pose de una seña (articulaciones lógicas) → canales físicos del brazo."""
    return {CHANNEL.get(sid, sid): angle for sid, angle in pose.items()}


def clamp(servo_id: int, angle: int) -> int:
    spec = SERVOS.get(servo_id)
    if spec is None:
        return angle
    return max(spec.min_angle, min(spec.max_angle, angle))


def order_targets(targets: dict[int, int]) -> list[tuple[int, int]]:
    """Devuelve [(servo_id, ángulo_seguro)] en el orden de ejecución del brazo."""
    ordered = [(sid, clamp(sid, targets[sid])) for sid in EXECUTION_ORDER if sid in targets]
    rest = [(sid, clamp(sid, a)) for sid, a in targets.items() if sid not in EXECUTION_ORDER]
    return ordered + rest


def initial_positions() -> dict[int, int]:
    return {sid: spec.initial for sid, spec in SERVOS.items()}


def encode_command(servo_id: int, angle: int) -> bytes:
    return bytes([HEADER_BYTE, servo_id, clamp(servo_id, angle)])
