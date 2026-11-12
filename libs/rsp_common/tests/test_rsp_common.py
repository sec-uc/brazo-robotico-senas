"""Biblioteca común: contratos de eventos, verificación JWT, bus y esqueleto de servicio."""
import asyncio
import json

import jwt
import pytest
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient
from pydantic import ValidationError

from rsp_common import auth, events
from rsp_common.bus import EventBus, dump
from rsp_common.events import (
    SUBJECT_TO_MODEL, ActuatorState, ActuatorStatus, AudioChunk, Gesture, GesturePlan,
    GlossSequence, ServoTarget, Subjects, TextTranscribed,
)
from rsp_common.service import Service
from rsp_testkit import SECRET, make_token


# ------------------------------------------------------------------ contratos (QA-08: trazas)
def test_every_subject_maps_to_its_contract():
    assert set(SUBJECT_TO_MODEL) == {
        Subjects.AUDIO_CHUNK, Subjects.TEXT_TRANSCRIBED, Subjects.GLOSS_SEQUENCE,
        Subjects.GESTURE_PLAN, Subjects.ACTUATOR_STATE, Subjects.TELEMETRY,
    }
    assert all(issubclass(m, events.BaseEvent) for m in SUBJECT_TO_MODEL.values())


def test_base_fields_have_defaults_for_correlation():
    a = TextTranscribed(session_id="s", text="hola")
    b = TextTranscribed(session_id="s", text="hola")
    assert a.event_id != b.event_id and len(a.event_id) == 32
    assert a.ts > 1_600_000_000_000  # epoch en milisegundos
    assert a.trace == {} and a.options == {} and a.producer == "unknown"


def test_trace_and_options_survive_json_round_trip():
    ev = GlossSequence(session_id="s", source_text="hola", glosses=["HOLA"],
                       trace={"captured": 1, "transcribed": 2, "glossed": 3}, options={"dry_run": True})
    back = GlossSequence.model_validate_json(ev.model_dump_json())
    assert back.trace == {"captured": 1, "transcribed": 2, "glossed": 3}
    assert back.options == {"dry_run": True} and back == ev


@pytest.mark.parametrize("servo_id,angle", [(0, 90), (16, 90), (1, -1), (1, 181)])
def test_servo_target_rejects_out_of_protocol_values(servo_id, angle):
    with pytest.raises(ValidationError):
        ServoTarget(servo_id=servo_id, angle=angle)


def test_audio_chunk_language_hint_is_constrained():
    assert AudioChunk(session_id="s", audio_b64="", language_hint="qu").language_hint == "qu"
    with pytest.raises(ValidationError):
        AudioChunk(session_id="s", audio_b64="", language_hint="en")


def test_actuator_state_serializes_status_as_string():
    st = ActuatorState(session_id="s", status=ActuatorStatus.DONE, execution_ms=10)
    assert json.loads(st.model_dump_json())["status"] == "done"
    plan = GesturePlan(session_id="s", gestures=[Gesture(gloss="A", targets=[ServoTarget(servo_id=1, angle=90)])])
    assert plan.unknown_glosses == [] and plan.gestures[0].hold_ms == 600


def test_dump_returns_plain_json_dict():
    d = dump(ActuatorState(session_id="s", status=ActuatorStatus.FAILED))
    assert d["status"] == "failed" and isinstance(d, dict)


# ------------------------------------------------------------------ auth (QA-09)
def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_require_user_accepts_valid_token(auth_env):
    user = asyncio.run(auth.require_user(_creds(make_token("ana", ["admin"]))))
    assert user["sub"] == "ana" and user["roles"] == ["admin"]


@pytest.mark.parametrize("token", [
    None,
    "no-es-un-jwt",
    make_token(secret="otro-secreto-distinto-de-32-bytes-o-mas"),
    make_token(ttl=-10),  # expirado
])
def test_require_user_rejects_missing_invalid_or_expired(auth_env, token):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        asyncio.run(auth.require_user(_creds(token) if token else None))
    assert e.value.status_code == 401


