"""API Gateway.

Único punto de entrada para los clientes (app Flutter, web, scripts de
evaluación). Responsabilidades: autenticación (verifica JWT emitidos por
`auth`), enrutamiento por prefijo hacia los servicios internos, y un canal de
eventos por sesión (Server-Sent Events) para que el cliente siga en vivo el
estado del robot sin hacer polling (resuelve P7 del diagnóstico).

Los servicios internos NO se exponen fuera de la red Docker: el gateway es
la frontera de seguridad (ADR-006).

Rutas públicas:
  POST /auth/token                          → auth
  POST /v1/sessions                         crea un id de sesión
  POST /v1/sessions/{id}/audio              → voice-capture (multipart)
  POST /v1/sessions/{id}/text               → voice-capture
  GET  /v1/sessions/{id}/events             SSE con el estado del pipeline
  GET  /v1/sessions/{id}                    → telemetry
  /v1/signs/**                              → sign-translation
  /v1/actuator/**                           → actuator-control
  /v1/telemetry/**                          → telemetry
  /v1/nlp/gloss                             → nlp
"""
from __future__ import annotations

import asyncio
import json
import os
import posixpath
import re
from collections import defaultdict

import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse

from rsp_common.auth import has_role, require_role, require_user

# ADR-011: lo que mueve el brazo o cambia el vocabulario exige rol de operador
OPERATOR = require_role("operator", "admin")
from rsp_common.events import Subjects, new_id
from rsp_common.service import Service

svc = Service("gateway")
app = svc.app
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
                   allow_methods=["*"], allow_headers=["*"])

UPSTREAMS = {
    "auth": os.getenv("AUTH_URL", "http://auth:8000"),
    "voice-capture": os.getenv("VOICE_CAPTURE_URL", "http://voice-capture:8000"),
    "nlp": os.getenv("NLP_URL", "http://nlp:8000"),
    "sign-translation": os.getenv("SIGN_TRANSLATION_URL", "http://sign-translation:8000"),
    "actuator-control": os.getenv("ACTUATOR_URL", "http://actuator-control:8000"),
    "telemetry": os.getenv("TELEMETRY_URL", "http://telemetry:8000"),
    "stt": os.getenv("STT_URL", "http://stt:8000"),
}
HOP_HEADERS = {"host", "content-length", "connection", "transfer-encoding"}
client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))

# suscriptores SSE por sesión
_subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)
# ponytail: dueños de sesión en memoria; se pierden al reiniciar la pasarela y no se comparten entre
# réplicas (pasarlos a la base o al token de sesión si la pasarela se replica).
_owners: dict[str, str] = {}
_SEGMENT = re.compile(r"[A-Za-z0-9_.-]+")


def _owned(session_id: str, user: dict) -> None:
    """Solo el creador de la sesión (o un admin) la usa o la escucha; si no, 404 para no revelar que existe."""
    if _owners.get(session_id) != user.get("sub") and not has_role(user, "admin"):
        raise HTTPException(404, "sesión no encontrada")


async def _fanout(subject: str, data: dict) -> None:
    for q in list(_subscribers.get(data.get("session_id", ""), [])):
        q.put_nowait({"subject": subject, "data": data})


@svc.on_startup
async def start():
    for pattern in ["voice.text.>", "nlp.>", "signs.>", "actuator.>"]:
        await svc.bus.subscribe_raw(pattern, _fanout)


@svc.on_shutdown
async def stop():
    await client.aclose()


async def _proxy(service: str, path: str, request: Request) -> Response:
    url = f"{UPSTREAMS[service]}{path}"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
    body = await request.body()
    try:
        with svc.timer(f"proxy:{service}"):
            r = await client.request(request.method, url, params=request.query_params, content=body, headers=headers)
    except httpx.HTTPError as exc:
        svc.track(service, "upstream_error")
        raise HTTPException(502, f"{service} no disponible: {exc}") from exc
    svc.track(service)
    return Response(content=r.content, status_code=r.status_code,
                    media_type=r.headers.get("content-type"))


# ----------------------------------------------------------------- auth
@app.post("/auth/token", tags=["auth"])
async def token(request: Request):
    return await _proxy("auth", "/token", request)


# ----------------------------------------------------------------- sesiones
@app.post("/v1/sessions", tags=["sessions"])
async def create_session(user: dict = Depends(OPERATOR)):
    sid = new_id()
    _owners[sid] = user.get("sub")
    return {"session_id": sid}


@app.post("/v1/sessions/{session_id}/audio", tags=["sessions"])
async def session_audio(session_id: str, request: Request, user: dict = Depends(OPERATOR)):
    _owned(session_id, user)
    return await _proxy("voice-capture", f"/sessions/{session_id}/audio", request)


