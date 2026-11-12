"""Control de actuadores: ejecución de planes, límites físicos (tabla única), trazas y API."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from rsp_common.events import ActuatorState, Gesture, GesturePlan, ServoTarget, Subjects
from rsp_testkit import make_token


@pytest.fixture
def act(monkeypatch, fake_bus):
    monkeypatch.setenv("ACTUATOR_DRIVER", "simulator")
    monkeypatch.setenv("SIM_INTER_COMMAND_MS", "0")
    monkeypatch.setenv("INTER_COMMAND_MS", "0")
    import app.main as m
    m.svc.bus = fake_bus
    asyncio.run(m.driver.connect())
    return m, fake_bus


def plan(gestures, **kw):
    return GesturePlan(session_id="s1", gestures=gestures, trace={"captured": 1, "planned": 2}, **kw)


def g(gloss, angles: dict, hold=0):
    return Gesture(gloss=gloss, targets=[ServoTarget(servo_id=k, angle=v) for k, v in angles.items()], hold_ms=hold)


def states(bus):
    return [e for e in bus.events(Subjects.ACTUATOR_STATE)]


def test_plan_runs_executing_per_gesture_then_done_with_trace(act):
    m, bus = act
    asyncio.run(m.execute_plan(plan([g("HOLA", {1: 100}), g("MAMA", {2: 100})])))
    sts = states(bus)
    assert [s.status.value for s in sts] == ["executing", "executing", "done"]
    assert [s.gesture_index for s in sts] == [0, 1, 1] and [s.gesture_gloss for s in sts[:2]] == ["HOLA", "MAMA"]
    done = sts[-1]
    assert isinstance(done, ActuatorState) and done.execution_ms >= 0 and done.driver == "simulator"
    # QA-08: la traza conserva las marcas previas y añade las propias
    assert done.trace["captured"] == 1 and done.trace["planned"] == 2
    assert "executing" in sts[0].trace and "done" in done.trace


def test_out_of_range_angles_are_clamped_by_the_single_hardware_table(act):
    m, bus = act
    from app.hardware import SERVOS
    asyncio.run(m.execute_plan(plan([g("X", {1: 0, 5: 180, 12: 0, 15: 0})])))
    pos = states(bus)[-1].servo_positions
    # articulación 1 (punta del meñique) → canal 5 y 5 (punta del pulgar) → canal 1: cada una
    # se recorta con el límite de su canal físico
    assert pos[5] == SERVOS[5].min_angle == 65
    assert pos[1] == SERVOS[1].max_angle == 135
    assert pos[12] == SERVOS[12].min_angle and pos[15] == 90  # servo fijo


def test_servos_are_moved_in_safe_order(act, monkeypatch):
    m, _ = act
    from app.hardware import EXECUTION_ORDER
    moved = []
    orig = m.driver.move

    async def spy(sid, angle, ms):
        moved.append(sid)
        await orig(sid, angle, ms)

    monkeypatch.setattr(m.driver, "move", spy)
    asyncio.run(m.execute_plan(plan([g("TODO", {sid: 90 for sid in reversed(EXECUTION_ORDER)})])))
    assert moved == EXECUTION_ORDER


def test_driver_failure_publishes_failed_with_error(act, monkeypatch):
    m, bus = act

    async def broken(*a):
        raise OSError("puerto USB desconectado")

    monkeypatch.setattr(m.driver, "move", broken)
    asyncio.run(m.execute_plan(plan([g("A", {1: 90})])))
    last = states(bus)[-1]
    assert last.status.value == "failed" and "USB" in last.error and "failed" in last.trace


def test_dry_run_accepts_and_finishes_without_moving(act):
    m, bus = act
    before = dict(m.driver.positions)
    asyncio.run(m.handle_plan(plan([g("A", {1: 20})], options={"dry_run": True})))
    sts = states(bus)
    assert [s.status.value for s in sts] == ["accepted", "done"] and sts[-1].execution_ms == 0
    assert m.queue.qsize() == 0 and m.driver.positions == before


def test_normal_plan_is_accepted_and_queued(act):
    m, bus = act
    asyncio.run(m.handle_plan(plan([g("A", {1: 20})])))
    assert [s.status.value for s in states(bus)] == ["accepted"] and m.queue.qsize() == 1


def test_empty_plan_is_done(act):
    m, bus = act
    asyncio.run(m.execute_plan(plan([])))
    (done,) = states(bus)
    assert done.status.value == "done" and done.gesture_index is None


# ------------------------------------------------------------------ API de operación
@pytest.fixture
def api(act, auth_env):
    m, bus = act
    return m, TestClient(m.app), {"Authorization": f"Bearer {make_token()}"}


@pytest.mark.parametrize("method,path,body", [
    ("POST", "/actuator/servo", {"servo_id": 1, "angle": 90}),
    ("POST", "/actuator/pose", {"targets": [{"servo_id": 1, "angle": 90}]}),
    ("POST", "/actuator/reset", None),
])
def test_movement_endpoints_require_token(api, method, path, body):
    """QA-09 también en el propio servicio (defensa en profundidad tras el gateway)."""
    m, c, _ = api
    assert c.request(method, path, json=body).status_code == 401


def test_servo_endpoint_clamps_and_validates(api):
    m, c, h = api
    assert c.post("/actuator/servo", json={"servo_id": 2, "angle": 0}, headers=h).json() == {"servo_id": 2, "angle": 24}
    assert c.post("/actuator/servo", json={"servo_id": 16, "angle": 90}, headers=h).status_code == 422
    assert c.post("/actuator/servo", json={"servo_id": 1, "angle": 200}, headers=h).status_code == 422


def test_pose_and_reset(api):
    m, c, h = api
    from app.hardware import initial_positions
    pos = c.post("/actuator/pose", json={"targets": [{"servo_id": 13, "angle": 180}]}, headers=h).json()["positions"]
    assert pos["13"] == 112
    assert c.post("/actuator/pose", json={"targets": []}, headers=h).status_code == 422
    reset = c.post("/actuator/reset", headers=h).json()["positions"]
    assert {int(k): v for k, v in reset.items()} == initial_positions()


def test_busy_arm_rejects_manual_commands(api):
    m, c, h = api

    # TestClient corre en otro loop: basta con marcar el lock como tomado
    m._lock._locked = True  # noqa: SLF001
    try:
        assert c.post("/actuator/servo", json={"servo_id": 1, "angle": 90}, headers=h).status_code == 409
        assert c.post("/actuator/pose", json={"targets": [{"servo_id": 1, "angle": 90}]}, headers=h).status_code == 409
    finally:
        m._lock._locked = False  # noqa: SLF001


def test_status_and_servo_table(api):
    m, c, _ = api
    st = c.get("/actuator/status").json()
    assert st["driver"] == "simulator" and st["connected"] is True and st["busy"] is False
    table = c.get("/actuator/servos").json()
    assert len(table["servos"]) == 15 and len(table["execution_order"]) == 15


def test_build_driver_selects_serial_by_config(monkeypatch):
    from app import drivers

    class FakeSerial:
        def __init__(self, port, baud):
            self.port, self.baud, self.name = port, baud, "serial"

    monkeypatch.setattr(drivers, "SerialDriver", FakeSerial)
    monkeypatch.setenv("ACTUATOR_DRIVER", "serial")
    monkeypatch.setenv("SERIAL_PORT", "/dev/ttyACM0")
    d = drivers.build_driver()
    assert (d.name, d.port, d.baud) == ("serial", "/dev/ttyACM0", 9600)


def test_serial_driver_writes_legacy_frame(monkeypatch):
    """Sin hardware: el driver serial escribe cabecera 200 y luego [servo, ángulo recortado]."""
    from app import drivers

    class Port:
        def __init__(self, *a, **k):
            self.writes, self.is_open = [], True

        def write(self, b):
            self.writes.append(bytes(b))

        def close(self):
            self.is_open = False

    import types
    fake_mod = types.SimpleNamespace(Serial=Port)
    monkeypatch.setitem(__import__("sys").modules, "serial", fake_mod)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(drivers.asyncio, "sleep", lambda s: real_sleep(0))  # sin los 2 s de reinicio del Arduino
    d = drivers.SerialDriver("/dev/x")

    async def run():
        await d.connect()
        await d.move(3, 250, 0)
        await d.close()

    asyncio.run(run())
    assert d._ser.writes == [bytes([200]), bytes([3, 144])] and d.positions[3] == 144 and not d.is_connected()


def test_batch_driver_sends_whole_pose_in_one_frame():
    """QA-05: un driver nuevo se añade dentro del servicio; la pose viaja en una trama y espera una vez."""
    import asyncio
    import time
    from app.drivers import BatchSimulatorDriver, move_all
    d = BatchSimulatorDriver(inter_command_ms=20)
    targets = [(i, 90) for i in range(1, 16)]
    t0 = time.perf_counter()
    asyncio.run(move_all(d, targets, 20))
    assert time.perf_counter() - t0 < 0.15  # una espera de 20 ms, no 15
    assert d.last_frame[0] == 201 and d.last_frame[1] == 15 and len(d.last_frame) == 2 + 2 * 15
    assert all(d.positions[i] == 90 for i in range(1, 16))


def test_final_event_carries_the_whole_trace(act):
    """ADR-012 / QA-08: el evento final conserva `accepted` (antes se perdía en vista previa)."""
    m, bus = act
    asyncio.run(m.handle_plan(plan([g("A", {1: 20})], options={"dry_run": True})))
    done = states(bus)[-1]
    assert done.status.value == "done"
    assert {"accepted", "done"} <= set(done.trace)
    assert done.trace["accepted"] <= done.trace["done"]


def test_keyframes_are_executed_after_the_main_pose(act):
    """Seña con movimiento: el brazo termina en la última pose del trazo, no en la inicial."""
    m, bus = act
    wave = g("HOLA", {13: 70})
    wave.keyframes = [[ServoTarget(servo_id=13, angle=110)], [ServoTarget(servo_id=13, angle=80)]]
    asyncio.run(m.execute_plan(plan([wave])))
    assert states(bus)[-1].servo_positions[13] == 80
