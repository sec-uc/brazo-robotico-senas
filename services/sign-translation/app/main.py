"""Servicio de traducción a lengua de señas.

Consume `nlp.gloss.sequence` y produce `signs.gesture.plan`: para cada glosa
busca una seña en su base de datos; si no existe, la deletrea letra a letra
con el alfabeto dactilológico (A–Z); si tampoco hay letra, la marca como
desconocida (el plan sigue: el robot ejecuta lo que sabe y telemetría
registra la brecha de vocabulario).

Expone además un CRUD de señas para que el equipo lingüístico amplíe el
vocabulario sin desplegar nada (antes: editar un Excel y recompilar la app).
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import Depends, HTTPException

from rsp_common.auth import require_user
from rsp_common.events import Gesture, GesturePlan, GlossSequence, ServoTarget, Subjects, now_ms
from rsp_common.service import Service

from .repository import Sign, SignRepository

svc = Service("sign-translation")
app = svc.app

# base propia del servicio (ADR-014): PostgreSQL en el despliegue; un archivo SQLite sin contenedores
DB_PATH = os.getenv("SIGNS_DATABASE_URL") or os.getenv("SIGNS_DB_PATH", "/data/signs.db")
SEED_PATH = os.getenv("SIGNS_SEED_PATH", "/data/signs_seed.json")
FINGERSPELL_HOLD_MS = int(os.getenv("FINGERSPELL_HOLD_MS", "450"))
repo = SignRepository(DB_PATH)


def _to_gesture(sign: Sign, kind: str = "sign") -> Gesture:
    return Gesture(
        gloss=sign.gloss,
        kind=kind,  # type: ignore[arg-type]
        targets=[ServoTarget(servo_id=i + 1, angle=a) for i, a in enumerate(sign.angles)],
        hold_ms=sign.hold_ms if kind == "sign" else FINGERSPELL_HOLD_MS,
        image_path=sign.image_path,
        kichwa=sign.kichwa,
        keyframes=[[ServoTarget(servo_id=i + 1, angle=a) for i, a in enumerate(pose)] for pose in sign.motion],
    )


def plan_for(glosses: list[str], fingerspell: bool = True) -> tuple[list[Gesture], list[str]]:
    """`fingerspell=False` reproduce el modo "mostrar no encontrada" de la app
    heredada: las glosas sin seña se reportan como desconocidas sin deletrear."""
    gestures: list[Gesture] = []
    unknown: list[str] = []
    for gloss in glosses:
        sign = repo.get(gloss)
        if sign:
            gestures.append(_to_gesture(sign))
            continue
        if not fingerspell:
            unknown.append(gloss)
            continue
        # deletreo dactilológico; una letra sin seña se reporta como "PALABRA:L"
        # (mismo formato que el gesto) en lugar de perderse en silencio (QA-12)
        spelled = 0
        missing: list[str] = []
        for ch in gloss:
            if not ch.isalpha():
                continue
            letter = repo.get(ch)
            if letter:
                g = _to_gesture(letter, kind="fingerspell")
                g.gloss = f"{gloss}:{ch}"
                gestures.append(g)
                spelled += 1
            else:
                missing.append(ch)
        if spelled == 0:
            unknown.append(gloss)
        else:
            unknown.extend(f"{gloss}:{ch}" for ch in dict.fromkeys(missing))
    return gestures, unknown


@svc.on_startup
async def start():
    if Path(SEED_PATH).exists():
        n = repo.import_json(SEED_PATH, only_new=repo.count() > 0)
        svc.log.info("semilla: %d señas nuevas desde %s", n, SEED_PATH)
    svc.log.info("señas disponibles: %d", repo.count())
    await svc.bus.subscribe(Subjects.GLOSS_SEQUENCE, handle_glosses, queue="signs")


async def handle_glosses(event: GlossSequence):  # type: ignore[override]
    with svc.timer("plan"):
        gestures, unknown = plan_for(event.glosses, bool(event.options.get("fingerspell", True)))
    out = GesturePlan(
        session_id=event.session_id,
        gestures=gestures,
        unknown_glosses=unknown,
        trace={**event.trace, "planned": now_ms()},
        options=event.options,
    )
    await svc.bus.publish(Subjects.GESTURE_PLAN, out)
    svc.track(Subjects.GLOSS_SEQUENCE)
    svc.log.info("[%s] %d glosas → %d gestos (desconocidas: %s)",
                 event.session_id[:8], len(event.glosses), len(gestures), unknown)


# ----------------------------------------------------------------- API de vocabulario
@app.get("/signs", tags=["signs"])
async def list_signs(prefix: str | None = None, limit: int = 500):
    return {"count": repo.count(), "items": repo.list(prefix, limit)}


@app.get("/signs/{gloss}", tags=["signs"])
async def get_sign(gloss: str):
    if gloss.lower() == "plan":
        raise HTTPException(405, "use POST /signs/plan")
    sign = repo.get(gloss)
    if not sign:
        raise HTTPException(404, "seña no encontrada")
    return sign


@app.put("/signs/{gloss}", tags=["signs"])
async def upsert_sign(gloss: str, sign: Sign, user: dict = Depends(require_user)):
    sign.gloss = gloss
    sign.source = sign.source or user.get("sub", "api")
    return repo.upsert(sign)


@app.delete("/signs/{gloss}", tags=["signs"])
async def delete_sign(gloss: str, user: dict = Depends(require_user)):
    if not repo.delete(gloss):
        raise HTTPException(404, "seña no encontrada")
    return {"deleted": gloss.upper()}


@app.post("/plan", tags=["signs"])
@app.post("/signs/plan", tags=["signs"])
async def plan_endpoint(body: dict):
    """Planificación síncrona sin ejecutar (vista previa / confirmación manual en
    clientes, depuración): {"glosses": ["HOLA", "MAMA"], "fingerspell": true}"""
    gestures, unknown = plan_for([g.upper() for g in body.get("glosses", [])], bool(body.get("fingerspell", True)))
    return {"gestures": gestures, "unknown_glosses": unknown}
