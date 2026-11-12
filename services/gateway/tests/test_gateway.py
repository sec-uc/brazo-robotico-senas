"""Gateway: frontera de seguridad (QA-09), proxy por prefijo y canal SSE por sesión."""
import asyncio
import json

import httpx
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from rsp_common.auth import require_user
from rsp_testkit import make_token

PUBLIC = {"/auth/token", "/health", "/metrics", "/v1/system/health"}


@pytest.fixture
def gw(monkeypatch, auth_env, fake_bus):
    import app.main as m
    m.svc.bus = fake_bus
    calls = []

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"upstream": str(request.url)}, headers={"x-up": "1"})

    monkeypatch.setattr(m, "client", httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
    monkeypatch.setitem(m._owners, "s1", "operator")  # sesión creada por el usuario de make_token()
    return m, TestClient(m.app), calls


def test_session_belongs_to_its_creator(gw):
    """Otro usuario no puede enviar a una sesión ni escuchar sus eventos (404); un admin sí puede verla."""
    m, c, _ = gw
    sid = c.post("/v1/sessions", headers={"Authorization": f"Bearer {make_token('ana')}"}).json()["session_id"]
    otro = {"Authorization": f"Bearer {make_token('beto')}"}
    assert c.post(f"/v1/sessions/{sid}/text", headers=otro, content=b"{}").status_code == 404
    assert c.get(f"/v1/sessions/{sid}/events", headers=otro).status_code == 404
    assert c.get(f"/v1/sessions/{sid}", headers=otro).status_code == 404
    admin = {"Authorization": f"Bearer {make_token('root', roles=('admin',))}"}
    assert c.get(f"/v1/sessions/{sid}", headers=admin).status_code == 200


def _protected_routes(app):
    out = []
    for r in app.routes:
        if not isinstance(r, APIRoute):
            continue
        # require_user puede ir directo o dentro de require_role (ADR-011)
        deps, stack = set(), list(r.dependant.dependencies)
        while stack:
            d = stack.pop()
            deps.add(d.call)
            stack.extend(d.dependencies)
        for method in sorted(r.methods):
            out.append((method, r.path, require_user in deps))
    return out


def _concrete(path: str) -> str:
    return path.replace("{session_id}", "s1").replace("{path:path}", "HOLA").replace("{path}", "HOLA")


def test_every_non_public_route_requires_a_token(gw):
    m, _, _ = gw
    routes = _protected_routes(m.app)
    unprotected = {p for _, p, prot in routes if not prot}
    assert unprotected == PUBLIC, f"rutas sin require_user: {unprotected - PUBLIC}"
    assert sum(prot for *_, prot in routes) >= 15


@pytest.mark.parametrize("header", [
    None,
    "Bearer basura",
    "Bearer " + make_token(secret="otro-secreto-distinto-de-32-bytes-o-mas"),
    "Bearer " + make_token(ttl=-30),
    "Basic dXNlcjpwYXNz",
])
def test_requests_without_valid_token_get_401_on_all_protected_routes(gw, header):
    """QA-09: el 100 % de las peticiones sin token válido se rechazan, sin tocar ningún upstream."""
    m, c, calls = gw
    headers = {"Authorization": header} if header else {}
    protected = [(meth, p) for meth, p, prot in _protected_routes(m.app) if prot]
    codes = [c.request(meth, _concrete(p), headers=headers).status_code for meth, p in protected]
    assert codes == [401] * len(protected)
    assert calls == []


def test_valid_token_reaches_the_actuator_upstream(gw):
    m, c, calls = gw
    tok = make_token("ana")
    r = c.post("/v1/actuator/servo", json={"servo_id": 1, "angle": 90},
               headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    (req,) = calls
    assert str(req.url) == f"{m.UPSTREAMS['actuator-control']}/actuator/servo"
    assert json.loads(req.content) == {"servo_id": 1, "angle": 90}
    assert req.headers["authorization"] == f"Bearer {tok}"  # el upstream también puede verificar


@pytest.mark.parametrize("method,path,service,upstream_path", [
    ("POST", "/v1/sessions/s1/text", "voice-capture", "/sessions/s1/text"),
    ("POST", "/v1/sessions/s1/audio", "voice-capture", "/sessions/s1/audio"),
    ("GET", "/v1/sessions/s1", "telemetry", "/telemetry/sessions/s1"),
    ("GET", "/v1/signs", "sign-translation", "/signs"),
    ("PUT", "/v1/signs/HOLA", "sign-translation", "/signs/HOLA"),
    ("GET", "/v1/telemetry/summary", "telemetry", "/telemetry/summary"),
    ("POST", "/v1/nlp/gloss", "nlp", "/gloss"),
    ("GET", "/v1/nlp/dictionary", "nlp", "/dictionary"),
])
def test_prefix_routing(gw, method, path, service, upstream_path):
    m, c, calls = gw
    r = c.request(method, path + "?limit=5", headers={"Authorization": f"Bearer {make_token()}"}, content=b"{}")
    assert r.status_code == 200
    assert calls[-1].url.path == upstream_path and calls[-1].url.params["limit"] == "5"
    assert str(calls[-1].url).startswith(m.UPSTREAMS[service])


def test_login_is_public_and_proxied_to_auth(gw):
    m, c, calls = gw
    assert c.post("/auth/token", json={"username": "a", "password": "b"}).status_code == 200
    assert calls[-1].url.path == "/token"


def test_upstream_down_is_502(gw, monkeypatch):
    m, c, _ = gw

    def down(request):
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(m, "client", httpx.AsyncClient(transport=httpx.MockTransport(down)))
    r = c.get("/v1/signs", headers={"Authorization": f"Bearer {make_token()}"})
    assert r.status_code == 502 and "sign-translation no disponible" in r.json()["detail"]


def test_client_host_header_is_not_forwarded(gw):
    """El Host del cliente no viaja al upstream (si no, el servicio interno recibiría el host público)."""
    m, c, calls = gw
    c.get("/v1/signs", headers={"Authorization": f"Bearer {make_token()}", "Host": "publico.example"})
    assert calls[-1].headers["host"] == "sign-translation:8000"


def test_create_session_returns_new_ids(gw):
    _, c, _ = gw
    h = {"Authorization": f"Bearer {make_token()}"}
    a, b = c.post("/v1/sessions", headers=h).json(), c.post("/v1/sessions", headers=h).json()
    assert a["session_id"] != b["session_id"] and len(a["session_id"]) == 32


def test_system_health_marks_down_services(gw, monkeypatch):
    """QA-06: la salud agregada distingue servicios caídos sin fallar el gateway."""
    m, c, _ = gw

    def partial(request):
        if request.url.host == "stt":
            raise httpx.ConnectError("caído", request=request)
        return httpx.Response(200, json={"status": "ok"})

    monkeypatch.setattr(m, "client", httpx.AsyncClient(transport=httpx.MockTransport(partial)))
    h = c.get("/v1/system/health").json()
    assert h["stt"]["status"] == "down"
    assert all(h[s]["status"] == "ok" for s in m.UPSTREAMS if s != "stt")
    assert h["gateway"]["bus_connected"] is True


# ------------------------------------------------------------------ SSE por sesión
def _sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(l.split(": ", 1) for l in block.splitlines() if ": " in l and not l.startswith(":"))
        if "event" in lines:
            out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_sse_streams_only_own_session_and_closes_on_done(gw):
    m = gw[0]

    async def run():
        resp = await m.session_events("s1", request=None, user={"sub": "operator"}, until_done=True, timeout_s=5)
        chunks = []

        async def consume():
            async for c in resp.body_iterator:
                chunks.append(c)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0)
        await m._fanout("nlp.gloss.sequence", {"session_id": "otra", "glosses": ["X"]})
        await m._fanout("nlp.gloss.sequence", {"session_id": "s1", "glosses": ["HOLA"]})
        await m._fanout("actuator.state", {"session_id": "s1", "status": "executing"})
        await m._fanout("actuator.state", {"session_id": "s1", "status": "done"})
        await asyncio.wait_for(task, 2)
        return "".join(chunks)

    events = _sse(asyncio.run(run()))
    assert [e for e, _ in events] == ["nlp.gloss.sequence", "actuator.state", "actuator.state"]
    assert all(d["session_id"] == "s1" for _, d in events)
    assert m._subscribers == {}  # se limpia al cerrar


def test_sse_times_out(gw):
    m = gw[0]

    async def run():
        resp = await m.session_events("s9", request=None, user={}, until_done=True, timeout_s=0.05)
        return "".join([c async for c in resp.body_iterator])

    assert "event: timeout" in asyncio.run(run())


@pytest.mark.parametrize("method,path,body", [
    ("POST", "/v1/sessions", None),
    ("POST", "/v1/sessions/s1/text", {"text": "hola"}),
    ("POST", "/v1/actuator/servo", {"servo_id": 1, "angle": 90}),
    ("PUT", "/v1/signs/HOLA", {"gloss": "HOLA", "angles": [90] * 15}),
    ("DELETE", "/v1/signs/HOLA", None),
])
def test_viewer_role_cannot_move_the_arm_or_edit_vocabulary(gw, method, path, body):
    """QA-09 / ADR-011: un token válido sin rol de operador recibe 403 y no llega a ningún servicio."""
    m, c, calls = gw
    tok = make_token("visor", roles=("viewer",))
    r = c.request(method, path, json=body, headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403 and calls == []


def test_viewer_role_can_read(gw):
    m, c, calls = gw
    tok = make_token("visor", roles=("viewer",))
    r = c.get("/v1/signs/HOLA", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code != 403 and r.status_code != 401


def test_telemetry_proxy_guards_sessions(gw):
    """La lista de sesiones es solo para admin y el detalle, para su dueño, también por la ruta genérica."""
    m, c, _ = gw
    op = {"Authorization": f"Bearer {make_token('beto')}"}
    assert c.get("/v1/telemetry/sessions", headers=op).status_code == 403
    assert c.get("/v1/telemetry/sessions/s1", headers=op).status_code == 404
    assert c.get("/v1/telemetry/summary/../sessions/s1", headers=op).status_code == 404
    assert c.get("/v1/telemetry/sessions%3Fx=1", headers=op).status_code == 400  # «?» codificado no cuela la lista
    assert c.get("/v1/telemetry/sessions%23", headers=op).status_code == 400
    assert c.get("/v1/telemetry/summary", headers=op).status_code == 200
    admin = {"Authorization": f"Bearer {make_token('root', roles=('admin',))}"}
    assert c.get("/v1/telemetry/sessions", headers=admin).status_code == 200
