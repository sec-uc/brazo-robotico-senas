import sys, pathlib, asyncio
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.hardware import clamp, order_targets, encode_command, EXECUTION_ORDER, SERVOS, initial_positions
from app.drivers import SimulatorDriver


def test_clamp_respects_physical_limits():
    assert clamp(1, 0) == 15 and clamp(1, 180) == 135 and clamp(1, 90) == 90
    assert clamp(15, 0) == 90  # servo fijo


def test_execution_order_fingers_first_then_joints():
    targets = {sid: 90 for sid in SERVOS}
    ordered = [sid for sid, _ in order_targets(targets)]
    assert ordered == EXECUTION_ORDER
    assert ordered.index(4) < ordered.index(1) < ordered.index(5) < ordered.index(12)


def test_encode_command_matches_legacy_protocol():
    assert encode_command(3, 200) == bytes([200, 3, 144])


def test_simulator_moves_and_clamps():
    drv = SimulatorDriver(inter_command_ms=0)
    asyncio.run(drv.connect())
    asyncio.run(drv.move(2, 999, 0))
    assert drv.positions[2] == 144
    assert initial_positions()[11] == 50


def test_sign_joints_map_to_physical_channels():
    """Cableado verificado en el brazo: puntas de pulgar y meñique cruzadas, bases de los dedos invertidas."""
    from app.hardware import to_channels
    assert to_channels({1: 10, 5: 11, 6: 20, 9: 21, 7: 30, 8: 31, 2: 40, 3: 41, 4: 42, 12: 60}) == \
        {5: 10, 1: 11, 9: 20, 6: 21, 8: 30, 7: 31, 2: 40, 3: 41, 4: 42, 12: 60}
    assert to_channels(to_channels({i: i for i in range(1, 16)})) == {i: i for i in range(1, 16)}


def test_hardware_table_lives_in_the_service_database(tmp_path):
    """ADR-014: límites, orden, cableado y canales sin respuesta se siembran una vez y después manda la base."""
    from app import hardware
    from rsp_common.db import Database
    before = (dict(hardware.SERVOS), list(hardware.EXECUTION_ORDER), dict(hardware.CHANNEL), set(hardware.INACTIVE))
    db = Database(tmp_path / "actuator.db")
    try:
        hardware.load_from(db)
        assert (dict(hardware.SERVOS), list(hardware.EXECUTION_ORDER), dict(hardware.CHANNEL), set(hardware.INACTIVE)) == before
        with db.conn() as c:  # recalibrar y reparar un canal es un cambio de datos
            c.execute("UPDATE servos SET min_angle = 20, active = 1 WHERE servo_id = 3")
            c.execute("UPDATE wiring SET channel = 2 WHERE joint = 1")
        hardware.load_from(db)
        assert hardware.SERVOS[3].min_angle == 20 and hardware.clamp(3, 0) == 20
        assert 3 not in hardware.INACTIVE and hardware.to_channels({1: 99}) == {2: 99}
    finally:
        hardware.SERVOS.clear(); hardware.SERVOS.update(before[0])
        hardware.EXECUTION_ORDER[:] = before[1]
        hardware.CHANNEL.clear(); hardware.CHANNEL.update(before[2])
        hardware.INACTIVE.clear(); hardware.INACTIVE.update(before[3])
