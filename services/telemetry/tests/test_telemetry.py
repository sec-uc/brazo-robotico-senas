"""Telemetría: reconstrucción de la línea de tiempo (QA-08) y estadísticas de la evaluación."""
import asyncio
import math
import statistics

import pytest
from fastapi.testclient import TestClient

from rsp_testkit import fresh_registry

STAGES = ["captured", "transcribed", "glossed", "planned", "accepted", "executing", "done"]


@pytest.fixture
def tel(tmp_path, monkeypatch, fake_bus):
    monkeypatch.setenv("TELEMETRY_DB_PATH", str(tmp_path / "t.db"))
    fresh_registry("rsp_e2e_latency", "rsp_pipeline_stage", "rsp_unknown_glosses", "rsp_actuator_failures")
    import app.main as m
    m.svc.bus = fake_bus
    m.init_db()
    return m, TestClient(m.app)


def feed(m, session_id, base=1000, step=(2, 1, 1, 1, 0, 2000), gestures=2, unknown=0, status="done", text="hola"):
    """Publica la secuencia de eventos de una sesión con marcas acumuladas."""
    t, trace = base, {"captured": base}
    stamps = {}
    for stage, d in zip(STAGES[1:], step):
        t += d
        stamps[stage] = t

    def ev(subject, upto, **extra):
        tr = {"captured": base, **{s: stamps[s] for s in STAGES[1:STAGES.index(upto) + 1]}} if upto != "captured" else dict(trace)
        asyncio.run(m.on_event(subject, {"session_id": session_id, "producer": "x", "ts": t, "trace": tr, **extra}))

    ev("voice.audio.chunk", "captured")
    ev("voice.text.transcribed", "transcribed", text=text)
    ev("nlp.gloss.sequence", "glossed")
    ev("signs.gesture.plan", "planned", gestures=[{}] * gestures, unknown_glosses=["X"] * unknown)
    ev("actuator.state", "accepted", status="accepted")
    ev("actuator.state", "executing", status="executing")
    final = "done" if status == "done" else "executing"
    ev("actuator.state", final, status=status, execution_ms=2000 if status == "done" else None)
    return stamps


def test_session_timeline_has_all_seven_marks_and_stage_latencies(tel):
    m, c = tel
    feed(m, "s1")
    s = c.get("/telemetry/sessions/s1").json()
    assert all(s[k] is not None for k in STAGES)  # QA-08: las 7 marcas
    assert s["status"] == "done" and s["text"] == "hola" and s["gestures"] == 2 and s["unknown"] == 0
    assert s["stages_ms"] == {"captured->transcribed": 2, "transcribed->glossed": 1, "glossed->planned": 1,
                              "planned->accepted": 1, "accepted->executing": 0, "executing->done": 2000}
    assert s["e2e_ms"] == 2005 == sum(s["stages_ms"].values())
    assert len(s["events"]) == 7 and s["execution_ms"] == 2000


def test_first_mark_wins(tel):
    """Si un evento repite una marca (reintentos), se conserva la primera."""
    m, c = tel
    feed(m, "s1", base=1000)
    asyncio.run(m.on_event("actuator.state", {"session_id": "s1", "trace": {"captured": 5}, "status": "done"}))
    assert c.get("/telemetry/sessions/s1").json()["captured"] == 1000


def test_unknown_session(tel):
    _, c = tel
    assert c.get("/telemetry/sessions/nada").json() == {"session_id": "nada", "found": False}


def test_summary_statistics_match_reference_values(tel):
    m, c = tel
    e2e = []
    for i in range(30):
        exec_ms = 2000 + 10 * i
        feed(m, f"s{i:02d}", base=10_000 * (i + 1), step=(1, 1, 1, 1, 0, exec_ms))
        e2e.append(4 + exec_ms)
    summ = c.get("/telemetry/summary").json()
    assert summ["sessions"] == 30 and summ["failed"] == 0 and summ["unknown_gloss_rate"] == 0
    s = summ["e2e_ms"]
    assert s["n"] == 30 and s["min"] == min(e2e) and s["max"] == max(e2e)
    assert s["mean"] == round(statistics.mean(e2e), 1) and s["median"] == statistics.median(e2e)
    assert s["stdev"] == round(statistics.stdev(e2e), 1)
    # p95 por rango más cercano: el ⌈0,95·n⌉-ésimo valor ordenado
    assert s["p95"] == sorted(e2e)[math.ceil(0.95 * 30) - 1]
    assert summ["stages_ms"]["captured->transcribed"]["mean"] == 1


@pytest.mark.parametrize("n", [1, 2, 10, 20, 30, 50, 101])
def test_p95_is_nearest_rank(tel, n):
    m, c = tel
    for i in range(n):
        feed(m, f"p{i:03d}", base=10_000 * (i + 1), step=(1, 1, 1, 1, 0, 100 + i))
    p95 = c.get("/telemetry/summary").json()["e2e_ms"]["p95"]
    values = sorted(4 + 100 + i for i in range(n))
    assert p95 == values[max(0, math.ceil(0.95 * n) - 1)]


def test_unknown_rate_and_failures(tel):
    m, c = tel
    feed(m, "ok", base=1000, gestures=3, unknown=1)
    feed(m, "fail", base=9000, gestures=2, unknown=0, status="failed")
    summ = c.get("/telemetry/summary").json()
    assert summ["sessions"] == 1  # las fallidas no tienen `done`
    assert summ["unknown_gloss_rate"] == round(1 / 4, 3)
    assert c.get("/telemetry/sessions/fail").json()["status"] == "failed"


def test_empty_summary(tel):
    assert tel[1].get("/telemetry/summary").json() == {"sessions": 0}


def test_sessions_list_is_newest_first(tel):
    m, c = tel
    feed(m, "viejo", base=1000)
    feed(m, "nuevo", base=50_000)
    assert [s["session_id"] for s in c.get("/telemetry/sessions?limit=10").json()] == ["nuevo", "viejo"]


def test_subscribes_to_all_domain_subjects(tel):
    m, _ = tel
    asyncio.run(m.start())
    assert m.svc.bus.subscriptions == ["voice.>", "nlp.>", "signs.>", "actuator.>", "telemetry.>"]
