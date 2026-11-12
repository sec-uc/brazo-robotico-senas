"""Servicio de telemetría.

Observa TODOS los asuntos del dominio (voice.>, nlp.>, signs.>, actuator.>,
telemetry.>) sin participar en el pipeline: registra cada evento por sesión y
reconstruye la línea de tiempo end-to-end a partir de las marcas `trace`
(captured → transcribed → glossed → planned → accepted → done).

Es el instrumento de medición de la tesis: de aquí salen las latencias por
etapa y totales (escenarios QA de rendimiento), la tasa de glosas
desconocidas (cobertura de vocabulario) y la tasa de fallos del actuador.
Expone además métricas Prometheus para Grafana.
"""
from __future__ import annotations

import json
import math
import os
import statistics

from prometheus_client import Counter, Histogram

from rsp_common.db import Database
from rsp_common.events import Subjects
from rsp_common.service import Service

svc = Service("telemetry")
app = svc.app

# base propia del servicio (ADR-014): PostgreSQL en el despliegue; un archivo SQLite sin contenedores
DB_PATH = os.getenv("TELEMETRY_DATABASE_URL") or os.getenv("TELEMETRY_DB_PATH", "/data/telemetry.db")
STAGES = ["captured", "transcribed", "glossed", "planned", "accepted", "executing", "done"]

E2E = Histogram("rsp_e2e_latency_seconds", "latencia captura→seña ejecutada",
                buckets=(0.1, 0.25, 0.5, 1, 2, 3, 5, 8, 13, 21))
STAGE = Histogram("rsp_pipeline_stage_seconds", "latencia entre etapas", ["stage"],
                  buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5))
UNKNOWN = Counter("rsp_unknown_glosses_total", "glosas sin seña ni deletreo")
FAILED = Counter("rsp_actuator_failures_total", "planes fallidos en el actuador")


db = Database(DB_PATH)
conn = db.conn


def init_db() -> None:
    with conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS events (
            id {serial}, session_id TEXT, subject TEXT,
            producer TEXT, ts BIGINT, received_ts BIGINT, payload TEXT)""")
        c.execute("CREATE INDEX IF NOT EXISTS ix_events_session ON events(session_id)")
        c.execute("""CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY, captured BIGINT, transcribed BIGINT, glossed BIGINT,
            planned BIGINT, accepted BIGINT, executing BIGINT, done BIGINT,
            status TEXT, gestures INTEGER, unknown INTEGER, text TEXT, execution_ms INTEGER)""")


async def on_event(subject: str, data: dict) -> None:
    import time

    session_id = data.get("session_id", "?")
    now = int(time.time() * 1000)
    trace = data.get("trace", {})
    with conn() as c:
        c.execute("INSERT INTO events (session_id, subject, producer, ts, received_ts, payload) VALUES (?,?,?,?,?,?)",
                  (session_id, subject, data.get("producer"), data.get("ts"), now, json.dumps(data)[:20000]))
        c.execute("INSERT INTO sessions (session_id) VALUES (?) ON CONFLICT (session_id) DO NOTHING", (session_id,))
        for stage in STAGES:
            if stage in trace:
                c.execute(f"UPDATE sessions SET {stage} = COALESCE({stage}, ?) WHERE session_id = ?",
                          (trace[stage], session_id))
        if subject == Subjects.TEXT_TRANSCRIBED:
            c.execute("UPDATE sessions SET text = ? WHERE session_id = ?", (data.get("text"), session_id))
        if subject == Subjects.GESTURE_PLAN:
            unknown = len(data.get("unknown_glosses", []))
            UNKNOWN.inc(unknown)
            c.execute("UPDATE sessions SET gestures = ?, unknown = ? WHERE session_id = ?",
                      (len(data.get("gestures", [])), unknown, session_id))
        if subject == Subjects.ACTUATOR_STATE:
            status = data.get("status")
            c.execute("UPDATE sessions SET status = ? WHERE session_id = ?", (status, session_id))
            if status == "done":
                c.execute("UPDATE sessions SET execution_ms = ? WHERE session_id = ?",
                          (data.get("execution_ms"), session_id))
                if "captured" in trace and "done" in trace:
                    E2E.observe((trace["done"] - trace["captured"]) / 1000)
                    prev = "captured"
                    for stage in STAGES[1:]:
                        if stage in trace:
                            STAGE.labels(f"{prev}->{stage}").observe((trace[stage] - trace[prev]) / 1000)
                            prev = stage
            elif status == "failed":
                FAILED.inc()
    svc.track(subject)


@svc.on_startup
async def start():
    init_db()
    for pattern in Subjects.ALL_DOMAIN:
        await svc.bus.subscribe_raw(pattern, on_event)


def _session_row(r) -> dict:
    d = dict(r)
    if d.get("captured") and d.get("done"):
        d["e2e_ms"] = d["done"] - d["captured"]
        stages = {}
        prev = "captured"
        for s in STAGES[1:]:
            if d.get(s):
                stages[f"{prev}->{s}"] = d[s] - d[prev]
                prev = s
        d["stages_ms"] = stages
    return d


@app.get("/telemetry/sessions", tags=["telemetry"])
async def sessions(limit: int = 100):
    with conn() as c:
        rows = c.execute("SELECT * FROM sessions ORDER BY captured DESC NULLS LAST LIMIT ?", (limit,)).fetchall()
    return [_session_row(r) for r in rows]


@app.get("/telemetry/sessions/{session_id}", tags=["telemetry"])
async def session(session_id: str):
    with conn() as c:
        row = c.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        events = c.execute("SELECT subject, producer, ts, received_ts FROM events WHERE session_id = ? ORDER BY id",
                           (session_id,)).fetchall()
    if not row:
        return {"session_id": session_id, "found": False}
    return {**_session_row(row), "events": [dict(e) for e in events]}


@app.get("/telemetry/summary", tags=["telemetry"])
async def summary(limit: int = 1000):
    """Resumen estadístico para la evaluación: media, mediana, p95 por etapa."""
    with conn() as c:
        rows = c.execute("SELECT * FROM sessions WHERE done IS NOT NULL ORDER BY captured DESC LIMIT ?",
                         (limit,)).fetchall()
    sessions = [_session_row(r) for r in rows]
    if not sessions:
        return {"sessions": 0}

    def stats(values: list[int]) -> dict:
        values = sorted(values)
        # p95 por rango más cercano (⌈0,95·n⌉-ésimo valor). Antes se usaba round(), que en Python
        # redondea al par: con n=30 daba el 28.º valor en lugar del 29.º.
        p95 = values[max(0, math.ceil(0.95 * len(values)) - 1)]
        return {"n": len(values), "mean": round(statistics.mean(values), 1), "median": statistics.median(values),
                "p95": p95, "min": values[0], "max": values[-1],
                "stdev": round(statistics.stdev(values), 1) if len(values) > 1 else 0}

    out = {"sessions": len(sessions), "e2e_ms": stats([s["e2e_ms"] for s in sessions]), "stages_ms": {}}
    keys = {k for s in sessions for k in s.get("stages_ms", {})}
    for k in sorted(keys):
        out["stages_ms"][k] = stats([s["stages_ms"][k] for s in sessions if k in s.get("stages_ms", {})])
    out["unknown_gloss_rate"] = round(sum(s.get("unknown") or 0 for s in sessions) /
                                      max(1, sum((s.get("gestures") or 0) + (s.get("unknown") or 0) for s in sessions)), 3)
    out["failed"] = sum(1 for s in sessions if s.get("status") == "failed")
    return out