def test_require_user_disabled_returns_anonymous_operator(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    assert asyncio.run(auth.require_user(None)) == {"sub": "anonymous", "roles": ["operator"]}


def test_decode_token_uses_configured_secret(auth_env):
    assert auth.secret() == SECRET
    with pytest.raises(jwt.PyJWTError):
        auth.decode_token(make_token(secret="x" * 40))


# ------------------------------------------------------------------ bus (sin NATS)
class _FakeNats:
    def __init__(self):
        self.sent = []
        self.callbacks = {}
        self.is_connected = True
        self.is_closed = False

    async def publish(self, subject, payload):
        self.sent.append((subject, payload))

    async def subscribe(self, subject, queue="", cb=None):
        self.callbacks[subject] = (queue, cb)
        return object()

    async def drain(self):
        self.is_closed = True


class _Msg:
    def __init__(self, subject, data):
        self.subject, self.data = subject, data


def _bus():
    bus = EventBus("nats://x", "svc-test")
    bus._nc = _FakeNats()  # noqa: SLF001
    return bus


def test_publish_stamps_producer_and_serializes():
    bus = _bus()
    asyncio.run(bus.publish(Subjects.TEXT_TRANSCRIBED, TextTranscribed(session_id="s", text="hola")))
    subject, payload = bus._nc.sent[0]  # noqa: SLF001
    assert subject == Subjects.TEXT_TRANSCRIBED
    assert json.loads(payload)["producer"] == "svc-test"


def test_subscribe_validates_against_contract_and_uses_queue_group():
    bus, got = _bus(), []

    async def handler(ev):
        got.append(ev)

    async def run():
        await bus.subscribe(Subjects.TEXT_TRANSCRIBED, handler, queue="nlp")
        queue, cb = bus._nc.callbacks[Subjects.TEXT_TRANSCRIBED]  # noqa: SLF001
        assert queue == "nlp"
        good = TextTranscribed(session_id="s", text="hola").model_dump_json().encode()
        await cb(_Msg(Subjects.TEXT_TRANSCRIBED, good))
        await cb(_Msg(Subjects.TEXT_TRANSCRIBED, b"{no json"))          # descartado
        await cb(_Msg(Subjects.TEXT_TRANSCRIBED, b'{"session_id": "s"}'))  # falta `text`: descartado

    asyncio.run(run())
    assert len(got) == 1 and isinstance(got[0], TextTranscribed)


def test_handler_exception_does_not_break_subscription():
    bus = _bus()

    async def boom(ev):
        raise RuntimeError("x")

    async def run():
        await bus.subscribe(Subjects.TEXT_TRANSCRIBED, boom)
        _, cb = bus._nc.callbacks[Subjects.TEXT_TRANSCRIBED]  # noqa: SLF001
        await cb(_Msg(Subjects.TEXT_TRANSCRIBED, TextTranscribed(session_id="s", text="a").model_dump_json().encode()))

    asyncio.run(run())  # no propaga


def test_subscribe_raw_passes_subject_and_dict():
    bus, got = _bus(), []

    async def handler(subject, data):
        got.append((subject, data))

    async def run():
        await bus.subscribe_raw("voice.>", handler)
        _, cb = bus._nc.callbacks["voice.>"]  # noqa: SLF001
        await cb(_Msg("voice.audio.chunk", b'{"session_id": "s"}'))

    asyncio.run(run())
    assert got == [("voice.audio.chunk", {"session_id": "s"})]


def test_connected_and_close():
    bus = _bus()
    assert bus.connected
    asyncio.run(bus.close())
    assert bus._nc.is_closed  # noqa: SLF001
    assert not EventBus("nats://x", "y").connected


def test_connect_gives_up_after_retries(monkeypatch):
    import nats

    async def fail(*a, **k):
        raise OSError("sin NATS")

    monkeypatch.setattr(nats, "connect", fail)
    with pytest.raises(RuntimeError, match="no se pudo conectar"):
        asyncio.run(EventBus("nats://x", "y").connect(retries=2, delay=0))


# ------------------------------------------------------------------ esqueleto de servicio
def test_health_reports_degraded_without_bus_and_ok_without_bus_need():
    svc = Service("prueba")
    h = TestClient(svc.app).get("/health").json()
    assert h["service"] == "prueba" and h["status"] == "degraded" and h["bus_connected"] is False
    svc2 = Service("prueba2", needs_bus=False)
    assert TestClient(svc2.app).get("/health").json()["status"] == "ok"


def test_metrics_endpoint_exposes_prometheus_counters():
    svc = Service("prueba-metrics", needs_bus=False)
    svc.track("voice.audio.chunk")
    with svc.timer("etapa"):
        pass
    body = TestClient(svc.app).get("/metrics").text
    assert 'rsp_events_processed_total{outcome="ok",service="prueba-metrics",subject="voice.audio.chunk"}' in body
    assert 'rsp_stage_latency_seconds_count{service="prueba-metrics",stage="etapa"}' in body


def test_lifespan_runs_startup_and_shutdown_hooks():
    svc = Service("prueba-life", needs_bus=False)
    calls = []

    @svc.on_startup
    async def a():
        calls.append("start")

    @svc.on_shutdown
    async def b():
        calls.append("stop")

    with TestClient(svc.app):
        assert calls == ["start"]
    assert calls == ["start", "stop"]


def test_bus_processes_messages_of_one_subscription_concurrently(monkeypatch):
    """ADR-009: nats-py entrega en serie; el bus despacha cada mensaje como tarea propia."""
    monkeypatch.setenv("BUS_MAX_INFLIGHT", "8")

    class FakeNC:
        async def subscribe(self, subject, queue="", cb=None):
            self.cb = cb
            return object()

    class Msg:
        def __init__(self, data):
            self.data, self.subject = data, "voice.text.transcribed"

    async def scenario():
        bus = EventBus("nats://x", "prueba")
        bus._nc = FakeNC()
        seen = []

        async def handler(ev):
            await asyncio.sleep(0.2)
            seen.append(ev.text)

        await bus.subscribe(events.Subjects.TEXT_TRANSCRIBED, handler)
        t0 = asyncio.get_running_loop().time()
        for i in range(5):
            ev = events.TextTranscribed(session_id=f"s{i}", text=f"t{i}")
            await bus._nc.cb(Msg(ev.model_dump_json().encode()))
        await asyncio.wait(bus._tasks)
        return asyncio.get_running_loop().time() - t0, seen

    elapsed, seen = asyncio.run(scenario())
    assert sorted(seen) == [f"t{i}" for i in range(5)]
    assert elapsed < 0.6  # en serie serían ≥ 1,0 s


def test_bus_never_has_more_than_max_inflight_tasks(monkeypatch):
    """El cupo se toma antes de crear la tarea: con 2 de cupo nunca hay más de 2 tareas vivas."""
    monkeypatch.setenv("BUS_MAX_INFLIGHT", "2")

    class FakeNC:
        async def subscribe(self, subject, queue="", cb=None):
            self.cb = cb
            return object()

    class Msg:
        def __init__(self, data):
            self.data, self.subject = data, "voice.text.transcribed"

    async def scenario():
        bus = EventBus("nats://x", "prueba")
        bus._nc = FakeNC()
        peak = 0

        async def handler(ev):
            nonlocal peak
            peak = max(peak, len(bus._tasks))
            await asyncio.sleep(0.05)

        await bus.subscribe(events.Subjects.TEXT_TRANSCRIBED, handler)
        msgs = [Msg(events.TextTranscribed(session_id=f"s{i}", text="t").model_dump_json().encode()) for i in range(6)]
        await asyncio.gather(*(bus._nc.cb(m) for m in msgs))
        await asyncio.wait(bus._tasks)
        return peak

    assert asyncio.run(scenario()) <= 2


def test_database_helper_is_portable_and_transactional(tmp_path):
    """ADR-014: el mismo SQL sirve para SQLite (pruebas) y PostgreSQL (despliegue)."""
    import pytest as _pytest
    from rsp_common.db import Database
    db = Database(tmp_path / "sub" / "x.db")  # crea la carpeta
    assert not db.postgres and Database("postgresql://u:p@h/d").postgres
    with db.conn() as c:
        c.execute("CREATE TABLE t (id {serial}, k TEXT UNIQUE, v BIGINT)")
        c.execute("INSERT INTO t (k, v) VALUES (?, ?)", ("a", 1794490009495))  # epoch ms no cabe en 32 bits
        c.execute("INSERT INTO t (k, v) VALUES (?, ?) ON CONFLICT (k) DO NOTHING", ("a", 2))
    with db.conn() as c:
        row = c.execute("SELECT id, v FROM t WHERE k = ?", ("a",)).fetchone()
        assert (row["id"], row["v"]) == (1, 1794490009495)
        assert c.execute("SELECT COUNT(*) AS n FROM t").fetchone()["n"] == 1
    assert db.columns("t") == {"id", "k", "v"}
    with _pytest.raises(ZeroDivisionError):
        with db.conn() as c:
            c.execute("INSERT INTO t (k, v) VALUES (?, ?)", ("b", 3))
            1 / 0
    with db.conn() as c:  # la transacción fallida no dejó rastro
        assert c.execute("SELECT COUNT(*) AS n FROM t").fetchone()["n"] == 1