@app.post("/v1/sessions/{session_id}/text", tags=["sessions"])
async def session_text(session_id: str, request: Request, user: dict = Depends(OPERATOR)):
    _owned(session_id, user)
    return await _proxy("voice-capture", f"/sessions/{session_id}/text", request)


@app.get("/v1/sessions/{session_id}", tags=["sessions"])
async def session_detail(session_id: str, request: Request, user: dict = Depends(require_user)):
    _owned(session_id, user)
    return await _proxy("telemetry", f"/telemetry/sessions/{session_id}", request)


@app.get("/v1/sessions/{session_id}/events", tags=["sessions"])
async def session_events(session_id: str, request: Request, user: dict = Depends(require_user),
                         until_done: bool = True, timeout_s: float = 60.0):
    """Server-Sent Events con cada evento del pipeline para la sesión."""
    _owned(session_id, user)
    q: asyncio.Queue = asyncio.Queue()
    _subscribers[session_id].append(q)

    async def gen():
        try:
            deadline = asyncio.get_running_loop().time() + timeout_s
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    yield "event: timeout\ndata: {}\n\n"
                    break
                try:
                    item = await asyncio.wait_for(q.get(), timeout=min(15.0, remaining))
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"event: {item['subject']}\ndata: {json.dumps(item['data'])}\n\n"
                d = item["data"]
                if until_done and item["subject"] == Subjects.ACTUATOR_STATE and d.get("status") in ("done", "failed"):
                    break
        finally:
            _subscribers[session_id].remove(q)
            if not _subscribers[session_id]:
                _subscribers.pop(session_id, None)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ----------------------------------------------------------------- proxies por prefijo
@app.api_route("/v1/signs/{path:path}", methods=["GET", "PUT", "DELETE", "POST"], tags=["proxy"])
async def signs(path: str, request: Request, user: dict = Depends(require_user)):
    # leer y planificar (vista previa) es libre para usuarios autenticados; escribir, solo operador
    if request.method in ("PUT", "DELETE") and not has_role(user, "operator", "admin"):
        raise HTTPException(403, "se requiere el rol operator o admin")
    return await _proxy("sign-translation", f"/signs/{path}".rstrip("/"), request)


@app.api_route("/v1/signs", methods=["GET"], tags=["proxy"])
async def signs_root(request: Request, user: dict = Depends(require_user)):
    return await _proxy("sign-translation", "/signs", request)


@app.api_route("/v1/actuator/{path:path}", methods=["GET", "POST"], tags=["proxy"])
async def actuator(path: str, request: Request, user: dict = Depends(require_user)):
    if request.method != "GET" and not has_role(user, "operator", "admin"):
        raise HTTPException(403, "se requiere el rol operator o admin")
    return await _proxy("actuator-control", f"/actuator/{path}", request)


@app.api_route("/v1/telemetry/{path:path}", methods=["GET"], tags=["proxy"])
async def telemetry(path: str, request: Request, user: dict = Depends(require_user)):
    parts = [p for p in posixpath.normpath("/" + path).split("/") if p]  # normaliza ./, // y ../
    if not all(_SEGMENT.fullmatch(p) for p in parts):  # sin ?, #, %, ; ni \: lo que se comprueba es lo que llega arriba
        raise HTTPException(400, "ruta no válida")
    if parts and parts[0] == "sessions":  # sesiones: la de uno (o cualquiera, si es admin); la lista, solo admin
        if len(parts) > 1:
            _owned(parts[1], user)
        elif not has_role(user, "admin"):
            raise HTTPException(403, "se requiere el rol admin")
    return await _proxy("telemetry", "/telemetry/" + "/".join(parts), request)


@app.post("/v1/nlp/gloss", tags=["proxy"])
async def nlp_gloss(request: Request, user: dict = Depends(require_user)):
    return await _proxy("nlp", "/gloss", request)


@app.get("/v1/nlp/dictionary", tags=["proxy"])
async def nlp_dictionary(request: Request, user: dict = Depends(require_user)):
    return await _proxy("nlp", "/dictionary", request)


@app.get("/v1/system/health", tags=["ops"])
async def system_health():
    """Agrega la salud de todos los servicios (lo que consume Monitoreo y la app)."""
    out = {}
    for name, base in UPSTREAMS.items():
        try:
            r = await client.get(f"{base}/health", timeout=3)
            out[name] = r.json()
        except Exception as exc:  # noqa: BLE001
            out[name] = {"status": "down", "error": str(exc)}
    out["gateway"] = {"status": "ok", "bus_connected": svc.bus.connected}
    return out
